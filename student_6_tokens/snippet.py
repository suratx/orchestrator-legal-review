"""
snippet.py -- Student 6 / Global Graph Layer: Context Window Explosion & Token Burn

Failure mode: the graph loops. Every loop appends turns to `state.messages`, and
every turn re-sends the whole accumulated history to the model. History grows
linearly, but *cumulative token spend grows quadratically*, because turn N pays
for turns 1..N all over again. In a legal-review domain the turns are large --
verbatim clause quotes and full redline drafts -- so the curve bites early.

WHERE THIS SITS IN main_system.py
    A dedicated node at the head of every loop transition, exactly as
    ARCHITECTURE_DESIGN.md 4 specifies ("runs before Coordinator re-entry"):

        entry ──► [ context_manager ] ──► [ coordinator ] ──► workers
                        ▲                                        │
        analyzer  ──────┤                                        │
        validator ──────┴────────────────────────────────────────┘

    It is the last thing to touch state before the Coordinator routes, so no
    worker can hand the model a window this node has not already bounded.

WHO PRODUCES THE HISTORY
    Nobody did. `AgentState.messages` is in the frozen contract but no worker
    wrote to it, so there was nothing to prune and no honest way to measure a
    saving. Rather than edit four teammates' graded nodes, this layer owns
    history production too: `with_turn_recording()` wraps each worker in
    `build_graph`, so a node's turn is appended by the graph, not by the node.
    A global layer owning global state is the same reasoning that put redaction
    on the boundary rather than inside the workers.

FAIRNESS OF THE BEFORE/AFTER COMPARISON
    The recording wrappers are applied identically in both runs. The ONLY
    difference between "unguarded" and "guarded" is which callable is injected
    as the context node:

        unguarded -> context_manager_NO_GUARDRAIL  (counts, prunes nothing)
        guarded   -> context_manager_node          (counts, prunes, summarizes)

    Same producer, same turns, same accounting. If the two runs built their
    histories differently the comparison would be measuring the fixture, not
    the guardrail.

WHY THE ROLLING SUMMARY IS AN AGGREGATE AND NOT PROSE
    There is exactly ONE summary entry and it is REPLACED, never appended --
    otherwise the pinned summaries accumulate and the compressor becomes the
    thing that blows the window. But replacement alone is not enough: if the
    summary were prose, merging "old summary + newly evicted turns" would
    concatenate, and one entry would still grow without bound.

    So the summary carries a fixed-schema `aggregate` dict (counts, maxima, the
    latest rejection). Merging two aggregates adds numbers and keeps the same
    keys, so the merged summary is O(1) in size no matter how many turns have
    been compressed into it. Bounded by construction, with a hard character cap
    as a backstop.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from contract import AgentState

logger = logging.getLogger("context_manager")


# ==========================================================================
# 1. THE TURN SCHEMA
# ==========================================================================
#
# `AgentState.messages` is typed `List[Dict[str, Any]]` -- deliberately loose
# in the frozen contract, which means this layer must impose its own structure
# or the pruning policy has nothing to reason about. "Prune intermediate tool
# outputs" is only implementable if a turn declares that it IS a tool output.

TURN_SCHEMA_VERSION = 1

ROLE_SYSTEM = "system"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"

#: `kind` drives the entire pruning policy, cheapest-to-lose first.
KIND_SYSTEM = "system"            # pinned; the operating instruction
KIND_SUMMARY = "summary"          # pinned; exactly one, replaced in place
KIND_TOOL_OUTPUT = "tool_output"  # bulkiest, least reusable -> digested first
KIND_ANALYSIS = "analysis"
KIND_VALIDATION = "validation"
KIND_ROUTING = "routing"
KIND_REPORT = "report"

#: Never evicted. Losing these loses the thread of the whole conversation.
PINNED_KINDS = frozenset({KIND_SYSTEM, KIND_SUMMARY})

REQUIRED_TURN_KEYS = ("v", "turn", "node", "role", "kind", "content")


def make_turn(
    *,
    node: str,
    role: str,
    kind: str,
    content: str,
    turn: int,
    **extra: Any,
) -> Dict[str, Any]:
    """Build one history entry. Every appended turn goes through here."""
    entry: Dict[str, Any] = {
        "v": TURN_SCHEMA_VERSION,
        "turn": turn,
        "node": node,
        "role": role,
        "kind": kind,
        "content": content,
    }
    entry.update(extra)
    return entry


def validate_turn(entry: Any) -> Dict[str, Any]:
    """Contract-first: a malformed turn fails loudly instead of silently
    breaking the pruning policy (an entry with no `kind` would be un-prunable
    and would sit in the window forever)."""
    if not isinstance(entry, dict):
        raise TypeError(f"history entry must be a dict, got {type(entry).__name__}")
    missing = [key for key in REQUIRED_TURN_KEYS if key not in entry]
    if missing:
        raise ValueError(f"history entry missing required keys: {missing}")
    if not isinstance(entry["content"], str):
        raise TypeError("history entry 'content' must be a str")
    return entry


def is_pinned(entry: Dict[str, Any]) -> bool:
    return entry.get("kind") in PINNED_KINDS


def is_summary(entry: Dict[str, Any]) -> bool:
    return entry.get("kind") == KIND_SUMMARY


# ==========================================================================
# 2. TOKEN ACCOUNTING
# ==========================================================================
#
# WHAT `state.token_count` MEANS -- stated precisely, and stated HONESTLY,
# because an overclaimed metric is worse than an ambiguous one:
#
#     token_count = the estimated number of tokens in `state.messages` AFTER
#                   this node has run. It is the size of the managed window.
#
# What it is NOT, in this system as currently built:
#
#     It is NOT "what the next model call pays". No worker feeds
#     `state.messages` to a model -- Person 2's Analyzer builds its prompt from
#     `raw_input`, and the Coordinator and Validator are deterministic and call
#     no model at all. A context-node visit is therefore not the same event as
#     an LLM invocation.
#
# So the run-level figure this layer reports is a PROJECTION: what a
# history-consuming agent would pay if one were wired in. `fixtures.py` ships
# `history_consuming_analyzer_stub`, which does read the window, so the
# projection can also be measured at genuine consumer invocations rather than
# at every graph transition. Both numbers are reported separately in METRICS.md
# and neither is labelled as observed spend on the current agents.

#: FITTED from llama3.2 via /api/chat with real message ARRAYS -- see
#: `calibrate_tokens.py`. Three constants, because a chat prompt has three
#: cost components and collapsing them misprices the window:
#:
#:   tokens ≈ CONVERSATION_OVERHEAD          (once per window)
#:          + PER_MESSAGE_OVERHEAD × messages (role header per turn)
#:          + characters / CHARS_PER_TOKEN    (the content itself)
#:
#: An earlier version of the calibration posted a single joined string to
#: /api/generate, measured the template cost ONCE, and then applied that figure
#: to every message. That put per-message overhead at 25 tokens. The correct
#: experiment -- vary the message count in a real array and take the SLOPE --
#: puts it at 2, with the remaining ~24 being a one-off conversation prefix.
#: The old method overpriced a 35-turn window by roughly 800 tokens.
CONVERSATION_OVERHEAD_TOKENS = 24
PER_MESSAGE_OVERHEAD_TOKENS = 2


class HeuristicTokenCounter:
    """Offline token estimator with constants fitted against llama3.2.

    Dependency-free on purpose, so the guardrail, its tests and its metrics run
    with no model server and no tokenizer package. `calibrate_tokens.py` fits
    the constants and reports the delta against whatever is in this file; it
    imports nothing from here before fitting, so it cannot measure its own
    assumption.
    """

    #: Fitted: ~5.77 characters per token on contract prose.
    chars_per_token: float = 5.77

    def count_text(self, text: str) -> int:
        if not text:
            return 0
        return max(1, math.ceil(len(text) / self.chars_per_token))

    def count_message(self, entry: Dict[str, Any]) -> int:
        """Content plus this turn's role header.

        The `role`/`kind`/`node` framing is NOT counted separately: the fitted
        per-message slope already includes the role header the template emits,
        and adding it again would double-count.
        """
        return (
            self.count_text(str(entry.get("content", "")))
            + PER_MESSAGE_OVERHEAD_TOKENS
        )

    def count_messages(self, messages: Sequence[Dict[str, Any]]) -> int:
        if not messages:
            return 0
        return CONVERSATION_OVERHEAD_TOKENS + sum(
            self.count_message(entry) for entry in messages
        )


DEFAULT_COUNTER = HeuristicTokenCounter()


# ==========================================================================
# 3. THE ROLLING SUMMARY -- a fixed-schema aggregate, not prose
# ==========================================================================


@dataclass
class SummaryAggregate:
    """Everything the compressed history retains, in fixed-size fields.

    Merging is addition, so the rendered summary does not grow as more turns
    are folded into it. That is the property that stops the compressor from
    becoming the leak.
    """

    turns_compressed: int = 0
    node_counts: Dict[str, int] = field(default_factory=dict)
    clauses_analyzed: int = 0
    max_risk: str = "none"
    redlines_proposed: int = 0
    rejections: int = 0
    latest_rejection: str = ""

    _RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    #: Hard cap so one pathological rejection string cannot inflate the summary.
    LATEST_REJECTION_CHARS = 120

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "SummaryAggregate":
        if not isinstance(data, dict):
            return cls()
        return cls(
            turns_compressed=int(data.get("turns_compressed", 0)),
            node_counts=dict(data.get("node_counts", {})),
            clauses_analyzed=int(data.get("clauses_analyzed", 0)),
            max_risk=str(data.get("max_risk", "none")),
            redlines_proposed=int(data.get("redlines_proposed", 0)),
            rejections=int(data.get("rejections", 0)),
            latest_rejection=str(data.get("latest_rejection", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turns_compressed": self.turns_compressed,
            "node_counts": dict(self.node_counts),
            "clauses_analyzed": self.clauses_analyzed,
            "max_risk": self.max_risk,
            "redlines_proposed": self.redlines_proposed,
            "rejections": self.rejections,
            "latest_rejection": self.latest_rejection,
        }

    def merge(self, other: "SummaryAggregate") -> "SummaryAggregate":
        """Old summary + newly evicted turns -> ONE updated summary."""
        nodes = dict(self.node_counts)
        for name, count in other.node_counts.items():
            nodes[name] = nodes.get(name, 0) + count

        worst = max(
            (self.max_risk, other.max_risk),
            key=lambda risk: self._RISK_ORDER.get(risk, 0),
        )
        return SummaryAggregate(
            turns_compressed=self.turns_compressed + other.turns_compressed,
            node_counts=nodes,
            clauses_analyzed=max(self.clauses_analyzed, other.clauses_analyzed),
            max_risk=worst,
            redlines_proposed=self.redlines_proposed + other.redlines_proposed,
            rejections=self.rejections + other.rejections,
            latest_rejection=(other.latest_rejection or self.latest_rejection)[
                : self.LATEST_REJECTION_CHARS
            ],
        )

    def render(self) -> str:
        nodes = ", ".join(
            f"{name} x{count}" for name, count in sorted(self.node_counts.items())
        )
        parts = [
            f"[COMPRESSED HISTORY] {self.turns_compressed} earlier turn(s) folded.",
            f"Nodes: {nodes or 'none'}.",
            f"Clauses analyzed: {self.clauses_analyzed} (max risk {self.max_risk}).",
            f"Redlines proposed: {self.redlines_proposed}.",
            f"Rejections: {self.rejections}.",
        ]
        if self.latest_rejection:
            parts.append(f"Latest rejection: {self.latest_rejection}")
        return " ".join(parts)


def aggregate_of(turns: Sequence[Dict[str, Any]]) -> SummaryAggregate:
    """Reduce a batch of evicted turns to the fixed-schema aggregate."""
    nodes: Dict[str, int] = {}
    clauses = 0
    redlines = 0
    rejections = 0
    latest = ""
    worst = "none"

    for entry in turns:
        node = str(entry.get("node", "unknown"))
        nodes[node] = nodes.get(node, 0) + 1

        clauses = max(clauses, int(entry.get("clause_count", 0) or 0))
        redlines += int(entry.get("redline_count", 0) or 0)

        risk = str(entry.get("max_risk", "") or "")
        if SummaryAggregate._RISK_ORDER.get(risk, 0) > SummaryAggregate._RISK_ORDER.get(
            worst, 0
        ):
            worst = risk

        if entry.get("kind") == KIND_VALIDATION and entry.get("rejected"):
            rejections += 1
            latest = str(entry.get("content", ""))[
                : SummaryAggregate.LATEST_REJECTION_CHARS
            ]

    return SummaryAggregate(
        turns_compressed=len(turns),
        node_counts=nodes,
        clauses_analyzed=clauses,
        max_risk=worst,
        redlines_proposed=redlines,
        rejections=rejections,
        latest_rejection=latest,
    )


def build_summary_turn(aggregate: SummaryAggregate, turn: int) -> Dict[str, Any]:
    content = aggregate.render()[:SUMMARY_MAX_CHARS]
    return make_turn(
        node="context_manager",
        role=ROLE_SYSTEM,
        kind=KIND_SUMMARY,
        content=content,
        turn=turn,
        aggregate=aggregate.to_dict(),
    )


# ==========================================================================
# 4. POLICY CONSTANTS
# ==========================================================================

#: Window ceiling. Below this the node does nothing at all -- a guardrail that
#: fires on every turn is one that gets switched off.
MAX_CONTEXT_TOKENS = 1200

#: Non-pinned turns kept verbatim. Recency is what the model actually needs.
RECENCY_TURNS = 4

#: Tool outputs outside the recency window collapse to this many characters.
TOOL_DIGEST_CHARS = 110

#: Any retained turn collapses to this in the last-resort stage.
TURN_DIGEST_CHARS = 80

#: Backstop on the rendered summary.
SUMMARY_MAX_CHARS = 600


# ==========================================================================
# 5. THE CONTEXT MANAGEMENT NODE
# ==========================================================================


def _digest(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}... [+{len(text) - limit} chars pruned]"


def next_turn_id(messages: Sequence[Dict[str, Any]]) -> int:
    """One past the highest turn id present.

    NOT `len(messages)`. After compression the list is short but the retained
    entries carry high ids -- a 10-entry window can hold turns 25..34 -- so
    length-based numbering restarts at 10 and produces duplicate,
    non-chronological ids for everything appended afterwards. Turn ids are how
    the summary and the window are ordered, so collisions there are silent
    corruption rather than cosmetic.
    """
    return max((int(m.get("turn", -1)) for m in messages), default=-1) + 1


def _split(messages: Sequence[Dict[str, Any]]):
    pinned = [m for m in messages if is_pinned(m) and not is_summary(m)]
    summary = next((m for m in messages if is_summary(m)), None)
    body = [m for m in messages if not is_pinned(m)]
    return pinned, summary, body


def _assemble(
    pinned: List[Dict[str, Any]],
    summary: Optional[Dict[str, Any]],
    body: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    return [*pinned, *([summary] if summary else []), *body]


def compress_history(
    messages: Sequence[Dict[str, Any]],
    *,
    counter: HeuristicTokenCounter = DEFAULT_COUNTER,
    max_tokens: int = MAX_CONTEXT_TOKENS,
    recency_turns: int = RECENCY_TURNS,
) -> Tuple[List[Dict[str, Any]], int, List[str]]:
    """Bring `messages` under `max_tokens`. Returns (messages, tokens, stages).

    Five stages, cheapest loss first, each followed by a RECOUNT so the node
    stops the moment it is under budget and never prunes more than it must.
    Deterministic and terminating: every stage strictly shrinks the window, and
    the ladder has a fixed floor.
    """
    working = [dict(entry) for entry in messages]
    stages: List[str] = []

    total = counter.count_messages(working)
    if total <= max_tokens:
        return working, total, stages

    pinned, summary, body = _split(working)
    aggregate = SummaryAggregate.from_dict(
        (summary or {}).get("aggregate")
    )
    next_turn = next_turn_id(working)

    # --- Stage 1: digest bulky tool outputs outside the recency window ------
    # `body[:-0]` is `body[:0]` == [], so a plain slice silently inverts the
    # split when recency_turns is 0. Branch explicitly.
    if recency_turns > 0:
        head, tail = body[:-recency_turns], body[-recency_turns:]
    else:
        head, tail = list(body), []
    changed = False
    for entry in head:
        if entry.get("kind") == KIND_TOOL_OUTPUT and len(entry["content"]) > TOOL_DIGEST_CHARS:
            entry["content"] = _digest(entry["content"], TOOL_DIGEST_CHARS)
            changed = True
    if changed:
        stages.append("digest_tool_outputs")
        working = _assemble(pinned, summary, head + tail)
        total = counter.count_messages(working)
        if total <= max_tokens:
            return working, total, stages

    # --- Stage 2: evict everything outside the recency window into the ------
    #     SINGLE rolling summary (replacing it, never appending a second one).
    if head:
        aggregate = aggregate.merge(aggregate_of(head))
        summary = build_summary_turn(aggregate, next_turn)
        body = list(tail)
        stages.append("fold_into_summary")
        working = _assemble(pinned, summary, body)
        total = counter.count_messages(working)
        if total <= max_tokens:
            return working, total, stages

    # --- Stage 3: shrink the recency window one turn at a time -------------
    while len(body) > 1 and total > max_tokens:
        aggregate = aggregate.merge(aggregate_of(body[:1]))
        summary = build_summary_turn(aggregate, next_turn)
        body = body[1:]
        if "shrink_recency_window" not in stages:
            stages.append("shrink_recency_window")
        working = _assemble(pinned, summary, body)
        total = counter.count_messages(working)
    if total <= max_tokens:
        return working, total, stages

    # --- Stage 4: digest whatever single turn is left ----------------------
    if body:
        body = [dict(body[-1])]
        body[0]["content"] = _digest(body[0]["content"], TURN_DIGEST_CHARS)
        stages.append("digest_last_turn")
        working = _assemble(pinned, summary, body)
        total = counter.count_messages(working)
        if total <= max_tokens:
            return working, total, stages

    # --- Stage 5: truncate the summary itself ------------------------------
    if summary:
        summary = dict(summary)
        summary["content"] = _digest(summary["content"], TURN_DIGEST_CHARS)
        stages.append("truncate_summary")
        working = _assemble(pinned, summary, body)
        total = counter.count_messages(working)

    # FLOOR. Pinned system turns + one summary + one digested turn is the
    # smallest window that still carries the thread. If even that exceeds the
    # ceiling we log and proceed rather than loop or drop core context: an
    # over-budget window is a cost problem, an empty one is a correctness
    # problem, and this layer must not trade the second for the first.
    if total > max_tokens:
        stages.append("floor_reached")
        logger.warning(
            "context floor reached: %s tokens still exceeds %s", total, max_tokens
        )

    return working, total, stages


def context_manager_node(
    state: AgentState,
    *,
    max_tokens: int = MAX_CONTEXT_TOKENS,
    recency_turns: int = RECENCY_TURNS,
) -> AgentState:
    """LangGraph node. Bounds the window, then updates `token_count`.

    Touches ONLY `messages` and `token_count`. Every routing and payload field
    the Coordinator and the workers depend on is left exactly as found -- that
    is the "preserving the system's core state values" requirement, and it is
    asserted in test_integration.py rather than assumed.
    """
    state = state.model_copy(deep=True)

    messages, tokens, stages = compress_history(
        state.messages, max_tokens=max_tokens, recency_turns=recency_turns
    )

    if "floor_reached" in stages:
        # Visible in state, not just in a log line. The contract is frozen so
        # there is no field to set -- but `messages` is this layer's own, and a
        # pinned marker means the condition survives into telemetry and is
        # inspectable by anything reading state. It does NOT reroute the graph:
        # deciding that an over-budget window warrants partial output is a
        # policy call for the Coordinator's owner, not this node's to take
        # unilaterally. Recorded as an open item instead.
        messages = messages + [
            make_turn(
                node="context_manager",
                role=ROLE_SYSTEM,
                kind=KIND_SYSTEM,
                turn=next_turn_id(messages),
                content=(
                    f"CONTEXT BUDGET EXCEEDED: minimum safe window is {tokens} "
                    f"tokens against a target of {max_tokens}. Compression is "
                    "exhausted; proceeding rather than discarding core context."
                ),
                over_budget=True,
            )
        ]
        tokens = DEFAULT_COUNTER.count_messages(messages)

    if stages:
        logger.info(
            "context compressed: %s -> %s tokens via %s",
            state.token_count or DEFAULT_COUNTER.count_messages(state.messages),
            tokens,
            ", ".join(stages),
        )

    state.messages = messages
    state.token_count = tokens
    return state


def make_context_manager(
    *,
    max_tokens: int = MAX_CONTEXT_TOKENS,
    recency_turns: int = RECENCY_TURNS,
) -> Callable[[AgentState], AgentState]:
    """Build a context node with an explicit budget.

    The ceiling is a deployment policy, not a law of nature -- it should track
    the model's window and the operator's cost appetite. Exposing it here keeps
    it out of module-global state and lets tests exercise the deeper stages of
    the ladder without depending on the default happening to be tight enough.
    """

    def node(state: AgentState) -> AgentState:
        return context_manager_node(
            state, max_tokens=max_tokens, recency_turns=recency_turns
        )

    node.__name__ = f"context_manager_{max_tokens}"
    return node


def context_manager_NO_GUARDRAIL(state: AgentState) -> AgentState:
    """The unguarded twin: identical accounting, zero pruning.

    Used by test_failure.py so the before/after comparison differs in exactly
    one variable. It still writes `token_count`, so the burn is measured the
    same way on both sides.
    """
    state = state.model_copy(deep=True)
    state.token_count = DEFAULT_COUNTER.count_messages(state.messages)
    return state


# ==========================================================================
# 6. HISTORY PRODUCTION -- the wrappers applied in build_graph
# ==========================================================================
#
# Applied identically in guarded and unguarded runs. This is the piece that
# makes `state.messages` real without editing a single teammate's node.


def _clause_stats(payload: Dict[str, Any]) -> Tuple[int, str]:
    clauses = payload.get("clauses") if isinstance(payload, dict) else None
    if not isinstance(clauses, list):
        return 0, "none"
    order = SummaryAggregate._RISK_ORDER
    worst = "none"
    for clause in clauses:
        if isinstance(clause, dict):
            risk = str(clause.get("risk_level", ""))
            if order.get(risk, 0) > order.get(worst, 0):
                worst = risk
    return len(clauses), worst


def describe_turns(name: str, result: AgentState, turn: int) -> List[Dict[str, Any]]:
    """Render what a node just did as history entries.

    Deliberately verbose for the Actor: full redline drafts are exactly the
    "intermediate tool output" the assignment asks to prune, and a fixture that
    quietly shortened them would be measuring an easier problem.
    """
    turns: List[Dict[str, Any]] = []

    if name == "analyzer":
        count, worst = _clause_stats(result.analysis_payload)
        if count:
            detail = "; ".join(
                f"{c.get('clause_id')} [{c.get('clause_type')}] "
                f"risk={c.get('risk_level')} :: {c.get('verbatim_quote', '')}"
                for c in result.analysis_payload.get("clauses", [])
                if isinstance(c, dict)
            )
            turns.append(
                make_turn(
                    node=name,
                    role=ROLE_ASSISTANT,
                    kind=KIND_ANALYSIS,
                    turn=turn,
                    content=f"Extracted {count} clause(s). {detail}",
                    clause_count=count,
                    max_risk=worst,
                )
            )
        else:
            turns.append(
                make_turn(
                    node=name,
                    role=ROLE_ASSISTANT,
                    kind=KIND_ANALYSIS,
                    turn=turn,
                    content=f"Analysis produced no usable payload. {result.error_log or ''}",
                )
            )

    elif name == "actor":
        results = result.execution_state.get("results", [])
        if isinstance(results, list) and results:
            for offset, item in enumerate(results):
                turns.append(
                    make_turn(
                        node=name,
                        role=ROLE_TOOL,
                        kind=KIND_TOOL_OUTPUT,
                        turn=turn + offset,
                        content=f"TOOL {item.get('tool')} -> {item}",
                        redline_count=1 if item.get("tool") == "propose_redline" else 0,
                    )
                )
        else:
            turns.append(
                make_turn(
                    node=name,
                    role=ROLE_TOOL,
                    kind=KIND_TOOL_OUTPUT,
                    turn=turn,
                    content=f"Actor produced no results. status="
                    f"{result.execution_state.get('status')}",
                )
            )

    elif name == "validator":
        turns.append(
            make_turn(
                node=name,
                role=ROLE_ASSISTANT,
                kind=KIND_VALIDATION,
                turn=turn,
                content=str(result.validation_notes or "no validation notes"),
                rejected=bool(result.rejection_flag),
            )
        )

    elif name == "reporter":
        turns.append(
            make_turn(
                node=name,
                role=ROLE_ASSISTANT,
                kind=KIND_REPORT,
                turn=turn,
                content=str(result.final_report or ""),
            )
        )

    elif name == "coordinator":
        turns.append(
            make_turn(
                node=name,
                role=ROLE_SYSTEM,
                kind=KIND_ROUTING,
                turn=turn,
                content=f"route={result.next_route} round={result.round_number}",
            )
        )

    return [validate_turn(entry) for entry in turns]


def with_turn_recording(
    node: Callable[[AgentState], AgentState], name: str
) -> Callable[[AgentState], AgentState]:
    """Wrap a worker so the GRAPH records its turn, not the worker itself.

    Keeps `state.messages` truthful without any teammate's node having to know
    this layer exists.
    """

    def wrapped(state: AgentState) -> AgentState:
        result = node(state)
        additions = describe_turns(name, result, next_turn_id(result.messages))
        if additions:
            result = result.model_copy(deep=True)
            result.messages = list(result.messages) + additions
        return result

    wrapped.__name__ = f"{name}_with_history"
    return wrapped
