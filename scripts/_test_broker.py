"""Read-only broker contracts: isolated data and an in-memory official-SDK double."""
from __future__ import annotations

import copy
import json
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from copilot import broker

NOW = "2026-10-02T15:00:00+00:00"
ACCOUNT = "U1234567"
SETTINGS = {"enabled": True, "account_id": ACCOUNT, "account_alias": "primary",
            "orders_scope_confirmed": True, "market_data_feed": "consolidated", "paper": False}


def raw_fixture(base="USD"):
    values = [{"account_id": ACCOUNT, "tag": tag, "value": value, "currency": currency}
              for tag, value, currency in (("Currency", base, "BASE"), ("NetLiquidation", "10000", base),
                                          ("AccountType", "INDIVIDUAL", "BASE"), ("AvailableFunds", "50000", base),
                                          ("CashBalance", "1000", "USD"), ("SettledCash", "900", "USD"))]
    for row in values:
        if row["tag"] in {"CashBalance", "SettledCash"}:
            row["value_scope"] = "per_currency"
    return {"accounts": [ACCOUNT], "account_values": values,
            "positions": [{"account_id": ACCOUNT, "instrument_id": "SPY", "con_id": 101,
                           "currency": "USD", "quantity": "10", "avg_cost": "90"}],
            "orders": [], "fills": [], "complete": {s: True for s in broker._SECTIONS},
            "contracts": {"SPY": [{"symbol": "SPY", "con_id": 101, "sec_type": "STK", "currency": "USD",
                                     "tick_size": "0.01", "market_rule": [{"low_edge": "0", "increment": "0.01"}],
                                     "liquid_hours": "20261002:0930-20261002:1600", "time_zone_id": "US/Eastern"}]},
            "quotes": {"SPY": {"bid": "100", "ask": "100.02", "bid_size": "100", "ask_size": "200",
                                "actual_data_type": 1, "halted": 0,
                                "bid_received_at": NOW, "ask_received_at": NOW}}, "issues": []}


class FixtureTransport:
    def __init__(self, raw=None):
        self.raw = raw if raw is not None else raw_fixture()
        self.calls = 0

    def collect(self, settings, symbols, *, now=None):
        self.calls += 1
        return copy.deepcopy(self.raw)


def collect(raw=None, settings=None, now=NOW):
    return broker.collect_execution_snapshot(settings or SETTINGS, ["SPY"], now=now,
                                             transport=FixtureTransport(raw))


def order(**kwargs):
    return {"account_id": ACCOUNT, "instrument_id": "SPY", "con_id": 101, "currency": "USD",
            "order_id": 1, "perm_id": 900, "client_id": 71, "side": "BUY", "order_type": "LMT",
            "total_quantity": "5", "filled_quantity": "1", "remaining_quantity": "4",
            "limit_price": "100", "status": "Submitted", **kwargs}


def add_symbol(raw, symbol, con_id, industry=None):
    contract = dict(raw["contracts"]["SPY"][0], symbol=symbol, con_id=con_id,
                    industry=industry, category="declared category", subcategory="declared subcategory")
    raw["contracts"][symbol] = [contract]
    raw["quotes"][symbol] = dict(raw["quotes"]["SPY"])


