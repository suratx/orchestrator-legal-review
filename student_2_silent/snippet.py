"""
snippet.py -- Student 2 / Worker A (Analyzer): Silent Hallucination Guardrail

FAILURE MODE
    The Analyzer reads raw contract text and emits a confident, well-formed
    analysis that is factually invented -- a liability cap that is not in the
    document, a clause ID that does not exist, a counterparty name that drifted
    by one word. Nothing crashes. Worker B redlines against fiction, Worker C
    validates fiction against fiction, and the Reporter signs off on a contract
    whose real indemnity is uncapped. That silence is the whole problem.

WHERE THIS SITS IN main_system.py
    Coordinator --(Route A)--> [ Worker A: Analyzer ]  <-- this file
                                      |
                                      | writes analysis_payload,
                                      | sets is_validated
                                      v
                                 Coordinator --(Route B)--> Actor

    The Analyzer is the first node to convert unstructured text into
    structured state. Every downstream node trusts `state.analysis_payload`.
    So this node is the only place where a hallucination can still be caught
    cheaply -- one node later it is indistinguishable from fact.

THE GUARDRAIL -- THREE LAYERS, ALL IN CODE
    Layer 1 -- STRUCTURE.  `.with_structured_output(ContractAnalysis)` forces
        the model through a Pydantic schema. Missing `clause_id`, an invented
        `clause_type` outside the enum, or a one-word `risk_rationale` raise a
        `ValidationError` at parse time instead of flowing downstream.

    Layer 2 -- GROUNDING.  Structure is not enough: a hallucination is
        structurally perfect by definition. So every analysis is re-validated
        against the source contract via `contract.validate_grounded()`, which
        asserts that each `verbatim_quote`, each `clause_id` and the
        `counterparty` actually occur in `state.raw_input`. This is a plain
        substring assertion in Python -- the model cannot talk its way past it.

    Layer 3 -- ONE-SHOT SELF-CORRECTION.  On either failure the exception text
        is fed back to the model as a correction instruction and the node
        retries EXACTLY once (`MAX_ANALYZER_RETRIES`). If the retry also
        fails, the node does not guess: it clears `analysis_payload`, sets
        `rejection_flag`, appends to `rejection_reason_history` and hands
        control back to the Coordinator, whose round guardrail eventually
        routes to a partial "MANUAL REVIEW REQUIRED" report.

WHY THE RETRY IS IN-NODE AND NOT A COORDINATOR ROUND
    See CONTRACT_FREEZE_NOTES.md item 3. Briefly: the self-correction never
    leaves this node, so charging it a `round_number` would spend 40% of the
    graph's total loop budget on a repair that is already hard-capped at one
    attempt. `analysis_retry_count` keeps it observable instead.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, List, Optional

from pydantic import ValidationError

from contract import (
    MAX_ANALYZER_RETRIES,
    AgentState,
    ContractAnalysis,
    validate_grounded,
)

logger = logging.getLogger("analyzer")

#: Feedback handed back to the model on a retry is truncated so a pathological
#: error list cannot itself blow up the context window (Person 5's failure
#: mode 6 -- guardrails should not create each other's problems).
MAX_FEEDBACK_CHARS = 800

SYSTEM_PROMPT = """You are a contract clause extraction and risk analysis engine \
for a legal review team. You do not give advice; you extract what is on the page.

Absolute rules:
1. Extract ONLY clauses that are physically present in the contract text you are \
given. Never add a clause because contracts of this type usually contain one.
2. `verbatim_quote` must be copied character-for-character from the contract. \
Never paraphrase, summarize, tidy up, or complete a sentence yourself.
3. `clause_id` must start with the section reference exactly as printed in the \
document (for example "Section 4.2" or "Article 3"). Never invent a number.
4. `counterparty` is the party that is NOT our client. Our client is the party \
identified in the document as "Client". Return the other one.
5. `overall_risk` must equal the highest `risk_level` you assigned to any clause.
6. `risk_rationale` is mandatory for every clause and must be a full sentence of \
at least 20 characters. An empty string is never acceptable.
7. Report at most the five most significant clauses. Do not list boilerplate.

