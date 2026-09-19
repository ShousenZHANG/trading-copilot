# Plan 1 — Skeleton and Deletions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the deep multi-agent pipeline and stock-oriented features, narrow the instrument registry to an ETF whitelist plus Chinese gold, add a private TOML config module, write ADR-0004, and leave the repo green on every existing gate — so Plans 2–7 build on a clean base.

**Architecture:** This plan only deletes and narrows. The evidence core (`scripts/copilot/market_data.py`, `policy.py`, `journal.py`, `providers.py`) keeps its contracts; the only behavioural change is that unknown US tickers are rejected at `normalize_instrument` instead of being provisionally accepted as stocks, which lets the "dynamic ETF identity" branch in `policy.py` be deleted. A new `scripts/copilot/config.py` reads `config/user.toml` (gitignored) with stdlib `tomllib` — no new dependency, because the offline CI job installs zero third-party packages. ADR-0004 records the invariant later plans implement: the policy engine computes `{action, quantity, limit_price, rule_id}`; the language model only explains.

**Tech Stack:** Python 3.11 stdlib (`tomllib`, `dataclasses`, `unittest`), existing PEP 723 script headers, `scripts/check.py`, `scripts/sync_runtimes.py`, `scripts/package_release.py`.

**Design decisions this plan implements (from the 2026-09-19 design session):** Q2/Q17 (US ETFs only, no ASX), Q18 (delete `/earnings /screen /debate /analyze /advise`, all 14 agents, `trading-copilot` deep skill; keep `/gold /portfolio /scan /watchlist /weekly-review`), Q21 (rule library public, adopted parameters private), Q30 (ADR-0004), Q37 (QQQI/JEPQ/JEPI must be resolvable so Plan 3 can proxy-backtest them), Q43 (delete the deep pipeline), Q44 (delete legacy ASX scripts and data — after a backup outside the repo).

**Deferred to later plans (do NOT do here):** new providers (Plan 2), `catalyst-calendar` replacement by a macro-calendar skill (Plan 2 creates it next to the provider; this plan deletes the old one), backtest (Plan 3), policy output shape change (Plan 4), SMTP/scheduler (Plan 5), gold sleeve journal fields (Plan 6), README/INSTALL full rewrite and `evals/` fate (Plan 7). `research_data.py:355-360` still computes an (always empty) `stocks` list after this plan; Plan 2 rewrites that file.

---

## File structure

**Delete (tracked):**
- `.claude/commands/advise.md`, `analyze.md`, `debate.md`, `earnings.md`, `screen.md`
- `.claude/agents/**` (14 files: `investment-advisor.md`, `trader.md`, `analysts/{fundamentals,macro,market,news,social}-analyst.md`, `managers/{portfolio,research}-manager.md`, `researchers/{bull,bear}-researcher.md`, `risk-debators/{aggressive,conservative,neutral}-debator.md`)
- `.claude/skills/trading-copilot/SKILL.md`, `.claude/skills/catalyst-calendar/SKILL.md`
- Generated mirrors: `.codex/agents/*.toml` (14), `.agents/skills/trading-copilot/`, `.agents/skills/catalyst-calendar/`
- Legacy scripts: `scripts/assemble_report.py`, `validate_outputs.py`, `validate_pm_output.py`, `trigger_state.py`, `memory.py`, `portfolio_check.py`, `benchmarks.py`, `montecarlo.py`, `polymarket_odds.py`, `prices.py`, `monitor.ps1`, `monitor-wt.ps1`, `scripts/_test_memory.py`, `scripts/_test_validation.py`
- **Keep** `scripts/parse_rating.py` and `scripts/ticker.py` (imported by `evals/stockbench/`), `scripts/notify.py` (Telegram; Plan 5 reuses it), `scripts/copilot_probe.py`, `scripts/mcp_handshake.py`.

**Delete (private, gitignored — after backup):** `data/decisions/`, `data/runs/`, `data/memory/trading_memory.md`, `data/positions.md`, `data/audit/`.

**Create:**
- `scripts/copilot/config.py` — TOML config loader, frozen dataclasses, validation against the instrument registry
- `scripts/_test_config.py`, `scripts/_test_instruments.py`, `scripts/_test_ticker_rating.py`
- `config/user.example.toml` — shipped template
- `docs/adr/0004-engine-computes-model-explains.md`

**Modify:**
- `scripts/check.py` (commands set, drop analyze markers, agents check tolerates zero, drop deep-skill mirror check, drop methodology marker, private-state list)
- `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `scripts/copilot/__init__.py` (version 0.5.0, `agents: []`, description, keywords)
- `scripts/copilot/instruments.py` (whitelist), `scripts/copilot/policy.py` (remove dynamic-ETF branch)
- `scripts/_test_policy.py`, `scripts/_test_market_data.py`, `scripts/_test_copilot_service.py`
- `scripts/copilot/service.py` (remove `prepare_run`, `resume_run`, `code_version`), `scripts/copilot_cli.py` (remove verbs, add `config`)
- `scripts/package_release.py` (exclude `config/user.toml`, include example, self-test cases)
- `.gitignore`, `.claude/settings.json`, `.github/workflows/ci.yml`
- `.claude/skills/investment-chat/SKILL.md`, `.claude/commands/scan.md`, `CLAUDE.md`, `README.md`, `README_zh.md`, `AGENTS.md`, `docs/methodology.md`

---

## Baseline commands (run before Task 1 and after every task)

```bash
python scripts/check.py
python scripts/sync_runtimes.py --check
python -m unittest discover -s scripts -p "_test_*.py"
python scripts/package_release.py --self-test
```

Expected at start (verified 2026-09-19): `OK - 95 file(s) checked, 0 errors, 0 warning(s).` / `Checked runtime files; 0 changed.` / `Ran 121 tests ... OK (skipped=2)` / `34/34` self-test lines pass.

---

### Task 0: Back up private state, then confirm deletion

**Files:**
- Read-only: `data/decisions/`, `data/runs/`, `data/memory/trading_memory.md`, `data/positions.md`, `data/audit/`

- [ ] **Step 1: Create the backup zip outside the repository**

```bash
python - <<'EOF'
import shutil, datetime, pathlib
root = pathlib.Path(".").resolve()
stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
dest = root.parent / f"trading-copilot-private-backup-{stamp}"
tmp = dest.with_suffix(".staging")
tmp.mkdir()
for rel in ("data/decisions", "data/runs", "data/audit", "data/memory/trading_memory.md", "data/positions.md"):
    src = root / rel
    if src.is_dir():
        shutil.copytree(src, tmp / rel)
    elif src.is_file():
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, tmp / rel)
archive = shutil.make_archive(str(dest), "zip", tmp)
shutil.rmtree(tmp)
print("BACKUP:", archive)
EOF
```

Expected: one line `BACKUP: D:\trading-copilot-private-backup-<stamp>.zip` (the parent directory of the checkout, never inside it).

- [ ] **Step 2: STOP and report the backup path to the user. Do not proceed to Step 3 until the user replies with explicit confirmation to delete.**

- [ ] **Step 3: Delete the private state (only after confirmation)**

```bash
python - <<'EOF'
import shutil, pathlib
for rel in ("data/decisions", "data/runs", "data/audit"):
    p = pathlib.Path(rel)
    if p.is_dir():
        shutil.rmtree(p); print("removed dir", rel)
for rel in ("data/memory/trading_memory.md", "data/positions.md"):
    p = pathlib.Path(rel)
    if p.is_file():
        p.unlink(); print("removed file", rel)
