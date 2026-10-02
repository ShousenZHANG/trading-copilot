#!/usr/bin/env python3
"""FinanceBench-subset evaluator.

Scores previously collected answers against reference answers. No model is
dispatched here. The prefilled demo is excluded from model-effectiveness scores.

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


def summarize_coverage(questions: list[dict]) -> dict:
    """Demo answers demonstrate plumbing and never measure a model."""
    real = [q for q in questions if not q.get("demo") and not q.get("id", "").startswith("fb-demo")]
    answered = [q for q in real if isinstance(q.get("model_answer"), str) and q["model_answer"].strip()]
    return {"question_count": len(real), "answered_count": len(answered),
            "demo_count": len(questions) - len(real),
            "coverage": len(answered) / len(real) if real else 0.0,
            "evaluation_complete": bool(real) and len(answered) == len(real)}


def _self_test() -> int:
    import unittest

    class CoverageTests(unittest.TestCase):
        def test_shipped_sample_reports_no_measured_accuracy(self):
            import contextlib
            import io
            from unittest.mock import patch
            output = io.StringIO()
            with patch.object(sys, "argv", ["runner.py", "--sample-size=4"]), contextlib.redirect_stdout(output):
                status = main()
            self.assertEqual(status, 2)
            self.assertNotIn("accuracy:       100.0%", output.getvalue())
            self.assertIn("0/3", output.getvalue())

        def test_prefilled_demo_does_not_count_as_a_model_answer(self):
            result = summarize_coverage([{"id": "fb-1"}, {"id": "fb-demo", "model_answer": "$100"}])
            self.assertEqual(result["answered_count"], 0)
            self.assertEqual(result["coverage"], 0.0)
            self.assertFalse(result["evaluation_complete"])

        def test_partial_real_answers_report_incomplete_coverage(self):
            result = summarize_coverage([{"id": "fb-1", "model_answer": "$100"}, {"id": "fb-2"}])
            self.assertEqual(result["coverage"], 0.5)
            self.assertFalse(result["evaluation_complete"])

    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(CoverageTests))
    return 0 if result.wasSuccessful() else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--questions", type=Path, default=SAMPLE_FILE,
                        help="JSONL containing reference questions and collected model_answer fields")
    parser.add_argument("--sample-size", type=int, default=None,
                        help="Limit to first N questions")
    parser.add_argument("--out", default=None,
                        help="Write an explicit JSON coverage/score report to this path")
    args = parser.parse_args()
    if args.self_test:
        return _self_test()

    questions = load_questions(args.questions)
    if not questions:
        print(f"No questions found at {args.questions}", file=sys.stderr)
        print("Populate the file first — see runner.py docstring.", file=sys.stderr)
        return 1

    if args.sample_size is not None and args.sample_size <= 0:
        parser.error("--sample-size must be positive")
    if args.sample_size is not None:
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

    coverage = summarize_coverage(questions)
    real_questions = [q for q in questions if not q.get("demo") and not q["id"].startswith("fb-demo")]
    answered = [q for q in real_questions if isinstance(q.get("model_answer"), str) and q["model_answer"].strip()]
    print(f"Model answer coverage: {coverage['answered_count']}/{coverage['question_count']} "
          f"({coverage['coverage']:.1%}); excluded {coverage['demo_count']} demo row(s).")
    if not answered:
        print("Accuracy: not measured; no real model answers collected.")
        if args.out:
            Path(args.out).write_text(json.dumps({**coverage, "accuracy": None, "status": "unmeasured"}, indent=2) + "\n", encoding="utf-8")
        return 2

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
    print(f"\nScored {n}/{coverage['question_count']} real questions.")
    print(f"  accuracy:       {acc:.1%}")
    print(f"  hallucinations: {tallies['hallucination']}  (confident-wrong; worst failure)")
    print(f"  plain misses:   {tallies['fail']}")
    if args.out:
        Path(args.out).write_text(json.dumps({**coverage, "accuracy": acc, "tallies": tallies,
                                             "status": "complete" if coverage["evaluation_complete"] else "incomplete"}, indent=2) + "\n", encoding="utf-8")
    # Partial answer coverage must not make an incomplete benchmark pass.
    return 0 if (coverage["evaluation_complete"] and tallies["hallucination"] == 0 and acc >= 0.8) else 1


if __name__ == "__main__":
    sys.exit(main())
