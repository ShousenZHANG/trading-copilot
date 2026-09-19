# Plan 2 — Credential and Provider Pipeline Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the credential-loading and URL-sanitising pipeline that every future external data source depends on, and record — with evidence — the three scope decisions that external verification forced on 2026-09-19.

**Architecture:** No new data source ships in this plan. Four defects are fixed in place: the Finnhub MCP server reads its key at import time from the host environment and never loads `.env`, so every one of its tools currently returns HTTP 401; `load_credentials` cannot fill in a variable the client injected as an empty string, which is exactly how that happens; `safe_url` redacts by substring-matching the parameter *name*, so a single-character secret parameter such as IBKR Flex's `?t=` would be written into evidence and cached to disk; and `research_data._redact` keeps a second hardcoded list of credential names that has already drifted from `service.KEY_NAMES`. ADR-0005 then records what verification proved impossible and amends the two clauses of ADR-0004 that promised it.

**Tech Stack:** Python 3.11 stdlib, existing PEP 723 script headers, `unittest`, `scripts/check.py`, `scripts/sync_runtimes.py`.

---

## Why this plan is smaller than originally designed

A verification workflow on 2026-09-19 fetched the primary sources for all four subsystems Plan 2 was going to build. Three cannot be built as designed. The user's decisions, taken on that evidence:

| Subsystem | Finding | Decision |
|---|---|---|
| Macro surprise signal | Finnhub `/calendar/economic` returns **HTTP 403** on this repo's key while `/quote` returns 200 — an entitlement refusal, not an auth failure. Trading Economics' guest tier returns **HTTP 410, discontinued**. The one free feed carrying a forecast (`nfs.faireconomy.media/ff_calendar_thisweek.json`) has **no `actual` key at all**, only a single rolling week, and `lastweek`/`nextweek` both 404. Finnhub's own docs say historical surprises are Enterprise-only, so even paying likely would not permit a backtest. | **Cut.** No macro leg in this plan or the brake. |
| Gold ≥15-year history | Both LBMA FRED series were **deleted** (not discontinued) on 2022-01-31; the series pages 301-redirect to the removal announcement and the CSV endpoints 404. FRED search for `LBMA` returns **0 series**. Every surviving FRED gold series is an *index*, not a price level. LBMA's own JSON works but LBMA states an IBA licence is required to obtain or use historical benchmark data. | **Defer.** Gold sleeve is out of this plan; SGE stays the day-to-day signal. |
| IB Gateway third price source | IBKR: "the API always requires Level 1 streaming real time data to return historical data" — `reqMarketDataType(3)` does **not** unlock it. `ib_async 2.1.0` requires `tzdata>=2025.2,<2026.0` while all three PEP 723 headers in this repo pin `tzdata==2026.3`; that is a resolution failure, not a degraded mode. | **Dropped.** Prices stay on the Yahoo + Nasdaq two-source cross-check. |
| IBKR Flex client | IBKR publishes no XSD. Every XML attribute spelling comes from one third-party library and a single 2011 fixture; the attribute set is defined by the user's own Client Portal template, not by the protocol; errors arrive as **HTTP 200**; and the retry path cannot be exercised without a live token. | **Waits for a real token.** The pipeline fixes it needs ship here; the client does not. |

Only the pipeline work, which is a prerequisite for all of the above and is broken today regardless, is in scope.

**Out of scope, owned by later plans:** the backtest engine and rule library (Plan 3), the policy output shape `{action, quantity, limit_price, rule_id}` and the context augmentation that unblocks position sizing (Plan 4), SMTP notification and the scheduled runner (Plan 5), the gold sleeve and manual execution prices (Plan 6), README/INSTALL rewrite and release (Plan 7).

---

## File structure

**Create:**
- `docs/adr/0005-verified-data-constraints.md` — the decisions above, with their evidence
- `scripts/_test_credentials.py` — loader and redaction contracts

**Modify:**
- `scripts/copilot/service.py` — `KEY_NAMES`, `load_credentials` empty-value bug, a public `secret_values()` helper
- `scripts/copilot/providers.py` — `safe_url` hardening, named throttle table
- `scripts/copilot/research_data.py` — `_redact` derives its name list from `KEY_NAMES`
- `mcps/finnhub_mcp.py` — lazy, `.env`-aware key loading
- `scripts/_test_market_data.py` — `safe_url` contract cases
- `docs/adr/0004-engine-computes-model-explains.md` — amendment pointer only
- `.env.example`, `docs/INSTALL.md` — credential documentation