EOF
git status --short
```

Expected: `git status --short` prints nothing for these paths (they were gitignored). `data/memory/README.md` and `data/watchlist.md` must still exist.

- [ ] **Step 4: No commit (nothing tracked changed).**

---

### Task 1: Delete the deep pipeline (commands, agents, skills, mirrors) and re-green `check.py`

**Files:**
- Delete: listed in "File structure → Delete (tracked)" first four bullets
- Modify: `scripts/check.py:27-38, 71-78, 175-224, 275-302, 397-406`
- Modify: `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `scripts/copilot/__init__.py:3`

- [ ] **Step 1: Remove the files with git**

```bash
git rm -q .claude/commands/advise.md .claude/commands/analyze.md .claude/commands/debate.md .claude/commands/earnings.md .claude/commands/screen.md
git rm -q -r .claude/agents
git rm -q -r .claude/skills/trading-copilot .claude/skills/catalyst-calendar
git rm -q -r .codex/agents .agents/skills/trading-copilot .agents/skills/catalyst-calendar
git status --short | wc -l
```

Expected: 40 deleted entries (5 commands + 14 agents + 2 skills + 14 codex agent tomls + 2 mirrored skill dirs ≥ 2 files each; exact count depends on files inside the mirrored skill dirs — anything between 38 and 42 is fine).

- [ ] **Step 2: Run the checker to see it break**

```bash
python scripts/check.py
```

Expected: a Python traceback ending in `FileNotFoundError: ... .claude/commands/analyze.md` (line 187 reads it unconditionally). This is the red state.

- [ ] **Step 3: Rewrite the command set and delete the analyze guard in `scripts/check.py`**

Replace lines 27-38 with:

```python
EXPECTED_COMMANDS = {
    "gold.md",
    "portfolio.md",
    "scan.md",
    "watchlist.md",
    "weekly-review.md",
}
```

Delete lines 71-78 (`OPUS_AGENTS = ...` through the closing `}` of `INTERNAL_DEBATE_AGENTS`).

Replace the whole `check_commands` function (lines 175-195) with:

```python
def check_commands() -> None:
    command_dir = ROOT / ".claude" / "commands"
    found = {p.name for p in command_dir.glob("*.md")}
    missing = EXPECTED_COMMANDS - found
    extra = found - EXPECTED_COMMANDS
    if missing:
        err(f".claude/commands: missing commands {sorted(missing)}")
    if extra:
        warn(f".claude/commands: unexpected command files {sorted(extra)}")
    for command in command_dir.glob("*.md"):
        meta = parse_frontmatter(command)
        for key in ("description", "argument-hint"):
            if key not in meta:
                err(f"{rel(command)}: missing frontmatter key '{key}'")
```

Replace the whole `check_agents` function (lines 198-224) with:

```python
def check_agents() -> None:
    """The deep pipeline is gone (ADR-0004). Any agent that still exists must be
    well-formed, but zero agents is the expected state."""
    agent_dir = ROOT / ".claude" / "agents"
    if not agent_dir.is_dir():
        return
    for path in sorted(agent_dir.rglob("*.md")):
        meta = parse_frontmatter(path)
        for key in ("name", "description", "tools", "model"):
            if key not in meta:
                err(f"{rel(path)}: missing frontmatter key '{key}'")
        if not meta.get("tools"):
            err(f"{rel(path)}: tools list is empty")
        check_agent_mcp_grants(path, meta.get("name", ""), meta.get("tools") or "", read(path))
```

In `check_skill_mirror` (lines 275-289) delete the four lines from `# Preserve the original named mirror check` through `err(".agents/skills/trading-copilot/SKILL.md drifted from .claude source")`, so the function ends after the `missing shared runtime component` loop.

Replace the whole `check_docs_and_workflows` function (lines 292-302) with:

```python
def check_docs_and_workflows() -> None:
    """Docs must not advertise commands or agents the plugin no longer ships."""
    stale = ("/analyze", "/advise", "/debate", "/earnings", "/screen",
             "portfolio-manager", "research-manager", "bull-researcher")
    for relative in ("README.md", "README_zh.md", "AGENTS.md", "CLAUDE.md"):
        text = read(ROOT / relative)
        for marker in stale:
            if marker in text:
                err(f"{relative}: still references removed feature '{marker}'")
```

In `check_plugin_manifest` (line 156-168) the agents block stays as-is: with `.claude/agents` deleted, `rglob` on a missing directory yields nothing, so `on_disk == []` and the manifest must declare `"agents": []`.

- [ ] **Step 4: Update both plugin manifests and the package version**

`.claude-plugin/plugin.json` — replace the whole file with:

```json
{
  "name": "trading-copilot",
  "displayName": "Trading Copilot",
  "version": "0.5.0",
  "description": "Rule-driven long-term ETF and Chinese gold accumulation: verified free-data snapshots, backtested rules the user adopts, deterministic orders, local journal. No return is promised.",
  "author": {
    "name": "eddy.zhang24",
    "email": "eddy.zhang24@gmail.com"
  },
  "license": "MIT",
  "keywords": [
    "trading",
    "finance",
    "etf",
    "gold",
    "macro",
    "portfolio",
    "risk-management",
    "backtest",
    "investment-research",
    "market-data",
    "mcp"
  ],
  "homepage": "https://github.com/ShousenZHANG/trading-copilot",
  "repository": "https://github.com/ShousenZHANG/trading-copilot",
  "commands": "./.claude/commands",
  "agents": [],
  "skills": "./.claude/skills",
  "mcpServers": "./.mcp.json"
}
```

`.codex-plugin/plugin.json` — change line 3 to `"version": "0.5.0",`, and replace both the top-level `description` (line 4) and `interface.longDescription` (line 14) with the same sentence used above: `"Rule-driven long-term ETF and Chinese gold accumulation: verified free-data snapshots, backtested rules the user adopts, deterministic orders, local journal. No return is promised."`. Change `interface.shortDescription` (line 13) to `"Backtested ETF and gold rules with a local journal."`.

`scripts/copilot/__init__.py` line 3: `__version__ = "0.5.0"`.

- [ ] **Step 5: Regenerate runtimes and run the checker**

```bash
python scripts/sync_runtimes.py
python scripts/check.py
```

Expected sync: `Generated runtime files; 0 changed.` (the deleted mirrors have no sources, so nothing is regenerated). Expected check: **FAIL** listing only doc references — `README.md: still references removed feature '/analyze'`, `README.md: ... '/advise'`, `README_zh.md: ... '/analyze'`, `CLAUDE.md: ... '/analyze'`. Those are fixed in Task 6; do not fix them here.

- [ ] **Step 6: Run the unit tests to confirm nothing else depended on the deleted prompts**

```bash
python -m unittest discover -s scripts -p "_test_*.py"
```

Expected: `OK (skipped=2)` with 121 tests. (Prompts are not imported by tests.)

- [ ] **Step 7: Commit**

```bash
git add -A .claude .codex .agents scripts/check.py .claude-plugin .codex-plugin scripts/copilot/__init__.py
git commit -m "refactor: remove deep multi-agent pipeline, agents and stock-oriented commands

check.py now expects the five surviving commands, tolerates zero agents,
and fails on docs that still advertise removed features. Version 0.5.0."
```

---

### Task 2: Remove `prepare_run` / `resume_run` / `code_version` and their CLI verbs

**Files:**
- Modify: `scripts/copilot/service.py:163-206`, `scripts/copilot_cli.py:46-51, 68-71`
- Test: `scripts/_test_copilot_service.py:85-93`

- [ ] **Step 1: Delete the test that exercises the removed function**

