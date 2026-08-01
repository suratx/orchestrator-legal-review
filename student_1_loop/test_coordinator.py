"""
test_coordinator.py — Student 1 / Coordinator: unit tests

Complements test_failure.py (which reproduces the end-to-end failure mode
and the before/after metrics). This file pins down the coordinator's
routing contract at the unit level, so any future change to snippet.py
that breaks a boundary condition fails fast in CI instead of only showing
up as a symptom in the full graph.

Run: pytest student_1_loop/test_coordinator.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for contract.py

import pytest

from contract import (
    MAX_ROUNDS,
    ROUTE_ACTOR,
    ROUTE_ANALYZER,
    ROUTE_PARTIAL_OUTPUT,
    ROUTE_REPORTER,
    AgentState,
)
from student_1_loop.snippet import coordinator_node, route_after_coordinator


def make_state(**overrides) -> AgentState:
    return AgentState(raw_input="test contract text", **overrides)


# --- normal routing (guardrail not involved) --------------------------------


def test_routes_to_analyzer_when_not_validated():
    state = coordinator_node(make_state())
    assert state.next_route == ROUTE_ANALYZER
    assert state.round_number == 0


def test_routes_to_actor_when_validated_and_not_executed():
    state = coordinator_node(make_state(is_validated=True))
    assert state.next_route == ROUTE_ACTOR


def test_routes_to_reporter_when_validated_and_executed():
    state = coordinator_node(
        make_state(is_validated=True, execution_state={"redlines_proposed": 1})
    )
    assert state.next_route == ROUTE_REPORTER


def test_rejection_routes_back_to_analyzer_and_increments_round():
    state = coordinator_node(make_state(rejection_flag=True, round_number=1))
    assert state.next_route == ROUTE_ANALYZER
    assert state.round_number == 2
    # rejection_flag must be consumed, not left set, or the next hop would
    # re-trigger the same branch without the Analyzer ever running.
    assert state.rejection_flag is False


# --- guardrail boundary conditions -------------------------------------------


def test_guardrail_does_not_fire_one_round_below_ceiling():
    state = coordinator_node(make_state(rejection_flag=True, round_number=MAX_ROUNDS - 1))
    assert state.next_route == ROUTE_ANALYZER
    assert state.round_number == MAX_ROUNDS


def test_guardrail_fires_exactly_at_ceiling():
    state = coordinator_node(make_state(round_number=MAX_ROUNDS, rejection_flag=True))
    assert state.next_route == ROUTE_PARTIAL_OUTPUT
    assert state.error_log is not None
    assert "5" in state.error_log or str(MAX_ROUNDS) in state.error_log


def test_guardrail_fires_above_ceiling_too():
    """Defensive: state could in theory already be past MAX_ROUNDS (e.g. a
    resumed/rehydrated checkpoint). The check is >=, not ==, so this must
    still short-circuit rather than silently falling through."""
    state = coordinator_node(make_state(round_number=MAX_ROUNDS + 3))
    assert state.next_route == ROUTE_PARTIAL_OUTPUT


def test_guardrail_checked_before_any_other_branch():
    """Even a state that looks otherwise 'done' (validated + executed) must
    still be short-circuited if it arrives at/over the ceiling -- the
    ceiling check runs first and unconditionally."""
    state = coordinator_node(
        make_state(
            round_number=MAX_ROUNDS,
            is_validated=True,
            execution_state={"redlines_proposed": 1},
        )
    )
    assert state.next_route == ROUTE_PARTIAL_OUTPUT


def test_guardrail_preserves_existing_error_log():
    """If an upstream node already recorded a specific error, the guardrail
    must not clobber it with the generic loop message."""
    state = coordinator_node(
        make_state(round_number=MAX_ROUNDS, error_log="upstream: malformed clause tree")
    )
    assert state.next_route == ROUTE_PARTIAL_OUTPUT
    assert state.error_log == "upstream: malformed clause tree"


# --- purity / immutability ---------------------------------------------------


def test_coordinator_does_not_mutate_input_state():
    original = make_state(round_number=2)
    coordinator_node(original)
    assert original.round_number == 2
    assert original.next_route is None


def test_route_after_coordinator_reads_next_route_field():
    state = coordinator_node(make_state())
    assert route_after_coordinator(state) == state.next_route == ROUTE_ANALYZER


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
