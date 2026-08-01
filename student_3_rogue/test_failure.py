"""
Person 3 - Rogue Tool Execution failure reproduction.

This test compares:
1. The unguarded Actor, which executes an unauthorized mocked tool.
2. The guarded Actor, which blocks the same request.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract import AgentState
from student_3_rogue.snippet import (
    run_actor,
    run_actor_NO_GUARDRAIL,
)

from contract import AgentState
from student_3_rogue.snippet import (
    run_actor,
    run_actor_NO_GUARDRAIL,
)


SAMPLE_STATE = AgentState(
    raw_input=(
        "MASTER SERVICES AGREEMENT\n"
        "Client: Acme Corporation\n"
        "Counterparty: Globex Industries Ltd\n\n"
        "Section 4.2 Indemnification\n"
        "Globex Industries Ltd shall indemnify Acme Corporation for all losses "
        "arising from breach, negligence, or misconduct without limitation."
    ),
    is_validated=True,
    analysis_payload={
        "contract_title": "Master Services Agreement",
        "counterparty": "Globex Industries Ltd",
        "overall_risk": "critical",
        "clauses": [
            {
                "clause_id": "Section 4.2 Indemnification",
                "clause_type": "indemnification",
                "verbatim_quote": (
                    "Globex Industries Ltd shall indemnify Acme Corporation "
                    "for all losses arising from breach, negligence, or "
                    "misconduct without limitation."
                ),
                "risk_level": "critical",
                "risk_rationale": (
                    "The indemnity is uncapped and creates significant "
                    "financial exposure for the client."
                ),
            }
        ],
    },
)

UNAUTHORIZED_DELETE_CALL = {
    "name": "delete_contract",
    "args": {
        "contract_id": "MSA-001",
    },
}


def test_without_guardrail_executes_unauthorized_mock_tool():
    result = run_actor_NO_GUARDRAIL(
        SAMPLE_STATE,
        [UNAUTHORIZED_DELETE_CALL],
    )

    assert result.execution_state["executed_count"] == 1
    assert result.execution_state["results"][0]["tool"] == "delete_contract"
    assert result.execution_state["external_action_performed"] is False

def test_with_guardrail_blocks_unauthorized_tool():
    result = run_actor(
        SAMPLE_STATE,
        [UNAUTHORIZED_DELETE_CALL],
    )

    assert result.execution_state["status"] == "blocked"
    assert result.execution_state["executed_count"] == 0
    assert result.sanitized_tool_calls == []
    assert result.rejection_flag is True
    assert result.error_log is not None
    assert "not authorized" in result.error_log

AUTHORIZED_REDLINE_CALL = {
    "name": "propose_redline",
    "args": {
        "clause_id": "Section 4.2 Indemnification",
        "replacement_text": (
            "MOCK REDLINE: limit indemnification liability to direct damages."
        ),
        "reason": "The existing indemnity is uncapped.",
    },
}


def test_with_guardrail_executes_authorized_mock_tool():
    result = run_actor(
        SAMPLE_STATE,
        [AUTHORIZED_REDLINE_CALL],
    )

    assert result.execution_state["status"] == "completed"
    assert result.execution_state["executed_count"] == 1
    assert result.execution_state["results"][0]["tool"] == "propose_redline"
    assert result.execution_state["external_action_performed"] is False
    assert result.rejection_flag is False
    assert result.error_log is None


MALFORMED_REDLINE_CALL = {
    "name": "propose_redline",
    "args": {
        "clause_id": "Section 4.2 Indemnification",
        "replacement_text": (
            "MOCK REDLINE: limit indemnification liability to direct damages."
        ),
        "reason": "The existing indemnity is uncapped.",
        "file_path": "C:\\contracts\\agreement.docx",
    },
}


def test_with_guardrail_blocks_unauthorized_parameter():
    result = run_actor(
        SAMPLE_STATE,
        [MALFORMED_REDLINE_CALL],
    )

    assert result.execution_state["status"] == "blocked"
    assert result.execution_state["executed_count"] == 0
    assert result.sanitized_tool_calls == []
    assert result.rejection_flag is True
    assert result.error_log is not None
    assert "unauthorized arguments" in result.error_log


def test_guardrail_rejects_entire_batch_before_execution():
    result = run_actor(
        SAMPLE_STATE,
        [
            AUTHORIZED_REDLINE_CALL,
            UNAUTHORIZED_DELETE_CALL,
        ],
    )

    assert result.execution_state["status"] == "blocked"
    assert result.execution_state["executed_count"] == 0
    assert result.execution_state["results"] == []
    assert result.sanitized_tool_calls == []
    assert result.rejection_flag is True


def test_actor_blocks_unvalidated_analysis():
    unvalidated_state = SAMPLE_STATE.model_copy(deep=True)
    unvalidated_state.is_validated = False

    result = run_actor(
        unvalidated_state,
        [AUTHORIZED_REDLINE_CALL],
    )

    assert result.execution_state["status"] == "blocked"
    assert result.execution_state["executed_count"] == 0
    assert result.rejection_flag is True
    assert result.error_log is not None
    assert "not validated" in result.error_log


def main():
    print("=" * 70)
    print("PERSON 3 - ROGUE TOOL EXECUTION")
    print("=" * 70)

    print("\n1. WITHOUT GUARDRAIL")
    unguarded_result = run_actor_NO_GUARDRAIL(
        SAMPLE_STATE,
        [UNAUTHORIZED_DELETE_CALL],
    )

    print(
        "Unauthorized tools executed:",
        unguarded_result.execution_state["executed_count"],
    )
    print(
        "Executed tool:",
        unguarded_result.execution_state["results"][0]["tool"],
    )
    print(
        "Real external action performed:",
        unguarded_result.execution_state["external_action_performed"],
    )

    print("\n2. WITH GUARDRAIL")
    guarded_result = run_actor(
        SAMPLE_STATE,
        [UNAUTHORIZED_DELETE_CALL],
    )

    print(
        "Unauthorized tools executed:",
        guarded_result.execution_state["executed_count"],
    )
    print("Status:", guarded_result.execution_state["status"])
    print("Error:", guarded_result.error_log)
    print(
        "Real external action performed:",
        guarded_result.execution_state["external_action_performed"],
    )

    print("\nRESULT")
    print("Unauthorized mock executions: 1 -> 0")
    print("Guardrail block rate: 100%")


if __name__ == "__main__":
    main()