In `scripts/_test_copilot_service.py` delete the whole method `test_tampered_resume_manifest_rejected` (lines 85-93).

- [ ] **Step 2: Run the tests — still green (nothing removed yet)**

```bash
python -m unittest scripts._test_copilot_service -v 2>&1 | tail -3
```

Expected: `Ran 8 tests` ... `OK`.

- [ ] **Step 3: Remove the functions from `service.py`**

Delete lines 163-206 of `scripts/copilot/service.py` — everything from `def prepare_run(` through the `return {...}` of `resume_run` — leaving `render_decision` (ends line 160) immediately followed by `def capabilities()`.

- [ ] **Step 4: Remove the CLI verbs**

In `scripts/copilot_cli.py` delete lines 46-51:

```python
    prepare = sub.add_parser("prepare-run")
    prepare.add_argument("instruments", nargs="+")
    prepare.add_argument("--mode", choices=("tactical", "accumulation"), default="tactical")
    prepare.add_argument("--horizon", choices=("daily", "swing", "long_term"), default="daily")
    resume = sub.add_parser("resume-run")
    resume.add_argument("run_id")
```

and lines 68-71:

```python
        elif args.command == "prepare-run":
            result = service.prepare_run(args.instruments, mode=args.mode, horizon=args.horizon, db_path=args.db)
        elif args.command == "resume-run":
            result = service.resume_run(args.run_id, db_path=args.db)
```

- [ ] **Step 5: Verify nothing else references them**

```bash
grep -rn "prepare_run\|resume_run\|code_version\|prepare-run\|resume-run" scripts mcps --include=*.py --include=*.md | grep -v __pycache__
```

Expected: no output.

- [ ] **Step 6: Run the full suite**

```bash
python -m unittest discover -s scripts -p "_test_*.py" 2>&1 | tail -3
```

Expected: `Ran 120 tests` ... `OK (skipped=2)`.

- [ ] **Step 7: Commit**

```bash
git add scripts/copilot/service.py scripts/copilot_cli.py scripts/_test_copilot_service.py
git commit -m "refactor: drop run manifests (prepare-run/resume-run) with the deep pipeline"
```

---

### Task 3: Narrow the instrument registry to an ETF whitelist

**Files:**
- Test: `scripts/_test_instruments.py` (new)
- Modify: `scripts/copilot/instruments.py` (full rewrite), `scripts/copilot/policy.py:224-229, 270-283`
- Modify: `scripts/_test_policy.py:43-77`, `scripts/_test_market_data.py:93`

- [ ] **Step 1: Write the failing registry tests**

Create `scripts/_test_instruments.py`:

```python
"""Registry contract: only whitelisted ETFs, the two Nasdaq indexes and SGE gold resolve."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from copilot.instruments import ETF_REGISTRY, get_instrument, normalize_instrument


class RegistryContracts(unittest.TestCase):
    def test_whitelisted_etf_is_registered_etf(self):
        for symbol in ("QQQ", "QQQM", "SPY", "VTI", "QQQI", "JEPQ", "JEPI", "IOO"):
            item = get_instrument(symbol)
            self.assertEqual(item["asset_class"], "etf", symbol)
            self.assertEqual(item["identity_status"], "registered", symbol)
            self.assertEqual(item["currency"], "USD")
            self.assertEqual(item["unit"], "share")
            self.assertTrue(item["tradable"])

    def test_registry_has_issuer_urls_for_covered_call_proxies(self):
        for symbol in ("QQQI", "JEPQ", "JEPI"):
            self.assertTrue(get_instrument(symbol)["issuer_url"].startswith("https://"), symbol)

    def test_unknown_us_ticker_is_rejected_not_provisionally_accepted(self):
        for symbol in ("AAPL", "NVDA", "ABCD", "BRK-B"):
            with self.assertRaisesRegex(ValueError, "not in the ETF registry"):
                normalize_instrument(symbol)

    def test_foreign_suffix_and_fx_are_rejected(self):
        for symbol in ("NDQ.AX", "IOO.AX", "0700.HK", "AUDUSD=X", "GC=F"):
            with self.assertRaises(ValueError):
                normalize_instrument(symbol)

    def test_indexes_and_gold_are_unchanged(self):
        self.assertEqual(get_instrument("^NDX")["asset_class"], "index")
        self.assertEqual(get_instrument("纳斯达克100")["instrument_id"], "^NDX")
        self.assertEqual(get_instrument("^IXIC")["unit"], "point")
        gold = get_instrument("GOLD.CNY")
        self.assertEqual(gold["asset_class"], "physical_gold")
        self.assertEqual(gold["quote_role"], "market_benchmark_not_retail_quote")
        self.assertEqual(get_instrument("黄金")["instrument_id"], "GOLD.CNY")

    def test_registry_is_frozen_and_uppercase(self):
        self.assertIsInstance(ETF_REGISTRY, frozenset)
        self.assertTrue(all(s == s.upper() for s in ETF_REGISTRY))
        self.assertGreaterEqual(len(ETF_REGISTRY), 39)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to see it fail**

```bash
python -m unittest scripts._test_instruments 2>&1 | tail -5
```

Expected: `ImportError: cannot import name 'ETF_REGISTRY'`.

- [ ] **Step 3: Rewrite `scripts/copilot/instruments.py`**

Replace the whole file with:

```python
"""Small, explicit registry for the conversation copilot's supported markets.

Scope (ADR-0004 / design session 2026-09-19): US-listed ETFs from the whitelist
below, the two Nasdaq indexes as benchmarks, and Shanghai Gold Exchange gold in
RMB. Nothing else resolves. An exchange benchmark is never silently converted
into a tradeable product, and an unknown US ticker is rejected rather than
provisionally accepted as a stock.
"""
from __future__ import annotations

import re

_ALIASES = {
    "NASDAQ100": "^NDX", "NASDAQ-100": "^NDX", "NDX": "^NDX",
    "NASDAQ": "^IXIC", "NASDAQ COMPOSITE": "^IXIC", "IXIC": "^IXIC",
    "纳斯达克100": "^NDX", "纳斯达克综合指数": "^IXIC",
    "GOLD": "GOLD.CNY", "黄金": "GOLD.CNY", "实物黄金": "GOLD.CNY",
    "SGE.AU9999": "GOLD.CNY", "AU99.99": "GOLD.CNY", "SHAU": "SGE.SHAU",
}

# The complete tradable universe. config/user.toml picks a subset of this set;
# Plan 3 backtests only symbols found here. Adding a symbol here is a code
# change with a test, never a runtime decision.
ETF_REGISTRY = frozenset({
    # broad US / global equity
    "SPY", "VOO", "IVV", "SPLG", "VTI", "VT", "DIA", "IWM", "VUG", "VTV", "SCHD",
    "VEA", "VWO", "VXUS", "IOO",
    # Nasdaq-100 family
    "QQQ", "QQQM",
    # covered-call income on the Nasdaq-100 / S&P 500 (proxy-backtested, ADR-0004)
    "QQQI", "JEPQ", "JEPI",
    # sectors
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC",
    "SMH", "SOXX",
    # bonds and gold ETFs stay resolvable for context; the ETF sleeve excludes
    # them by design (Q46) and config validation enforces that.
    "TLT", "BND", "GLD", "IAU", "SGOL", "GLDM",
})

DEFENSIVE_ETFS = frozenset({"TLT", "BND", "GLD", "IAU", "SGOL", "GLDM"})