If a protection you would expect is absent from the contract, that absence is \
itself a finding -- report the risky clause that IS present, never a clause that \
is not."""


def build_prompt(raw_input: str, correction_feedback: Optional[str] = None) -> List[Any]:
    """Assemble the message list for one Analyzer attempt.

    On a retry, `correction_feedback` carries the exact validator output from
    the previous attempt, so the model is told precisely which field it
    invented rather than being vaguely asked to "try again".
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    user_parts = [
        "Review the following contract and extract every risk-bearing clause.",
        "",
        "----- BEGIN CONTRACT -----",
        raw_input,
        "----- END CONTRACT -----",
    ]

    if correction_feedback:
        user_parts += [
            "",
            "Your previous answer was REJECTED by an automated validator.",
            "Validator output:",
            correction_feedback[:MAX_FEEDBACK_CHARS],
            "",
            "Fix exactly these problems. If a quote could not be found in the "
            "contract, it means you invented it -- drop that clause entirely "
            "and report only clauses whose text you can copy from above.",
        ]

    return [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content="\n".join(user_parts))]


def format_validation_error(exc: Exception) -> str:
    """Turn an exception into compact, model-readable correction feedback."""
    if isinstance(exc, ValidationError):
        lines = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
            lines.append(f"{location}: {error.get('msg', '')}")
        return " | ".join(lines)
    return f"{type(exc).__name__}: {exc}"


# ==========================================================================
# THE GUARDED NODE
# ==========================================================================


def run_analyzer(state: AgentState, llm: Any) -> AgentState:
    """Analyzer node core, with `llm` injected so it is testable offline.

    `llm` must behave like `ChatOllama(...).with_structured_output(ContractAnalysis)`:
    `.invoke(messages)` returns a `ContractAnalysis` or raises.
    """
    state = state.model_copy(deep=True)
    attempt = 0
    feedback: Optional[str] = None

    while True:
        try:
            # --- Layer 1: structure enforced by the schema itself.
            parsed = llm.invoke(build_prompt(state.raw_input, feedback))
            if parsed is None:
                raise ValueError(
                    "model returned no structured output (empty tool call)"
                )

            # --- Layer 2: grounding enforced against the source contract.
            analysis = validate_grounded(parsed, state.raw_input)

        except (ValidationError, ValueError, TypeError) as exc:
            reason = format_validation_error(exc)
            logger.warning("analyzer attempt %s rejected: %s", attempt + 1, reason)

            # --- Layer 3: exactly one self-correction, then stop guessing.
            if attempt >= MAX_ANALYZER_RETRIES:
                state.analysis_payload = {}
                state.is_validated = False
                state.analysis_retry_count = attempt
                state.rejection_flag = True
                state.rejection_reason_history.append(f"analyzer: {reason}")
                state.error_log = (
                    f"ANALYZER REJECTED after {attempt + 1} attempt(s): {reason}"
                )
                return state

            attempt += 1
            feedback = reason
            continue

        # --- Success: the payload is both well-formed and grounded.
        state.analysis_payload = analysis.model_dump(mode="json")
        state.is_validated = True
        state.analysis_retry_count = attempt
        state.error_log = None
        logger.info(
            "analyzer accepted %s clause(s) after %s retry(ies)",
            len(analysis.clauses),
            attempt,
        )
        return state


def make_analyzer_node(llm: Any) -> Callable[[AgentState], AgentState]:
    """Bind an LLM and return the `AgentState -> AgentState` node signature
    that `main_system.build_graph()` expects."""

    def _node(state: AgentState) -> AgentState:
        return run_analyzer(state, llm)

    return _node


def default_structured_llm(model: Optional[str] = None, temperature: float = 0.0) -> Any:
    """The real model used in production runs: local Ollama, schema-bound.

    Kept behind a function (not a module-level constant) so importing this
    module never requires a running Ollama server -- the offline tests import
    `run_analyzer` directly and inject a scripted model instead.
    """
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=model or os.getenv("OLLAMA_MODEL", "llama3.2"),
        temperature=temperature,
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    ).with_structured_output(ContractAnalysis)


def analyzer_node(state: AgentState) -> AgentState:
    """Drop-in replacement for `analyzer_stub` in main_system.py."""
    return run_analyzer(state, default_structured_llm())


# ==========================================================================
# THE UNGUARDED NODE -- reproduction only, never wired into main_system.py
# ==========================================================================


def run_analyzer_NO_GUARDRAIL(state: AgentState, llm: Any) -> AgentState:
    """What this node looks like before the guardrail: ask for JSON, parse it,
    trust it.

    No schema, no grounding check, no retry. Note the two lines that make the
    failure *silent* -- `analysis_payload` is written from unvalidated model
    output and `is_validated` is set to True unconditionally, so the
    Coordinator waves it straight through to Worker B.
    """
    import json

    state = state.model_copy(deep=True)
    reply = llm.invoke(build_prompt(state.raw_input))
    payload = json.loads(getattr(reply, "content", reply))

    state.analysis_payload = payload  # unvalidated
    state.is_validated = True  # unconditional -- the silent part
    return state