---

## Baseline (run before Task 1 and after every task)

```bash
python scripts/check.py
python scripts/sync_runtimes.py --check
python -m unittest discover -s scripts -p "_test_*.py"
python scripts/package_release.py --self-test
```

Expected at start: `OK - 18 file(s) checked, 0 errors, 0 warning(s).` / `Checked runtime files; 0 changed.` / `Ran 162 tests ... OK (skipped=2)` / `37/37`.

---

### Task 1: ADR-0005, and amend ADR-0004

**Files:**
- Create: `docs/adr/0005-verified-data-constraints.md`
- Modify: `docs/adr/0004-engine-computes-model-explains.md`

- [ ] **Step 1: Read the existing ADRs to match their form**

```bash
cat docs/adr/0003-shared-evidence-and-operation-journal.md
sed -n '1,20p' docs/adr/0004-engine-computes-model-explains.md
```

Note the convention: `# ADR-NNNN: Title`, then `Status: accepted, YYYY-MM-DD.`, then `## Context` / `## Decision` / `## Consequences`.

- [ ] **Step 2: Write ADR-0005**

Create `docs/adr/0005-verified-data-constraints.md`:

```markdown
# ADR-0005: Three data sources named in ADR-0004 do not exist on the terms it assumed

Status: accepted, 2026-09-19. Amends ADR-0004 clauses 3 and 6.

## Context

ADR-0004 was written before its external data sources were verified. A
verification pass on 2026-09-19 fetched the primary sources and found that
three of them cannot supply what the decision assumed.

**Macro surprise.** Clause 3 lets the model emit a brake derived from "the
day's macro-calendar surprise". A surprise needs an actual value and a market
consensus for the same release. Finnhub's economic calendar returns HTTP 403
for this repository's key while the same key returns 200 on `/quote`, and its
documentation marks the endpoint premium. Trading Economics' guest credential
returns HTTP 410, discontinued. The one free feed that carries a forecast
publishes a single rolling week with no `actual` field, and its previous- and
next-week URLs return 404, so the history cannot be backfilled. Finnhub's own
documentation states historical surprises are available to Enterprise clients,
so a paid tier is not known to permit a backtest either. A signal that cannot
be backtested cannot pass the admission gate in ADR-0004 clause 4.

**Gold history.** The rule admission gate requires at least fifteen years of
history. The Shanghai Gold Exchange adapter reaches about four. The obvious
extension, reconstructing a yuan gold price from a dollar benchmark and the
exchange rate, has no licence-clean source: both LBMA series were deleted from
FRED on 2022-01-31, FRED search returns no LBMA series at all, every surviving
FRED gold series is an index rather than a price level, and LBMA's own endpoint
carries a statement that an IBA licence is required to obtain or use historical
benchmark data. The exchange-rate leg alone is unaffected.

**IB Gateway.** Clause 6 offers an IB Gateway session as a third price source.
IBKR documents that the API requires a Level 1 streaming subscription to return
historical data and that delayed market data does not lift that requirement.
Separately, `ib_async` constrains `tzdata` below 2026.0 while every entry point
in this repository pins 2026.3, which makes the dependency unresolvable rather
than merely degraded.

## Decision

1. The macro-surprise input to the brake is removed. The brake keeps its
   `{none, reduce_50, skip}` shape and its one-way constraint, and is derived
   from news evidence alone. It remains outside the admission gate, is marked
   unbacktested wherever it appears, and can only reduce or cancel.
2. The gold sleeve is deferred. `GOLD.CNY` stays a conversational signal on the
   existing Shanghai Gold Exchange adapter. No gold rule enters the rule library
   until a source exists that is free, licence-clean, long enough for the
   admission gate, and expressed as a price level rather than an index.
3. IB Gateway is not integrated. Prices remain on the Yahoo and Nasdaq
   two-source cross-check. The IBKR Flex Web Service stays the intended
   account-data path; its client is not built until a live token allows its
   response shape to be verified, because IBKR publishes no schema and the
   attribute set is defined by the user's own query template.
4. The pipeline defects these sources exposed are fixed now, because they are
   live defects independent of any of them: see this plan's remaining tasks.

## Consequences

- The worked example that motivated the macro leg — a rate rise the market had
  already priced, so the release itself is not news — is not something this tool
  will detect automatically. That limitation is documented rather than papered
  over with a model's guess.
- Position sizing still waits on the account-data path, which now waits on a
  live Flex token rather than on code.
- Anyone reading ADR-0004 alone would plan against sources that are not
  reachable. It now carries a pointer here.
```