class BrokerContracts(unittest.TestCase):
    def test_prefixed_ledger_and_base_sentinel_use_reported_nav_currency(self):
        raw = raw_fixture()
        for row in raw["account_values"]:
            if row["tag"] in {"Currency", "CashBalance", "SettledCash"}:
                row["tag"] = "$LEDGER-" + row["tag"]
            if row["tag"] == "$LEDGER-Currency":
                row["value"] = "BASE"
            if row["tag"] == "NetLiquidation":
                row["source"] = "account_summary"
        result = collect(raw)
        self.assertEqual(result["status"], "ready", result["issues"])
        self.assertEqual(result["account"]["base_currency"], "USD")
        self.assertEqual(result["cash"]["USD"]["settled"], "900")
        self.assertEqual(result["cash"]["USD"]["settled_evidence"]["raw_tag"], "$LEDGER-SettledCash")
        self.assertEqual(result["account"]["base_currency_evidence"]["sources"][0]["tag"], "NetLiquidation")

    def test_account_aggregate_settled_is_not_native_cash(self):
        raw = raw_fixture()
        raw["account_values"][-1]["tag"] = "$LEDGER-SettledCash"
        raw["account_values"].append({"account_id": ACCOUNT, "tag": "SettledCash", "value": "5000",
                                      "currency": "USD", "source": "account_summary"})
        result = collect(raw)
        self.assertEqual(result["status"], "ready", result["issues"])
        self.assertEqual(result["cash"]["USD"]["settled"], "900")
        self.assertEqual(result["cash"]["USD"]["available"], "900")
        self.assertEqual(result["cash"]["USD"]["settled_evidence"]["value_scope"], "per_currency")

    def test_conflicting_prefixed_and_legacy_ledger_values_block(self):
        raw = raw_fixture()
        raw["account_values"].append({"account_id": ACCOUNT, "tag": "$LEDGER-CashBalance", "value": "2000",
                                      "currency": "USD", "source": "account_updates"})
        result = collect(raw)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("account_values_changed_during_collection", result["issues"])
        raw["account_values"][-1]["value"] = "1000.000"
        result = collect(raw)
        self.assertEqual(result["status"], "ready", result["issues"])
        self.assertEqual(result["cash"]["USD"]["gross_evidence"]["raw_tag"], "$LEDGER-CashBalance")

    def test_missing_native_settled_is_not_replaced_by_gross_or_summary(self):
        raw = raw_fixture()
        raw["account_values"] = [row for row in raw["account_values"] if row["tag"] != "SettledCash"]
        raw["account_values"].append({"account_id": ACCOUNT, "tag": "SettledCash", "value": "9000",
                                      "currency": "USD", "source": "account_summary"})
        raw["account_values"][-2]["tag"] = "$LEDGER-CashBalance"
        result = collect(raw)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["cash"]["USD"]["gross"], "1000")
        self.assertIsNone(result["cash"]["USD"]["settled"])
        self.assertIsNone(result["cash"]["USD"]["available"])
        self.assertIn("cash_or_reservation_unknown:USD", result["issues"])
        self.assertNotIn("BASE", result["cash"])

    def test_base_currency_conflicts_and_ambiguous_nav_fail_closed(self):
        raw = raw_fixture()
        raw["account_values"][1].update(currency="AUD", source="account_summary")
        self.assertIn("base_currency_conflict", collect(raw)["issues"])
        raw["account_values"][0].update(tag="$LEDGER-Currency", value="BASE")
        raw["account_values"].append({"account_id": ACCOUNT, "tag": "NetLiquidation", "value": "10000",
                                      "currency": "USD", "source": "account_summary"})
        self.assertIn("base_currency_ambiguous", collect(raw)["issues"])
        raw["account_values"][-1]["account_id"] = "U7654321"
        self.assertEqual(collect(raw)["account"]["base_currency"], "AUD")

    def test_normalizer_requires_quotes_for_account_stock_positions_and_orders(self):
        raw = raw_fixture()
        raw["positions"].append(dict(account_id=ACCOUNT, instrument_id="NVDA", con_id=102,
                                      currency="USD", sec_type="STK", quantity="2", avg_cost="90"))
        raw["orders"] = [order(instrument_id="AAPL", con_id=103, sec_type="STK")]
        result = collect(raw)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["coverage"]["quotes"]["required_instrument_ids"], ["SPY", "NVDA", "AAPL"])
        self.assertIn("contract_missing_or_ambiguous:NVDA", result["issues"])
        self.assertIn("contract_missing_or_ambiguous:AAPL", result["issues"])
        add_symbol(raw, "NVDA", 102, "Technology")
        add_symbol(raw, "AAPL", 103, "Technology")
        result = collect(raw)
        self.assertEqual(result["status"], "ready", result["issues"])
        self.assertEqual(set(result["quotes"]), {"SPY", "NVDA", "AAPL"})
        position = next(row for row in result["positions"] if row["instrument_id"] == "NVDA")
        self.assertEqual(position["sector"], "technology")
        self.assertFalse(position["sector_evidence"]["gics_verified"])

    def test_industry_mapping_is_exact_and_unknown_text_is_not_inferred(self):
        raw = raw_fixture()
        raw["positions"].append(dict(account_id=ACCOUNT, instrument_id="AAPL", con_id=102,
                                      currency="USD", sec_type="STK", quantity="2", avg_cost="90"))
        for industry in (None, "technology", "Consumer, Non-cyclical", "unclassified"):
            add_symbol(raw, "AAPL", 102, industry)
            result = collect(raw)
            quote = result["quotes"]["AAPL"]
            self.assertIsNone(quote["sector"])
            self.assertEqual(quote["sector_evidence"]["status"], "unknown")
            self.assertEqual(quote["broker_industry"]["industry"], industry)
        add_symbol(raw, "AAPL", 102, "Financial")
        self.assertEqual(collect(raw)["quotes"]["AAPL"]["sector"], "financials")

    def test_account_quote_expansion_never_truncates_at_limit(self):
        raw = raw_fixture()
        raw["positions"] = [dict(account_id=ACCOUNT, instrument_id=f"S{index}", con_id=1000+index,
                                 currency="USD", sec_type="STK", quantity="1", avg_cost="90")
                            for index in range(64)]
        result = collect(raw)
        self.assertEqual(result["issues"], ["account_quote_universe_limit_exceeded"])
        self.assertFalse(result["complete"])

    def test_valid_readonly_snapshot_keeps_decimal_strings_and_scrubs_account(self):
        result = collect()
        self.assertEqual(result["status"], "ready", result["issues"])
        self.assertNotIn(ACCOUNT, json.dumps(result))
        self.assertEqual(result["cash"]["USD"]["available"], "900")
        self.assertEqual(result["account"]["nav"], "10000")
        self.assertEqual(result["quotes"]["SPY"]["bid"], "100")
        self.assertIsNone(result["quotes"]["SPY"]["event_time"])
        self.assertEqual(broker.validate_execution_snapshot(result, now=NOW)["status"], "ready")

    def test_disabled_does_not_import_or_connect_sdk(self):
        with patch.object(broker.ReadOnlyIBKRTransport, "collect", side_effect=AssertionError("no query")):
            result = broker.collect_execution_snapshot({}, ["SPY"], now=NOW)
        self.assertEqual(result["issues"], ["broker_disabled"])

    def test_injected_fixture_allowed_without_live_enable(self):
        settings = dict(SETTINGS, enabled=False)
        self.assertEqual(collect(settings=settings)["status"], "ready")

    def test_client_zero_and_remote_host_rejected_before_transport(self):
        for key, value in (("client_id", 0), ("host", "broker.example"), ("client_id", True)):
            transport = FixtureTransport()
            result = broker.collect_execution_snapshot(dict(SETTINGS, **{key: value}), ["SPY"], now=NOW, transport=transport)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(transport.calls, 0)

    def test_missing_sdk_is_visible(self):
        with patch.dict(sys.modules, {"ibapi": None}):
            result = broker.collect_execution_snapshot(SETTINGS, ["SPY"], now=NOW)
        self.assertEqual(result["issues"], ["ibapi_sdk_missing"])

    def test_upstream_exception_does_not_leak_account_or_credentials(self):
        transport = FixtureTransport()
        with patch.object(transport, "collect", side_effect=RuntimeError(ACCOUNT + " token=fixture-private")):
            result = broker.collect_execution_snapshot(SETTINGS, ["SPY"], now=NOW, transport=transport)
        self.assertEqual(result["issues"], ["broker_transport_failed"])
        self.assertNotIn(ACCOUNT, json.dumps(result))
        self.assertNotIn("fixture-private", json.dumps(result))

    def test_empty_success_and_position_timeout_are_different(self):
        raw = raw_fixture()
        raw["positions"] = []
        self.assertEqual(collect(raw)["status"], "ready")
        raw["complete"]["positions"] = False
        self.assertIn("incomplete:positions", collect(raw)["issues"])

    def test_manual_order_scope_is_not_certified_by_end(self):
        result = collect(settings=dict(SETTINGS, orders_scope_confirmed=False))
        self.assertIn("orders_scope_unverified", result["issues"])
        self.assertIsNone(result["cash"]["USD"]["reserved"])

    def test_requested_live_is_not_received_live(self):
        for data_type in (None, 2, 3, 4):
            raw = raw_fixture()
            raw["quotes"]["SPY"]["actual_data_type"] = data_type
            raw["quotes"]["SPY"]["requested_data_type"] = 1
            self.assertIn("quote_not_confirmed_live:SPY", collect(raw)["issues"])

    def test_unknown_halt_and_crossed_or_one_sided_quotes_block(self):
        for values, issue in (({"halted": None}, "halt_status_unknown_or_halted"),
                              ({"halted": 1}, "halt_status_unknown_or_halted"),
                              ({"bid": "101"}, "quote_crossed"),
                              ({"ask": None}, "quote_bid_ask_missing"),
                              ({"bid_size": "0"}, "quote_size_missing")):
            raw = raw_fixture()
            raw["quotes"]["SPY"].update(values)
            self.assertIn(issue + ":SPY", collect(raw)["issues"])

    def test_unknown_feed_is_not_nbbo(self):
        result = collect(settings=dict(SETTINGS, market_data_feed="unknown"))
        self.assertIn("quote_feed_scope_unknown:SPY", result["issues"])

    def test_stale_future_and_widely_spaced_bbo_block(self):
        for value, issue in (("2026-10-02T14:58:00+00:00", "quote_expired"),
                             ("2026-10-02T15:00:10+00:00", "quote_timestamp_future"),
                             ("2026-10-02T14:59:40+00:00", "quote_collection_window_inconsistent")):
            raw = raw_fixture()
            raw["quotes"]["SPY"]["bid_received_at"] = value
            self.assertIn(issue + ":SPY", collect(raw)["issues"])

    def test_port_does_not_prove_paper_or_live(self):
        for port in (7496, 7497, 4001, 4002):
            result = collect(settings=dict(SETTINGS, port=port, paper=True))
            self.assertTrue(result["account"]["paper"])
            self.assertEqual(result["account"]["paper_status"], "declared")

    def test_account_selection_and_account_changes_block(self):
        raw = raw_fixture()
        raw["accounts"] = [ACCOUNT, "U7654321"]
        settings = dict(SETTINGS, account_id="")
        self.assertEqual(collect(raw, settings)["issues"], ["account_selection_missing_or_ambiguous"])
        raw["changed_during_collection"] = True
        self.assertIn("account_changed_during_collection", collect(raw)["issues"])

    def test_other_account_does_not_supply_cash_positions_or_orders(self):
        raw = raw_fixture()
        raw["account_values"].append(dict(raw["account_values"][-1], account_id="U7654321", value="999999"))
        raw["positions"].append(dict(raw["positions"][0], account_id="U7654321", quantity="999"))
        raw["orders"] = [order(account_id="U7654321")]
        result = collect(raw)
        self.assertEqual(len(result["positions"]), 1)
        self.assertEqual(result["cash"]["USD"]["available"], "900")
        self.assertNotIn("U7654321", json.dumps(result))

    def test_pending_cancel_keeps_reservation_terminal_states_release(self):
        raw = raw_fixture()
        raw["orders"] = [order(status="PendingCancel")]
        result = collect(raw)
        self.assertEqual(result["cash"]["USD"]["reserved"], "400")
        self.assertEqual(result["cash"]["USD"]["available"], "500")
        self.assertEqual(result["orders"][0]["status"], "cancel_pending")
        for status in ("Cancelled", "Filled", "Rejected"):
            raw["orders"][0]["status"] = status
            self.assertEqual(collect(raw)["cash"]["USD"]["reserved"], "0")

    def test_unknown_market_buy_cannot_be_priced_or_assumed_zero(self):
        raw = raw_fixture()
        raw["orders"] = [order(order_type="MKT", limit_price=None)]
        result = collect(raw)
        self.assertIsNone(result["cash"]["USD"]["reserved"])
        self.assertEqual(result["status"], "blocked")

    def test_sell_order_does_not_fund_buy(self):
        raw = raw_fixture()
        raw["orders"] = [order(side="SELL")]
        self.assertEqual(collect(raw)["cash"]["USD"]["available"], "900")

    def test_margin_available_funds_not_cash(self):
        result = collect()
        self.assertEqual(result["account"]["available_funds"], "50000")
        self.assertEqual(result["cash"]["USD"]["available"], "900")
        self.assertEqual(result["cash"]["USD"]["available_basis"], "settled_cash_net_open_orders")

    def test_missing_settled_cash_not_inferred_from_gross(self):
        raw = raw_fixture()
        raw["account_values"] = [r for r in raw["account_values"] if r["tag"] != "SettledCash"]
        self.assertIn("cash_or_reservation_unknown:USD", collect(raw)["issues"])

    def test_nonfinite_and_unset_sdk_values_are_not_zero(self):
        for value in ("NaN", "Infinity", "1.7976931348623157e308", True):
            raw = raw_fixture()
            raw["quotes"]["SPY"]["ask"] = value
            self.assertEqual(collect(raw)["status"], "blocked")

    def test_contract_ambiguity_currency_and_conid_mismatch_block(self):
        raw = raw_fixture()
        raw["contracts"]["SPY"] *= 2
        self.assertIn("contract_missing_or_ambiguous:SPY", collect(raw)["issues"])
        raw = raw_fixture()
        raw["contracts"]["SPY"][0]["currency"] = "AUD"
        self.assertIn("contract_identity_mismatch:SPY", collect(raw)["issues"])
        raw = raw_fixture()
        raw["positions"][0]["con_id"] = 102
        self.assertIn("position_contract_mismatch:SPY", collect(raw)["issues"])

    def test_market_rule_zero_or_missing_increment_block(self):
        for rules in ([], [{"low_edge": "0", "increment": "0"}]):
            raw = raw_fixture()
            raw["contracts"]["SPY"][0]["market_rule"] = rules
            self.assertIn("price_increment_unverified:SPY", collect(raw)["issues"])

    def test_regular_hours_use_contract_calendar_not_weekday_guess(self):
        raw = raw_fixture()
        raw["contracts"]["SPY"][0]["liquid_hours"] = "20261002:CLOSED"
        self.assertIn("regular_session_unverified_or_closed:SPY", collect(raw)["issues"])
        raw["contracts"]["SPY"][0]["time_zone_id"] = "unknown"
        self.assertIsNone(collect(raw)["quotes"]["SPY"]["regular_hours"])

    def test_aud_nav_fx_is_explicit_and_never_funds_usd_cash(self):
        raw = raw_fixture("AUD")
        self.assertIn("NAV_FX_unknown", collect(raw)["issues"])
        raw["contracts"]["AUD.USD"] = [{"symbol": "AUD", "con_id": 201, "currency": "USD", "sec_type": "CASH",
                                           "tick_size": "0.00005", "market_rule": [{"low_edge": "0", "increment": "0.00005"}]}]
        raw["quotes"]["AUD.USD"] = dict(raw["quotes"]["SPY"], bid="0.6500", ask="0.6502", halted=None)
        result = collect(raw)
        self.assertEqual(result["status"], "ready", result["issues"])
        self.assertEqual(result["fx_rates"]["AUD.USD"]["rate"], "0.6501")
        self.assertEqual(result["cash"]["USD"]["available"], "900")
        self.assertEqual(result["account"]["nav_currency"], "AUD")

    def test_missing_commission_is_pending_not_zero(self):
        raw = raw_fixture()
        raw["fills"] = [{"account_id": ACCOUNT, "execution_id": "exec-1", "con_id": 101,
                         "instrument_id": "SPY", "side": "buy", "quantity": "1", "price": "100", "currency": "USD"}]
        result = collect(raw)
        self.assertIsNone(result["fills"][0]["commission"])
        self.assertEqual(result["fills"][0]["fee_status"], "pending")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("execution_fees_pending", result["issues"])

    def test_recent_execution_end_is_not_complete_strategy_history(self):
        result = collect()
        self.assertTrue(result["coverage"]["fills"]["request_complete"])
        self.assertFalse(result["coverage"]["fills"]["history_complete"])
        self.assertEqual(result["coverage"]["fills"]["scope"], "TWS_available_recent_executions_only")

    def test_numeric_formatting_does_not_change_account_version(self):
        first = collect()
        raw = raw_fixture()
        raw["account_values"][-1]["value"] = "900.000"
        self.assertEqual(first["account_version"], collect(raw)["account_version"])

    def test_inconsistent_or_duplicate_orders_do_not_make_funds_available(self):
        raw = raw_fixture()
        raw["orders"] = [order(remaining_quantity="10")]
        self.assertEqual(collect(raw)["issues"], ["order_values_inconsistent"])
        raw["orders"] = [order(), order()]
        result = collect(raw)
        self.assertEqual(len(result["orders"]), 1)
        self.assertEqual(result["cash"]["USD"]["reserved"], "400")
        raw["orders"][1]["limit_price"] = "101"
        self.assertEqual(collect(raw)["issues"], ["duplicate_order_conflict"])

    def test_account_server_reset_blocks_end_markers(self):
        raw = raw_fixture()
        raw["account_values"].append({"account_id": ACCOUNT, "tag": "AccountReady", "value": "false", "currency": "BASE"})
        self.assertIn("account_not_ready", collect(raw)["issues"])

    def test_account_version_ignores_quote_and_clock_changes(self):
        first = collect()
        raw = raw_fixture()
        raw["quotes"]["SPY"]["ask"] = "100.03"
        later = collect(raw, now="2026-10-02T15:00:01+00:00")
        self.assertEqual(first["account_version"], later["account_version"])
        self.assertNotEqual(first["snapshot_id"], later["snapshot_id"])
        raw["positions"][0]["quantity"] = "11"
        self.assertNotEqual(first["account_version"], collect(raw)["account_version"])

    def test_digest_tampering_and_expiry_rejected(self):
        result = collect()
        result["cash"]["USD"]["available"] = "100000"
        self.assertIn("broker_digest_invalid", broker.validate_execution_snapshot(result, now=NOW)["issues"])
        result = collect()
        self.assertIn("broker_snapshot_expired_or_future", broker.validate_execution_snapshot(result, now="2026-10-02T15:01:00+00:00")["issues"])


