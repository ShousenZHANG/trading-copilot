"""Complete prerequisite diagnosis and config routing regression contracts."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from io import StringIO
from copilot.config import Config, EtfConfig, GoldConfig, NotifyConfig
from copilot.readiness import diagnose
from _test_trade_plan import NOW, execution


def config():
    return Config(False,'isolated',EtfConfig(),GoldConfig(),NotifyConfig())


class Readiness(unittest.TestCase):
    def test_doctor_never_initializes_or_migrates_a_database(self):
        from copilot import service
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'missing.sqlite'
            with patch('copilot.config.load_config',return_value=config()):
                result=service.execution_readiness(db_path=db,execution_snapshot_id='absent',now=NOW)
            self.assertFalse(db.exists())
            self.assertIn('execution_snapshot',result['blockers'])
    def test_unfunded_foreign_ledger_does_not_block_verified_usd_but_debt_does(self):
        from _test_broker import raw_fixture, collect, ACCOUNT
        raw=raw_fixture()
        raw['account_values'].append(dict(account_id=ACCOUNT,tag='CashBalance',value='300',currency='AUD',value_scope='per_currency'))
        result=collect(raw)
        self.assertEqual(result['status'],'ready',result['issues'])
        self.assertIn('cash_or_reservation_unknown:AUD',result['warnings'])
        raw['account_values'][-1]['value']='-1'
        result=collect(raw)
        self.assertEqual(result['status'],'blocked')
        self.assertIn('negative_currency_cash:AUD',result['issues'])
    def test_all_independent_blockers_reported_without_broker_or_writes(self):
        report=diagnose(config(),now=NOW)
        self.assertTrue({'broker_enabled','broker_sdk','fee_model','strategy_adoption'}<=set(report['blockers']))
        self.assertIn('settled_usd',report['unknown'])
        self.assertFalse(report['order_authorization'])

    def test_settled_usd_and_each_delayed_quote_are_separate_blockers(self):
        snap=execution()
        snap['cash']['USD']['settled']=None
        for q in snap['quotes'].values(): q['actual_data_type']=3
        report=diagnose(config(),execution=snap,now=NOW)
        self.assertTrue({'settled_usd','quote_QQQ','quote_SPY'}<=set(report['blockers']))

    def test_global_config_path_is_honored_and_conflicting_paths_rejected(self):
        import copilot_cli
        with tempfile.TemporaryDirectory() as folder:
            isolated=str(Path(folder)/'explicit.toml')
            with patch('sys.argv',['cli','--config-path',isolated,'config']),patch('sys.stdout',new_callable=StringIO),patch('copilot.config.load_config',return_value=config()) as loader:
                self.assertEqual(copilot_cli.main(),0)
                loader.assert_called_once_with(isolated)
            with patch('sys.argv',['cli','--config-path',isolated,'config','--path',str(Path(folder)/'other.toml')]),patch('sys.stderr',new_callable=StringIO),patch('copilot.config.load_config') as loader:
                self.assertEqual(copilot_cli.main(),2)
                loader.assert_not_called()


if __name__=='__main__': unittest.main()
