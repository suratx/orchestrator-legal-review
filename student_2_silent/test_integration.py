"""
test_integration.py -- Student 2: the real Analyzer inside the real graph.

`test_failure.py` tests the node in isolation. This file proves the node drops
into Person 1's compiled LangGraph `StateGraph` and that my guardrail composes
correctly with the Coordinator's loop guardrail instead of fighting it.

Runs entirely offline: the model is injected via `make_analyzer_node`, so no
Ollama server is needed. That injection point is also the hook Person 4 should
use for deterministic end-to-end tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contract import MAX_ROUNDS, AgentState  # noqa: E402
from main_system import build_graph  # noqa: E402
from student_2_silent.fixtures import (  # noqa: E402
    GOOD_ANALYSIS,
    HALLUCINATED_ANALYSIS,
    SAMPLE_CONTRACT,
    ScriptedStructuredLLM,
)
from student_2_silent.snippet import make_analyzer_node  # noqa: E402

RECURSION_LIMIT = 40


def _graph(*responses):
    """Compile the real graph with a scripted Analyzer swapped in for the stub."""
    return build_graph(
        analyzer=make_analyzer_node(ScriptedStructuredLLM(list(responses)))
    )


def test_grounded_analysis_produces_a_full_report():
    result = _graph(GOOD_ANALYSIS).invoke(
        AgentState(raw_input=SAMPLE_CONTRACT),
        config={"recursion_limit": RECURSION_LIMIT},
    )

    assert result["final_report"].startswith("Contract Review Complete")
    assert result["is_validated"] is True
    assert len(result["analysis_payload"]["clauses"]) == 3
    assert result["analysis_payload"]["counterparty"] == "Globex Industries Ltd"
    assert result["round_number"] == 0  # no rollbacks needed
    assert result["rejection_reason_history"] == []


def test_stubborn_hallucination_degrades_gracefully_instead_of_looping():
    """The two guardrails composing: mine refuses to emit invented data, Person
    1's refuses to retry forever. Together they must terminate cleanly.

    A deterministic hallucination produces a byte-identical rejection reason on
    every attempt, which trips the Coordinator's repeated-rejection escalation
    (`student_1_loop/snippet.py`) rather than the round ceiling -- so this
    terminates in one round instead of spending all of MAX_ROUNDS.
    """
    result = _graph(HALLUCINATED_ANALYSIS).invoke(
        AgentState(raw_input=SAMPLE_CONTRACT),
        config={"recursion_limit": RECURSION_LIMIT},
    )

    assert result["final_report"].startswith("PARTIAL -- MANUAL REVIEW REQUIRED")
    # Escalated early, and never exhausted the budget. Pinned to MAX_ROUNDS
    # rather than to a literal 1 so retuning the escalation cannot silently
    # turn this into an unbounded loop.
    assert 0 < result["round_number"] < MAX_ROUNDS
    assert len(result["rejection_reason_history"]) == 2
    assert result["analysis_payload"] == {}  # nothing invented was persisted
    assert result["is_validated"] is False
    assert result["rejection_reason_history"]
    assert all(
        "UNGROUNDED OUTPUT" in reason for reason in result["rejection_reason_history"]
    )


def test_hallucination_then_self_correction_still_reports_in_full():
    """One bad answer, corrected on the in-node retry, must NOT cost a
    Coordinator round -- see CONTRACT_FREEZE_NOTES.md section 3."""
    result = _graph(HALLUCINATED_ANALYSIS, GOOD_ANALYSIS).invoke(
        AgentState(raw_input=SAMPLE_CONTRACT),
        config={"recursion_limit": RECURSION_LIMIT},
    )

    assert result["final_report"].startswith("Contract Review Complete")
    assert result["analysis_retry_count"] == 1
    assert result["round_number"] == 0
    assert result["rejection_reason_history"] == []


def test_invented_clause_never_reaches_worker_b():
    """The point of the whole exercise: the Actor must never see Section 12.4."""
    result = _graph(HALLUCINATED_ANALYSIS).invoke(
        AgentState(raw_input=SAMPLE_CONTRACT),
        config={"recursion_limit": RECURSION_LIMIT},
    )

    assert "Section 12.4" not in str(result["analysis_payload"])
    assert result["execution_state"] == {}  # Worker B never ran
    assert result["sanitized_tool_calls"] == []