class CallbackContracts(unittest.TestCase):
    def reader(self):
        return broker._IBCollector(SETTINGS | {"timeout_seconds": 1}, ["SPY"], types.SimpleNamespace, types.SimpleNamespace)

    def test_delayed_tick_id_overrules_claimed_live(self):
        reader = self.reader()
        reader.request_symbols[1] = "SPY"
        reader.quote(1)["actual_data_type"] = 1
        reader.price(1, 66, 100)
        self.assertEqual(reader.quote(1)["actual_data_type"], 3)

    def test_commission_before_fill_and_duplicate_execution(self):
        reader = self.reader()
        reader.commission(types.SimpleNamespace(execId="e1", commissionAndFees=0.35, currency="USD"))
        contract = types.SimpleNamespace(conId=101, symbol="SPY", currency="USD")
        execution = types.SimpleNamespace(acctNumber=ACCOUNT, execId="e1", permId=900, side="BOT", shares=1, price=100, time="ambiguous local time")
        reader.fill(contract, execution)
        reader.fill(contract, execution)
        self.assertEqual(len(reader.raw["fills"]), 1)
        self.assertEqual(reader.raw["fills"][0]["commission"], "0.35")
        self.assertIsNone(reader.raw["fills"][0]["executed_at"])

    def test_fill_after_initial_end_marks_account_unstable(self):
        reader = self.reader()
        reader.done("fills")
        contract = types.SimpleNamespace(conId=101, symbol="SPY", currency="USD")
        execution = types.SimpleNamespace(acctNumber=ACCOUNT, execId="e1", permId=900, side="BOT", shares=1, price=100, time="unknown")
        reader.fill(contract, execution)
        self.assertTrue(reader.raw["changed_during_collection"])

    def test_execution_times_require_explicit_timezone(self):
        self.assertEqual(broker._execution_timestamp("20261002 15:00:00 UTC"), NOW)
        self.assertEqual(broker._execution_timestamp(NOW), NOW)
        self.assertIsNone(broker._execution_timestamp("20261002 15:00:00"))

    def test_account_same_value_formatting_not_detected_as_new_change(self):
        reader = self.reader()
        reader.account_value(ACCOUNT, "CashBalance", "1000.00", "USD")
        reader.account_value(ACCOUNT, "CashBalance", "1000", "USD")
        self.assertFalse(reader.raw.get("changed_during_collection", False))

    def test_protobuf_absent_fee_does_not_become_sdk_default_zero(self):
        reader = self.reader()
        proto = types.SimpleNamespace(execId="e1", HasField=lambda field: field in {"execId", "currency"})
        reader.commission_proto(proto)
        reader.commission(types.SimpleNamespace(execId="e1", commissionAndFees=0.0, currency="USD"))
        self.assertIsNone(reader._commissions["e1"]["commission"])

    def test_manual_unbound_orders_do_not_share_status_by_api_order_zero(self):
        reader = self.reader()
        reader.order_status(0, "Submitted", 1, 4, 1001)
        reader.order_status(0, "Submitted", 2, 3, 1002)
        contract = types.SimpleNamespace(conId=101, symbol="SPY", currency="USD", secType="STK")
        for perm_id in (1001, 1002):
            reader.order(0, contract, types.SimpleNamespace(account=ACCOUNT, permId=perm_id,
                clientId=0, action="BUY", totalQuantity=5, orderType="LMT", lmtPrice=100),
                types.SimpleNamespace(status="Submitted"))
        self.assertEqual([o["remaining_quantity"] for o in reader.raw["orders"]], ["4", "3"])