- [ ] **Step 3: Point ADR-0004 at the amendment**

`docs/adr/0004-engine-computes-model-explains.md` — change the status line from `Status: accepted, 2026-09-19.` to:

```markdown
Status: accepted, 2026-09-19. Clauses 3 and 6 amended by [ADR-0005](0005-verified-data-constraints.md).
```

Do not edit clauses 3 and 6 themselves — an accepted ADR records what was decided at the time; the amendment records what changed and why.

- [ ] **Step 4: Verify and commit**

```bash
python scripts/check.py
git add docs/adr/0005-verified-data-constraints.md docs/adr/0004-engine-computes-model-explains.md
git commit -m "docs: ADR-0005 records the verified data constraints amending ADR-0004"
```

Expected: check.py still `OK ... 0 errors`.

---

### Task 2: Make credential loading work when the client injects an empty variable

**Files:**
- Test: `scripts/_test_credentials.py` (new)
- Modify: `scripts/copilot/service.py:13-33`

The bug: `.mcp.json` declares `"env": {"FINNHUB_API_KEY": "${FINNHUB_API_KEY}"}`. The MCP client expands `${VAR}` from **its own** environment. If Claude Code was not started through `scripts/start.*`, that expands to an empty string and the variable is injected as `""`. `os.environ.setdefault` will not replace an existing empty value, so `.env` is ignored and every call 401s. The docstring's intent — "preserve explicit process environment overrides" — is right; an empty string is not an override.

- [ ] **Step 1: Write the failing test**

Create `scripts/_test_credentials.py`:

```python
"""Credential loading and redaction contracts. No real secret is ever written here."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from copilot import service

FIXTURE = "fixture-not-a-real-key"


class CredentialLoadingContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".env").write_text(
            f"FINNHUB_API_KEY={FIXTURE}\nFRED_API_KEY=fixture-fred\n"
            "# comment line\nNOT_A_KNOWN_NAME=should-be-ignored\n",
            encoding="utf-8")

    def load(self, environ: dict) -> dict:
        with patch.object(service, "ROOT", self.root), patch.dict(os.environ, environ, clear=True):
            service.load_credentials()
            return dict(os.environ)

    def test_loads_known_names_from_env_file(self):
        result = self.load({})
        self.assertEqual(result["FINNHUB_API_KEY"], FIXTURE)
        self.assertEqual(result["FRED_API_KEY"], "fixture-fred")

    def test_ignores_names_outside_the_allowlist(self):
        self.assertNotIn("NOT_A_KNOWN_NAME", self.load({}))

    def test_empty_injected_value_does_not_block_the_env_file(self):
        """An MCP client expanding ${VAR} against an empty host environment
        injects "", which must not win over a real value in .env."""
        self.assertEqual(self.load({"FINNHUB_API_KEY": ""})["FINNHUB_API_KEY"], FIXTURE)
        self.assertEqual(self.load({"FINNHUB_API_KEY": "   "})["FINNHUB_API_KEY"], FIXTURE)

    def test_real_process_override_still_wins(self):
        self.assertEqual(self.load({"FINNHUB_API_KEY": "explicit"})["FINNHUB_API_KEY"], "explicit")

    def test_missing_env_file_is_not_an_error(self):
        with patch.object(service, "ROOT", self.root / "nope"), patch.dict(os.environ, {}, clear=True):
            service.load_credentials()

    def test_secret_values_reports_live_credentials_only(self):
        with patch.object(service, "ROOT", self.root), patch.dict(os.environ, {}, clear=True):
            service.load_credentials()
            values = service.secret_values()
        self.assertIn(FIXTURE, values)
        self.assertIn("fixture-fred", values)
        self.assertNotIn("", values)
        self.assertNotIn("should-be-ignored", values)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to see it fail**

```bash
python -m unittest scripts._test_credentials 2>&1 | tail -8
```

Expected: `test_empty_injected_value_does_not_block_the_env_file` FAILS (`'' != 'fixture-not-a-real-key'`) and `test_secret_values_reports_live_credentials_only` errors with `AttributeError: module 'copilot.service' has no attribute 'secret_values'`.

- [ ] **Step 3: Fix `load_credentials` and add `secret_values`**

In `scripts/copilot/service.py`, replace the whole `load_credentials` function (lines 19-33) with:

```python
def load_credentials() -> None:
    """Load only the allow-listed provider settings from .env.

    A real process override still wins. An EMPTY one does not: an MCP client
    expanding `${VAR}` against a host environment that never sourced .env
    injects "", and `setdefault` would let that empty string mask the real
    value. Every Finnhub tool in this repository returned HTTP 401 for exactly
    that reason.
    """
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name in KEY_NAMES and value and not os.environ.get(name, "").strip():
            os.environ[name] = value


