"""Config contract: TOML in, frozen validated dataclasses out; no secrets ever live here."""
from __future__ import annotations

import sys
import ast
import copy
import json
import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from copilot import config as cfg

ROOT = Path(__file__).resolve().parent.parent

VALID = """
schema_version = 1

[etf]
universe = ["QQQ", "SPY", "VEA", "VWO", "IWM"]
investable_cash_usd = 0
min_cash_reserve_pct = 0.15
max_drawdown_pct = 0.20
adopted_rule_id = ""

[gold]
investable_total_cny = 0
min_order_cny = 1200
order_increment_cny = 200
max_orders_per_day = 10

[notify]
email_to = ""
timezone = "Australia/Sydney"
scan_time_local = "07:00"
language = "zh"
"""


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "user.toml"

    def write(self, text: str) -> Path:
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def test_valid_file_loads_into_frozen_dataclasses(self):
        loaded = cfg.load_config(self.write(VALID))
        self.assertTrue(loaded.present)
        self.assertEqual(loaded.etf.universe, ("QQQ", "SPY", "VEA", "VWO", "IWM"))
        self.assertEqual(loaded.gold.min_order_cny, 1200)
        self.assertEqual(loaded.notify.language, "zh")
        with self.assertRaises(Exception):
            loaded.etf.universe = ()

    def test_missing_file_yields_defaults_marked_absent(self):
        loaded = cfg.load_config(Path(self.temp.name) / "nope.toml")
        self.assertFalse(loaded.present)
        self.assertEqual(loaded.etf.universe, ())
        self.assertEqual(loaded.gold.min_order_cny, 1200)

    def test_universe_must_be_registered_equity_etfs(self):
        with self.assertRaisesRegex(ValueError, "etf.universe.*ABCD"):
            cfg.load_config(self.write(VALID.replace('"IWM"', '"ABCD"')))
        with self.assertRaisesRegex(ValueError, "etf.universe.*TLT.*defensive"):
            cfg.load_config(self.write(VALID.replace('"IWM"', '"TLT"')))

    def test_universe_size_bounds(self):
        thirteen = ", ".join(f'"{s}"' for s in ("QQQ", "SPY", "VEA", "VWO", "IWM", "VTI", "VUG",
                                                "VTV", "XLK", "XLV", "XLF", "XLE", "XLY"))
        with self.assertRaisesRegex(ValueError, "etf.universe.*at most 12"):
            cfg.load_config(self.write(VALID.replace('["QQQ", "SPY", "VEA", "VWO", "IWM"]', f"[{thirteen}]")))

    def test_duplicate_symbols_rejected(self):
        with self.assertRaisesRegex(ValueError, "etf.universe.*duplicate"):
            cfg.load_config(self.write(VALID.replace('"IWM"', '"QQQ"')))

    def test_percentages_and_amounts_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "etf.min_cash_reserve_pct"):
            cfg.load_config(self.write(VALID.replace("min_cash_reserve_pct = 0.15", "min_cash_reserve_pct = 1.5")))
        with self.assertRaisesRegex(ValueError, "gold.order_increment_cny"):
            cfg.load_config(self.write(VALID.replace("order_increment_cny = 200", "order_increment_cny = 0")))
        with self.assertRaisesRegex(ValueError, "notify.scan_time_local"):
            cfg.load_config(self.write(VALID.replace('scan_time_local = "07:00"', 'scan_time_local = "7am"')))

    def test_booleans_are_not_numbers(self):
        with self.assertRaisesRegex(ValueError, "etf.min_cash_reserve_pct"):
            cfg.load_config(self.write(VALID.replace("min_cash_reserve_pct = 0.15", "min_cash_reserve_pct = true")))

    def test_unknown_schema_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "schema_version"):
            cfg.load_config(self.write(VALID.replace("schema_version = 1", "schema_version = 2")))

    def test_missing_section_is_rejected(self):
        with self.assertRaisesRegex(ValueError, r"\[gold\]"):
            cfg.load_config(self.write(VALID.split("[gold]")[0] + VALID.split("[notify]")[1].join(["[notify]", ""])))

    def test_shipped_example_is_valid(self):
        loaded = cfg.load_config(ROOT / "config" / "user.example.toml")
        self.assertTrue(loaded.present)
        self.assertGreaterEqual(len(loaded.etf.universe), 8)

    def test_secrets_are_not_config_fields(self):
        with self.assertRaisesRegex(ValueError, "unknown section 'smtp'"):
            cfg.load_config(self.write(VALID + '\n[smtp]\npassword = "x"\n'))


