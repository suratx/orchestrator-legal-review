"""
test_failure.py — Student 1 / Coordinator: Infinite Graph Loop reproduction

Simulates the adversarial case: an upstream node (standing in for a
mis-tuned Analyzer/Validator pair) that ALWAYS sets rejection_flag=True, so
the Coordinator keeps getting told "go back and try again" forever.

We do not literally run an unbounded loop (that would hang CI and burn
tokens for real) -- we run the ungoverned coordinator with a hard SAFETY_CAP
purely as a test harness ceiling, and show it (a) never sets next_route to
partial_output and (b) blows straight through where the real guardrail
would have stopped it at round 5. That gap is the failure mode.

Run: python test_failure.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for contract.py

from contract import MAX_ROUNDS, ROUTE_PARTIAL_OUTPUT, AgentState  # noqa: E402
from student_1_loop.snippet import (  # noqa: E402
    coordinator_node,
    coordinator_node_NO_GUARDRAIL,
)

# Test-harness-only ceiling. NOT the guardrail under test -- just prevents
# this reproduction script itself from hanging the grader's machine.
SAFETY_CAP = 500


def adversarial_upstream_step(state: AgentState) -> AgentState:
    """Stands in for Analyzer/Validator behaving adversarially: every time
    control returns to it, it rejects. This is the condition that a real
    prompt-injection or a persistently malformed contract clause could
    trigger in production."""
    state = state.model_copy(deep=True)
    state.rejection_flag = True
    state.validation_notes = "adversarial: always rejects"
    return state


def run_without_guardrail() -> dict:
    state = AgentState(raw_input="Sample NDA clause set...")
    iterations = 0
    start = time.perf_counter()

    while iterations < SAFETY_CAP:
        state = coordinator_node_NO_GUARDRAIL(state)
        if state.next_route == ROUTE_PARTIAL_OUTPUT:
            break  # would only happen if a guardrail existed -- it doesn't
        state = adversarial_upstream_step(state)
        iterations += 1

    elapsed = time.perf_counter() - start
    return {
        "guardrail_active": False,
        "iterations_run": iterations,
        "hit_safety_cap": iterations >= SAFETY_CAP,
        "terminated_cleanly": state.next_route == ROUTE_PARTIAL_OUTPUT,
        "elapsed_seconds": round(elapsed, 4),
        # Rough illustrative cost: pretend each round is one LLM call at $0.03
        "estimated_api_cost_usd": round(iterations * 0.03, 2),
    }


def run_with_guardrail() -> dict:
    state = AgentState(raw_input="Sample NDA clause set...")
    iterations = 0
    start = time.perf_counter()

    while iterations < SAFETY_CAP:
        state = coordinator_node(state)
        if state.next_route == ROUTE_PARTIAL_OUTPUT:
            break
        state = adversarial_upstream_step(state)
        iterations += 1

    elapsed = time.perf_counter() - start
    return {
        "guardrail_active": True,
        "iterations_run": iterations,
        "hit_safety_cap": iterations >= SAFETY_CAP,
        "terminated_cleanly": state.next_route == ROUTE_PARTIAL_OUTPUT,
        "elapsed_seconds": round(elapsed, 4),
        "estimated_api_cost_usd": round(iterations * 0.03, 2),
        "round_number_at_exit": state.round_number,
        "error_log": state.error_log,
    }


# --- pytest entry points (same logic as main(), collectible by CI) ---------


def test_unguarded_coordinator_runs_away():
    result = run_without_guardrail()
    assert result["hit_safety_cap"], (
        "Expected the unguarded coordinator to run away and hit the test "
        "harness's safety cap -- if this fails, the 'failure' isn't reproducing."
    )
    assert not result["terminated_cleanly"]


def test_guarded_coordinator_terminates_bounded():
    result = run_with_guardrail()
    assert result["terminated_cleanly"]
    assert result["round_number_at_exit"] == MAX_ROUNDS
    assert result["iterations_run"] < SAFETY_CAP


def main() -> None:
    print("=" * 70)
    print("FAILURE MODE: Infinite Graph Loop (Coordinator, adversarial reject)")
    print("=" * 70)

    print(f"\n[1] Running WITHOUT guardrail (safety cap = {SAFETY_CAP} for the test harness only)...")
    before = run_without_guardrail()
    for k, v in before.items():
        print(f"    {k}: {v}")
    assert before["hit_safety_cap"], (
        "Expected the unguarded coordinator to run away and hit the test "
        "harness's safety cap -- if this fails, the 'failure' isn't reproducing."
    )
    assert not before["terminated_cleanly"]

    print(f"\n[2] Running WITH guardrail (MAX_ROUNDS = {MAX_ROUNDS})...")
    after = run_with_guardrail()
    for k, v in after.items():
        print(f"    {k}: {v}")
    assert after["terminated_cleanly"]
    assert after["round_number_at_exit"] == MAX_ROUNDS

    print("\n" + "=" * 70)
    print("BEFORE / AFTER METRICS")
    print("=" * 70)
    print(f"  Loop count:          {before['iterations_run']}+ (uncapped) -> {after['iterations_run']} iterations")
    print(f"  Terminates cleanly:  {before['terminated_cleanly']} -> {after['terminated_cleanly']}")
    print(
        "  Est. API spend:      "
        f"${before['estimated_api_cost_usd']}+ (runaway) -> ${after['estimated_api_cost_usd']} (bounded)"
    )
    print("\nPASS: guardrail reproduces the failure when absent, and deterministically")
    print("caps it at round_number >= MAX_ROUNDS when active.")


if __name__ == "__main__":
    main()
