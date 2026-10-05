"""Income research contracts: synthetic events and HTTP doubles, no state/network."""
from __future__ import annotations

import copy
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from copilot import income
from copilot.income_sources import (
    ISSUER_URLS,
    SourceError,
    exchange_url,
    parse_exchange_json,
    parse_issuer_html,
)

NOW = datetime(2026, 10, 5, 10, tzinfo=timezone.utc)


def events(amount="0.60"):
    rows = []
    for offset in range(13):
        year, month = divmod(2025*12+9+offset, 12)
        ex = date(year, month+1, 1)
        rows.append({"declaration_date": (ex-timedelta(days=1)).isoformat(), "ex_date": ex.isoformat(),
            "record_date": ex.isoformat(), "pay_date": (ex+timedelta(days=4)).isoformat(),
            "amount_per_share": amount, "amount_kind": "actual", "currency": "USD"})
    return rows


def issuer_html(symbol, rows=None):
    rows = rows or events()
    cells = "".join("<tr>"+"".join(f"<td>{row[key] or ''}</td>" for key in
        ("declaration_date", "ex_date", "record_date", "pay_date", "amount_per_share"))+"</tr>" for row in rows)
    return f"<h1>{symbol}</h1><table><tr><th>Declaration Date</th><th>Ex-Div Date</th><th>Record Date</th><th>Payable Date</th><th>Amount ($)</th></tr>{cells}</table>"


def exchange_json(rows=None):
    rows = rows or events()
    raw = [{"exOrEffDate": row["ex_date"], "type": "Cash", "amount": "$"+row["amount_per_share"],
        "declarationDate": row["declaration_date"], "recordDate": row["record_date"], "paymentDate": row["pay_date"], "currency": "USD"} for row in rows]
    return json.dumps({"status":{"rCode":200},"data":{"dividends":{"rows":raw,"totalRecords":len(raw)}}})


class Client:
    def __init__(self, routes=None):
        self.routes = routes or {ISSUER_URLS["QQQI"]:issuer_html("QQQI"), ISSUER_URLS["JEPQ"]:issuer_html("JEPQ",events("0.40"))}
        self.calls = []

    def get(self, url, provider, headers=None):
        self.calls.append(url)
        body = self.routes.get(url, RuntimeError("mock secret must not cross the interface"))
        if isinstance(body, Exception):
            raise body
        return {"body": body, "source_url": url, "retrieved_at": NOW.isoformat(), "cache_status": "live"}


def snapshot(**kwargs):
    return income.collect_income_snapshot(["QQQI","JEPQ"], now=NOW, transport=Client(), **kwargs)


def compare(**changes):
    args = {"prices": {"QQQI":"50","JEPQ":"50"}, "budget_usd": "250", "cost_model": {"per_share_usd":"0","minimum_usd":"1","other_cost_bps":"0"},
                "constraints": {"cash_floor_pct":"0","weight_bounds":{}}, "now": NOW}
    args.update(changes)
    return income.compare_income_allocations(snapshot(), **args)


