"""Replay current advisor contracts with frozen fixture inputs and real core calls.

This is a deterministic workflow evaluation, not a generated-model benchmark.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import platform
import sys
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))

SUITES=('_test_directed_plan.py','_test_plan_confirmations.py','_test_opening_balance.py',
        '_test_readiness.py','_test_income.py','_test_cash_ledger.py','_test_journal.py','_test_plan_store.py')


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run():
    from copilot import service, plan_store
    trace=[]
    results=[]
    def wrap(name,function):
        def call(*args,**kwargs):
            event={'tool':name,'sequence':len(trace)+1}
            trace.append(event)
            try:
                output=function(*args,**kwargs)
                if isinstance(output,dict):
                    view=output.get('plan',output)
                    event.update(status=view.get('status'),review_state=view.get('review_state'),
                        committed=view.get('committed'),orders_count=len(view.get('orders',[])),
                        output_sha256=hashlib.sha256(json.dumps(service.public_response(output),sort_keys=True,default=str).encode()).hexdigest())
                return output
            except Exception as exc:
                event.update(error_type=type(exc).__name__)
                raise
        return call
    class Result(unittest.TextTestResult):
        def startTest(self,test):
            self.trace_start=len(trace)
            super().startTest(test)
        def addSuccess(self,test):
            results.append(dict(scenario=test.id(),outcome='pass',trace_range=[self.trace_start,len(trace)]))
            super().addSuccess(test)
        def addFailure(self,test,err):
            results.append(dict(scenario=test.id(),outcome='fail'))
            super().addFailure(test,err)
        def addError(self,test,err):
            results.append(dict(scenario=test.id(),outcome='error'))
            super().addError(test,err)
    with ExitStack() as stack:
        for module,names in ((service,('prepare_directed_plan','confirm_manual_intent','get_trade_plan','confirm_plan_review','revalidate_trade_plan','income_report')),
                             (plan_store,('create_plan','review_plan','revalidate_plan','record_cash_flow','record_mode_allocations'))):
            for name in names:
                stack.enter_context(patch.object(module,name,wrap(module.__name__+'.'+name,getattr(module,name))))
        suites=unittest.TestSuite(unittest.defaultTestLoader.discover(str(ROOT/'scripts'),pattern=p) for p in SUITES)
        log=io.StringIO()
        result=unittest.TextTestRunner(stream=log,verbosity=0,resultclass=Result).run(suites)
    paths=[ROOT/'scripts'/p for p in SUITES]
    paths += [ROOT/'scripts/copilot'/p for p in ('manual_intent.py','plan_store.py','journal.py','opening_balance.py','income.py','income_sources.py','cash_ledger.py','readiness.py','service.py')]
    paths += [ROOT/'mcps/copilot_mcp.py',ROOT/'.claude/skills/investment-chat/SKILL.md']
    paths += sorted((ROOT/'.claude/skills/investment-chat/references').glob('*.md'))
    paths += sorted((ROOT/'scripts').glob('_test_*.py'))
    paths += sorted((ROOT/'scripts/copilot').rglob('*.py'))
    paths += sorted((ROOT/'scripts/copilot/backtest').glob('*.json'))
    paths += sorted((ROOT/'mcps').glob('*.lock'))
    paths=sorted(set(paths))
    return dict(schema_version=1,evaluation='current_advisor_contract_replay',
        evidence_kind='synthetic_isolated_program_contracts',model=None,model_accuracy='not_measured',
        market_profitability='not_measured',created_at=datetime.now(timezone.utc).isoformat(),
        python_version=platform.python_version(),tool_manifest='mcps/copilot_mcp.py',
        tested=result.testsRun,passed=result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
        failed=len(result.failures)+len(result.errors),skipped=len(result.skipped),
        status='pass' if result.wasSuccessful() else 'fail',scenarios=results,tool_trace=trace,
        input_code_prompt_hashes={p.relative_to(ROOT).as_posix():fingerprint(p) for p in paths},
        diagnostics=log.getvalue())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,help='local output JSON; no private account is read')
    args=parser.parse_args()
    result=run()
    destination=Path(args.out)
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('evidence_kind','status','tested','passed','failed','model_accuracy')},ensure_ascii=False))
    return 0 if result['status']=='pass' else 1


if __name__=='__main__':
    raise SystemExit(main())
