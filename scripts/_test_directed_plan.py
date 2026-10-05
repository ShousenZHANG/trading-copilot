"""Real stored user-intent lifecycle with synthetic evidence and isolated accounts."""
import copy
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from _test_trade_plan import NOW, execution, policy
from _test_policy import engine_fixture
from copilot import service
from copilot.config import Config, AdvisorConfig, IbkrConfig, EtfConfig, GoldConfig, NotifyConfig
from copilot.broker import seal_execution_snapshot
from copilot.journal import save_snapshot as save_research
from copilot.plan_store import save_snapshot, create_plan, review_plan, revalidate_plan
from copilot.manual_intent import record_intent, read_intent, effective_policy


def instruction(**changes):
    value=dict(kind='buy_budget_usd', targets={'QQQ':'2000'},allowed_sells=[],
        risk_limits=dict(cash_floor_pct='0',max_single_name_pct='1',max_trade_notional_pct='1',long_term_nav_cap_pct='1'))
    value.update(changes)
    return value


class DirectedPlan(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'test.sqlite'
        self.snap=execution()
        self.research=engine_fixture()

    def stage(self, intent=None, snap=None):
        snap=seal_execution_snapshot(snap or self.snap)
        save_snapshot(snap,db_path=self.db,now=NOW)
        save_research(self.research,db_path=self.db)
        receipt=record_intent(snap['snapshot_id'],self.research['snapshot_id'],intent or instruction(),
            'I confirm this one-off allocation',expected_account_version=snap['account_version'],db_path=self.db,now=NOW)
        adopted=read_intent(receipt['intent_id'],db_path=self.db)
        settings=effective_policy(policy(),adopted)
        return snap,adopted,settings

    def create(self, intent=None, snap=None):
        snap,adopted,settings=self.stage(intent,snap)
        return create_plan(execution_snapshot=snap,research_snapshot=self.research,adoption=adopted,
                           policy=settings,db_path=self.db,now=NOW)

    def test_budget_from_live_ask_and_fees_never_promises_alpha(self):
        plan=self.create()
        self.assertEqual(plan['status'],'ready_for_review',plan['issues'])
        self.assertEqual(plan['orders'][0]['quantity'],'19')
        self.assertEqual(plan['orders'][0]['limit_price'],'100.01')
        self.assertEqual(plan['cash_plan']['buy_cost'],'1901.19')
        self.assertEqual(plan['strategy_validation'],'not_claimed_user_directed')
        self.assertFalse(plan['price_is_optimal'])
        self.assertFalse(plan['order_submitted'])

    def test_actual_income_etfs_can_be_user_directed_without_historical_adoption(self):
        self.research=engine_fixture(symbols=('QQQI','JEPQ'))
        snap=self.snap
        snap['quotes']={'QQQI':snap['quotes']['QQQ'],'JEPQ':snap['quotes']['SPY']}
        plan=self.create(instruction(targets={'QQQI':'2000'}),snap)
        self.assertEqual(plan['orders'][0]['instrument_id'],'QQQI',plan['issues'])

    def test_no_usd_funds_means_no_buy_despite_account_nav(self):
        self.snap['cash']['USD'].update(gross='0',settled='0',available='0')
        plan=self.create()
        self.assertEqual(plan['orders'],[])
        self.assertEqual(plan['cash_plan']['buy_cost'],'0')

    def test_sales_cannot_fund_the_same_card_buys(self):
        self.snap['cash']['USD'].update(gross='0',settled='0',available='0')
        self.snap['positions']=[dict(instrument_id='SPY',quantity='40',con_id=2,currency='USD',account_key='account-hash')]
        plan=self.create(instruction(kind='target_shares',targets={'QQQ':'20','SPY':'10'},allowed_sells=['SPY']))
        self.assertEqual([(o['instrument_id'],o['side'],o['quantity']) for o in plan['orders']],[('SPY','sell','30')])
        self.assertFalse(plan['cash_plan']['unfilled_sales_fund_buys'])

    def test_unmentioned_positions_preserved_and_unauthorized_sale_blocked(self):
        self.snap['positions']=[dict(instrument_id='SPY',quantity='5',con_id=2,currency='USD',account_key='account-hash')]
        plan=self.create()
        self.assertEqual(plan['preserved_positions'],['SPY'])
        bad=self.create(instruction(kind='target_shares',targets={'SPY':'0'}))
        self.assertEqual(bad['status'],'blocked')
        self.assertEqual(bad['orders'],[])

    def test_delayed_or_expired_quote_cannot_produce_orders(self):
        for change in ({'actual_data_type':3},{'valid_until':(NOW-timedelta(seconds=1)).isoformat()}):
            with self.subTest(change=change):
                snap=copy.deepcopy(self.snap)
                snap['quotes']['QQQ'].update(change)
                plan=self.create(snap=snap)
                self.assertEqual(plan['orders'],[])
                self.assertEqual(plan['status'],'blocked')

    def test_nonaffirmative_intent_writes_nothing(self):
        save_snapshot(self.snap,db_path=self.db,now=NOW)
        save_research(self.research,db_path=self.db)
        for text in ['I do not accept this plan','I refuse to approve this plan','我不予批准这张交易卡']:
            with self.assertRaises(ValueError):
                record_intent(self.snap['snapshot_id'],self.research['snapshot_id'],instruction(),text,
                    expected_account_version=self.snap['account_version'],db_path=self.db,now=NOW)

    def test_review_and_preflight_do_not_fill_and_changed_account_retires_card(self):
        plan=self.create()
        reviewed=review_plan(plan['plan_id'],statement='I approve this plan',expected_version=1,db_path=self.db,now=NOW)
        self.assertEqual(reviewed['review_state'],'reviewed')
        intent=read_intent(plan['rule_id'],db_path=self.db)
        fresh=copy.deepcopy(self.snap)
        fresh['account_version']='changed-after-fill'
        fresh=seal_execution_snapshot(fresh)
        result=revalidate_plan(plan['plan_id'],execution_snapshot=fresh,policy=effective_policy(policy(),intent),
            expected_version=2,db_path=self.db,now=NOW)
        self.assertEqual(result['revalidation']['status'],'blocked')
        self.assertEqual(result['orders'],[])

    def test_fresh_same_account_preflight_passes(self):
        plan=self.create()
        review_plan(plan['plan_id'],statement='I approve this plan',expected_version=1,db_path=self.db,now=NOW)
        intent=read_intent(plan['rule_id'],db_path=self.db)
        result=revalidate_plan(plan['plan_id'],execution_snapshot=self.snap,policy=effective_policy(policy(),intent),
            expected_version=2,db_path=self.db,now=NOW)
        self.assertEqual(result['revalidation']['status'],'pass',result['revalidation'])
        self.assertFalse(result['order_submitted'])

    def test_replayed_old_snapshot_cannot_pass_after_newer_observation(self):
        self.snap['positions']=[dict(instrument_id='SPY',quantity='40',con_id=2,currency='USD',account_key='account-hash')]
        self.snap['cash']['USD'].update(gross='0',settled='0',available='0')
        self.snap=seal_execution_snapshot(self.snap)
        plan=self.create(instruction(kind='target_shares',targets={'SPY':'20'},allowed_sells=['SPY']))
        review_plan(plan['plan_id'],statement='I approve this plan',expected_version=1,db_path=self.db,now=NOW)
        newest=copy.deepcopy(self.snap)
        newest.update(account_version='new-account-binding',as_of=NOW.isoformat())
        save_snapshot(seal_execution_snapshot(newest),db_path=self.db,now=NOW)
        intent=read_intent(plan['rule_id'],db_path=self.db)
        result=revalidate_plan(plan['plan_id'],execution_snapshot=self.snap,policy=effective_policy(policy(),intent),
            expected_version=2,db_path=self.db,now=NOW)
        self.assertEqual(result['review_state'],'needs_recompile')
        self.assertEqual(result['revalidation']['status'],'blocked')
        self.assertEqual(result['orders'],[])

    def test_simultaneous_reviewed_cards_cannot_spend_cash_twice(self):
        first=self.create()
        review_plan(first['plan_id'],statement='I approve this plan',expected_version=1,db_path=self.db,now=NOW)
        second=self.create(instruction(targets={'SPY':'3000'}))
        self.assertEqual(second['orders'],[])
        self.assertIn('already binds',' '.join(second['issues']))

    def test_public_facades_reject_changed_risk_configuration(self):
        settings=Config(False,'isolated',EtfConfig(),GoldConfig(),NotifyConfig(),advisor=AdvisorConfig(fee_model_confirmed=True,min_commission_usd=1),ibkr=IbkrConfig(enabled=True))
        _,adopted,_=self.stage()
        with patch('copilot.config.load_config',return_value=settings):
            created=service.prepare_directed_plan(adopted['rule_id'],db_path=self.db,now=NOW)['plan']
            viewed=service.get_trade_plan(created['plan_id'],db_path=self.db,now=NOW)['plan']
            self.assertEqual(viewed['review_state'],'awaiting_review',viewed['issues'])
        changed=Config(False,'isolated',EtfConfig(),GoldConfig(),NotifyConfig(),advisor=AdvisorConfig(fee_model_confirmed=True,min_commission_usd=2),ibkr=IbkrConfig(enabled=True))
        with patch('copilot.config.load_config',return_value=changed):
            viewed=service.get_trade_plan(created['plan_id'],db_path=self.db,now=NOW)['plan']
            self.assertEqual(viewed['review_state'],'needs_recompile')
            self.assertEqual(viewed['orders'],[])


if __name__=='__main__':
    unittest.main()
