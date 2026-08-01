"""
test_main_system.py — Person 1 integration test

Verifies the compiled LangGraph as a whole, not just the coordinator in
isolation:
  1. The happy path: coordinator -> analyzer -> coordinator -> actor ->
     validator -> coordinator -> reporter -> END produces a full report.
  2. The guardrail path: an always-rejecting validator drives round_number
     to MAX_ROUNDS *inside the real compiled graph* (not the manual
     while-loop harness in test_failure.py) and the graph still terminates
     with a partial/manual-review report instead of hitting LangGraph's
     own recursion_limit.

Run: pytest test_main_system.py -v
"""

from __future__ import annotations

import pytest

from contract import MAX_ROUNDS, AgentState
from main_system import build_graph


def test_happy_path_produces_full_report():
    app = build_graph()
    initial_state = AgentState(raw_input="Sample NDA for counter-party review...")
    result = app.invoke(initial_state, config={"recursion_limit": 50})

    assert result["final_report"] is not None
    assert "PARTIAL" not in result["final_report"]
    assert "Contract Review Complete" in result["final_report"]
    assert result["round_number"] == 0


def test_guardrail_terminates_graph_with_partial_report():
    def analyzer_stub(state: AgentState) -> AgentState:
        state = state.model_copy(deep=True)
        state.analysis_payload = {"clauses": ["Indemnification"], "risk": "high"}
        state.is_validated = True
        return state

    def actor_stub(state: AgentState) -> AgentState:
        state = state.model_copy(deep=True)
        state.sanitized_tool_calls = ["propose_redline(clause='Indemnification')"]
        state.execution_state = {"redlines_proposed": 1}
        return state

    def always_rejecting_validator(state: AgentState) -> AgentState:
        """Stands in for a validator that never approves -- the same
        adversarial condition test_failure.py exercises, but driven
        through the real compiled graph this time."""
        state = state.model_copy(deep=True)
        state.rejection_flag = True
        state.is_validated = False
        state.execution_state = {}
        state.validation_notes = "adversarial: always rejects"
        return state

    app = build_graph(
        analyzer=analyzer_stub,
        actor=actor_stub,
        validator=always_rejecting_validator,
    )
    initial_state = AgentState(raw_input="Adversarial NDA...")
    # Small recursion_limit proves the guardrail -- not LangGraph's own
    # ceiling -- is what stops the graph. If the guardrail were missing,
    # this would raise GraphRecursionError instead of returning cleanly.
    result = app.invoke(initial_state, config={"recursion_limit": 50})

    assert result["round_number"] == MAX_ROUNDS
    assert "PARTIAL -- MANUAL REVIEW REQUIRED" in result["final_report"]
    assert result["error_log"] is not None


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