def sdk_double(error_args=None, raw=None):
    """Exercises the production transport, without installing or connecting ibapi."""
    calls, clients = [], []
    data = copy.deepcopy(raw if raw is not None else raw_fixture())

    class Wrapper:
        def __init__(self): pass

    class Client:
        def __init__(self, wrapper):
            self.connected = False
            clients.append(self)

        def connect(self, host, port, clientId):
            calls.append(("connect", host, port, clientId))
            self.connected = True
            if error_args: self.error(*error_args)
            self.nextValidId(1)

        def run(self): pass
        def isConnected(self): return self.connected
        def disconnect(self): self.connected = False
        def reqManagedAccts(self):
            calls.append(("reqManagedAccts",))
            self.managedAccounts(ACCOUNT)

        def reqAccountSummary(self, reqId, groupName, tags):
            calls.append(("reqAccountSummary",))
            for row in data["account_values"]:
                self.accountSummary(reqId, row["account_id"], row["tag"], row["value"], row["currency"])
            self.accountSummaryEnd(reqId)

        def reqAccountUpdates(self, subscribe, acctCode):
            calls.append(("reqAccountUpdates", subscribe))
            if subscribe:
                for row in data["account_values"]:
                    tag = row["tag"]
                    if row.get("value_scope") == "per_currency" and not tag.startswith("$LEDGER-"):
                        tag = "$LEDGER-" + tag
                    self.updateAccountValue(tag, row["value"], row["currency"], row["account_id"])
                self.accountDownloadEnd(acctCode)

        def reqPositions(self):
            calls.append(("reqPositions",))
            for row in data["positions"]:
                contract = types.SimpleNamespace(conId=row["con_id"], symbol=row["instrument_id"],
                                                 currency=row["currency"], secType=row.get("sec_type", "STK"))
                self.position(row["account_id"], contract, row["quantity"], row["avg_cost"])
            self.positionEnd()

        def reqAllOpenOrders(self):
            calls.append(("reqAllOpenOrders",))
            for row in data["orders"]:
                contract = types.SimpleNamespace(conId=row["con_id"], symbol=row["instrument_id"],
                                                 currency=row["currency"], secType=row.get("sec_type", "STK"))
                self.orderStatus(row["order_id"], row["status"], row["filled_quantity"], row["remaining_quantity"],
                                 0, row["perm_id"], 0, 0, row["client_id"], "")
                self.openOrder(row["order_id"], contract, types.SimpleNamespace(account=row["account_id"],
                    permId=row["perm_id"], clientId=row["client_id"], action=row["side"], totalQuantity=row["total_quantity"],
                    orderType=row["order_type"], lmtPrice=row["limit_price"]), types.SimpleNamespace(status=row["status"]))
            self.openOrderEnd()

        def reqExecutions(self, reqId, execFilter):
            calls.append(("reqExecutions",))
            self.execDetailsEnd(reqId)

        def reqContractDetails(self, reqId, contract):
            calls.append(("reqContractDetails", contract.symbol))
            saved = data["contracts"].get(contract.symbol.replace(" ", "."), [{}])[0]
            contract.conId = saved.get("con_id", 101)
            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            detail = types.SimpleNamespace(contract=contract, validExchanges=contract.exchange, marketRuleIds="26",
                                           minTick=0.01, liquidHours=today + ":0000-" + today + ":2359", timeZoneId="UTC",
                                           industry=saved.get("industry"), category=saved.get("category"), subcategory=saved.get("subcategory"))
            self.contractDetails(reqId, detail)
            self.contractDetailsEnd(reqId)

        def reqMarketRule(self, marketRuleId):
            calls.append(("reqMarketRule",))
            self.marketRule(marketRuleId, [types.SimpleNamespace(lowEdge=0, increment=0.01)])

        def reqMarketDataType(self, marketDataType): calls.append(("reqMarketDataType", marketDataType))

        def reqMktData(self, reqId, contract, genericTickList, snapshot, regulatorySnapshot, mktDataOptions):
            calls.append(("reqMktData", snapshot, regulatorySnapshot))
            self.marketDataType(reqId, 1)
            self.tickPrice(reqId, 1, 100, types.SimpleNamespace())
            self.tickPrice(reqId, 2, 100.02, types.SimpleNamespace())
            self.tickSize(reqId, 0, 100)
            self.tickSize(reqId, 3, 200)
            self.tickGeneric(reqId, 49, 0)
            self.tickSnapshotEnd(reqId)

        def cancelMktData(self, reqId): calls.append(("cancelMktData",))
        def cancelAccountSummary(self, reqId): calls.append(("cancelAccountSummary",))
        def cancelPositions(self): calls.append(("cancelPositions",))

    modules = {name: types.ModuleType(name) for name in ("ibapi", "ibapi.client", "ibapi.wrapper", "ibapi.contract", "ibapi.execution")}
    modules["ibapi"].__path__ = []
    modules["ibapi.client"].EClient = Client
    modules["ibapi.wrapper"].EWrapper = Wrapper
    modules["ibapi.contract"].Contract = types.SimpleNamespace
    modules["ibapi.execution"].ExecutionFilter = types.SimpleNamespace
    return modules, calls, clients