_ISSUERS = {
    "QQQ": "https://www.invesco.com/qqq-etf/en/about.html",
    "QQQM": "https://www.invesco.com/us/financial-products/etfs/product-detail?productId=ETF-QQQM",
    "QQQI": "https://neosfunds.com/qqqi/",
    "JEPQ": "https://am.jpmorgan.com/us/en/asset-management/adv/products/jpmorgan-nasdaq-equity-premium-income-etf-etf-shares-46654q203",
    "JEPI": "https://am.jpmorgan.com/us/en/asset-management/adv/products/jpmorgan-equity-premium-income-etf-etf-shares-46641q332",
    "IOO": "https://www.ishares.com/us/products/239737/ishares-global-100-etf",
}

_BENCHMARKS = frozenset({"GOLD.CNY", "SGE.SHAU", "^NDX", "^IXIC"})


def normalize_instrument(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("instrument must be a non-empty string")
    symbol = _ALIASES.get(value.strip().upper(), value.strip().upper())
    if symbol in _BENCHMARKS:
        return symbol
    # No foreign suffixes, futures, FX, paths, or ambiguous gold spot substitutes.
    if not re.fullmatch(r"[A-Z]{1,6}", symbol):
        raise ValueError("unsupported instrument: use a whitelisted US ETF, ^NDX, ^IXIC, or GOLD.CNY")
    if symbol not in ETF_REGISTRY:
        raise ValueError(f"{symbol} is not in the ETF registry; supported ETFs: {', '.join(sorted(ETF_REGISTRY))}")
    return symbol


def get_instrument(value: str) -> dict:
    symbol = normalize_instrument(value)
    if symbol in {"GOLD.CNY", "SGE.SHAU"}:
        return {
            "instrument_id": symbol,
            "asset_class": "physical_gold" if symbol == "GOLD.CNY" else "index",
            "currency": "CNY", "unit": "gram", "tradable": False,
            "calendar": "SGE", "timezone": "Asia/Shanghai",
            "price_kind": "sge_au9999_close" if symbol == "GOLD.CNY" else "shau_pm_benchmark",
            "adjustment": "none", "purity": "0.9999",
            "quote_role": "market_benchmark_not_retail_quote",
        }
    index = symbol in {"^NDX", "^IXIC"}
    return {
        "instrument_id": symbol, "asset_class": "index" if index else "etf",
        "currency": "USD", "unit": "point" if index else "share", "tradable": not index,
        "calendar": "XNYS", "timezone": "America/New_York",
        "price_kind": "index_close" if index else "regular_session_close",
        "adjustment": "none" if index else "split",
        "issuer_url": _ISSUERS.get(symbol),
        "identity_status": "registered",
    }
```

- [ ] **Step 4: Run the registry tests**

```bash
python -m unittest scripts._test_instruments 2>&1 | tail -3
```

Expected: `Ran 6 tests` ... `OK`.

- [ ] **Step 5: Remove the dynamic-ETF branch from `scripts/copilot/policy.py`**

Replace lines 224-229:

```python
    if identity["asset_class"] in {"stock", "etf"} and item.get("asset_class") not in {"stock", "etf"}:
        blockers.append("unsupported asset class for a US stock/ETF")
    if identity.get("identity_status") == "registered" and item.get("asset_class") != identity["asset_class"]:
        blockers.append("registered instrument class cannot be changed")
    dynamic_etf = (identity.get("identity_status") == "requires_provider_confirmation"
                   and identity["asset_class"] == "stock" and item.get("asset_class") == "etf")
```

with:

```python
    if identity["asset_class"] == "etf" and item.get("asset_class") != "etf":
        blockers.append("registered ETF class cannot be changed")
```

Delete lines 270-283 (the `if dynamic_etf:` block through `blockers.append("dynamic ETF identity lacks matching verified market-source metadata")`).

- [ ] **Step 6: Run the policy tests to see which ones now encode the deleted behaviour**

```bash
python -m unittest scripts._test_policy 2>&1 | tail -12
```

Expected: 2 failures — `test_unregistered_etf_requires_matching_provider_identity` (its final assertion expects `requires_provider_confirmation` to block; that status no longer exists) and `test_research_record_cannot_confirm_dynamic_etf_identity` (asserts `data_insufficient` for a case that is now a valid registered ETF).

- [ ] **Step 7: Rewrite those two tests in `scripts/_test_policy.py`**

Replace lines 43-60 (`test_unregistered_etf_requires_matching_provider_identity`) with:

```python
    def test_registered_etf_requires_matching_market_evidence_identity(self):
        for provider, upstream in (("yahoo", "Yahoo Finance"), ("nasdaq", "Nasdaq US market data")):
            snapshot = fixture("JEPI")
            snapshot["instruments"]["JEPI"].update(identity_status="provider_confirmed")
            snapshot["evidence"][0].update(provider=provider, upstream=upstream, instrument_id="JEPI",
                asset_class="etf", currency="USD", unit="share", price_kind="regular_session_close")
            seal(snapshot)
            self.assertEqual(assess_proposal(proposal("JEPI"), snapshot, now=NOW)["action"], "buy")
            for change in ({"instrument_id": "QQQ"}, {"provider": "synthetic"},
                           {"upstream": "unverified mirror"}, {"status": "unknown"}, {"currency": "CNY"},
                           {"unit": "gram"}, {"price_kind": "indicative"}):
                bad = copy.deepcopy(snapshot)
                bad["evidence"][0].update(change)
                seal(bad)
                self.assertEqual(assess_proposal(proposal("JEPI"), bad, now=NOW)["action"], "data_insufficient", change)
```

Delete lines 68-77 (`test_research_record_cannot_confirm_dynamic_etf_identity`) entirely.

Note the dropped `{"asset_class": "stock"}` mutation: with the registry change, an evidence record claiming `stock` for a registered ETF is still blocked, but by `_validate_source` at snapshot-collection time, not by the policy; `test_registered_etf_cannot_be_reclassified_as_stock` (lines 62-66, unchanged) keeps the policy-level guard covered.

- [ ] **Step 8: Rename the market-data test whose name describes the deleted path**

In `scripts/_test_market_data.py` line 93 rename `test_dynamic_etf_identity_routes_later_providers_as_etf` to `test_registered_etf_is_confirmed_by_provider_metadata`. Body unchanged (JEPI is now registered; `market_data.py:271-275` still upgrades `identity_status` to `provider_confirmed`).

- [ ] **Step 9: Run the full suite**

```bash
python -m unittest discover -s scripts -p "_test_*.py" 2>&1 | tail -3
```

Expected: `Ran 125 tests` ... `OK (skipped=2)` (120 − 1 deleted + 6 new registry tests).

- [ ] **Step 10: Commit**

```bash
git add scripts/copilot/instruments.py scripts/copilot/policy.py scripts/_test_instruments.py scripts/_test_policy.py scripts/_test_market_data.py
git commit -m "feat(core): registry is an explicit ETF whitelist; drop provisional stock identity

Unknown US tickers are rejected at normalize_instrument. QQQI/JEPQ/JEPI/IOO
join the registry so Plan 3 can proxy-backtest them. The dynamic-ETF
confirmation branch in policy.py is deleted with its tests."
```

---

### Task 4: Private TOML config module

**Files:**
- Test: `scripts/_test_config.py` (new)
- Create: `scripts/copilot/config.py`, `config/user.example.toml`
- Modify: `.gitignore` (after line 23), `scripts/check.py` (private-state list), `scripts/package_release.py:59-90, 108-120, 184-196, 303-360`, `.github/workflows/ci.yml:192-198`, `scripts/copilot_cli.py`

- [ ] **Step 1: Write the failing config tests**

Create `scripts/_test_config.py`:

```python
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
investable_total_usd = 0
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
            loaded.etf.universe = ()  # frozen

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
        twelve_plus = ", ".join(f'"{s}"' for s in ("QQQ", "SPY", "VEA", "VWO", "IWM", "VTI", "VUG", "VTV", "XLK", "XLV", "XLF", "XLE", "XLY"))
        with self.assertRaisesRegex(ValueError, "etf.universe.*at most 12"):
            cfg.load_config(self.write(VALID.replace('["QQQ", "SPY", "VEA", "VWO", "IWM"]', f"[{twelve_plus}]")))

    def test_percentages_and_amounts_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "etf.min_cash_reserve_pct"):
            cfg.load_config(self.write(VALID.replace("min_cash_reserve_pct = 0.15", "min_cash_reserve_pct = 1.5")))
        with self.assertRaisesRegex(ValueError, "gold.order_increment_cny"):
            cfg.load_config(self.write(VALID.replace("order_increment_cny = 200", "order_increment_cny = 0")))
        with self.assertRaisesRegex(ValueError, "notify.scan_time_local"):
            cfg.load_config(self.write(VALID.replace('scan_time_local = "07:00"', 'scan_time_local = "7am"')))

    def test_unknown_schema_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "schema_version"):
            cfg.load_config(self.write(VALID.replace("schema_version = 1", "schema_version = 2")))

    def test_shipped_example_is_valid(self):
        loaded = cfg.load_config(ROOT / "config" / "user.example.toml")
        self.assertTrue(loaded.present)
        self.assertGreaterEqual(len(loaded.etf.universe), 8)

    def test_secrets_are_not_config_fields(self):
        leaked = VALID + '\n[smtp]\npassword = "x"\n'
        with self.assertRaisesRegex(ValueError, "unknown section 'smtp'"):
            cfg.load_config(self.write(leaked))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to see it fail**