class IncomeEvents(unittest.TestCase):
    def normalize(self, rows=None, **changes):
        args = {"source_url": ISSUER_URLS["QQQI"], "source_family": "NEOS","retrieved_at": NOW,"as_of": NOW}
        args.update(changes)
        return income.normalize_income_events("QQQI",rows or events(),**args)

    def test_actual_amount_is_not_account_entitlement_receipt_or_final_tax(self):
        event = self.normalize()[-1]
        self.assertEqual(event["amount_per_share"], "0.60")
        self.assertEqual(event["published_at"],None)
        self.assertEqual(event["publication_precision"],"date")
        self.assertEqual(event["payment_status"],"user_receipt_unknown")
        self.assertIn("actual_pre_ex",event["holding_entitlement_status"])
        self.assertEqual(event["tax_components_status"],"unknown")
        self.assertIn("unknown",event["return_of_capital_status"])

    def test_schedule_and_estimate_do_not_enter_actual_distribution_metrics(self):
        rows = events()
        rows += [dict(rows[-1],declaration_date="2026-10-20",ex_date="2026-10-21",record_date="2026-10-21",pay_date="2026-10-23",amount_per_share="",amount_kind="scheduled"),
                 dict(rows[-1],declaration_date="2026-10-01",ex_date="2026-11-01",record_date="2026-11-01",pay_date="2026-11-05",amount_per_share="99",amount_kind="estimate")]
        snap = snapshot()
        snap["instruments"]["QQQI"]["events"] = self.normalize(rows)
        snap = income._seal(snap)
        metric = income.analyze_income(snap,now=NOW)["instruments"]["QQQI"]
        self.assertEqual(Decimal(metric["ttm_distribution_per_share"]),Decimal("7.2"))
        self.assertEqual(len(metric["future_or_estimated_events"]),2)

    def test_reversed_dates_unknown_currency_bad_amount_and_implicit_kind_rejected(self):
        for changes in ({"pay_date":"2020-01-01"},{"currency":"AUD"},{"amount_per_share":"NaN"},{"amount_kind":None}):
            with self.subTest(changes=changes),self.assertRaises((ValueError,ArithmeticError)):
                self.normalize([dict(events()[-1],**changes)])

    def test_zero_is_explicit_actual_and_different_duplicate_amount_blocks(self):
        self.assertEqual(self.normalize([dict(events()[-1],amount_per_share="0")])[0]["amount_kind"],"actual")
        with self.assertRaisesRegex(ValueError,"conflicting"):
            self.normalize([events()[-1],dict(events()[-1],amount_per_share="100")])
        self.assertEqual(len(self.normalize([events()[-1],events()[-1]])),1)

    def test_same_date_without_time_not_backdated_to_midnight(self):
        event = dict(events()[-1],declaration_date="2026-10-01")
        result = self.normalize([event],as_of="2026-10-01T00:01:00Z")[0]
        self.assertFalse(result["available_by_cutoff"])
        self.assertIsNone(result["published_at"])

    def test_exact_publication_is_cutoff_bound_and_naive_times_refused(self):
        event = dict(events()[-1],published_at="2026-09-30T20:00:00Z")
        self.assertFalse(self.normalize([event],as_of="2026-09-30T19:00:00Z")[0]["available_by_cutoff"])
        self.assertTrue(self.normalize([event],as_of="2026-09-30T21:00:00Z")[0]["available_by_cutoff"])
        with self.assertRaises(ValueError):
            self.normalize([event],retrieved_at="2026-10-05T10:00:00")

    def test_primary_source_role_family_and_ticker_are_bound(self):
        for change in ({"source_url":"https://example.test/data"},{"source_family":"Nasdaq"},{"source_role":"exchange"}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.normalize(**change)

    def test_issuer_html_parses_real_column_contract_and_rejects_wrong_identity(self):
        self.assertEqual(len(parse_issuer_html("QQQI",issuer_html("QQQI"))),13)
        with self.assertRaises(SourceError):
            parse_issuer_html("QQQI",issuer_html("SPYI")+"QQQI nav link")
        with self.assertRaisesRegex(SourceError,"table_unavailable"):
            parse_issuer_html("JEPQ","<h1>JEPQ</h1>12 month yield 11.2%")

    def test_exchange_schema_requires_success_cash_currency_and_untruncated_history(self):
        self.assertEqual(len(parse_exchange_json("JEPQ",exchange_json())),13)
        payload = json.loads(exchange_json())
        for field,value in (("type","Stock"),("currency","AUD")):
            bad = copy.deepcopy(payload);bad["data"]["dividends"]["rows"][0][field]=value
            with self.assertRaises(SourceError):parse_exchange_json("JEPQ",bad)
        payload["data"]["dividends"]["totalRecords"]=14
        with self.assertRaisesRegex(SourceError,"truncated"):parse_exchange_json("JEPQ",payload)

    def test_exchange_fallback_remains_exchange_with_issuer_failure_diagnostic(self):
        client=Client({ISSUER_URLS["JEPQ"]:"<h1>JEPQ</h1>dynamic table",exchange_url("JEPQ"):exchange_json()})
        result=income.collect_income_snapshot(["JEPQ"],now=NOW,transport=client)
        self.assertEqual(result["status"],"ready")
        item=result["instruments"]["JEPQ"]
        self.assertEqual(item["source_role"],"exchange")
        self.assertEqual(item["events"][0]["provenance"]["source_family"],"Nasdaq")
        self.assertIn("table_unavailable",item["source_diagnostics"][0]["code"])

    def test_failed_source_is_blocked_without_empty_success_or_exception_secret(self):
        result=income.collect_income_snapshot(["JEPQ"],now=NOW,transport=Client({ISSUER_URLS["JEPQ"]:RuntimeError("mock secret")}),allow_exchange_fallback=False)
        self.assertEqual(result["status"],"blocked")
        self.assertFalse(result["complete"])
        self.assertEqual(result["instruments"]["JEPQ"]["events"],[])
        self.assertEqual(result["instruments"]["JEPQ"]["source_diagnostics"][0]["code"],"income_source_unavailable:RuntimeError")
        self.assertNotIn("mock secret",json.dumps(result))

    def test_one_failed_fund_preserves_other_fund_research(self):
        result=income.collect_income_snapshot(["QQQI","JEPQ"],now=NOW,transport=Client({ISSUER_URLS["QQQI"]:issuer_html("QQQI")}),allow_exchange_fallback=False)
        report=income.analyze_income(result,now=NOW)
        self.assertEqual(report["status"],"partial")
        self.assertEqual(report["instruments"]["QQQI"]["status"],"ready")
        self.assertEqual(report["instruments"]["JEPQ"]["status"],"blocked")


class IncomeMetrics(unittest.TestCase):
    def test_common_calendar_ttm_and_zero_variability_are_decimal_research(self):
        report=income.analyze_income(snapshot(),prices={"QQQI":"50","JEPQ":"50"},now=NOW)
        metric=report["instruments"]["QQQI"]
        self.assertEqual(metric["ttm_event_count"],12)
        self.assertEqual(Decimal(metric["ttm_distribution_per_share"]),Decimal("7.2"))
        self.assertEqual(Decimal(metric["monthly_cashflow_proxy"]),Decimal(".6"))
        self.assertEqual(Decimal(metric["stability"]["coefficient_of_variation"]),0)
        self.assertEqual(report["withholding_status"],"unknown")
        self.assertEqual(report["fx_status"],"unknown")
        self.assertEqual(metric["total_return"]["status"],"unknown")
        self.assertEqual(report["orders"],[])

    def test_short_history_withholds_ttm_without_padding_or_annualizing_latest(self):
        snap=snapshot();snap["instruments"]["QQQI"]["events"]=snap["instruments"]["QQQI"]["events"][-3:]
        metric=income.analyze_income(income._seal(snap),now=NOW)["instruments"]["QQQI"]
        self.assertIsNone(metric["ttm_distribution_per_share"])
        self.assertIsNone(metric["monthly_cashflow_proxy"])
        self.assertEqual(metric["latest_12_count"],3)

    def test_twelve_events_cannot_hide_a_missing_month_behind_an_extra_ex_date(self):
        rows = [row for row in events() if row["ex_date"] != "2026-06-01"]
        rows.append(dict(rows[2],declaration_date="2025-12-14",ex_date="2025-12-15",record_date="2025-12-15",pay_date="2025-12-19"))
        snap=income.collect_income_snapshot(["QQQI","JEPQ"],now=NOW,
            transport=Client({ISSUER_URLS["QQQI"]:issuer_html("QQQI",rows),ISSUER_URLS["JEPQ"]:issuer_html("JEPQ",events(".4"))}))
        metric=income.analyze_income(snap,now=NOW)["instruments"]["QQQI"]
        self.assertEqual(metric["ttm_event_count"],12)
        self.assertEqual(metric["ttm_status"],"incomplete")
        self.assertEqual(metric["monthly_continuity"]["maximum_event_gap_days"],61)
        self.assertFalse(metric["monthly_continuity"]["source_completeness_authenticated"])
        self.assertIsNone(metric["ttm_distribution_per_share"])
        self.assertIsNone(metric["monthly_cashflow_proxy"])
        result=income.compare_income_allocations(snap,prices={"QQQI":"50","JEPQ":"50"},budget_usd="250",
            cost_model={"per_share_usd":"0","minimum_usd":"1","other_cost_bps":"0"},constraints={"cash_floor_pct":"0"},now=NOW)
        self.assertEqual(result["status"],"blocked")
        self.assertEqual(result["candidates"],[])

    def test_month_boundary_shifts_are_allowed_but_stale_window_tail_is_not(self):
        rows=events()
        rows[-1]=dict(rows[-1],declaration_date="2026-09-28",ex_date="2026-09-29",record_date="2026-09-29",pay_date="2026-10-02")
        def metric(values):
            snap=income.collect_income_snapshot(["QQQI"],now=NOW,transport=Client({ISSUER_URLS["QQQI"]:issuer_html("QQQI",values)}))
            return income.analyze_income(snap,now=NOW)["instruments"]["QQQI"]
        self.assertEqual(metric(rows)["ttm_status"],"complete_monthly_history_observed")
        stale=[row for row in events() if row["ex_date"] not in {"2026-09-01","2026-10-01"}]
        for month in (11,12):
            stale.append(dict(stale[1],declaration_date=f"2025-{month}-14",ex_date=f"2025-{month}-15",record_date=f"2025-{month}-15",pay_date=f"2025-{month}-19"))
        result=metric(stale)
        self.assertEqual(result["ttm_event_count"],12)
        self.assertEqual(result["monthly_continuity"]["window_end_gap_days"],65)
        self.assertEqual(result["ttm_status"],"incomplete")

    def test_tampering_expiry_future_cutoff_and_wrong_universe_are_refused(self):
        snap=snapshot();snap["instruments"]["QQQI"]["events"][-1]["amount_per_share"]="999"
        self.assertFalse(income.validate_income_snapshot(snap,now=NOW)["valid"])
        self.assertFalse(income.validate_income_snapshot(snapshot(),now=NOW+timedelta(hours=1))["valid"])
        for symbols in (["AAPL"],["QQQI","QQQI"],[]):
            with self.assertRaises(ValueError):income.collect_income_snapshot(symbols,now=NOW,transport=Client())
        with self.assertRaises(ValueError):snapshot(as_of=NOW+timedelta(seconds=1))

    def test_explicit_different_fund_withholding_and_FX_do_not_become_tax_certification(self):
        report=income.analyze_income(snapshot(),withholding_scenarios=[{"name":"sensitivity","rates":{"QQQI":".30","JEPQ":".15"}}],
            fx_scenarios=[{"name":"FX assumption","base_currency":"AUD","usd_to_base":"1.5"}],now=NOW)
        metrics=report["scenario_results"][0]["instruments"]
        self.assertEqual(Decimal(metrics["QQQI"]["monthly_net_usd_proxy"]),Decimal(".42"))
        self.assertEqual(Decimal(metrics["JEPQ"]["monthly_net_usd_proxy"]),Decimal(".34"))
        self.assertEqual(Decimal(metrics["QQQI"]["fx_scenarios"][0]["net_base_proxy"]),Decimal(".63"))
        self.assertIn("not_certified",report["withholding_status"])

    def test_gross_FX_can_be_shown_while_withholding_remains_unknown(self):
        report=income.analyze_income(snapshot(),fx_scenarios=[{"name":"fx","base_currency":"AUD","usd_to_base":"1.5"}],now=NOW)
        self.assertEqual(Decimal(report["gross_fx_scenarios"][0]["instruments"]["QQQI"]),Decimal(".9"))
        self.assertEqual(report["withholding_status"],"unknown")

    def test_NAV_plus_entitlement_is_not_distribution_only_or_adjusted_double_count(self):
        nav={"basis":"raw_nav","currency":"USD","splits_verified_absent":True,"distribution_events_complete":True,
             "start_date":"2025-10-05","end_date":"2026-10-05","start_nav":"100","end_nav":"90","source_url":"https://neosfunds.com/qqqi/"}
        metric=income.analyze_income(snapshot(),nav_history={"QQQI":nav},now=NOW)["instruments"]["QQQI"]["total_return"]
        self.assertEqual(Decimal(metric["distribution_entitlements_per_share"]),Decimal("7.2"))
        self.assertEqual(Decimal(metric["total_return_fraction"]),Decimal("-.028"))
        self.assertFalse(metric["reinvested"])
        self.assertIn("not_subtracted_twice",metric["fund_expenses"])
        bad=dict(nav,basis="total_return_adjusted")
        self.assertEqual(income.analyze_income(snapshot(),nav_history={"QQQI":bad},now=NOW)["instruments"]["QQQI"]["total_return"]["status"],"unknown")


class IncomeAllocations(unittest.TestCase):
    def test_costs_floor_integer_budget_bounds_and_non_executable_result(self):
        result=compare(constraints={"cash_floor_pct":".2","weight_bounds":{"QQQI":{"max":".6"},"JEPQ":{"min":".2"}}})
        self.assertEqual(result["status"],"research_comparison",result["issues"])
        card=result["candidates"][0]
        self.assertEqual(card["hypothetical_target_holdings"],{"JEPQ":"1","QQQI":"2"})
        self.assertEqual(Decimal(card["estimated_reallocation_cost_usd"]),2)
        self.assertGreaterEqual(Decimal(card["cash_remaining_usd"]),50)
        self.assertEqual(result["execution_scope"],"research_only")
        self.assertEqual(result["orders"],[])
        self.assertEqual(result["funding_status"],"not_verified")

    def test_gross_and_net_objectives_can_choose_different_funds(self):
        gross=compare()["candidates"][0]
        net=compare(objective="net_cashflow",withholding_scenarios=[{"name":"sensitivity","rates":{"QQQI":".50","JEPQ":"0"}}])["candidates"][0]
        self.assertEqual(gross["hypothetical_target_holdings"],{"JEPQ":"0","QQQI":"4"})
        self.assertEqual(net["hypothetical_target_holdings"],{"JEPQ":"4","QQQI":"0"})

    def test_net_requires_complete_per_fund_tax_assumptions_and_costs_are_not_zero_default(self):
        for changes in ({"objective":"net_cashflow"},{"cost_model":{"per_share_usd":"0","minimum_usd":"1"}},
                        {"constraints":{}},{"objective":"net_cashflow","withholding_scenarios":[{"name":"bad","rates":{"QQQI":".15"}}]}):
            with self.subTest(changes=changes):
                result=compare(**changes)
                self.assertEqual(result["status"],"blocked")
                self.assertEqual(result["candidates"],[])

    def test_existing_research_holdings_charge_only_changed_legs(self):
        result=compare(existing_holdings={"QQQI":"4","JEPQ":"0"})
        card=result["candidates"][0]
        self.assertEqual(Decimal(card["estimated_reallocation_cost_usd"]),0)
        self.assertEqual(card["hypothetical_target_holdings"]["QQQI"],"4")

    def test_total_return_objective_requires_comparable_raw_NAV_and_can_disagree_with_income(self):
        self.assertEqual(compare(objective="total_return")["status"],"blocked")
        nav={"basis":"raw_nav","currency":"USD","splits_verified_absent":True,"distribution_events_complete":True,
            "start_date":"2025-10-05","end_date":"2026-10-05","start_nav":"100","end_nav":"90","source_url":"https://neosfunds.com/qqqi/"}
        result=compare(objective="total_return",nav_history={"QQQI":nav,"JEPQ":dict(nav,end_nav="110",source_url=ISSUER_URLS["JEPQ"])})
        self.assertEqual(result["status"],"research_comparison",result["issues"])
        self.assertEqual(result["candidates"][0]["hypothetical_target_holdings"],{"JEPQ":"4","QQQI":"0"})
        self.assertEqual(result["window"],{"start":"2025-10-05","end":"2026-10-05","basis":"declared_raw_NAV_plus_entitlements"})
        mismatched={"QQQI":nav,"JEPQ":dict(nav,end_date="2026-09-05")}
        self.assertEqual(compare(objective="total_return",nav_history=mismatched)["status"],"blocked")

    def test_infeasible_negative_nonfinite_and_unbounded_enumeration_refused(self):
        for change in ({"budget_usd":"NaN"},{"budget_usd":"-1"},{"budget_usd":"10000000"},
                       {"constraints":{"cash_floor_pct":"0","weight_bounds":{"QQQI":{"min":".9"},"JEPQ":{"min":".9"}}}}):
            with self.subTest(change=change):self.assertEqual(compare(**change)["status"],"blocked")

    def test_invalid_FX_never_leaves_previously_selected_targets_reusable(self):
        result=compare(fx_scenarios=[{"name":"bad","base_currency":"AUD","usd_to_base":"NaN"}])
        self.assertEqual(result["status"],"blocked")
        self.assertEqual(result["candidates"],[])

    def test_malformed_or_unrelated_bounds_cannot_silently_remove_constraints(self):
        for change in ({"existing_holdings":[]}, {"constraints":{"cash_floor_pct":"0","weight_bounds":[]}},
                       {"constraints":{"cash_floor_pct":"0","weight_bounds":{"QQQI":".5"}}},
                       {"constraints":{"cash_floor_pct":"0","weight_bounds":{"AAPL":{"max":"0"}}}}):
            with self.subTest(change=change):
                result=compare(**change)
                self.assertEqual(result["status"],"blocked")
                self.assertEqual(result["candidates"],[])

    def test_sealed_malformed_snapshot_and_bad_reference_price_are_not_ready(self):
        malformed=income._seal(dict(snapshot(),instruments=[]))
        self.assertEqual(income.analyze_income(malformed,now=NOW)["status"],"blocked")
        result=income.analyze_income(snapshot(),prices={"QQQI":"NaN"},now=NOW)
        self.assertEqual(result["status"],"partial")
        self.assertTrue(result["issues"])


if __name__ == "__main__":
    unittest.main()
