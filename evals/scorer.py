#!/usr/bin/env python3
"""Deterministic answer scorer for the eval harness.

This is the falsifiable core the eval scaffolds were missing. Given a model
answer string and a ground-truth reference, it decides PASS / FAIL / HALLUCINATION
without any LLM call — pure, unit-testable Python. Once answers are collected
(headless Claude Code dispatch, separate step), this module scores them rigorously.

Scoring modes
-------------
1. **numeric**: extract one unambiguous non-calendar magnitude from answer and
   reference (handles $, commas, %, and the scale words k/m/bn/b/billion/
   million/trillion). PASS if within `tolerance_pct`.
2. **textual**: exact phrase containment with a conservative negation check.
   Ambiguous values and complex paraphrases require manual grading.
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
import math
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
    r"(?<![\w.])(-?\$?\s*\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|trillion|kk|mm|bn|tn|k|m|b|t)?\b",
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
    """Return one unambiguous non-calendar magnitude, otherwise None.

    "$383.285 billion" -> 3.83285e11 ; "12.5%" -> 12.5 ; "1,250" -> 1250.0
    """
    if text is None:
        return None
    date_spans = [m.span() for m in re.finditer(r"\b\d{4}-\d{2}-\d{2}\b", text)]
    values = []
    for m in _NUM_RE.finditer(text):
        if any(a <= m.start() < b for a, b in date_spans):
            continue
        raw = m.group(1).replace("$", "").replace(",", "").replace(" ", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        scale_word = (m.group(2) or "").lower()
        # Bare years are context, not the financial answer. An explicitly
        # currency/scale-marked 2023 is still a financial amount.
        if 1900 <= value <= 2100 and value.is_integer() and not scale_word and "$" not in m.group(1) and not text[m.end():].startswith("%"):
            continue
        value *= _SCALE.get(scale_word, 1)
        if math.isfinite(value):
            values.append(value)
    unique = set(values)
    return next(iter(unique)) if len(unique) == 1 else None


_NEGATION = re.compile(r"\b(?:not|no|never|neither|without|cannot|can't|don't|doesn't|isn't|wasn't)\b|不|没有|并非", re.I)
_UNCERTAIN = re.compile(r"\b(?:maybe|perhaps|might|could|unknown|uncertain)\b|可能|不确定", re.I)


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
    if not math.isfinite(tolerance_pct) or tolerance_pct < 0:
        raise ValueError("tolerance_pct must be finite and nonnegative")
    if bool(_NEGATION.search(answer)) != bool(_NEGATION.search(reference)):
        return Verdict("fail", "negation differs; lexical overlap is not entailment", None, None, None)
    if _UNCERTAIN.search(answer) and not _UNCERTAIN.search(reference):
        return Verdict("fail", "answer is conditional or uncertain", None, None, None)

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
    # Token overlap alone cannot prove the same assertion. Keep only an exact
    # phrase with matching polarity; complex paraphrases require human grading.
    if re.search(r"(?<!\w)" + re.escape(reference.strip().lower()) + r"(?!\w)", answer.strip().lower()):
        return Verdict("pass", f"textual match (overlap {overlap:.2f})", None, None, None)
    return Verdict("fail", f"textual mismatch (overlap {overlap:.2f})", None, None, None)


def score_batch(
    pairs: list[tuple[str, str]], tolerance_pct: float = 1.0
) -> dict[str, float | int]:
    """Score many (answer, reference) pairs and aggregate the verdicts.

    Returns counts per status plus ``pass_rate`` (share of *scorable* items —
    ``no-reference`` rows are excluded from the denominator, because a missing
    ground truth is a dataset defect, not a model failure). ``hallucination``
    is reported separately from ``fail``: a confidently wrong number is the
    dangerous failure mode, and averaging it into a pass rate hides it.
    """
    counts = {"pass": 0, "fail": 0, "hallucination": 0, "no-reference": 0}
    for answer, reference in pairs:
        counts[score(answer, reference, tolerance_pct).status] += 1
    scorable = counts["pass"] + counts["fail"] + counts["hallucination"]
    return {
        **counts,
        "total": len(pairs),
        "scorable": scorable,
        "pass_rate": round(counts["pass"] / scorable, 4) if scorable else 0.0,
    }


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
        ("In 2023 revenue was $50 billion", "$383.285 billion", 0.5, "hallucination"),
        ("In 2023 revenue was $383.3 billion", "$383.285 billion", 0.5, "pass"),
        ("2023 revenue: $50 billion", "2023 revenue: $383.285 billion", 0.5, "hallucination"),
        ("Do not Buy", "Buy", 1.0, "fail"),
        ("not $100", "$100", 1.0, "fail"),
        ("maybe Buy", "Buy", 1.0, "fail"),
        ("$100 or $200", "$100", 1.0, "fail"),
        ("buyback", "Buy", 1.0, "fail"),
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

    # score_batch aggregation (deterministic).
    batch = score_batch(
        [
            ("$383.3 billion", "$383.285 billion"),  # pass
            ("$400 billion", "$383.285 billion"),    # fail
            ("$50 billion", "$383.285 billion"),     # hallucination
            ("anything", ""),                        # no-reference (excluded)
        ],
        0.5,
    )
    batch_cases = [
        ("batch counts each status",
         (batch["pass"], batch["fail"], batch["hallucination"], batch["no-reference"])
         == (1, 1, 1, 1)),
        ("batch excludes no-reference from denominator",
         batch["scorable"] == 3 and batch["total"] == 4),
        ("batch pass_rate", abs(float(batch["pass_rate"]) - 1 / 3) < 1e-4),
        ("empty batch does not divide by zero",
         score_batch([], 0.5)["pass_rate"] == 0.0),
    ]
    for name, ok in batch_cases:
        passed += ok
        total += 1
        print(f"  {'ok ' if ok else 'XX '} {name}")

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
