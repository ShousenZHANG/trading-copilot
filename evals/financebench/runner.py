#!/usr/bin/env python3
"""FinanceBench-subset evaluator.

Loads sample-questions.jsonl, runs the relevant analyst against each, and
checks the answer against the ground-truth reference. Flags hallucinations.

This is a SCAFFOLD. To populate:
1. Pull ~20 representative Q&A from https://github.com/patronus-ai/financebench
2. Convert to the JSONL schema below
3. Run: python evals/financebench/runner.py

Schema (per line):
    {
      "id": "fb-001",
      "ticker": "AAPL",
      "filing": "10-K FY2023",
      "question": "What was Apple's total net sales for fiscal year 2023?",
      "reference_answer": "$383.285 billion",
      "reference_metric": {"value": 383.285, "unit": "billion USD"},
      "tolerance_pct": 0.5,
      "test_against_agent": "fundamentals-analyst"
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_FILE = Path(__file__).resolve().parent / "sample-questions.jsonl"
RESULTS_DIR = ROOT / "evals" / "results"


def load_questions(path: Path = SAMPLE_FILE) -> list[dict]:
    if not path.exists():
        return []
    questions: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        questions.append(json.loads(line))
    return questions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-size", type=int, default=None,
                        help="Limit to first N questions")
    parser.add_argument("--out", default=None,
                        help="Output JSON file (default: results/financebench-<timestamp>.json)")
    args = parser.parse_args()

    questions = load_questions()
    if not questions:
        print(f"No questions found at {SAMPLE_FILE}", file=sys.stderr)
        print("Populate the file first — see runner.py docstring.", file=sys.stderr)
        return 1

    if args.sample_size:
        questions = questions[: args.sample_size]

    print(f"Loaded {len(questions)} questions.")

    # Validate schema of every question.
    required = {"id", "ticker", "question", "reference_answer", "test_against_agent"}
    bad = 0
    for q in questions:
        missing = required - set(q.keys())
        if missing:
            print(f"  [BAD] {q.get('id', '?')}: missing {missing}", file=sys.stderr)
            bad += 1
    if bad:
        print(f"\n{bad} malformed question(s).")
        return 1

    # Score any answers already collected. A separate headless step (Claude Code
    # SDK dispatching `test_against_agent` per question) writes `model_answer`
    # back into each record; this runner then scores deterministically. If no
    # answers are present yet, we report coverage and exit 0 (schema is valid).
    from importlib import util as _importutil

    scorer_path = ROOT / "evals" / "scorer.py"
    spec = _importutil.spec_from_file_location("scorer", scorer_path)
    scorer = _importutil.module_from_spec(spec)  # type: ignore
    # Register before exec so @dataclass introspection can resolve __module__.
    sys.modules["scorer"] = scorer
    spec.loader.exec_module(scorer)  # type: ignore

    answered = [q for q in questions if q.get("model_answer")]
    if not answered:
        print(f"\n{len(questions)} questions OK (schema valid).")
        print("No `model_answer` fields yet — collect answers via headless dispatch,")
        print("write them back into the JSONL, then re-run to score.")
        return 0

    tallies = {"pass": 0, "fail": 0, "hallucination": 0, "no-reference": 0}
    for q in answered:
        v = scorer.score(
            q["model_answer"],
            q["reference_answer"],
            float(q.get("tolerance_pct", 1.0)),
        )
        tallies[v.status] = tallies.get(v.status, 0) + 1
        print(f"  [{v.status.upper():13}] {q['id']}: {v.detail}")

    n = len(answered)
    acc = tallies["pass"] / n if n else 0.0
    print(f"\nScored {n}/{len(questions)} answered.")
    print(f"  accuracy:       {acc:.1%}")
    print(f"  hallucinations: {tallies['hallucination']}  (confident-wrong; worst failure)")
    print(f"  plain misses:   {tallies['fail']}")
    # Fail the run if any hallucination or accuracy < 80% (tune as needed).
    return 0 if (tallies["hallucination"] == 0 and acc >= 0.8) else 1


if __name__ == "__main__":
    sys.exit(main())
