"""Config contract: TOML in, frozen validated dataclasses out; no secrets ever live here."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
