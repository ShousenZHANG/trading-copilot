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
import io
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

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
    print()
    print("This scaffold does not yet invoke the analyst subagents — that step")
    print("requires Claude Code SDK headless mode to dispatch the right subagent")
    print("per question. Implement in a follow-up commit:")
    print()
    print("  for q in questions:")
    print("    answer = invoke_subagent(q['test_against_agent'], q['question'])")
    print("    score  = compare(answer, q['reference_answer'], q['tolerance_pct'])")
    print("    record(q, answer, score)")
    print()
    print("Until then, this script just validates the question file is well-formed.")

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
    print(f"\n{len(questions)} questions OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