def secret_values() -> tuple[str, ...]:
    """Every live credential value, for redaction. Single source of truth.

    Callers that scrub text or URLs must use this rather than keeping their own
    list of variable names, which is how research_data._redact drifted from
    KEY_NAMES.
    """
    seen: list[str] = []
    for name in KEY_NAMES:
        value = os.environ.get(name, "").strip()
        if value and value not in seen:
            seen.append(value)
    return tuple(seen)
```

Also extend the `KEY_NAMES` comment so the extension point is explicit. Replace lines 13-16 with:

```python
# The allow-list of names load_credentials() will read out of .env. This is a
# deliberate boundary: an unknown name in .env is never promoted into the
# process environment. Adding a credential means adding its name here AND
# re-running scripts/sync_runtimes.py, because the generated Codex config
# forwards exactly this tuple.
KEY_NAMES = (
    "FINNHUB_API_KEY", "FRED_API_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY",
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "SEC_USER_AGENT",
)
```

- [ ] **Step 4: Run the tests**

```bash
python -m unittest scripts._test_credentials 2>&1 | tail -3
python -m unittest discover -s scripts -p "_test_*.py" 2>&1 | tail -3
```

Expected: `Ran 6 tests ... OK`, then `Ran 168 tests ... OK (skipped=2)`.

- [ ] **Step 5: Commit**

```bash
git add scripts/copilot/service.py scripts/_test_credentials.py
git commit -m "fix(credentials): an empty injected variable no longer masks .env

An MCP client expanding \${VAR} against a shell that never sourced .env
injects an empty string, and setdefault let it win. Adds secret_values() as
the single source of truth for redaction."
```

---

### Task 3: Harden `safe_url` against short secret parameter names

**Files:**
- Modify: `scripts/copilot/providers.py:38-42`
- Test: `scripts/_test_market_data.py` (add a contracts class)

Today `safe_url` drops a query parameter only when its *name contains* one of `key`, `token`, `secret`, `password`. IBKR Flex passes its token as `?t=` and its query id as `?q=`; neither matches, so the full token would be written into `source_url`, stored in evidence, and persisted by `_cache()` — whose comment already claims "Full URL has been sanitized".

The fix is two-layered so it does not depend on guessing future parameter names: drop known-secret parameters by exact name as well as by substring, then scrub any surviving occurrence of a live credential value.

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_market_data.py`, before the `if __name__ == "__main__":` line:

