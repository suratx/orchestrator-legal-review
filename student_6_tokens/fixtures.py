"""
fixtures.py -- Person 5 / Context & Token Management: deterministic drivers.

Everything here is offline and synthetic. No model server, no network, no file
writes -- the same standard applied to the tracing layer.

THE ROUND-COUNT PROBLEM, AND WHY THE VALIDATOR VARIES ITS REASON
    The obvious way to force a long run is an always-rejecting Validator. It
    does not work, and the reason is a guardrail, not a bug: Person 1's
    Coordinator implements ARCHITECTURE_DESIGN.md 5.3 -- "the same rejection
    reason twice in a row escalates straight to partial_output". A Validator
    that repeats itself therefore terminates the graph after ONE round.

    The honest fix is to make the Validator behave like a real one: a different
    defect each pass, so 5.3 correctly does not fire and the run reaches
    Person 1's `MAX_ROUNDS` ceiling of 5. Person 6 must NOT raise MAX_ROUNDS or
    disable anyone's guardrail to manufacture a longer demo -- the ceiling is
    the system working as designed, and measuring inside it is the point.

    Five rounds is a real bound, not a dramatic one, so cumulative burn is also
    measured as a node-level scaling study over synthetic histories. That is
    labelled as a projection everywhere it appears, never mixed into the
    in-graph numbers.
"""

from __future__ import annotations

from typing import Any, Dict, List

from contract import AgentState
from student_6_tokens.snippet import (
    KIND_SYSTEM,
    ROLE_SYSTEM,
    make_turn,
)

# ==========================================================================
# 1. A CONTRACT BIG ENOUGH TO MATTER
# ==========================================================================

LONG_CONTRACT = """MASTER SERVICES AGREEMENT

Counterparty: Globex Industries Ltd

Section 4.2 Indemnification
Globex Industries Ltd shall indemnify Acme Corporation for all losses arising
from breach, negligence, or misconduct without limitation.

Section 7.1 Limitation of Liability
Neither party shall be liable for indirect, incidental, special, consequential
or punitive damages arising out of or relating to this Agreement, regardless of
the form of action or theory of liability.

Section 9.1 Governing Law
This Agreement shall be governed by and construed in accordance with the laws
of the State of Delaware without regard to its conflict of laws provisions.
"""

#: A pinned operating instruction, as any real deployment would carry.
SYSTEM_TURN: Dict[str, Any] = make_turn(
    node="system",
    role=ROLE_SYSTEM,
    kind=KIND_SYSTEM,
    turn=0,
    content=(
        "You are a legal contract review orchestrator. Extract risk-bearing "
        "clauses, propose redlines for high and critical risk, and never "
        "invent contract text."
    ),
)


def initial_state() -> AgentState:
    return AgentState(raw_input=LONG_CONTRACT, messages=[dict(SYSTEM_TURN)])


# ==========================================================================
# 2. WORKER STUBS -- offline, and deliberately verbose
# ==========================================================================

ANALYSIS_PAYLOAD: Dict[str, Any] = {
    "contract_title": "Master Services Agreement",
    "counterparty": "Globex Industries Ltd",
    "overall_risk": "critical",
    "clauses": [
        {
            "clause_id": "Section 4.2 Indemnification",
            "clause_type": "indemnification",
            "verbatim_quote": (
                "Globex Industries Ltd shall indemnify Acme Corporation for all "
                "losses arising from breach, negligence, or misconduct without "
                "limitation."
            ),
            "risk_level": "critical",
            "risk_rationale": (
                "The indemnity is uncapped and creates unlimited financial "
                "exposure for the client."
            ),
        },
        {
            "clause_id": "Section 7.1 Limitation of Liability",
            "clause_type": "limitation_of_liability",
            "verbatim_quote": (
                "Neither party shall be liable for indirect, incidental, special, "
                "consequential or punitive damages arising out of or relating to "
                "this Agreement, regardless of the form of action or theory of "
                "liability."
            ),
            "risk_level": "high",
            "risk_rationale": (
                "A mutual consequential damages waiver limits recovery for "
                "foreseeable business losses."
            ),
        },
    ],
}


def analyzer_stub(state: AgentState) -> AgentState:
    state = state.model_copy(deep=True)
    state.analysis_payload = ANALYSIS_PAYLOAD
    state.is_validated = True
    return state