class CashSemantics(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "user.toml"

    def write(self, text: str) -> Path:
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def test_the_field_is_named_for_cash(self):
        config = cfg.load_config(self.write(VALID.replace(
            "investable_cash_usd = 0", "investable_cash_usd = 5000.0")))
        self.assertAlmostEqual(config.etf.investable_cash_usd, 5000.0)

    def test_the_old_name_is_refused_and_names_the_new_one(self):
        # VALID already carries the current field name, so the old-name
        # rejection is exercised by reintroducing it here.
        stale = VALID.replace("investable_cash_usd = 0", "investable_total_usd = 0")
        with self.assertRaisesRegex(ValueError, "investable_cash_usd"):
            cfg.load_config(self.write(stale))

    def test_the_error_explains_the_semantics_not_just_the_rename(self):
        stale = VALID.replace("investable_cash_usd = 0", "investable_total_usd = 0")
        try:
            cfg.load_config(self.write(stale))
        except ValueError as exc:
            self.assertIn("cash", str(exc).lower())
        else:
            self.fail("expected ValueError")


class AdoptionPointer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "user.toml"

    def write(self, text: str) -> Path:
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def config(self, pointer):
        return self.write(VALID.replace('adopted_rule_id = ""', f'adopted_rule_id = "{pointer}"'))

    def test_a_well_formed_pointer_is_accepted(self):
        self.assertEqual(cfg.load_config(self.config("rule-0123456789abcdef")).etf.adopted_rule_id,
                         "rule-0123456789abcdef")

    def test_empty_means_nothing_is_adopted(self):
        self.assertEqual(cfg.load_config(self.config("")).etf.adopted_rule_id, "")

    def test_a_malformed_pointer_is_refused(self):
        for bad in ("momentum", "rule-XYZ", "rule-0123", "rule-0123456789ABCDEF"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "adopted_rule_id"):
                cfg.load_config(self.config(bad))


class RuntimeMappingContracts(unittest.TestCase):
    def generated(self, servers):
        from sync_runtimes import generated_files
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = generated_files(root=root, config={"mcpServers": servers})[root / ".codex/config.toml"]
            return text, tomllib.loads(text)["mcp_servers"]

    def test_http_auth_preserves_bearer_and_custom_header_references(self):
        _, generated = self.generated({
            "alpha": {"url": "https://fixture.invalid/mcp",
                      "headers": {"Authorization": "Bearer ${ALPHA_VANTAGE_API_KEY}"}},
            "tushare": {"url": "https://fixture.invalid/mcp",
                        "headers": {"X-Tushare-Token": "${TUSHARE_TOKEN}"}},
        })
        self.assertEqual(generated["alpha"]["bearer_token_env_var"], "ALPHA_VANTAGE_API_KEY")
        self.assertEqual(generated["tushare"]["env_http_headers"], {"X-Tushare-Token": "TUSHARE_TOKEN"})

    def test_same_name_stdio_mapping_stays_direct(self):
        _, generated = self.generated({"fixture": {"command": "uv", "args": ["run", "fixture.py"],
                                                     "env": {"TOKEN": "${TOKEN}"}}})
        self.assertEqual(generated["fixture"]["args"], ["run", "fixture.py"])
        self.assertEqual(generated["fixture"]["env_vars"], ["TOKEN"])

    def test_renamed_stdio_mapping_uses_the_runtime_launcher(self):
        _, generated = self.generated({"gold": {"command": "uvx", "args": ["mcp-metal-price"],
                                               "env": {"GOLDAPI_KEY": "${GOLD_API_KEY}"}}})
        self.assertIn("scripts/mcp_env.py", generated["gold"]["args"])
        self.assertEqual(generated["gold"]["env_vars"], ["GOLD_API_KEY"])

    def test_actual_child_receives_the_renamed_value_and_preserves_exit_status(self):
        from mcp_env import launch
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "received.json"
            code = ("import json,os,sys; "
                    "open(sys.argv[1],'w').write(json.dumps(os.environ.get('GOLDAPI_KEY'))); "
                    "sys.exit(7)")
            spec = {"command": sys.executable, "args": ["-c", code, str(output)],
                    "env": {"GOLDAPI_KEY": "${GOLD_API_KEY}"}}
            inherited = {**os.environ, "GOLD_API_KEY": "fixture-only-value", "GOLDAPI_KEY": "stale"}
            self.assertEqual(launch(spec, inherited=inherited, cwd=Path(directory)), 7)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), "fixture-only-value")

    def test_unrepresentable_secrets_are_refused_without_disclosing_values(self):
        for spec in ({"command": "uv", "env": {"TOKEN": "fixture-secret"}},
                     {"url": "https://fixture.invalid", "headers": {"Authorization": "Bearer fixture-secret"}}):
            with self.assertRaises(ValueError) as captured:
                self.generated({"fixture": spec})
            self.assertNotIn("fixture-secret", str(captured.exception))


class ContextFacadeContracts(unittest.TestCase):
    def setUp(self):
        from copilot import service
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "journal.sqlite"
        service.declare_coverage(sleeve="gold", base_currency="CNY", db_path=self.db)

    def test_cli_reports_the_selected_books_coverage(self):
        for sleeve, expected in (("gold", True), ("etf", False)):
            result = subprocess.run([sys.executable, str(ROOT / "scripts/copilot_cli.py"),
                                     "--db", str(self.db), "context", "--sleeve", sleeve],
                                    capture_output=True, text=True, encoding="utf-8", check=True)
            context = json.loads(result.stdout)
            self.assertIs(context["portfolio_complete"], expected)
            self.assertEqual(context["base_currency"], "CNY" if expected else None)

    def test_mcp_facade_selects_the_same_book_without_loading_the_sdk(self):
        from copilot import service
        tree = ast.parse((ROOT / "mcps/copilot_mcp.py").read_text(encoding="utf-8"))
        function = copy.deepcopy(next(node for node in tree.body
                                      if isinstance(node, ast.FunctionDef) and node.name == "get_investment_context"))
        function.decorator_list = []
        namespace = {"service": SimpleNamespace(context=lambda instruments, **kwargs:
                         service.context(instruments, db_path=self.db, **kwargs))}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "mcp-context-fixture", "exec"), namespace)
        context = namespace["get_investment_context"](sleeve="gold")
        self.assertTrue(context["portfolio_complete"])
        self.assertEqual(context["base_currency"], "CNY")

    def test_watchlist_validation_cli_uses_the_registry(self):
        for symbol, expected_code in (("QQQ", 0), ("TSLA", 2)):
            result = subprocess.run([sys.executable, str(ROOT / "scripts/copilot_cli.py"),
                                     "resolve"], input=json.dumps({"instrument_id": symbol}),
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, expected_code)
            if not expected_code:
                self.assertEqual(json.loads(result.stdout)["instrument_id"], "QQQ")


if __name__ == "__main__":
    unittest.main()
