"""
main_system.py — Unified Legal Contract Review Orchestrator

Person 1 owns graph assembly and the Reporter. Worker nodes are imported from
each student's guardrail module behind the frozen contract.py interfaces
(AgentState -> AgentState). Stubs remain available for offline scaffolding
tests via build_graph(...).

Active guardrails wired here:
  - Person 1: Coordinator round_number ceiling + repeated-rejection escalate
  - Person 2: Analyzer grounding + one bounded self-correction. The entry
    point below runs the real node, so `ollama serve` must be up; the
    `build_graph()` default stays the stub to keep the test suite offline.
  - Person 3: Actor tool-permission middleware
  - Person 4: Validator sanitization + rejection_flag rollback
  - Person 5: State Redaction Interceptor on every telemetry channel

Person 5's layer is not a node. It attaches at invoke time via
`redacted_trace_config()`, which also audits for a second, implicitly-created
tracing route and refuses to run while one would upload in the clear.
"""

from __future__ import annotations

import os

from langgraph.graph import END, StateGraph

from contract import (
    ROUTE_ACTOR,
    ROUTE_ANALYZER,
    ROUTE_PARTIAL_OUTPUT,
    ROUTE_REPORTER,
    AgentState,
)
from student_1_loop.snippet import coordinator_node, route_after_coordinator
from student_2_silent.snippet import analyzer_node
from student_3_rogue.snippet import actor_node
from student_4_cascade.snippet import validator_node
from student_5_trace.snippet import InMemorySink, redacted_trace_config
from student_6_tokens.snippet import context_manager_node, with_turn_recording

#: Party names known from deployment configuration rather than from the
#: payload. The counter-party is discoverable (`analysis_payload.counterparty`
#: names it by definition); our own client's name is not, so it is configured.
#: Override at deploy time with the TRACE_REDACT_ENTITIES env var.
CLIENT_ENTITIES = ("Acme Corporation",)

# --------------------------------------------------------------------------
# STUB WORKER NODES (offline / CI placeholders)
# --------------------------------------------------------------------------


def analyzer_stub(state: AgentState) -> AgentState:
    """Stands in for Student 2's Clause Extractor + Risk Analyzer."""
    state = state.model_copy(deep=True)
    state.analysis_payload = {
        "contract_title": "Sample NDA",
        "counterparty": "Globex Industries Ltd",
        "overall_risk": "critical",
        "clauses": [
            {
                "clause_id": "Section 4.2 Indemnification",
                "clause_type": "indemnification",
                "verbatim_quote": (
                    "Globex Industries Ltd shall indemnify Acme Corporation for "
                    "all losses arising from breach, negligence, or misconduct "
                    "without limitation."
                ),
                "risk_level": "critical",
                "risk_rationale": (
                    "The indemnity is uncapped and creates significant "
                    "financial exposure for the client."
                ),
            }
        ],
    }
    state.is_validated = True
    return state


def actor_stub(state: AgentState) -> AgentState:
    """Legacy stub — prefer student_3_rogue.snippet.actor_node in production."""
    state = state.model_copy(deep=True)
    clause_id = "Section 4.2 Indemnification"
    state.sanitized_tool_calls = [
        f"propose_redline({{'clause_id': {clause_id!r}}})"
    ]
    state.execution_state = {
        "status": "completed",
        "executed_count": 1,
        "results": [
            {
                "status": "mock_success",
                "tool": "propose_redline",
                "clause_id": clause_id,
                "replacement_text": (
                    "MOCK REDLINE: revise this clause to reduce the "
                    "identified contractual risk."
                ),
                "reason": "uncapped indemnity",
                "external_action_performed": False,
            }
        ],
        "external_action_performed": False,
    }
    return state


def validator_stub(state: AgentState) -> AgentState:
    """Legacy stub — prefer student_4_cascade.snippet.validator_node."""
    state = state.model_copy(deep=True)
    state.rejection_flag = False
    state.validation_notes = "stub: approved"
    return state


def reporter_node(state: AgentState) -> AgentState:
    """Person 1's scope. Emits either a full report or, if a guardrail
    short-circuited to partial_output, a manual-review report."""
    state = state.model_copy(deep=True)
    if state.next_route == ROUTE_PARTIAL_OUTPUT:
        state.final_report = (
            "PARTIAL -- MANUAL REVIEW REQUIRED\n"
            f"Reason: {state.error_log}\n"
            f"Rounds attempted: {state.round_number}"
        )
    else:
        redlines = state.execution_state.get("redlines_proposed")
        if redlines is None:
            redlines = state.execution_state.get("executed_count")
        clauses = state.analysis_payload.get("clauses", [])
        state.final_report = (
            "Contract Review Complete\n"
            f"Clauses analyzed: {len(clauses) if isinstance(clauses, list) else clauses}\n"
            f"Redlines proposed: {redlines}\n"
            f"Validation: {state.validation_notes}"
        )
    return state