```python
class SafeUrlContracts(unittest.TestCase):
    def test_substring_named_secrets_are_dropped(self):
        cleaned = safe_url("https://example.test/v1?api_key=abc&token=xyz&symbol=QQQ")
        self.assertNotIn("abc", cleaned)
        self.assertNotIn("xyz", cleaned)
        self.assertIn("symbol=QQQ", cleaned)

    def test_short_secret_parameter_names_are_dropped(self):
        """IBKR Flex passes its token as ?t= — one character, no substring match."""
        cleaned = safe_url("https://example.test/FlexWebService/SendRequest?t=SECRETTOKEN&q=12345&v=3")
        self.assertNotIn("SECRETTOKEN", cleaned)
        self.assertIn("q=12345", cleaned)
        self.assertIn("v=3", cleaned)

    def test_live_credential_value_is_scrubbed_under_any_parameter_name(self):
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "live-fixture-value"}, clear=True):
            cleaned = safe_url("https://example.test/q?unexpected=live-fixture-value&symbol=QQQ")
        self.assertNotIn("live-fixture-value", cleaned)
        self.assertIn("symbol=QQQ", cleaned)

    def test_fragment_and_credentials_in_netloc_do_not_survive(self):
        self.assertNotIn("#", safe_url("https://example.test/p?a=1#secret-fragment"))

    def test_non_secret_urls_round_trip(self):
        url = "https://finance.yahoo.com/quote/QQQ/history/"
        self.assertEqual(safe_url(url), url)
```

Add whatever imports that class needs to the top of the file — check what is already imported before adding `os`, `patch`, or `safe_url`.

- [ ] **Step 2: Run it to see it fail**

```bash
python -m unittest scripts._test_market_data.SafeUrlContracts 2>&1 | tail -8
```

Expected: `test_short_secret_parameter_names_are_dropped` and `test_live_credential_value_is_scrubbed_under_any_parameter_name` FAIL. The other three should already pass.

- [ ] **Step 3: Rewrite `safe_url`**

In `scripts/copilot/providers.py`, replace lines 38-42 with:

```python
# Parameter names that carry a credential but contain none of the substrings
# below. IBKR's Flex Web Service passes its token as a single character, `t`.
_SECRET_PARAM_NAMES = frozenset({"t", "auth", "sig", "signature", "session", "sid", "pwd", "credential"})
_SECRET_PARAM_SUBSTRINGS = ("key", "token", "secret", "password")


def safe_url(url: str) -> str:
    """Drop credential-bearing query parameters, then scrub any live secret value.

    Two layers, because the first alone has failed: name matching cannot cover a
    parameter name nobody has seen yet, and this URL is written into evidence
    records and persisted by HttpClient._cache().
    """
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query)
             if key.lower() not in _SECRET_PARAM_NAMES
             and not any(secret in key.lower() for secret in _SECRET_PARAM_SUBSTRINGS)]
    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
    # Imported here: providers.py must stay importable without the service facade.
    from .service import secret_values
    for value in secret_values():
        cleaned = cleaned.replace(value, "[redacted]")
    return cleaned
```

- [ ] **Step 4: Check for an import cycle before running anything else**

```bash
python -c "import sys; sys.path.insert(0,'scripts'); import copilot.providers; print('providers ok')"
python -c "import sys; sys.path.insert(0,'scripts'); import copilot.service; print('service ok')"
python -c "import sys; sys.path.insert(0,'scripts'); from copilot.service import collect; print('facade ok')"
```

All three must print. `service.py` imports its own modules lazily inside functions, so the function-level import above is safe — but verify rather than assume, and if a cycle appears, report it instead of working around it silently.

- [ ] **Step 5: Run the tests**

```bash
python -m unittest scripts._test_market_data 2>&1 | tail -3
python -m unittest discover -s scripts -p "_test_*.py" 2>&1 | tail -3
```

Expected: the market-data file passes, and the full suite reports `Ran 173 tests ... OK (skipped=2)`.

- [ ] **Step 6: Commit**

```bash
git add scripts/copilot/providers.py scripts/_test_market_data.py
git commit -m "fix(providers): safe_url drops short secret parameters and scrubs live values

Name-substring matching missed a one-character parameter such as IBKR Flex's
?t=, and that URL is written into evidence and persisted by _cache()."
```

---

### Task 4: Make redaction derive from one list

**Files:**
- Modify: `scripts/copilot/research_data.py:53-64`
- Test: `scripts/_test_credentials.py`

`_redact` keeps its own hardcoded tuple of six variable names. `service.KEY_NAMES` has seven. The two have already drifted: `SEC_USER_AGENT` is loaded but never redacted. A future credential added to one and not the other produces a silent leak.

- [ ] **Step 1: Add the failing test**

Append to `scripts/_test_credentials.py`, before the `if __name__ == "__main__":` line:

```python
class RedactionContracts(unittest.TestCase):
    def test_every_loaded_credential_is_redactable(self):
        """research_data._redact must cover exactly what service loads."""
        from copilot import research_data
        environ = {name: f"value-of-{name}" for name in service.KEY_NAMES}
        with patch.dict(os.environ, environ, clear=True):
            text = " ".join(environ.values())
            cleaned = research_data._redact(text)
        for name in service.KEY_NAMES:
            self.assertNotIn(f"value-of-{name}", cleaned, name)

    def test_redaction_walks_nested_structures(self):
        from copilot import research_data
        with patch.dict(os.environ, {"FRED_API_KEY": "fixture-fred"}, clear=True):
            cleaned = research_data._redact({"a": ["fixture-fred"], "b": {"c": "x-fixture-fred-y"}})
        self.assertEqual(cleaned, {"a": ["[redacted]"], "b": {"c": "x-[redacted]-y"}})
```

- [ ] **Step 2: Run it to see it fail**

```bash
python -m unittest scripts._test_credentials.RedactionContracts 2>&1 | tail -6
```

Expected: `test_every_loaded_credential_is_redactable` FAILS on `SEC_USER_AGENT`.

- [ ] **Step 3: Rewrite `_redact`**

In `scripts/copilot/research_data.py`, replace lines 53-64 with:

```python
def _redact(value):
    """Scrub live credential values from anything that may reach a model or disk.

    The name list is not repeated here: it comes from service.KEY_NAMES through
    secret_values(), because a second hardcoded copy had already drifted —
    SEC_USER_AGENT was loaded but never redacted.
    """
    from .service import secret_values
    if isinstance(value, str):
        for secret in secret_values():
            value = value.replace(secret, "[redacted]")
        return value
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    return value
```

- [ ] **Step 4: Run the tests**

```bash
python -m unittest scripts._test_credentials 2>&1 | tail -3
python -m unittest discover -s scripts -p "_test_*.py" 2>&1 | tail -3
```

Expected: `Ran 8 tests ... OK`, full suite `Ran 175 tests ... OK (skipped=2)`.

- [ ] **Step 5: Commit**

```bash
git add scripts/copilot/research_data.py scripts/_test_credentials.py
git commit -m "fix(research): redaction derives its list from KEY_NAMES

The second hardcoded copy had drifted: SEC_USER_AGENT was loaded and never
redacted."
```

---

### Task 5: Revive the Finnhub MCP server

**Files:**
- Modify: `mcps/finnhub_mcp.py:35-53, 70-102`

Every tool on this server currently returns HTTP 401. `API_KEY` is read at **import time** from `os.environ` and the server never calls `load_credentials()`, so it sees whatever the MCP client injected — an empty string unless Claude Code was launched through `scripts/start.*`. `mcps/copilot_mcp.py` does not have this problem because `service.collect()` calls `load_credentials()` on every invocation.

- [ ] **Step 1: Reproduce the failure**

```bash
python -c "import sys; sys.path.insert(0,'mcps'); import finnhub_mcp; print('module API_KEY set:', bool(finnhub_mcp.API_KEY))"
```

Record the result. Then confirm the key really is present in `.env`:

```bash
python -c "import sys,os; sys.path.insert(0,'scripts'); from copilot.service import load_credentials; load_credentials(); print('FINNHUB_API_KEY present after load:', bool(os.getenv('FINNHUB_API_KEY')))"
```

If the second prints `True` while the first printed `False`, the diagnosis is confirmed. If the second prints `False`, stop and report: the key is genuinely absent from `.env` and this task cannot be verified.

- [ ] **Step 2: Replace the import-time constant with a lazy accessor**

In `mcps/finnhub_mcp.py`, add a `sys.path` insert next to the existing imports, following the pattern in `mcps/copilot_mcp.py:15`. Replace line 51 (`API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()`) with:

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

BASE_URL = "https://finnhub.io/api/v1"
TIMEOUT_SECONDS = 15


def _api_key() -> str:
    """Read the key at call time, loading .env if the client injected nothing.

    This used to be an import-time module constant. An MCP client expands
    ${FINNHUB_API_KEY} from its own environment, so launching Claude Code
    without sourcing .env injected an empty string and every tool on this
    server returned HTTP 401 — including healthcheck.
    """
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if key:
        return key
    try:
        from copilot.service import load_credentials
    except ImportError:
        return ""
    load_credentials()
    return os.environ.get("FINNHUB_API_KEY", "").strip()