class OfficialSocketInterface(unittest.TestCase):
    def test_prefixed_base_sentinel_requests_reported_currency_fx(self):
        raw = raw_fixture(base="AUD")
        raw["account_values"][0].update(tag="$LEDGER-Currency", value="BASE")
        modules, calls, _ = sdk_double(raw=raw)
        with patch.dict(sys.modules, modules):
            result = broker.collect_execution_snapshot(SETTINGS, ["SPY"])
        self.assertEqual(result["status"], "ready", result["issues"])
        self.assertEqual(result["account"]["base_currency"], "AUD")
        self.assertIn(("reqContractDetails", "AUD"), calls)
        self.assertNotIn(("reqContractDetails", "BASE"), calls)
        self.assertEqual(result["cash"]["USD"]["available"], "900")
        self.assertEqual(result["cash"]["USD"]["settled_evidence"]["source"], "account_updates")

    def test_real_transport_expands_after_account_ends_before_contract_queries(self):
        raw = raw_fixture()
        raw["positions"].append(dict(account_id=ACCOUNT, instrument_id="NVDA", con_id=102,
                                      currency="USD", sec_type="STK", quantity="2", avg_cost="90"))
        raw["orders"] = [order(instrument_id="AAPL", con_id=103, sec_type="STK")]
        add_symbol(raw, "NVDA", 102, "Technology")
        add_symbol(raw, "AAPL", 103, "Technology")
        modules, calls, _ = sdk_double(raw=raw)
        with patch.dict(sys.modules, modules):
            result = broker.collect_execution_snapshot(SETTINGS, ["SPY"])
        self.assertEqual(result["status"], "ready", result["issues"])
        self.assertEqual(set(result["quotes"]), {"SPY", "NVDA", "AAPL"})
        self.assertLess(calls.index(("reqExecutions",)), calls.index(("reqContractDetails", "SPY")))
        self.assertEqual(result["quotes"]["NVDA"]["sector"], "technology")
        self.assertEqual(result["quotes"]["NVDA"]["broker_industry"]["subcategory"], "declared subcategory")

    def test_production_transport_invokes_only_read_queries_and_query_cleanup(self):
        modules, calls, clients = sdk_double()
        with patch.dict(sys.modules, modules):
            result = broker.collect_execution_snapshot(SETTINGS, ["SPY"])
        self.assertEqual(result["status"], "ready", result["issues"])
        self.assertIn(("reqAllOpenOrders",), calls)
        self.assertIn(("reqMktData", True, False), calls)
        names = {call[0] for call in calls}
        self.assertFalse(names & {"placeOrder", "cancelOrder", "reqGlobalCancel", "reqOpenOrders", "reqAutoOpenOrders"})
        self.assertFalse(clients[0].isConnected())
        for name in ("placeOrder", "cancelOrder", "reqGlobalCancel", "reqOpenOrders", "reqAutoOpenOrders"):
            with self.assertRaises(broker.BrokerError): getattr(clients[0], name)()

    def test_current_and_legacy_error_signatures_keep_entitlement_status(self):
        for args in ((1, 1759417200000, 354, "private account " + ACCOUNT, ""),
                     (1, 354, "private account " + ACCOUNT, "")):
            modules, _, _ = sdk_double(error_args=args)
            with patch.dict(sys.modules, modules):
                result = broker.collect_execution_snapshot(SETTINGS, ["SPY"])
            self.assertEqual(result["issues"], ["ibkr_not_entitled"])
            self.assertNotIn(ACCOUNT, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
