"""
Person 4 — Downstream Cascade Failure reproduction.

Compares:
1. An unguarded downstream consumer that trusts malformed Actor output and
   crashes (TypeError / ZeroDivisionError / IndexError / RuntimeError).
2. The guarded Validator, which catches the same payloads with programmatic
   assertions, sets rejection_flag, and clears poisoned execution_state so
   the Coordinator can roll back without deadlocking the graph.

Run:
    python student_4_cascade/test_failure.py
    pytest student_4_cascade/test_failure.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract import AgentState
from student_4_cascade.snippet import (
    build_malformed_actor_state,
    evaluate_fixture,
    run_validator,
    run_validator_NO_GUARDRAIL,
    summarize_cascade_metrics,
)

#: Defects that must crash the unguarded path and cleanly reject on the guarded path.
CASCADE_DEFECTS = (
    "string_count",
    "orphan_clause",
    "count_mismatch",
    "zero_divide",
)


@pytest.mark.parametrize("defect", CASCADE_DEFECTS)
def test_without_guardrail_crashes_on_malformed_actor_output(defect: str):
    state = build_malformed_actor_state(defect=defect)
    with pytest.raises((TypeError, ZeroDivisionError, IndexError, RuntimeError, KeyError)):
        run_validator_NO_GUARDRAIL(state)


@pytest.mark.parametrize("defect", CASCADE_DEFECTS)
def test_with_guardrail_rejects_without_crashing(defect: str):
    state = build_malformed_actor_state(defect=defect)
    result = run_validator(state)

    assert result.rejection_flag is True
    assert result.is_validated is False
    assert result.execution_state == {}
    assert result.sanitized_tool_calls == []
    assert result.validation_notes is not None
    assert result.error_log is not None
    assert result.error_log.startswith("validator:")
    assert any(h.startswith("validator:") for h in result.rejection_reason_history)


def test_with_guardrail_approves_healthy_actor_output():
    state = build_malformed_actor_state(defect="healthy")
    result = run_validator(state)

    assert result.rejection_flag is False
    assert result.execution_state["status"] == "completed"
    assert result.execution_state["redlines_proposed"] == 1
    assert result.validation_notes is not None
    assert "approved" in result.validation_notes


def test_guardrail_rejects_redline_on_medium_risk_clause():
    """Domain invariant: only high/critical clauses may be redlined."""
    state = build_malformed_actor_state(defect="healthy")
    state.sanitized_tool_calls = [
        "propose_redline({'clause_id': 'Section 7 Termination'})"
    ]
    state.execution_state = {
        "status": "completed",
        "executed_count": 1,
        "results": [
            {
                "status": "mock_success",
                "tool": "propose_redline",
                "clause_id": "Section 7 Termination",
                "replacement_text": "MOCK REDLINE: change notice period.",
                "external_action_performed": False,
            }
        ],
        "external_action_performed": False,
    }

    result = run_validator(state)
    assert result.rejection_flag is True
    assert "high/critical" in (result.validation_notes or "").lower() or (
        "risk_level" in (result.validation_notes or "")
    )


def test_empty_execution_state_is_rejected():
    state = AgentState(raw_input="empty", is_validated=True, analysis_payload={})
    # analysis_payload empty will also fail ContractAnalysis — either way reject.
    state.execution_state = {}
    result = run_validator(state)
    assert result.rejection_flag is True


def main() -> None:
    print("=" * 70)
    print("PERSON 4 — DOWNSTREAM CASCADE FAILURE")
    print("=" * 70)

    crashes_without = 0
    crashes_with = 0
    rejections_with = 0

    for defect in CASCADE_DEFECTS:
        print(f"\n--- defect: {defect} ---")
        unguarded_crashed, rejected_cleanly, reason = evaluate_fixture(defect)

        print(f"Without guardrail crashed: {unguarded_crashed}")
        print(f"With guardrail clean reject: {rejected_cleanly}")
        if reason:
            print(f"Rejection reason: {reason[:160]}...")

        crashes_without += int(unguarded_crashed)
        if not rejected_cleanly:
            # Guardrail failed open or raised — count as a crash/escape.
            try:
                run_validator(build_malformed_actor_state(defect=defect))
            except Exception:
                crashes_with += 1
        else:
            rejections_with += 1

    metrics = summarize_cascade_metrics(
        crashes_without=crashes_without,
        crashes_with=crashes_with,
        rejections_with=rejections_with,
        trials=len(CASCADE_DEFECTS),
    )

    print("\nRESULT")
    print(
        f"Downstream crashes per {metrics['trials']} malformed payloads: "
        f"{metrics['downstream_crashes_without_guardrail']} → "
        f"{metrics['downstream_crashes_with_guardrail']}"
    )
    print(
        f"Clean rejection rate with guardrail: "
        f"{metrics['clean_rejections_with_guardrail']}/{metrics['trials']} "
        f"({100 * metrics['clean_rejections_with_guardrail'] / metrics['trials']:.0f}%)"
    )
    print(
        f"Crash rate: "
        f"{100 * metrics['crash_rate_without']:.0f}% → "
        f"{100 * metrics['crash_rate_with']:.0f}%"
    )


if __name__ == "__main__":
    main()
