"""
Person 4 — integration / rollback tests through the compiled LangGraph.

Verifies:
1. Healthy Actor output → Validator approves → Reporter completes.
2. Malformed Actor output → Validator rejects → Coordinator rolls back
   (rejection_flag consumed, round_number increments) without crashing.
3. Identical rejection reason twice → Coordinator escalates to partial_output
   (ARCHITECTURE_DESIGN.md §5.3), independent of MAX_ROUNDS.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract import MAX_ROUNDS, ROUTE_PARTIAL_OUTPUT, AgentState
from main_system import build_graph, reporter_node
from student_1_loop.snippet import coordinator_node
from student_4_cascade.snippet import build_malformed_actor_state, validator_node


def _healthy_analysis_state() -> AgentState:
    return build_malformed_actor_state(defect="healthy")


def test_validator_approves_and_reporter_completes_in_graph():
    healthy = _healthy_analysis_state()

    def analyzer(state: AgentState) -> AgentState:
        state = state.model_copy(deep=True)
        state.analysis_payload = healthy.analysis_payload
        state.is_validated = True
        return state

    def actor(state: AgentState) -> AgentState:
        state = state.model_copy(deep=True)
        state.sanitized_tool_calls = healthy.sanitized_tool_calls
        state.execution_state = {
            k: v
            for k, v in healthy.execution_state.items()
            if k != "redlines_proposed"
        }
        return state

    app = build_graph(analyzer=analyzer, actor=actor, validator=validator_node)
    result = app.invoke(
        AgentState(raw_input=healthy.raw_input),
        config={"recursion_limit": 50},
    )

    assert "PARTIAL" not in result["final_report"]
    assert "Contract Review Complete" in result["final_report"]
    assert result["rejection_flag"] is False
    assert result["validation_notes"] is not None
    assert "approved" in result["validation_notes"]
    assert result["execution_state"].get("redlines_proposed") == 1


def test_malformed_actor_output_rolls_back_without_crash():
    """Validator rejects string executed_count; Coordinator increments round."""
    healthy = _healthy_analysis_state()
    calls = {"actor": 0}

    def analyzer(state: AgentState) -> AgentState:
        state = state.model_copy(deep=True)
        state.analysis_payload = healthy.analysis_payload
        state.is_validated = True
        state.rejection_flag = False
        return state

    def actor(state: AgentState) -> AgentState:
        state = state.model_copy(deep=True)
        calls["actor"] += 1
        if calls["actor"] == 1:
            # First pass: poison the state the way a buggy Actor would.
            poisoned = build_malformed_actor_state(defect="string_count")
            state.sanitized_tool_calls = poisoned.sanitized_tool_calls
            state.execution_state = poisoned.execution_state
        else:
            # After rollback, emit a healthy payload so the graph can finish.
            state.sanitized_tool_calls = healthy.sanitized_tool_calls
            state.execution_state = {
                k: v
                for k, v in healthy.execution_state.items()
                if k != "redlines_proposed"
            }
        return state

    app = build_graph(analyzer=analyzer, actor=actor, validator=validator_node)
    result = app.invoke(
        AgentState(raw_input=healthy.raw_input),
        config={"recursion_limit": 50},
    )

    assert calls["actor"] >= 2
    assert result["round_number"] >= 1
    assert "Contract Review Complete" in result["final_report"]
    assert result["rejection_flag"] is False


def test_repeated_rejection_reason_escalates_to_partial_output():
    """§5.3: identical validator reason twice → partial_output immediately."""
    healthy = _healthy_analysis_state()

    def analyzer(state: AgentState) -> AgentState:
        state = state.model_copy(deep=True)
        state.analysis_payload = healthy.analysis_payload
        state.is_validated = True
        return state

    def actor(state: AgentState) -> AgentState:
        poisoned = build_malformed_actor_state(defect="orphan_clause")
        state = state.model_copy(deep=True)
        state.sanitized_tool_calls = poisoned.sanitized_tool_calls
        state.execution_state = poisoned.execution_state
        return state

    app = build_graph(analyzer=analyzer, actor=actor, validator=validator_node)
    result = app.invoke(
        AgentState(raw_input=healthy.raw_input),
        config={"recursion_limit": 50},
    )

    assert "PARTIAL -- MANUAL REVIEW REQUIRED" in result["final_report"]
    # Must escalate before burning the full loop budget.
    assert result["round_number"] < MAX_ROUNDS
    history = result["rejection_reason_history"]
    assert len(history) >= 2
    assert history[-1] == history[-2]


def test_coordinator_rollback_copies_validation_notes_into_error_log():
    state = build_malformed_actor_state(defect="orphan_clause")
    rejected = validator_node(state)
    assert rejected.rejection_flag is True

    routed = coordinator_node(rejected)
    assert routed.rejection_flag is False  # consumed
    assert routed.next_route is not None
    assert routed.error_log is not None
    assert routed.error_log.startswith("validator:")


def test_reporter_survives_validator_normalized_state():
    approved = validator_node(build_malformed_actor_state(defect="healthy"))
    approved.next_route = None
    report = reporter_node(approved)
    assert report.final_report is not None
    assert "Contract Review Complete" in report.final_report


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