```

Add `from pathlib import Path` to the imports at the top if it is not already there, and delete the now-duplicated `BASE_URL` / `TIMEOUT_SECONDS` assignments that followed the old line so they are not defined twice.

- [ ] **Step 3: Update the three call sites**

`_require_key` (line 70) becomes:

```python
def _require_key() -> None:
    if not _api_key():
        raise RuntimeError(
            "FINNHUB_API_KEY is not set in the environment or in .env. "
            "Get a free key at https://finnhub.io/register and put it in .env."
        )
```

`_redact` (line 78) becomes:

```python
def _redact(text: str) -> str:
    """Strip the API key from anything that may reach the model or a log."""
    key = _api_key()
    return text.replace(key, "<redacted>") if key else text
```

`_get` (line 83) — change `params["token"] = API_KEY` to `params["token"] = _api_key()`.

Then grep for any remaining use of the old constant:

```bash
grep -n "API_KEY" mcps/finnhub_mcp.py
```

Every hit must be either the string `"FINNHUB_API_KEY"` or a call to `_api_key()`. Report the output.

- [ ] **Step 4: Verify the fix end to end**

```bash
python -c "import sys; sys.path.insert(0,'mcps'); import finnhub_mcp; print('lazy key resolves:', bool(finnhub_mcp._api_key()))"
python scripts/mcp_handshake.py --server finnhub
```

Expected: the first prints `True`; the handshake reports an `ok` verdict. If the handshake script does not support that server name, say so and instead verify by calling `finnhub_mcp._get("quote", symbol="QQQ")` directly and reporting whether it returns a dict or raises.

- [ ] **Step 5: Run the gates and commit**

```bash
python -m unittest discover -s scripts -p "_test_*.py" 2>&1 | tail -3
python -m ruff check --select E9,F63,F7,F82 scripts evals mcps
git add mcps/finnhub_mcp.py
git commit -m "fix(finnhub-mcp): resolve the API key at call time, falling back to .env

The key was captured at import from the host environment. An MCP client
expanding \${FINNHUB_API_KEY} against a shell that never sourced .env injected
an empty string, so every tool on this server returned HTTP 401."
```

---

### Task 6: Name the throttle table and record what it cannot express

**Files:**
- Modify: `scripts/copilot/providers.py:106-127`

The per-provider minimum interval is an anonymous dict literal inside `_throttle`, and its `0.5` default is faster than some published limits. It also models only a minimum gap, not a rolling window, so a provider with a per-minute cap cannot be expressed. No such provider ships today; the point of this task is that the next person to add one finds the limitation written down rather than discovering it in production.

- [ ] **Step 1: Lift the table to a module constant**

In `scripts/copilot/providers.py`, above the `HttpClient` class definition, add:

```python
# Minimum seconds between requests, per provider. The default is deliberately
# conservative for a source whose published limit nobody has checked.
#
# LIMITATION: this is a minimum-gap model, not a rolling window. A provider
# capped at N requests per minute cannot be expressed here — a 1 req/s gap
# still permits a burst of sixty inside the first minute. Adding such a
# provider requires a windowed limiter, not a new row. IBKR's Flex Web Service
# (1 req/s AND 10 req/min) is the known case waiting on this.
_THROTTLE_SECONDS = {"sge": 0.4, "sec": 0.25, "fred": 0.6, "alpaca": 0.4}
_THROTTLE_DEFAULT = 0.5
```

Then in `_throttle`, replace the line

```python
        interval = {"sge": 0.4, "sec": 0.25, "fred": 0.6, "alpaca": 0.4}.get(provider, 0.5)
```

with

```python
        interval = _THROTTLE_SECONDS.get(provider, _THROTTLE_DEFAULT)