```bash
python -m unittest scripts._test_config 2>&1 | tail -3
```

Expected: `ModuleNotFoundError: No module named 'copilot.config'`.

- [ ] **Step 3: Create `scripts/copilot/config.py`**

```python
"""User configuration: adopted parameters, never secrets.

Secrets stay in `.env` (see service.KEY_NAMES). This file holds the values a
user *decides* — universe, capital, comfort constraints, notification cadence —
so they are reviewable, versionable locally, and excluded from the release.
TOML via stdlib `tomllib` keeps the offline CI job dependency-free.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .instruments import DEFENSIVE_ETFS, ETF_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "config" / "user.toml"
SCHEMA_VERSION = 1
_SECTIONS = ("etf", "gold", "notify")
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class EtfConfig:
    universe: tuple[str, ...] = ()
    investable_total_usd: float = 0.0
    min_cash_reserve_pct: float = 0.15
    max_drawdown_pct: float = 0.20
    adopted_rule_id: str = ""


@dataclass(frozen=True)
class GoldConfig:
    investable_total_cny: float = 0.0
    min_order_cny: int = 1200
    order_increment_cny: int = 200
    max_orders_per_day: int = 10


@dataclass(frozen=True)
class NotifyConfig:
    email_to: str = ""
    timezone: str = "Australia/Sydney"
    scan_time_local: str = "07:00"
    language: str = "zh"


@dataclass(frozen=True)
class Config:
    present: bool
    path: str
    etf: EtfConfig
    gold: GoldConfig
    notify: NotifyConfig


def _number(section: dict, key: str, prefix: str, *, low: float, high: float, integer: bool = False) -> float:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{prefix}.{key} must be a number")
    if integer and int(value) != value:
        raise ValueError(f"{prefix}.{key} must be an integer")
    if not (low <= value <= high):
        raise ValueError(f"{prefix}.{key} must be between {low} and {high}, got {value}")
    return int(value) if integer else float(value)


def _string(section: dict, key: str, prefix: str, *, pattern: re.Pattern | None = None, choices: tuple[str, ...] | None = None) -> str:
    value = section.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{prefix}.{key} must be a string")
    if pattern and not pattern.match(value):
        raise ValueError(f"{prefix}.{key} must match {pattern.pattern}, got {value!r}")
    if choices and value not in choices:
        raise ValueError(f"{prefix}.{key} must be one of {choices}, got {value!r}")
    return value


def _etf(section: dict) -> EtfConfig:
    universe = section.get("universe", [])
    if not isinstance(universe, list) or not all(isinstance(s, str) for s in universe):
        raise ValueError("etf.universe must be a list of ticker strings")
    symbols = tuple(s.upper() for s in universe)
    if len(symbols) > 12:
        raise ValueError(f"etf.universe may hold at most 12 symbols, got {len(symbols)}")
    if len(set(symbols)) != len(symbols):
        raise ValueError("etf.universe contains duplicates")
    for symbol in symbols:
        if symbol not in ETF_REGISTRY:
            raise ValueError(f"etf.universe: {symbol} is not in the ETF registry")
        if symbol in DEFENSIVE_ETFS:
            raise ValueError(f"etf.universe: {symbol} is a defensive (bond/gold) ETF; the ETF sleeve holds equity ETFs and cash only")
    return EtfConfig(
        universe=symbols,
        investable_total_usd=_number(section, "investable_total_usd", "etf", low=0, high=1e9),
        min_cash_reserve_pct=_number(section, "min_cash_reserve_pct", "etf", low=0, high=0.9),
        max_drawdown_pct=_number(section, "max_drawdown_pct", "etf", low=0.01, high=0.9),
        adopted_rule_id=_string(section, "adopted_rule_id", "etf"),
    )


def _gold(section: dict) -> GoldConfig:
    return GoldConfig(
        investable_total_cny=_number(section, "investable_total_cny", "gold", low=0, high=1e9),
        min_order_cny=_number(section, "min_order_cny", "gold", low=1, high=1e7, integer=True),
        order_increment_cny=_number(section, "order_increment_cny", "gold", low=1, high=1e6, integer=True),
        max_orders_per_day=_number(section, "max_orders_per_day", "gold", low=1, high=100, integer=True),
    )


def _notify(section: dict) -> NotifyConfig:
    return NotifyConfig(
        email_to=_string(section, "email_to", "notify"),
        timezone=_string(section, "notify" and "timezone", "notify"),
        scan_time_local=_string(section, "scan_time_local", "notify", pattern=_TIME_RE),
        language=_string(section, "language", "notify", choices=("zh", "en")),
    )


def load_config(path: str | Path | None = None) -> Config:
    """Return validated config. A missing file is not an error: defaults, `present=False`."""
    target = Path(path) if path else DEFAULT_PATH
    if not target.is_file():
        return Config(present=False, path=str(target), etf=EtfConfig(), gold=GoldConfig(), notify=NotifyConfig())
    with target.open("rb") as handle:
        raw = tomllib.load(handle)
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}, got {raw.get('schema_version')!r}")
    for key in raw:
        if key != "schema_version" and key not in _SECTIONS:
            raise ValueError(f"unknown section '{key}' — secrets belong in .env, not config")
    for name in _SECTIONS:
        if not isinstance(raw.get(name), dict):
            raise ValueError(f"missing [{name}] section")
    return Config(present=True, path=str(target), etf=_etf(raw["etf"]), gold=_gold(raw["gold"]), notify=_notify(raw["notify"]))


def as_dict(loaded: Config) -> dict:
    from dataclasses import asdict
    return asdict(loaded)
```

Fix the one deliberate typo before running: in `_notify`, the `timezone` line must read `timezone=_string(section, "timezone", "notify"),` (the `"notify" and` fragment above is wrong — remove it).

- [ ] **Step 4: Create `config/user.example.toml`**

