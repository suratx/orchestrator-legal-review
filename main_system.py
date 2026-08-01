"""
main_system.py — Person 1 integration scaffold (WORK IN PROGRESS)

This is the skeleton Person 1 owns: it wires the Coordinator (with the
round_number loop guardrail) and a Reporter node into a real LangGraph
StateGraph, using stub Analyzer/Actor/Validator nodes as placeholders until
Person 2/3/4 land their real implementations behind the same contract.py
interfaces.

Nothing here is final -- once Person 2 freezes contract.py and the other
students' guardrails are ready, their real nodes replace the stubs below
1:1 (same function signature: AgentState -> AgentState).
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from contract import (
    ROUTE_ACTOR,
    ROUTE_ANALYZER,
    ROUTE_PARTIAL_OUTPUT,
    ROUTE_REPORTER,
    AgentState,
)
from student_1_loop.snippet import coordinator_node, route_after_coordinator

# --------------------------------------------------------------------------
# STUB WORKER NODES (placeholders -- to be replaced by Person 2/3/4)
# --------------------------------------------------------------------------


def analyzer_stub(state: AgentState) -> AgentState:
    """Stands in for Student 2's Clause Extractor + Risk Analyzer."""
    state = state.model_copy(deep=True)
    state.analysis_payload = {"clauses": ["Indemnification", "Termination"], "risk": "medium"}
    state.is_validated = True
    return state


def actor_stub(state: AgentState) -> AgentState:
    """Stands in for Student 3's Redline Generator (tool-gated)."""
    state = state.model_copy(deep=True)
    state.sanitized_tool_calls = ["propose_redline(clause='Indemnification')"]
    state.execution_state = {"redlines_proposed": 1}
    return state


def validator_stub(state: AgentState) -> AgentState:
    """Stands in for Student 4's structural/counter-party validator.
    Approves on the first pass in this scaffold; real version enforces
    invariants and can set rejection_flag."""
    state = state.model_copy(deep=True)
    state.rejection_flag = False
    state.validation_notes = "stub: approved"
    return state


def reporter_node(state: AgentState) -> AgentState:
    """Person 1's scope. Emits either a full report or, if the loop
    guardrail fired, a partial/manual-review report."""
    state = state.model_copy(deep=True)
    if state.next_route == ROUTE_PARTIAL_OUTPUT or state.error_log:
        state.final_report = (
            "PARTIAL -- MANUAL REVIEW REQUIRED\n"
            f"Reason: {state.error_log}\n"
            f"Rounds attempted: {state.round_number}"
        )
    else:
        state.final_report = (
            "Contract Review Complete\n"
            f"Clauses analyzed: {state.analysis_payload.get('clauses')}\n"
            f"Redlines proposed: {state.execution_state.get('redlines_proposed')}\n"
            f"Validation: {state.validation_notes}"
        )
    return state


# --------------------------------------------------------------------------
# GRAPH ASSEMBLY
# --------------------------------------------------------------------------


def build_graph(*, analyzer=analyzer_stub, actor=actor_stub, validator=validator_stub):
    """Assembles the graph from injectable node callables so tests can swap
    in adversarial stand-ins (e.g. an always-rejecting validator) without
    duplicating the wiring. Defaults are the placeholder stubs used until
    Person 2/3/4 land their real nodes."""
    graph = StateGraph(AgentState)

    graph.add_node("coordinator", coordinator_node)
    graph.add_node("analyzer", analyzer)
    graph.add_node("actor", actor)
    graph.add_node("validator", validator)
    graph.add_node("reporter", reporter_node)

    graph.set_entry_point("coordinator")

    graph.add_conditional_edges(
        "coordinator",
        route_after_coordinator,
        {
            ROUTE_ANALYZER: "analyzer",
            ROUTE_ACTOR: "actor",
            ROUTE_REPORTER: "reporter",
            ROUTE_PARTIAL_OUTPUT: "reporter",
        },
    )

    graph.add_edge("analyzer", "coordinator")
    graph.add_edge("actor", "validator")
    graph.add_edge("validator", "coordinator")
    graph.add_edge("reporter", END)

    return graph.compile()


if __name__ == "__main__":
    app = build_graph()
    initial_state = AgentState(raw_input="Sample NDA for counter-party review...")
    result = app.invoke(initial_state, config={"recursion_limit": 50})
    print(result["final_report"])