```

- [ ] **Step 2: Verify nothing else read the literal**

```bash
grep -n "0.4\|0.25\|0.6\|_THROTTLE" scripts/copilot/providers.py
```

Confirm the only remaining occurrences are in the new constant and the lookup.

- [ ] **Step 3: Run the gates and commit**

```bash
python -m unittest discover -s scripts -p "_test_*.py" 2>&1 | tail -3
git add scripts/copilot/providers.py
git commit -m "refactor(providers): name the throttle table and record its window limitation"
```

---

### Task 7: Document the credentials and verify everything

**Files:**
- Modify: `.env.example`, `docs/INSTALL.md`

- [ ] **Step 1: Read both files first**

```bash
cat .env.example
grep -n "FINNHUB\|FRED\|credential\|\.env" docs/INSTALL.md
```

Report what is there before editing; the edits below describe intent, and you must fit them to the actual current text rather than replacing wholesale.

- [ ] **Step 2: Update `.env.example`**

Ensure it lists every name in `service.KEY_NAMES`, each with a one-line comment saying what it unlocks and whether it is optional, and carries a header stating that this file is the single place credentials go — `config/user.toml` holds decisions, never secrets. Add a line recording that the MCP client expands `${VAR}` from its own environment, so either launch through `scripts/start.*` or rely on the `.env` fallback added in this plan.

- [ ] **Step 3: Update `docs/INSTALL.md`**

In the credentials section, state which features each key unlocks with today's reality:
- `FINNHUB_API_KEY` — company news evidence. The economic calendar is **not** available on the free tier (HTTP 403); see ADR-0005.
- `FRED_API_KEY` — macro series (`DFII10`, `DGS10`, `DTWEXBGS`, `CPIAUCSL`). Free, from https://fredaccount.stlouisfed.org/apikeys
- `SEC_USER_AGENT` — SEC filing evidence; SEC requires a contact string.
- `APCA_*` / `ALPACA_*` — optional third price source. Absent means the Yahoo + Nasdaq cross-check is the only path.

Do not document IBKR, SMTP or Gateway variables: they are not loaded yet, and documenting a variable `load_credentials` ignores is how the Finnhub bug looked from outside.

- [ ] **Step 4: Full verification**

```bash
git status --short
python scripts/check.py
python scripts/sync_runtimes.py --check
python -m unittest discover -s scripts -p "_test_*.py"
python scripts/package_release.py --self-test
python -m ruff check --select E9,F63,F7,F82 scripts evals mcps
python scripts/mcp_handshake.py --server trading-copilot
python scripts/copilot_cli.py capabilities
```

Expected: `OK ... 0 errors`; `0 changed`; `Ran 175 tests ... OK (skipped=2)`; `37/37`; ruff clean; handshake `ok`; and `capabilities` reports `FINNHUB_API_KEY: true` and `FRED_API_KEY` reflecting whether that key is actually in `.env`.

- [ ] **Step 5: Commit**

```bash
git add .env.example docs/INSTALL.md
git commit -m "docs: credential reference matches what load_credentials actually reads"
```

- [ ] **Step 6: Report**

State: the before/after of the Finnhub diagnosis from Task 5 Step 1, the final gate outputs, the commit list, and that Plan 3 (backtest engine and rule library) is next.

---

## Self-review

**Spec coverage.** ADR-0005 records all three scope decisions with their evidence and amends ADR-0004 (Task 1). The four live defects named in the verification briefing each have a task: empty-variable masking (Task 2), `safe_url` short parameter names (Task 3), redaction list drift (Task 4), dead Finnhub server (Task 5). The throttle limitation is recorded rather than speculatively built (Task 6). Credentials are documented to match reality (Task 7). Everything the user deferred — Flex client, Gateway, macro leg, gold proxy — is explicitly out of scope and stated at the top.

**Placeholder scan.** Every code step shows full code. Task 7 deliberately describes intent rather than literal replacement text because the target files must be read first; its step says so explicitly and requires reporting the current contents before editing.

**Type consistency.** `secret_values()` is defined in Task 2 and consumed by name in Tasks 3 and 4. `_api_key()` is defined in Task 5 Step 2 and used at the three call sites in Step 3. `_THROTTLE_SECONDS` / `_THROTTLE_DEFAULT` are defined and consumed within Task 6. Test counts: 162 → 168 (Task 2) → 173 (Task 3) → 175 (Task 4).

**One risk flagged for the implementer.** Task 3 introduces a function-level import from `providers` into `service`. `service.py` imports every core module lazily inside functions, so no cycle should form, but Task 3 Step 4 verifies it explicitly rather than assuming, and instructs the implementer to report rather than work around a cycle if one appears.