```toml
# Copy to config/user.toml (gitignored) and edit. Secrets go in .env, never here.
schema_version = 1

[etf]
# 8–12 equity ETFs from the registry in scripts/copilot/instruments.py.
# Bond and gold ETFs are rejected: the ETF sleeve holds equity ETFs and cash only.
universe = ["SPY", "QQQ", "IWM", "VUG", "VTV", "VEA", "VWO", "XLK", "XLV", "XLF"]
# Replaced by the IBKR Flex sync once Plan 2 lands; until then set it by hand.
investable_total_usd = 0
# Structural cash reserve (Q45) and comfort constraint (Q13). Placeholders until adopted.
min_cash_reserve_pct = 0.15
max_drawdown_pct = 0.20
# Set by the proposal flow in Plan 4 when you adopt a rule set. Empty = nothing adopted.
adopted_rule_id = ""

[gold]
investable_total_cny = 0
# Bank of China 积存金 rules verified 2026-09-19: minimum 1200 CNY, top-ups in 200 CNY steps,
# at most 10 buy+sell orders per day on electronic channels.
min_order_cny = 1200
order_increment_cny = 200
max_orders_per_day = 10

[notify]
email_to = ""
timezone = "Australia/Sydney"
# Daily scan after the US close (about 07:00 AEST during US daylight time).
scan_time_local = "07:00"
language = "zh"
```

- [ ] **Step 5: Run the config tests**

```bash
python -m unittest scripts._test_config 2>&1 | tail -3
```

Expected: `Ran 8 tests` ... `OK`.

- [ ] **Step 6: Gitignore the private file and teach the release/leak gates about it**

`.gitignore` — insert after line 23 (`docs/strategy-checklist.md`):

```
# Personal adopted parameters (universe, capital, constraints). The example ships; this does not.
config/user.toml
```

`scripts/check.py` — in `check_private_state_not_tracked`, add `"config/user.toml",` to the `git ls-files` argument list immediately after `"docs/strategy.md",`.

`scripts/package_release.py`:
- `INCLUDE_PATHS` (line 59-90): add `"config/user.example.toml",` after `".env.example",`.
- `EXCLUDE_PATTERNS` (line 108-120): add `"config/user.toml",` after `"docs/strategy.md", "docs/strategy-checklist.md",`.
- `_forbidden_archive_name` (line 196): extend the `low.endswith((...))` tuple with `"/config/user.toml"`.
- `_self_test` cases: change line 341 from `not _excluded("scripts/montecarlo.py")` to `not _excluded("scripts/copilot_cli.py")` (montecarlo is deleted in Task 5); change line 339-340 to `not _excluded(".claude/skills/investment-chat/SKILL.md")` (the deep skill is gone); add after line 335:

```python
        ("private config excluded", _excluded("config/user.toml")),
        ("example config kept", not _excluded("config/user.example.toml")),
        ("archive leak: private config member",
            _forbidden_archive_name("trading-copilot/config/user.toml")),
```

`.github/workflows/ci.yml` — in the leak list (line 192-198) add `or "config/user.toml" in n` after `or "/data/state/" in n`.

- [ ] **Step 7: Add the `config` CLI verb**

In `scripts/copilot_cli.py`, after `backup.add_argument("destination")` add:

```python
    show = sub.add_parser("config", help="print validated config/user.toml as JSON")
    show.add_argument("--path", default=None)
```

and in the dispatch chain, before the final `else:` add:

```python
        elif args.command == "config":
            from copilot.config import as_dict, load_config
            result = as_dict(load_config(args.path))
```

- [ ] **Step 8: Verify the gates**

```bash
python scripts/package_release.py --self-test 2>&1 | tail -2
python scripts/copilot_cli.py config --path config/user.example.toml | head -c 200; echo
python -m unittest discover -s scripts -p "_test_*.py" 2>&1 | tail -3
git check-ignore -v config/user.toml
```

Expected: self-test `37/37` pass; the CLI prints JSON starting `{"etf": {"adopted_rule_id": "", ...`; suite `Ran 133 tests ... OK (skipped=2)`; `check-ignore` prints `.gitignore:25:config/user.toml	config/user.toml`.

- [ ] **Step 9: Commit**

```bash
git add scripts/copilot/config.py scripts/_test_config.py config/user.example.toml .gitignore scripts/check.py scripts/package_release.py .github/workflows/ci.yml scripts/copilot_cli.py
git commit -m "feat(config): private TOML user config with registry-validated ETF universe

config/user.toml holds adopted parameters, never secrets; the example ships,
the real file is gitignored and excluded from release archives."
```

---

### Task 5: Delete legacy scripts and their CI/permission references

**Files:**
- Delete: `scripts/assemble_report.py`, `validate_outputs.py`, `validate_pm_output.py`, `trigger_state.py`, `memory.py`, `portfolio_check.py`, `benchmarks.py`, `montecarlo.py`, `polymarket_odds.py`, `prices.py`, `monitor.ps1`, `monitor-wt.ps1`, `_test_memory.py`, `_test_validation.py`
- Create: `scripts/_test_ticker_rating.py`
- Modify: `.claude/settings.json:14-23`, `.github/workflows/ci.yml:74-77, 175-184`

- [ ] **Step 1: Preserve the two surviving modules' tests before deleting the file that holds them**

Create `scripts/_test_ticker_rating.py`:

```python
"""Contracts for scripts/ticker.py and scripts/parse_rating.py (still used by evals/)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_rating import explicit_rating, parse_rating
from ticker import validate_date_component, validate_ticker_component


class TickerContracts(unittest.TestCase):
    def test_safe_tickers_round_trip(self):
        for ticker in ("NVDA", "BRK-B", "0700.HK", "BHP.AX", "GC=F", "XAUUSD=X", "^GSPC"):
            self.assertEqual(validate_ticker_component(ticker), ticker)

    def test_unsafe_tickers_fail(self):
        for bad in ("", ".", "..", "../NVDA", "NV DA", "AAPL\x00", "CON", "NUL.T"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                validate_ticker_component(bad)

    def test_dates_are_strict(self):
        for good in ("2026-01-05", "2026-12-31", "2024-02-29"):
            self.assertEqual(validate_date_component(good), good)
        for bad in ("", "2026-1-5", "20260105", "2026-13-01", "2026-02-30", "../../etc/passwd",
                    "2026-01-05/..", "2026-01-05 ", "2026-01-05T00:00:00", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                validate_date_component(bad)  # type: ignore[arg-type]


class RatingContracts(unittest.TestCase):
    def test_header_beats_decoy_lines(self):
        card = ("**结论卡**\n- 现在做什么: 减半, rating: Buy 只是卡片措辞\n| rating: Buy | 表格诱饵 |\n\n"
                "**Rating**: Underweight\n\n**Executive Summary**: 降配.\n")
        self.assertEqual(parse_rating(card), "Underweight")

    def test_fullwidth_colon_accepted(self):
        self.assertEqual(parse_rating("**Rating**：Sell\n\n**Executive Summary**: x\n"), "Sell")

    def test_unsupported_scale_rejected_and_reduce_maps_to_hold(self):
        for unsupported in ("Reduce", "Avoid", "Strong Buy", "Buy or Sell"):
            with self.assertRaises(ValueError, msg=unsupported):
                explicit_rating(f"**Rating**: {unsupported}\nDo not Buy.\n")
        self.assertEqual(parse_rating("**Rating**: Reduce\nDo not Buy"), "Hold")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it (green — the modules still exist)**

```bash
python -m unittest scripts._test_ticker_rating 2>&1 | tail -3
```

Expected: `Ran 6 tests` ... `OK`.

- [ ] **Step 3: Delete the legacy scripts**

```bash
git rm -q scripts/assemble_report.py scripts/validate_outputs.py scripts/validate_pm_output.py scripts/trigger_state.py scripts/memory.py scripts/portfolio_check.py scripts/benchmarks.py scripts/montecarlo.py scripts/polymarket_odds.py scripts/prices.py scripts/monitor.ps1 scripts/monitor-wt.ps1 scripts/_test_memory.py scripts/_test_validation.py
```

- [ ] **Step 4: Confirm no surviving Python imports them**

```bash
grep -rnE "^\s*(from|import)\s+(assemble_report|validate_outputs|validate_pm_output|trigger_state|memory|portfolio_check|benchmarks|montecarlo|polymarket_odds|prices)\b" scripts mcps evals --include=*.py
```

Expected: no output. (`evals/` imports only `parse_rating` and `ticker`, which stay.)

- [ ] **Step 5: Prune `.claude/settings.json` Bash allow rules**

Replace lines 14-23 with exactly these three entries (keep `check.py` and `mcp_handshake.py`; drop the rest):

```json
      "Bash(python scripts/copilot_cli.py*)",
      "Bash(python scripts/check.py*)",
      "Bash(python scripts/mcp_handshake.py*)",
