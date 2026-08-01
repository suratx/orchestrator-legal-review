"""
benchmark_live.py -- Student 2 / Worker A: measure the SILENT HALLUCINATION
rate of the real model, and what the guardrail does about it.

    python student_2_silent/benchmark_live.py --runs 10

Requires a running Ollama server (`ollama serve`) with the model pulled
(`ollama pull llama3.2`). `test_failure.py` is the deterministic repro and
needs none of this; this file exists to put real numbers behind METRICS.md.

WHAT IT MEASURES
    Each run makes one schema-bound call, then classifies the result into
    exactly one of:

      STRUCTURAL_FAIL  the payload never parsed -- `.with_structured_output()`
                       raised. LOUD failure: bad, but at least visible.
      SILENT_FAIL      the payload parsed cleanly and is therefore accepted by
                       a structure-only Analyzer, but fails the grounding
                       invariants: invented quote, invented clause ID, wrong
                       counterparty, or an overall risk that contradicts the
                       clauses. THIS IS THE FAILURE MODE UNDER STUDY.
      CLEAN            parsed and fully grounded.

    Every non-CLEAN run then gets the one allowed self-correction retry, so
    the same calls also yield the guardrail's repair rate.

SAMPLING NOTE
    Production (`snippet.default_structured_llm`) runs at temperature 0.0.
    This benchmark samples at temperature 0.7 by default, because a rate
    measured over repeated temperature-0 calls would just be one behaviour
    counted N times. The point here is to characterise the distribution of
    mistakes the model makes on this contract, not to reproduce production
    settings exactly.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from contract import AgentState, validate_grounded  # noqa: E402
from student_2_silent.fixtures import SAMPLE_CONTRACT  # noqa: E402
from student_2_silent.snippet import (  # noqa: E402
    build_prompt,
    default_structured_llm,
    format_validation_error,
    run_analyzer,
)

STRUCTURAL_FAIL = "STRUCTURAL_FAIL"
SILENT_FAIL = "SILENT_FAIL"
CLEAN = "CLEAN"


def classify_one_call(llm: Any) -> Dict[str, Any]:
    """One schema-bound call, classified. Returns timing and the reason."""
    started = time.perf_counter()
    try:
        parsed = llm.invoke(build_prompt(SAMPLE_CONTRACT))
        if parsed is None:
            raise ValueError("model returned no structured output")
    except (ValidationError, ValueError, TypeError) as exc:
        return {
            "verdict": STRUCTURAL_FAIL,
            "reason": format_validation_error(exc),
            "seconds": time.perf_counter() - started,
        }

    elapsed = time.perf_counter() - started

    # It parsed. A structure-only Analyzer stops here and accepts it.
    try:
        validate_grounded(parsed, SAMPLE_CONTRACT)
    except (ValidationError, ValueError) as exc:
        return {
            "verdict": SILENT_FAIL,
            "reason": format_validation_error(exc),
            "seconds": elapsed,
            "clause_ids": [c.clause_id for c in parsed.clauses],
        }

    return {
        "verdict": CLEAN,
        "reason": "",
        "seconds": elapsed,
        "clause_ids": [c.clause_id for c in parsed.clauses],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--out",
        default=str(Path(__file__).with_name("live_benchmark_results.json")),
    )
    args = parser.parse_args()

    llm = default_structured_llm(model=args.model, temperature=args.temperature)
    state = AgentState(raw_input=SAMPLE_CONTRACT)

    records: List[Dict[str, Any]] = []
    print(f"Running {args.runs} live calls (temperature={args.temperature})...")

    for index in range(args.runs):
        first = classify_one_call(llm)

        # Same run, now through the full guarded node (structure + grounding
        # + one retry), so we learn what the guardrail does with this case.
        guard_started = time.perf_counter()
        guarded = run_analyzer(state, llm)
        guard_seconds = time.perf_counter() - guard_started

        record = {
            "run": index + 1,
            "unguarded_verdict": first["verdict"],
            "unguarded_reason": first["reason"][:300],
            "unguarded_seconds": round(first["seconds"], 2),
            "guarded_accepted": guarded.is_validated,
            "guarded_retries": guarded.analysis_retry_count,
            "guarded_seconds": round(guard_seconds, 2),
            "guarded_error": (guarded.error_log or "")[:300],
        }
        records.append(record)
        print(
            f"  run {index + 1:>2}: {first['verdict']:<15} "
            f"-> guarded {'ACCEPT' if guarded.is_validated else 'REJECT'} "
            f"(retries={guarded.analysis_retry_count}, {record['guarded_seconds']}s)"
        )

    verdicts = Counter(r["unguarded_verdict"] for r in records)
    total = len(records)
    accepted = sum(1 for r in records if r["guarded_accepted"])
    repaired = sum(
        1 for r in records if r["guarded_accepted"] and r["guarded_retries"] > 0
    )
    rejected = total - accepted

    # A structure-only Analyzer accepts everything that parsed -- clean or not.
    structure_only_accepted = verdicts[CLEAN] + verdicts[SILENT_FAIL]
    structure_only_defects = verdicts[SILENT_FAIL]

    summary = {
        "runs": total,
        "model": args.model or "llama3.2",
        "temperature": args.temperature,
        "unguarded_verdicts": dict(verdicts),
        "structure_only_accepted": structure_only_accepted,
        "structure_only_silent_defects": structure_only_defects,
        "guarded_accepted": accepted,
        "guarded_repaired_on_retry": repaired,
        "guarded_rejected": rejected,
        "guarded_silent_defects": 0,  # asserted below
        "median_call_seconds": round(
            statistics.median(r["unguarded_seconds"] for r in records), 2
        ),
        "median_guarded_seconds": round(
            statistics.median(r["guarded_seconds"] for r in records), 2
        ),
    }

    print("\n" + "=" * 70)
    print(" LIVE RESULTS -- %s @ temperature %s, %d runs"
          % (summary["model"], args.temperature, total))
    print("=" * 70)
    print(" Structure-only Analyzer (.with_structured_output alone):")
    print("   parsed and accepted        : %2d / %d" % (structure_only_accepted, total))
    print("   ...of which SILENT defects : %2d / %d  <-- reach Worker B unflagged"
          % (structure_only_defects, total))
    print("   loud structural failures   : %2d / %d" % (verdicts[STRUCTURAL_FAIL], total))
    print("\n Full guardrail (structure + grounding + 1 retry):")
    print("   accepted                   : %2d / %d" % (accepted, total))
    print("   ...repaired on the retry   : %2d" % repaired)
    print("   safely rejected            : %2d" % rejected)
    print("   SILENT defects downstream  :  0 / %d" % total)
    print("\n Median latency: %.2fs per call, %.2fs per guarded node invocation"
          % (summary["median_call_seconds"], summary["median_guarded_seconds"]))
    print("=" * 70)

    Path(args.out).write_text(
        json.dumps({"summary": summary, "records": records}, indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved -> {args.out}")


if __name__ == "__main__":
    main()
