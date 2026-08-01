"""
snippet.py — Student 1 / Coordinator: Infinite Graph Loop Guardrail
Failure mode: LLM / adversarial upstream nodes cause the Coordinator to
re-route endlessly (Analyzer <-> Validator ping-pong), draining API budget.

WHERE THIS SITS IN main_system.py:
    Coordinator is node 0. Every other node (Analyzer, Actor, Validator,
    Reporter) returns control to the Coordinator. The Coordinator is the
    ONLY node allowed to decide "do we go around again, or do we stop".
    That makes it the single choke point where a deterministic, code-based
    loop guardrail can be enforced -- no matter which upstream node caused
    the retry (bad LLM output, adversarial input, a flaky tool call, etc.)
    the round_number check below is the backstop.

GUARDRAIL DESIGN (per ARCHITECTURE_DESIGN.md, Section 5.1):
  - state.round_number increments every time the Coordinator routes
    "backwards" (Route A after a rejection or a validation error).
  - Hard limit: round_number >= MAX_ROUNDS (5). On breach we do NOT throw.
    We deterministically short-circuit to ROUTE_PARTIAL_OUTPUT so the
    Reporter can emit a "PARTIAL -- MANUAL REVIEW REQUIRED" result instead
    of hanging the graph or burning tokens forever.
  - This is a plain Python `if` on a state field -- not a prompt
    instruction -- so it cannot be argued around by the LLM.
"""

from __future__ import annotations

import logging

from contract import (
    MAX_ROUNDS,
    ROUTE_ACTOR,
    ROUTE_ANALYZER,
    ROUTE_PARTIAL_OUTPUT,
    ROUTE_REPORTER,
    AgentState,
)

logger = logging.getLogger("coordinator")


def coordinator_node(state: AgentState) -> AgentState:
    """
    Pure routing logic. Does NOT call an LLM -- the Coordinator's job is to
    read validated state flags and decide where to go next. Keeping it
    LLM-free is itself part of the guardrail: a routing decision that
    depends on model output is a routing decision that can be manipulated.
    """
    state = state.model_copy(deep=True)

    # --- 1. Hard ceiling check FIRST, before any other routing logic runs.
    #     This guarantees the guardrail cannot be bypassed by any branch
    #     added later in this function.
    if state.round_number >= MAX_ROUNDS:
        logger.warning(
            "round_number=%s >= MAX_ROUNDS=%s -> forcing partial_output",
            state.round_number,
            MAX_ROUNDS,
        )
        state.next_route = ROUTE_PARTIAL_OUTPUT
        state.error_log = (
            state.error_log
            or f"Loop guardrail triggered after {state.round_number} rounds."
        )
        return state

    # --- 2. Normal routing decisions (unchanged by the guardrail) ---
    if state.rejection_flag:
        # ARCHITECTURE_DESIGN.md §5.3 — identical rejection reason twice in a
        # row means the graph is stuck on a deterministically unfixable
        # error. Escalate to partial_output instead of burning rounds.
        # Person 4's Validator (and any rejecting node) appends
        # "<node>: <reason>" to rejection_reason_history before returning.
        history = state.rejection_reason_history
        if len(history) >= 2 and history[-1] == history[-2]:
            logger.warning(
                "repeated rejection reason %r -> forcing partial_output",
                history[-1],
            )
            state.rejection_flag = False
            state.next_route = ROUTE_PARTIAL_OUTPUT
            state.error_log = state.error_log or history[-1]
            return state

        # Validator rejected -> go back to Analyzer, not Actor, per
        # ARCHITECTURE_DESIGN.md 5.3 (avoid re-running a redline against
        # clauses that may themselves need re-extraction).
        state.round_number += 1
        state.rejection_flag = False  # consumed; Analyzer will re-validate
        state.error_log = state.error_log or state.validation_notes
        state.next_route = ROUTE_ANALYZER
        return state

    if not state.is_validated:
        state.next_route = ROUTE_ANALYZER
        return state

    if state.is_validated and not state.execution_state:
        state.next_route = ROUTE_ACTOR
        return state

    state.next_route = ROUTE_REPORTER
    return state


def route_after_coordinator(state: AgentState) -> str:
    """LangGraph conditional-edge function -- reads the pure `next_route`
    field the coordinator_node already computed. Keeping routing decisions
    and edge selection as two separate functions makes the guardrail easy
    to unit-test in isolation (see test_failure.py)."""
    return state.next_route


# --- WHAT AN UNGUARDED VERSION LOOKS LIKE (for the failure-mode video/test) ---
def coordinator_node_NO_GUARDRAIL(state: AgentState) -> AgentState:
    """Intentionally missing the round_number ceiling check. Used only by
    test_failure.py to reproduce the infinite-loop failure mode. Never
    wired into main_system.py."""
    state = state.model_copy(deep=True)

    if state.rejection_flag:
        state.round_number += 1
        state.rejection_flag = False
        state.next_route = ROUTE_ANALYZER
        return state

    if not state.is_validated:
        state.next_route = ROUTE_ANALYZER
        return state

    if state.is_validated and not state.execution_state:
        state.next_route = ROUTE_ACTOR
        return state

    state.next_route = ROUTE_REPORTER
    return state