```

The surrounding lines (`"Write(/docs/samples/**)",` above and `"Bash(git status)",` below) are unchanged.

- [ ] **Step 6: Fix `.github/workflows/ci.yml`**

Replace lines 74-77:

```yaml
      - name: Integration test suites
        run: |
          python scripts/_test_memory.py
          python scripts/_test_validation.py
```

with:

```yaml
      - name: Integration test suites
        run: |
          python scripts/_test_ticker_rating.py
          python scripts/_test_config.py
```

Replace the `required = [...]` list (lines 175-184) with:

```python
          required = [
              "scripts/ticker.py", "scripts/parse_rating.py", "scripts/notify.py",
              "scripts/copilot_cli.py", "scripts/copilot/config.py",
              "evals/scorer.py", "evals/stockbench/backtest_engine.py",
              "mcps/finnhub_mcp.py", "mcps/akshare_mcp.py",
              "mcps/copilot_mcp.py", "scripts/copilot/service.py",
              ".codex/config.toml", ".codex-plugin/plugin.json", "AGENTS.md",
              ".claude-plugin/plugin.json", "README.md", "DISCLAIMER.md",
              "config/user.example.toml",
          ]
```

- [ ] **Step 7: Run every gate**

```bash
python scripts/check.py 2>&1 | tail -6
python -m unittest discover -s scripts -p "_test_*.py" 2>&1 | tail -3
python scripts/package_release.py --self-test 2>&1 | tail -1
python scripts/notify.py --self-test 2>&1 | tail -1
python scripts/parse_rating.py --self-test 2>&1 | tail -1
```

Expected: check.py still FAILs only on the four doc references (fixed next task); suite `Ran 133 tests ... OK (skipped=2)` (the 6 new tests replace nothing the discover run counted — `_test_validation.py` and `_test_memory.py` were plain-function files not collected by `unittest discover`); self-tests all pass.

- [ ] **Step 8: Commit**

```bash
git add -A scripts .claude/settings.json .github/workflows/ci.yml
git commit -m "chore: delete legacy pipeline and ASX scripts; keep ticker/parse_rating for evals"
```

---

### Task 6: ADR-0004 and documentation scope updates

**Files:**
- Create: `docs/adr/0004-engine-computes-model-explains.md`
- Modify: `CLAUDE.md:9-11, 20`, `README.md:49`, `README_zh.md:26`, `AGENTS.md:9-11`, `docs/methodology.md:1`, `.claude/skills/investment-chat/SKILL.md:3, 14-17`, `.claude/commands/scan.md:8`

- [ ] **Step 1: Write ADR-0004**

Create `docs/adr/0004-engine-computes-model-explains.md`:

```markdown
# ADR-0004: The policy engine computes every order figure; the language model only explains

Status: accepted, 2026-09-19.

## Context

Until 0.4.0 the plugin ran a fourteen-agent research pipeline whose prose
was converted into a decision by a deterministic policy, and the concise
advisor refused to size positions because the journal never declares a
complete portfolio. Both paths left the user without a number, and the
multi-agent path produced text the policy could not verify.

The redesign (design session 2026-09-19) replaces discretionary analysis with
rules the user adopts: strategies with public code and ≥15-year reproducible
backtests are proposed with their statistics, the user accepts one, and from
then on a daily scan evaluates the adopted rule against a validated snapshot.

## Decision

1. `scripts/copilot/policy.py` is the only component that produces
   `action`, `quantity`, `limit_price` and `rule_id`. Every figure traces to a
   stored evidence record, an adopted rule in `config/user.toml`, or arithmetic
   over them.
2. The language model receives the engine's decision and renders it in the
   user's language. It has no interface through which it can alter quantity,
   price, direction or rule identity. Prompts, skills and MCP tool schemas must
   not expose such a parameter.
3. One bounded exception: a daily *brake* enum `{none, reduce_50, skip}` that
   the model may emit from the day's macro-calendar surprise and news
   headlines. The brake can only reduce or cancel an engine-generated buy; it
   cannot increase a quantity, change a price, or turn a skip into a buy. Every
   brake decision is journaled with its reason.
4. Rules enter the library only through the admission gate: backtest ≥15 years
   covering 2008, 2020 and 2022; annualised return, maximum drawdown, drawdown
   duration, turnover and Sharpe reported; transaction costs deducted; at most
   three parameters with sensitivity reported; an out-of-sample segment; code
   and data sources public and reproducible locally.
5. The ETF sleeve holds registered equity ETFs and cash; the gold sleeve is
   Shanghai Gold Exchange Au99.99 as signal with manually recorded Bank of China
   积存金 executions. The two sleeves are independent and are never summed.
6. Broker access is read-only: IBKR Flex Web Service for positions and cash;
   an optional IB Gateway session with the Read-Only API flag for a third price
   source and news. No component sends an order.

## Consequences

- The multi-agent pipeline, its agents, commands, validators and report
  assembler are removed (Plan 1). `docs/methodology.md` is historical.
- Position sizing becomes computable once `config/user.toml` and the Flex sync
  supply the denominator; until then the engine reports `research_only`.
- Users who replace the model do not change trading behaviour, because the
  model never held it.
- Every emitted instruction carries a `rule_id` and the backtest statistics
  under which that rule was adopted, so the user can audit why an order exists.
```

- [ ] **Step 2: Update `CLAUDE.md`**

Replace lines 9-11 (the paragraph beginning `Default: one advisor` through `An assessed, committed Decision is required before final assembly.`) with:

```markdown
Default: one advisor, concise Chinese in the conversation, evidence and operations saved in the background. Supported research: registered US ETFs (see scripts/copilot/instruments.py), ^NDX, ^IXIC as benchmarks, and GOLD.CNY meaning Shanghai Gold Exchange Au99.99 in CNY. Every recommendation consumes a newly collected shared snapshot and the current journal, then passes assess_investment_proposal. Show its assessed action and limits. A retrieved page or successful tool transport is not proof of valid data.