def actor_stub(state: AgentState) -> AgentState:
    """Produces full redline drafts -- the bulky 'intermediate tool output'
    the assignment asks to prune. Shortening these in the fixture would be
    measuring an easier problem than the real one."""
    state = state.model_copy(deep=True)
    results: List[Dict[str, Any]] = []
    calls: List[str] = []
    for clause in ANALYSIS_PAYLOAD["clauses"]:
        if clause["risk_level"] in ("high", "critical"):
            results.append(
                {
                    "status": "mock_success",
                    "tool": "propose_redline",
                    "clause_id": clause["clause_id"],
                    "replacement_text": (
                        "MOCK REDLINE: The indemnifying party's aggregate liability "
                        "under this clause shall not exceed the total fees paid in "
                        "the twelve (12) months preceding the claim, and shall "
                        "exclude indirect, incidental and consequential damages, "
                        "save in respect of fraud, wilful misconduct or breach of "
                        "confidentiality obligations."
                    ),
                    "reason": clause["risk_rationale"],
                    "external_action_performed": False,
                }
            )
            calls.append(f"propose_redline({{'clause_id': {clause['clause_id']!r}}})")

    state.sanitized_tool_calls = calls
    state.execution_state = {
        "status": "completed",
        "executed_count": len(results),
        "results": results,
        "external_action_performed": False,
    }
    return state


def make_varying_validator():
    """Rejects every pass with a DIFFERENT reason.

    A constant reason trips Person 1's 5.3 repeated-reason escalation and ends
    the run after one round. Varying it is also the realistic behaviour: a
    validator that finds a different defect each pass.
    """
    seen = {"n": 0}

    def validator(state: AgentState) -> AgentState:
        seen["n"] += 1
        state = state.model_copy(deep=True)
        reason = (
            f"redline {seen['n']} cites clause text that does not match the "
            f"extracted analysis (defect variant {seen['n']})"
        )
        state.rejection_flag = True
        state.is_validated = False
        state.execution_state = {}
        state.validation_notes = reason
        state.rejection_reason_history = list(state.rejection_reason_history) + [
            f"validator: {reason}"
        ]
        return state

    return validator


def approving_validator(state: AgentState) -> AgentState:
    state = state.model_copy(deep=True)
    state.rejection_flag = False
    state.validation_notes = (
        f"approved: {state.execution_state.get('executed_count', 0)} redline(s); "
        "structural and clause-grounding invariants passed."
    )
    return state


# ==========================================================================
# 3. SYNTHETIC HISTORY -- for the node-level scaling study
# ==========================================================================


def synthetic_history(turns: int) -> List[Dict[str, Any]]:
    """A history of `turns` worker turns plus the pinned system turn.

    Used ONLY for the scaling study, which is reported as a projection and
    never blended with the in-graph measurements.
    """
    from student_6_tokens.snippet import (
        KIND_ANALYSIS,
        KIND_TOOL_OUTPUT,
        KIND_VALIDATION,
        ROLE_ASSISTANT,
        ROLE_TOOL,
    )

    history = [dict(SYSTEM_TURN)]
    shapes = [
        (KIND_ANALYSIS, ROLE_ASSISTANT, "analyzer"),
        (KIND_TOOL_OUTPUT, ROLE_TOOL, "actor"),
        (KIND_VALIDATION, ROLE_ASSISTANT, "validator"),
    ]
    for index in range(turns):
        kind, role, node = shapes[index % len(shapes)]
        history.append(
            make_turn(
                node=node,
                role=role,
                kind=kind,
                turn=index + 1,
                content=(
                    f"Turn {index + 1}: "
                    + ANALYSIS_PAYLOAD["clauses"][index % 2]["verbatim_quote"]
                ),
                clause_count=2,
                max_risk="critical",
            )
        )
    return history


# ==========================================================================
# 4. A HISTORY-CONSUMING AGENT
# ==========================================================================
#
# The point of this stub is honesty about what is being measured.
#
# No production node in this repo reads `state.messages`: Person 2's Analyzer
# builds its prompt from `raw_input`, and the Coordinator and Validator call no
# model at all. So "tokens at every context-node visit" is a PROJECTION of what
# a history-consuming agent would pay -- not observed spend.
#
# This stub closes that gap for measurement purposes. It builds its prompt from
# the managed window exactly as a real chat agent would, and records the prompt
# size at each of ITS invocations. Those are genuine consumer events, so the
# figure derived from them is the defensible one.
#
# It is a stub, not a model call: deterministic, offline, no network.


class HistoryConsumingAnalyzer:
    """Analyzer that actually reads the window, and records what it read.

    `prompt_tokens` accumulates one entry per invocation -- the size of the
    prompt this agent would have sent. Sum it for the run's projected input
    spend, measured at real consumer events rather than at every graph edge.
    """

    def __init__(self) -> None:
        self.prompt_tokens: List[int] = []

    def build_prompt(self, state: AgentState) -> List[Dict[str, Any]]:
        """Exactly what a chat agent would send: the managed window, verbatim."""
        return list(state.messages)

    def __call__(self, state: AgentState) -> AgentState:
        from student_6_tokens.snippet import DEFAULT_COUNTER

        prompt = self.build_prompt(state)
        self.prompt_tokens.append(DEFAULT_COUNTER.count_messages(prompt))

        return analyzer_stub(state)

    @property
    def cumulative(self) -> int:
        return sum(self.prompt_tokens)