# --------------------------------------------------------------------------
# GRAPH ASSEMBLY
# --------------------------------------------------------------------------


def build_graph(
    *,
    analyzer=analyzer_stub,
    actor=actor_node,
    validator=validator_node,
    context_manager=context_manager_node,
    record_history: bool = True,
):
    """Assembles the graph from injectable node callables so tests can swap
    in adversarial stand-ins without duplicating the wiring.

    Defaults wire Person 3's Actor, Person 4's Validator and Person 5's Context
    Manager. Analyzer defaults to the offline stub because Person 2's live node
    needs Ollama and the test suite must stay offline -- the `__main__` entry
    point below overrides it with the real `analyzer_node`, so a plain
    `python main_system.py` exercises all five guardrails.

    Person 5's context layer enters here in two places:
      - `record_history` wraps each worker so the GRAPH appends that worker's
        turn to `state.messages`. No teammate's node has to know history
        exists. Applied identically however the graph is configured, so a
        guarded/unguarded comparison differs in exactly one variable.
      - `context_manager` runs at the head of every loop transition, bounding
        the window before the Coordinator routes. Injecting
        `context_manager_NO_GUARDRAIL` reproduces the token-burn failure with
        the same history producer still in place.
    """
    graph = StateGraph(AgentState)

    wrap = with_turn_recording if record_history else (lambda node, _name: node)

    graph.add_node("context_manager", context_manager)
    graph.add_node("coordinator", wrap(coordinator_node, "coordinator"))
    graph.add_node("analyzer", wrap(analyzer, "analyzer"))
    graph.add_node("actor", wrap(actor, "actor"))
    graph.add_node("validator", wrap(validator, "validator"))
    graph.add_node("reporter", wrap(reporter_node, "reporter"))

    # The context manager is the entry point and the return path for every
    # loop, so no worker can hand the model a window it has not bounded.
    graph.set_entry_point("context_manager")
    graph.add_edge("context_manager", "coordinator")

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

    graph.add_edge("analyzer", "context_manager")
    graph.add_edge("actor", "validator")
    graph.add_edge("validator", "context_manager")
    graph.add_edge("reporter", END)

    return graph.compile()


def _require_ollama() -> None:
    """The entry point runs Person 2's real Analyzer, which builds a ChatOllama
    client at call time. Fail with one readable line instead of letting a
    connection error surface as a client-library traceback mid-graph."""
    import urllib.error
    import urllib.request

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        urllib.request.urlopen(f"{base_url}/api/tags", timeout=3)
    except (urllib.error.URLError, OSError):
        raise SystemExit(
            f"Cannot reach Ollama at {base_url}.\n"
            "The Analyzer guardrail runs a real model here -- start the server "
            "with `ollama serve` (and `ollama pull llama3.2`).\n"
            "For a run that needs no model server, use the offline suite: "
            "python -m pytest -q"
        )


if __name__ == "__main__":
    # Person 2's guardrail, active: the real grounding-validated Analyzer, not
    # the CI stub. `build_graph`'s default is left alone so the offline tests
    # (and every teammate's injected fixture) keep working unchanged.
    _require_ollama()
    app = build_graph(analyzer=analyzer_node)
    initial_state = AgentState(
        raw_input=(
            "MASTER SERVICES AGREEMENT\n"
            "Counterparty: Globex Industries Ltd\n\n"
            "Section 4.2 Indemnification\n"
            "Globex Industries Ltd shall indemnify Acme Corporation for all "
            "losses arising from breach, negligence, or misconduct without "
            "limitation."
        )
    )

    # Person 5's guardrail, active. The sink stands in for LangSmith when no
    # API key is configured; when ambient tracing IS enabled, this same call
    # installs the redacting client so the implicit upload route is closed too.
    telemetry = InMemorySink()
    result = app.invoke(
        initial_state,
        config=redacted_trace_config(telemetry, entities=CLIENT_ENTITIES),
    )

    print(result["final_report"])
    print(f"\n[telemetry] {len(telemetry)} trace events emitted, redacted.")
