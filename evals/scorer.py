#!/usr/bin/env python3
"""Deterministic answer scorer for the eval harness.

This is the falsifiable core the eval scaffolds were missing. Given a model
answer string and a ground-truth reference, it decides PASS / FAIL / HALLUCINATION
without any LLM call — pure, unit-testable Python. Once answers are collected
(headless Claude Code dispatch, separate step), this module scores them rigorously.

Scoring modes
-------------
1. **numeric**: extract the leading numeric magnitude from both answer and
   reference (handles $, commas, %, and the scale words k/m/bn/b/billion/
   million/trillion). PASS if within `tolerance_pct`.
2. **textual**: case-insensitive containment / token-overlap fallback when no
   number is present.
3. **hallucination**: the answer asserts a confident numeric claim that is
   *materially* different from the reference (> 5x tolerance) — worse than a
   plain miss, flagged separately because a confident wrong number is the most
   dangerous failure mode in finance.

Determinism: no randomness, no clock, no network. Same inputs → same verdict.

CLI
---
    python evals/scorer.py --answer "$383.3 billion" --reference "$383.285 billion" --tol 0.5
    python evals/scorer.py --self-test     # run built-in unit tests
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass

try:
    from runtime import force_utf8_stdio  # type: ignore

    force_utf8_stdio()
except Exception:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    except Exception:
        pass

_SCALE = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mm": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "t": 1e12, "tn": 1e12, "trillion": 1e12,
}

# leading signed number with optional thousands separators + decimals,
# optionally followed by a scale word (possibly after a space).
_NUM_RE = re.compile(
    r"(-?\$?\s*[\d,]+(?:\.\d+)?)\s*(k|kk|mm|m|bn|b|tn|t|thousand|million|billion|trillion)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Verdict:
    status: str          # "pass" | "fail" | "hallucination" | "no-reference"
    detail: str
    answer_value: float | None
    reference_value: float | None
    rel_error: float | None


def extract_magnitude(text: str) -> float | None:
    """Return the first numeric magnitude in `text` in base units, or None.

    "$383.285 billion" -> 3.83285e11 ; "12.5%" -> 12.5 ; "1,250" -> 1250.0
    """
    if text is None:
        return None
    m = _NUM_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace("$", "").replace(",", "").replace(" ", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    scale_word = (m.group(2) or "").lower()
    if scale_word in _SCALE:
        value *= _SCALE[scale_word]
    return value


def _token_overlap(a: str, b: str) -> float:
    ta = {t for t in re.findall(r"[a-z0-9]+", a.lower()) if len(t) > 2}
    tb = {t for t in re.findall(r"[a-z0-9]+", b.lower()) if len(t) > 2}
    if not tb:
        return 0.0
    return len(ta & tb) / len(tb)


def score(answer: str, reference: str, tolerance_pct: float = 1.0) -> Verdict:
    """Score one answer against a reference.

    tolerance_pct is a percentage (0.5 means 0.5%).
    """
    if not reference or not reference.strip():
        return Verdict("no-reference", "empty reference", None, None, None)
    if answer is None or not answer.strip():
        return Verdict("fail", "empty answer", None, None, None)

    ref_val = extract_magnitude(reference)
    ans_val = extract_magnitude(answer)

    # Numeric path — only when the reference is itself numeric.
    if ref_val is not None:
        if ans_val is None:
            return Verdict("fail", "answer has no number to compare", None, ref_val, None)
        if ref_val == 0:
            rel = abs(ans_val)
        else:
            rel = abs(ans_val - ref_val) / abs(ref_val)
        rel_pct = rel * 100.0
        if rel_pct <= tolerance_pct:
            return Verdict("pass", f"within tolerance ({rel_pct:.3f}% <= {tolerance_pct}%)",
                           ans_val, ref_val, rel)
        # confident-but-very-wrong number => hallucination (> 5x tolerance)
        if rel_pct > max(5.0 * tolerance_pct, 10.0):
            return Verdict("hallucination",
                           f"confident wrong number ({rel_pct:.1f}% off)",
                           ans_val, ref_val, rel)
        return Verdict("fail", f"out of tolerance ({rel_pct:.2f}% > {tolerance_pct}%)",
                       ans_val, ref_val, rel)

    # Textual path.
    overlap = _token_overlap(answer, reference)
    if reference.strip().lower() in answer.strip().lower() or overlap >= 0.6:
        return Verdict("pass", f"textual match (overlap {overlap:.2f})", None, None, None)
    return Verdict("fail", f"textual mismatch (overlap {overlap:.2f})", None, None, None)


# --------------------------------------------------------------------------
# Built-in unit tests (deterministic). Run: python evals/scorer.py --self-test
# --------------------------------------------------------------------------
def _self_test() -> int:
    cases = [
        # (answer, reference, tol, expected_status)
        ("$383.3 billion", "$383.285 billion", 0.5, "pass"),
        ("$383.285 billion", "$383.285 billion", 0.5, "pass"),
        ("$400 billion", "$383.285 billion", 0.5, "fail"),
        ("$50 billion", "$383.285 billion", 0.5, "hallucination"),
        ("RSI is about 72", "72", 2.0, "pass"),
        ("revenue grew 12.5%", "12.5%", 1.0, "pass"),
        ("1,250", "1250", 0.5, "pass"),
        ("no idea", "$383.285 billion", 0.5, "fail"),
        ("", "$100", 1.0, "fail"),
        ("Buy", "Buy", 1.0, "pass"),
        ("the rating is buy now", "Buy", 1.0, "pass"),
        ("Sell", "Buy", 1.0, "fail"),
        ("anything", "", 1.0, "no-reference"),
    ]
    passed = 0
    for ans, ref, tol, expected in cases:
        v = score(ans, ref, tol)
        ok = v.status == expected
        passed += ok
        flag = "ok " if ok else "XX "
        print(f"  {flag} score({ans!r}, {ref!r}, {tol}) -> {v.status} "
              f"(expected {expected}) [{v.detail}]")
    total = len(cases)
    print(f"\n{passed}/{total} scorer unit tests passed.")
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic eval answer scorer")
    ap.add_argument("--answer")
    ap.add_argument("--reference")
    ap.add_argument("--tol", type=float, default=1.0, help="tolerance percent")
    ap.add_argument("--self-test", action="store_true", help="run built-in unit tests")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if args.answer is None or args.reference is None:
        print("usage: scorer.py --answer A --reference R [--tol PCT] | --self-test",
              file=sys.stderr)
        return 2
    v = score(args.answer, args.reference, args.tol)
    print(f"{v.status.upper()}: {v.detail}")
    if v.answer_value is not None:
        print(f"  answer={v.answer_value:,.4g}  reference={v.reference_value:,.4g}  "
              f"rel_error={v.rel_error*100:.3f}%")
    return 0 if v.status in ("pass",) else 1


if __name__ == "__main__":
    sys.exit(main())
