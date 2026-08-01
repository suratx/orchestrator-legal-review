"""
test_failure.py -- Student 2 / Worker A: reproduction of the SILENT
HALLUCINATION failure mode, and proof the guardrail traps it.

Run as a script for the demo/metrics table:

    python student_2_silent/test_failure.py

Run as a test suite:

    pytest student_2_silent/test_failure.py -v

DETERMINISM
    The assignment requires a *deterministic* reproduction script. A live
    llama3.2 call is not deterministic, so this file drives the real Analyzer
    code path with scripted model output (`ScriptedStructuredLLM`) taken from
    outputs an actual model produces on this contract. For live measurements
    against the real model see `benchmark_live.py`.

WHAT "SILENT" MEANS HERE
    Every payload in the BAD half of the corpus below would pass a human
    skim. `HALLUCINATED_ANALYSIS` in particular has correct types, valid
    enums, well-formed section numbers and an internally consistent overall
    risk -- it passes `.with_structured_output()` untouched. It is still
    wrong: it reports a twelve-month liability cap that does not exist in the
    document and omits the uncapped indemnity that does.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Allow `python student_2_silent/test_failure.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from contract import MAX_ANALYZER_RETRIES, AgentState, validate_grounded  # noqa: E402
from student_2_silent.fixtures import (  # noqa: E402
    GOOD_ANALYSIS,
    HALLUCINATED_ANALYSIS,
    MISSING_CLAUSE_ID_ANALYSIS,
    SAMPLE_CONTRACT,
    UNDERSTATED_RISK_ANALYSIS,
    WRONG_COUNTERPARTY_ANALYSIS,
    ScriptedRawLLM,
    ScriptedStructuredLLM,
)
from student_2_silent.snippet import (  # noqa: E402
    format_validation_error,
    run_analyzer,
    run_analyzer_NO_GUARDRAIL,
)

# ==========================================================================
# The benchmark corpus: 12 Analyzer outputs, 8 of them defective.
# ==========================================================================

Corpus = List[Tuple[str, Dict[str, Any]]]

CORPUS: Corpus = [
    ("good", GOOD_ANALYSIS),
    ("hallucinated-clause", HALLUCINATED_ANALYSIS),
    ("good", GOOD_ANALYSIS),
    ("missing-clause-id", MISSING_CLAUSE_ID_ANALYSIS),
    ("hallucinated-clause", HALLUCINATED_ANALYSIS),
    ("understated-risk", UNDERSTATED_RISK_ANALYSIS),
    ("good", GOOD_ANALYSIS),
    ("wrong-counterparty", WRONG_COUNTERPARTY_ANALYSIS),
    ("hallucinated-clause", HALLUCINATED_ANALYSIS),
    ("missing-clause-id", MISSING_CLAUSE_ID_ANALYSIS),
    ("good", GOOD_ANALYSIS),
    ("hallucinated-clause", HALLUCINATED_ANALYSIS),
]


def defect_of(payload: Dict[str, Any]) -> Optional[str]:
    """Ground-truth oracle: is this payload actually wrong about the contract?

    Deliberately uses the same frozen contract validators the guardrail uses,
    because they encode the team's agreed definition of a valid analysis.
    """
    try:
        validate_grounded(payload, SAMPLE_CONTRACT)
    except (ValidationError, ValueError) as exc:
        return format_validation_error(exc)
    return None


def fresh_state() -> AgentState:
    return AgentState(raw_input=SAMPLE_CONTRACT)


# ==========================================================================
# BEFORE -- guardrail disabled
# ==========================================================================


def run_unguarded_batch(corpus: Corpus = CORPUS) -> Dict[str, Any]:
    """No schema, no grounding, no retry. Everything the model says is state."""
    escaped: List[str] = []
    llm_calls = 0

    for label, payload in corpus:
        llm = ScriptedRawLLM([payload])
        result = run_analyzer_NO_GUARDRAIL(fresh_state(), llm)
        llm_calls += llm.call_count

        # Did a defective analysis get marked validated and passed downstream?
        if result.is_validated and defect_of(result.analysis_payload):
            escaped.append(label)

    return {
        "total": len(corpus),
        "escaped_defects": escaped,
        "escaped": len(escaped),
        "llm_calls": llm_calls,
        "rejections": 0,
    }


# ==========================================================================
# AFTER -- guardrail active
# ==========================================================================


def run_guarded_batch(corpus: Corpus = CORPUS, *, self_correcting: bool = False) -> Dict[str, Any]:
    """Structured output + grounding + one self-correction.

    `self_correcting=False` is the pessimistic case: the model repeats its bad
    answer on the retry, so every defect ends in a safe rejection rather than
    a repair. `self_correcting=True` models a cooperative model that fixes
    itself once it is told exactly which field it invented.
    """
    escaped: List[str] = []
    rejected: List[str] = []
    repaired: List[str] = []
    llm_calls = 0

    for label, payload in corpus:
        script: List[Any] = [payload]
        if self_correcting:
            script.append(GOOD_ANALYSIS)

        llm = ScriptedStructuredLLM(script)
        result = run_analyzer(fresh_state(), llm)
        llm_calls += llm.call_count

        if result.is_validated:
            if defect_of(result.analysis_payload):
                escaped.append(label)
            elif result.analysis_retry_count > 0:
                repaired.append(label)
        else:
            rejected.append(label)

    return {
        "total": len(corpus),
        "escaped_defects": escaped,
        "escaped": len(escaped),
        "rejections": len(rejected),
        "rejected_labels": rejected,
        "repaired": len(repaired),
        "llm_calls": llm_calls,
    }


# ==========================================================================
# PYTEST -- the assertions that make this a test and not just a demo
# ==========================================================================


def test_unguarded_analyzer_accepts_a_hallucination_silently():
    """The core failure: invented data is marked validated, with no error."""
    llm = ScriptedRawLLM([HALLUCINATED_ANALYSIS])
    result = run_analyzer_NO_GUARDRAIL(fresh_state(), llm)

    assert result.is_validated is True
    assert result.error_log is None
    assert result.rejection_flag is False
    # ...and yet the payload describes a clause that is not in the contract.
    assert defect_of(result.analysis_payload) is not None
    quoted = [c["clause_id"] for c in result.analysis_payload["clauses"]]
    assert "Section 12.4" in quoted
    assert "Section 4.2" not in quoted  # the real uncapped indemnity, dropped


def test_guarded_analyzer_rejects_the_same_hallucination():
    llm = ScriptedStructuredLLM([HALLUCINATED_ANALYSIS])  # model never corrects
    result = run_analyzer(fresh_state(), llm)

    assert result.is_validated is False
    assert result.analysis_payload == {}
    assert result.rejection_flag is True
    assert len(result.rejection_reason_history) == 1
    assert "UNGROUNDED OUTPUT" in result.error_log


def test_guarded_analyzer_self_corrects_within_one_retry():
    llm = ScriptedStructuredLLM([HALLUCINATED_ANALYSIS, GOOD_ANALYSIS])
    result = run_analyzer(fresh_state(), llm)

    assert result.is_validated is True
    assert result.analysis_retry_count == 1
    assert result.rejection_flag is False
    assert result.error_log is None
    assert defect_of(result.analysis_payload) is None
    assert llm.call_count == 2


def test_retry_prompt_carries_the_actual_validator_message():
    """The self-correction must be specific, not a vague 'try again'."""
    llm = ScriptedStructuredLLM([HALLUCINATED_ANALYSIS, GOOD_ANALYSIS])
    run_analyzer(fresh_state(), llm)

    assert llm.call_count == 2
    retry_prompt = llm.last_prompt_text
    assert "REJECTED by an automated validator" in retry_prompt
    assert "Section 12.4" in retry_prompt  # names the invented clause


def test_retry_is_capped_at_exactly_one_attempt():
    llm = ScriptedStructuredLLM([HALLUCINATED_ANALYSIS])  # repeats forever
    result = run_analyzer(fresh_state(), llm)

    assert llm.call_count == MAX_ANALYZER_RETRIES + 1 == 2
    assert result.analysis_retry_count == MAX_ANALYZER_RETRIES


def test_structural_failure_is_caught_at_parse_time():
    """Missing `clause_id` -- the critical domain identifier -- never parses."""
    llm = ScriptedStructuredLLM([MISSING_CLAUSE_ID_ANALYSIS])
    result = run_analyzer(fresh_state(), llm)

    assert result.is_validated is False
    assert "clause_id" in result.error_log


def test_understated_overall_risk_is_caught():
    """Flags a critical clause, then calls the contract low risk."""
    llm = ScriptedStructuredLLM([UNDERSTATED_RISK_ANALYSIS])
    result = run_analyzer(fresh_state(), llm)

    assert result.is_validated is False
    assert "overall_risk" in result.error_log


def test_wrong_counterparty_is_caught():
    llm = ScriptedStructuredLLM([WRONG_COUNTERPARTY_ANALYSIS])
    result = run_analyzer(fresh_state(), llm)

    assert result.is_validated is False
    assert "counterparty" in result.error_log


def test_good_analysis_passes_on_the_first_attempt():
    llm = ScriptedStructuredLLM([GOOD_ANALYSIS])
    result = run_analyzer(fresh_state(), llm)

    assert result.is_validated is True
    assert result.analysis_retry_count == 0
    assert llm.call_count == 1
    assert result.analysis_payload["counterparty"] == "Globex Industries Ltd"


def test_node_does_not_mutate_the_incoming_state():
    """Nodes must return a copy -- LangGraph replays state on retries."""
    state = fresh_state()
    run_analyzer(state, ScriptedStructuredLLM([GOOD_ANALYSIS]))

    assert state.is_validated is False
    assert state.analysis_payload == {}
    assert state.rejection_flag is False


def test_batch_metrics_before_and_after():
    """The headline numbers reported in METRICS.md."""
    before = run_unguarded_batch()
    after = run_guarded_batch()

    assert before["escaped"] == 8
    assert after["escaped"] == 0
    assert after["rejections"] == 8


def test_self_correcting_model_repairs_every_defect():
    after = run_guarded_batch(self_correcting=True)

    assert after["escaped"] == 0
    assert after["rejections"] == 0
    assert after["repaired"] == 8


# ==========================================================================
# DEMO ENTRY POINT
# ==========================================================================


def _bar(count: int, total: int, width: int = 24) -> str:
    filled = round(width * count / total) if total else 0
    return "#" * filled + "." * (width - filled)


def main() -> None:
    # The Analyzer logs a warning every time the guardrail fires. That is 20
    # lines of noise in a 2-minute demo, so it is off unless asked for.
    logging.basicConfig(
        level=logging.WARNING if "--verbose" in sys.argv else logging.ERROR,
        format="    [guardrail] %(message)s",
    )

    before = run_unguarded_batch()
    stubborn = run_guarded_batch()
    cooperative = run_guarded_batch(self_correcting=True)
    total = before["total"]

    print()
    print("=" * 74)
    print(" STUDENT 2 -- WORKER A (ANALYZER) -- SILENT HALLUCINATION")
    print(" Domain: Legal Contract Review   |   Corpus: %d analyses, 8 defective" % total)
    print("=" * 74)

    print("\n--- BEFORE: guardrail disabled -------------------------------------")
    print("    no schema, no grounding check, no retry")
    print("    defective analyses passed downstream : %2d / %d  [%s]"
          % (before["escaped"], total, _bar(before["escaped"], total)))
    print("    safe rejections                      : %2d" % before["rejections"])
    print("    LLM calls                            : %2d" % before["llm_calls"])
    print("    errors raised                        :  0   <-- this is the problem")
    print("    escaped defect types                 : %s"
          % ", ".join(sorted(set(before["escaped_defects"]))))

    print("\n--- AFTER: guardrail active (stubborn model, never self-corrects) ---")
    print("    structured output + grounding + 1 retry")
    print("    defective analyses passed downstream : %2d / %d  [%s]"
          % (stubborn["escaped"], total, _bar(stubborn["escaped"], total)))
    print("    safe rejections -> Coordinator       : %2d" % stubborn["rejections"])
    print("    LLM calls                            : %2d  (+%d retries)"
          % (stubborn["llm_calls"], stubborn["llm_calls"] - total))

    print("\n--- AFTER: guardrail active (model self-corrects when told why) -----")
    print("    defective analyses passed downstream : %2d / %d  [%s]"
          % (cooperative["escaped"], total, _bar(cooperative["escaped"], total)))
    print("    repaired on the one allowed retry    : %2d" % cooperative["repaired"])
    print("    safe rejections                      : %2d" % cooperative["rejections"])
    print("    LLM calls                            : %2d  (+%d retries)"
          % (cooperative["llm_calls"], cooperative["llm_calls"] - total))

    print("\n--- HEADLINE -------------------------------------------------------")
    print("    Silent defects reaching Worker B: %d/%d (%.0f%%)  ->  0/%d (0%%)"
          % (before["escaped"], total, 100 * before["escaped"] / total, total))
    print("    Cost of the fix: +%d LLM calls per %d reviews (+%.0f%%)"
          % (stubborn["llm_calls"] - total, total,
             100 * (stubborn["llm_calls"] - total) / total))
    print("=" * 74)
    print()


if __name__ == "__main__":
    main()