Order figures come only from the policy engine over adopted rules in config/user.toml; the model explains and may apply the bounded brake defined in [docs/adr/0004-engine-computes-model-explains.md](docs/adr/0004-engine-computes-model-explains.md). There is no deep multi-agent pipeline.
```

Replace line 20 (`- Tools listed in Claude agent frontmatter form an allowlist. ...`) with:

```markdown
- The plugin ships no agents. If one is added, its `tools:` frontmatter is an allowlist and must grant every MCP server the prompt calls.
```

Add `config/user.toml` to the `Private:` line (line 31) after `docs/strategy.md`.

- [ ] **Step 3: Update `README.md` line 49**

Replace:

```
Default /advise and /gold produce conversational answers. /analyze explicitly starts deep multi-agent research with versioned evidence and optional report assembly. Historical scripts and markdown reflections are compatibility tools, not the new transaction journal.
```

with:

```
Conversation and /gold produce concise answers from one validated snapshot. Order quantities and limit prices come from the policy engine over rules you adopt in config/user.toml (ADR-0004); nothing here promises a return. The deep multi-agent pipeline was removed in 0.5.0.
```

- [ ] **Step 4: Update `README_zh.md` line 26**

Replace `默认会话输出约 4–6 行，包含建议、依据、再观察条件、数据日期和来源。/analyze 仅在明确要求深入分析时启动；长报告按需生成。` with `默认会话输出约 4–6 行，包含建议、依据、再观察条件、数据日期和来源。买入数量与限价只来自策略引擎对你在 config/user.toml 中采纳的规则的计算（ADR-0004）；0.5.0 起不再有多 agent 深度流水线。`

- [ ] **Step 5: Update `AGENTS.md` lines 9-11**

Replace:

```
For repository work, read CLAUDE.md and relevant tests. Generated `.agents/skills/`
and `.codex/agents/` come from `.claude/` via `scripts/sync_runtimes.py`; edit their
source and run the generator. Shared business logic lives in `scripts/copilot/`.
```

with:

```
For repository work, read CLAUDE.md and relevant tests. Generated `.agents/skills/`
comes from `.claude/skills/` via `scripts/sync_runtimes.py`; edit the source and run
the generator. Shared business logic lives in `scripts/copilot/`. There are no agents.
```

- [ ] **Step 6: Mark `docs/methodology.md` historical**

Insert at the very top of the file, before the existing first line:

```markdown
> **Historical (superseded 2026-09-19).** This document describes the multi-agent
> pipeline removed in 0.5.0. Current architecture: [ADR-0004](adr/0004-engine-computes-model-explains.md).
> Plan 7 replaces this file.

```

- [ ] **Step 7: Narrow the skill and command scope text**

`.claude/skills/investment-chat/SKILL.md` line 3 — replace the description with:

```
description: Give concise evidence-backed investment guidance in conversation for registered US ETFs, Nasdaq indexes, and Chinese RMB investment gold; record user-reported actual purchases/sales and retrieve holdings. Use for investment conversations or transaction updates, not repository development.
```

Lines 14-17 — replace step 1 with:

```
1. Resolve the instrument: a registered US ETF (the whitelist in
   scripts/copilot/instruments.py — unknown tickers are rejected, never
   provisionally accepted); `^NDX` Nasdaq-100 and `^IXIC` Composite as
   benchmarks; `GOLD.CNY` Shanghai Gold Exchange Au99.99 in RMB. Ask when an
   ambiguity affects the action. Preserve index versus tradable ETF identities.
```

`.claude/commands/scan.md` line 8 — replace `Resolve only supported US stocks/ETFs, ^NDX, ^IXIC and GOLD.CNY/SGE.SHAU;` with `Resolve only registered US ETFs, ^NDX, ^IXIC and GOLD.CNY/SGE.SHAU;`.

- [ ] **Step 8: Regenerate mirrors and run every gate**

```bash
python scripts/sync_runtimes.py
python scripts/sync_runtimes.py --check
python scripts/check.py
python -m unittest discover -s scripts -p "_test_*.py" 2>&1 | tail -3
python scripts/package_release.py --self-test 2>&1 | tail -1
```

Expected: sync `Generated runtime files; 2 changed.` then `Checked runtime files; 0 changed.`; **`OK - N file(s) checked, 0 errors, 0 warning(s).`** (N ≈ 45); suite `OK (skipped=2)`; self-test pass.

- [ ] **Step 9: Commit**

```bash
git add docs/adr/0004-engine-computes-model-explains.md CLAUDE.md README.md README_zh.md AGENTS.md docs/methodology.md .claude/skills/investment-chat/SKILL.md .claude/commands/scan.md .agents/skills skills
git commit -m "docs: ADR-0004 engine-computes-model-explains; scope docs to ETF whitelist + gold"
```

---

### Task 7: Final verification and handoff

**Files:** none modified.

- [ ] **Step 1: Run the complete gate set from a clean state**

```bash
git status --short
python scripts/check.py
python scripts/sync_runtimes.py --check
python -m unittest discover -s scripts -p "_test_*.py"
python scripts/package_release.py --self-test
python scripts/mcp_handshake.py --server trading-copilot
```

Expected: empty `git status`; `OK ... 0 errors`; `0 changed`; `OK (skipped=2)`; self-test pass; handshake verdict `ok`.

- [ ] **Step 2: Smoke the surviving conversation path against a temporary journal**

```bash
python scripts/copilot_cli.py --db "%TEMP%\plan1-smoke.sqlite" context
python scripts/copilot_cli.py config --path config/user.example.toml
python - <<'EOF'
import sys; sys.path.insert(0, "scripts")
from copilot.instruments import normalize_instrument
for s in ("QQQ", "QQQI", "GOLD.CNY", "^NDX"):
    print(s, "->", normalize_instrument(s))
for s in ("AAPL", "NDQ.AX"):
    try: normalize_instrument(s); print("UNEXPECTED ACCEPT", s)
    except ValueError as e: print(s, "-> rejected:", str(e)[:60])
EOF
```

Expected: `context` prints JSON with `"holdings": []`; `config` prints the example as JSON; the four symbols echo themselves; `AAPL` and `NDQ.AX` print `rejected:`.

- [ ] **Step 3: Report to the user**

State: the backup zip path from Task 0; the seven commits; the final gate outputs; and that Plan 2 (data providers: IBKR Flex, IB Gateway, Finnhub economic calendar, FRED gold proxy, macro-calendar skill) is next.

---

## Self-review

**Spec coverage.** Q18 delete list → Task 1 (commands/agents/skills) and Task 5 (scripts). Q2/Q17 US-only → Task 3 regex `[A-Z]{1,6}` plus registry. Q37 proxy symbols resolvable → Task 3 registry includes QQQI/JEPQ/JEPI. Q46 no defensive ETFs in the sleeve → Task 4 config rejects `DEFENSIVE_ETFS`. Q21 public/private split → Task 4 gitignore + release exclusion + example. Q30 → Task 6 ADR-0004. Q44 delete with backup → Task 0. Version bump → Task 1. `catalyst-calendar` deletion → Task 1 (replacement lands with its provider in Plan 2). Gaps deliberately deferred are listed in the header.

**Placeholder scan.** Every code step shows full code; every command shows expected output. The one intentional fix-up note in Task 4 Step 3 (`timezone` line) is explicit.

**Type consistency.** `ETF_REGISTRY`, `DEFENSIVE_ETFS` (Task 3) are imported by `config.py` (Task 4) under the same names. `load_config`/`as_dict` (Task 4) are the names the CLI verb calls. `check_agents`, `check_commands`, `check_docs_and_workflows` keep their names so `main()` needs no edit. Test counts: 121 → 120 (Task 2) → 125 (Task 3) → 133 (Task 4) → 133 (Task 5; the two deleted files were never collected by discover) — the CI `Integration test suites` step now runs the two new unittest files standalone.
