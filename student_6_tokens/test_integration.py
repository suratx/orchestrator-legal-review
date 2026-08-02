"""
test_integration.py -- Person 5 (Context/Token layer): the properties that
make the compressor safe to leave switched on.

Cutting tokens is the easy half. The hard half is cutting them without the
compressor becoming the problem it was built to solve, without changing what
the graph computes, and without looping when compression cannot reach the
target.

Run:  pytest student_6_tokens/test_integration.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract import AgentState
from main_system import build_graph
from student_6_tokens.fixtures import (
    SYSTEM_TURN,
    actor_stub,
    analyzer_stub,
    approving_validator,
    initial_state,
    make_varying_validator,
    synthetic_history,
)
from student_6_tokens.snippet import (
    DEFAULT_COUNTER,
    KIND_SUMMARY,
    KIND_SYSTEM,
    KIND_TOOL_OUTPUT,
    MAX_CONTEXT_TOKENS,
    REQUIRED_TURN_KEYS,
    SummaryAggregate,
    compress_history,
    context_manager_NO_GUARDRAIL,
    context_manager_node,
    describe_turns,
    is_summary,
    make_turn,
    validate_turn,
    with_turn_recording,
)


# ==========================================================================
# 1. THE ROLLING SUMMARY -- exactly one, replaced, and BOUNDED
# ==========================================================================
#
# Appending a new pinned summary each time would make the compressor the thing
# that blows the window. Replacing is necessary but not sufficient: a prose
# summary would still grow every time old-summary and new-evictions were
# concatenated. The aggregate schema is what makes merging O(1) in size.


def test_exactly_one_summary_survives_repeated_compression():
    history = synthetic_history(12)
    for _ in range(6):
        history, _, _ = compress_history(history, max_tokens=300)
        history = history + synthetic_history(6)[1:]  # more turns arrive
        summaries = [m for m in history if is_summary(m)]
        assert len(summaries) <= 1, f"{len(summaries)} summaries accumulated"


def test_summary_size_does_not_grow_with_history_length():
    """The load-bearing property. If this fails, the guardrail leaks."""
    sizes = []
    for turns in (12, 24, 48, 96, 192):
        compressed, _, _ = compress_history(synthetic_history(turns), max_tokens=300)
        summary = next(m for m in compressed if is_summary(m))
        sizes.append(DEFAULT_COUNTER.count_message(summary))

    assert max(sizes) - min(sizes) <= 12, (
        f"summary grew with history: {sizes} -- merging is concatenating "
        "somewhere instead of aggregating"
    )


def test_summary_merge_is_aggregation_not_concatenation():
    first = SummaryAggregate(
        turns_compressed=3,
        node_counts={"actor": 2, "analyzer": 1},
        clauses_analyzed=2,
        max_risk="high",
        redlines_proposed=2,
        rejections=1,
        latest_rejection="first defect",
    )
    second = SummaryAggregate(
        turns_compressed=4,
        node_counts={"actor": 1, "validator": 3},
        clauses_analyzed=2,
        max_risk="critical",
        redlines_proposed=1,
        rejections=2,
        latest_rejection="later defect",
    )
    merged = first.merge(second)

    assert merged.turns_compressed == 7
    assert merged.node_counts == {"actor": 3, "analyzer": 1, "validator": 3}
    assert merged.redlines_proposed == 3
    assert merged.rejections == 3
    assert merged.max_risk == "critical"          # worst wins
    assert merged.latest_rejection == "later defect"  # newest wins, not both
    # merging twice more must not grow the key set
    assert set(merged.merge(second).to_dict()) == set(merged.to_dict())


def test_the_window_retains_the_pinned_system_instruction():
    compressed, _, _ = compress_history(synthetic_history(96), max_tokens=300)
    assert any(m.get("kind") == KIND_SYSTEM for m in compressed)


# ==========================================================================
# 2. RECOUNT AND FALLBACK -- compression may not be enough
# ==========================================================================


def test_token_count_is_recalculated_after_compression():
    """`token_count` must describe the window that actually exists, not the
    one that existed before pruning."""
    state = AgentState(raw_input="x", messages=synthetic_history(48))
    result = context_manager_node(state)

    assert result.token_count == DEFAULT_COUNTER.count_messages(result.messages)
    assert result.token_count <= MAX_CONTEXT_TOKENS


def test_token_count_means_the_current_window_not_a_running_total():
    """Defined semantics: what the NEXT call pays to read history. It must be
    able to go DOWN when the window is compressed."""
    state = AgentState(raw_input="x", messages=synthetic_history(48))
    before = context_manager_NO_GUARDRAIL(state).token_count
    after = context_manager_node(state).token_count

    assert before > after
    assert after == DEFAULT_COUNTER.count_messages(context_manager_node(state).messages)


def test_fallback_ladder_terminates_when_the_target_is_unreachable():
    """Pinned turns plus one retained turn can still exceed a tiny ceiling.
    The node must degrade deterministically and STOP, never loop."""
    compressed, tokens, stages = compress_history(synthetic_history(40), max_tokens=5)

    assert "floor_reached" in stages
    assert compressed, "compression emptied the window entirely"
    assert any(m.get("kind") == KIND_SYSTEM for m in compressed)
    assert tokens == DEFAULT_COUNTER.count_messages(compressed)


def test_ladder_stops_at_the_first_stage_that_reaches_the_target():
    """Never prune more than necessary -- lost context is a real cost."""
    _, _, gentle = compress_history(synthetic_history(24), max_tokens=1000)
    _, _, harsh = compress_history(synthetic_history(24), max_tokens=120)

    assert len(gentle) < len(harsh)
    assert "truncate_summary" not in gentle


def test_tool_outputs_are_pruned_before_anything_else():
    """The assignment names intermediate tool outputs specifically, and they
    are the right first choice: bulkiest and least reusable.

    Two separate claims, checked separately, because a deeper ladder folds the
    digested turns into the summary afterwards and they stop being visible:
      (a) tool-output digestion is always the FIRST stage attempted;
      (b) when that stage alone reaches the target, the digests survive in the
          window and nothing further is pruned.
    """
    for limit in (1550, 1400, 1200, 900):
        _, _, stages = compress_history(synthetic_history(24), max_tokens=limit)
        assert stages[0] == "digest_tool_outputs", (
            f"at limit {limit} the ladder began with {stages[0]!r}"
        )

    compressed, tokens, stages = compress_history(
        synthetic_history(24), max_tokens=1550
    )
    assert stages == ["digest_tool_outputs"], "pruned further than necessary"
    assert tokens <= 1550
    assert [
        m for m in compressed
        if m.get("kind") == KIND_TOOL_OUTPUT and "chars pruned" in m["content"]
    ]


def test_no_op_below_the_threshold():
    """A guardrail that fires constantly is one that gets switched off."""
    history = synthetic_history(3)
    compressed, tokens, stages = compress_history(history)

    assert stages == []
    assert compressed == history
    assert tokens == DEFAULT_COUNTER.count_messages(history)


# ==========================================================================
# 3. CORE STATE PRESERVATION -- explicitly required by the assignment
# ==========================================================================


def test_context_node_touches_only_messages_and_token_count():
    state = AgentState(
        raw_input="contract text",
        round_number=3,
        is_validated=True,
        rejection_flag=True,
        rejection_reason_history=["validator: something"],
        error_log="prior error",
        analysis_payload={"clauses": [{"clause_id": "Section 1"}]},
        analysis_retry_count=1,
        sanitized_tool_calls=["propose_redline({})"],
        execution_state={"status": "completed"},
        validation_notes="notes",
        next_route="analyzer",
        messages=synthetic_history(48),
    )
    result = context_manager_node(state)

    untouched = set(AgentState.model_fields) - {"messages", "token_count"}
    for field in untouched:
        assert getattr(result, field) == getattr(state, field), f"{field} was modified"


def test_graph_reaches_the_same_outcome_with_compression_on():
    def run(context_node):
        app = build_graph(
            analyzer=analyzer_stub,
            actor=actor_stub,
            validator=approving_validator,
            context_manager=context_node,
        )
        return app.invoke(initial_state(), config={"recursion_limit": 60})

    assert run(context_manager_node)["final_report"] == run(
        context_manager_NO_GUARDRAIL
    )["final_report"]


def test_contract_is_untouched():
    """No new state fields. Answers CONTRACT_FREEZE_NOTES.md section 7: the
    rolling summary lives inside `messages` as a pinned system entry, so no
    `history_summary` field is needed and the freeze holds."""
    fields = set(AgentState.model_fields)
    assert "history_summary" not in fields
    assert "context_stages" not in fields
    assert {"messages", "token_count"} <= fields


# ==========================================================================
# 4. THE TURN SCHEMA
# ==========================================================================


def test_recorded_turns_conform_to_the_schema():
    state = analyzer_stub(initial_state())
    for entry in describe_turns("analyzer", state, turn=1):
        validate_turn(entry)
        assert set(REQUIRED_TURN_KEYS) <= set(entry)


def test_malformed_turns_are_rejected_loudly():
    """An entry with no `kind` would be un-prunable and would sit in the
    window forever -- a silent leak of exactly the kind this layer prevents."""
    with pytest.raises(ValueError):
        validate_turn({"role": "system", "content": "no kind, no turn"})
    with pytest.raises(TypeError):
        validate_turn("not a dict")


def test_wrapper_records_without_mutating_the_input_state():
    state = initial_state()
    snapshot = state.model_dump_json()
    wrapped = with_turn_recording(analyzer_stub, "analyzer")

    result = wrapped(state)

    assert state.model_dump_json() == snapshot
    assert len(result.messages) > len(state.messages)


# ==========================================================================
# 5. COMPOSITION WITH THE TRACING LAYER (Person 5's other guardrail)
# ==========================================================================
#
# Worth one test, because this layer MANUFACTURES NEW TEXT out of PII-bearing
# state. The rolling summary did not exist when the redaction rules were
# written, so it is a genuinely new carrier -- not a hypothetical one.


def test_the_rolling_summary_is_still_redacted_before_telemetry():
    from student_5_trace.snippet import (
        InMemorySink,
        redacted_trace_config,
        scan_for_leaks,
    )

    sink = InMemorySink()
    app = build_graph(
        analyzer=analyzer_stub,
        actor=actor_stub,
        validator=make_varying_validator(),
        context_manager=context_manager_node,
    )
    result = app.invoke(
        initial_state(),
        config=redacted_trace_config(sink, entities=["Acme Corporation"],
                                     recursion_limit=60),
    )

    # the run really did produce a summary built from party-bearing state
    assert any(is_summary(m) for m in result["messages"])
    assert scan_for_leaks(
        sink, ["Globex Industries Ltd", "Acme Corporation", "Globex", "Acme"]
    ).unique_count == 0


# ==========================================================================
# 6. SAFETY MANDATE
# ==========================================================================


def test_no_network_traffic_during_compression(monkeypatch):
    import socket

    def refuse(self, address):
        raise AssertionError(f"network connection attempted -> {address}")

    monkeypatch.setattr(socket.socket, "connect", refuse)

    app = build_graph(
        analyzer=analyzer_stub,
        actor=actor_stub,
        validator=make_varying_validator(),
        context_manager=context_manager_node,
    )
    result = app.invoke(initial_state(), config={"recursion_limit": 60})
    assert result["final_report"] is not None


def test_module_touches_no_filesystem_or_shell():
    import student_6_tokens.snippet as ctx

    source = Path(ctx.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    for forbidden in ("open(", "os.remove", "os.system", "shutil.", "subprocess"):
        assert forbidden not in code, f"filesystem/shell primitive present: {forbidden}"
