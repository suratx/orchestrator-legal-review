"""
Person 3 - Actor and Tool Security

This module safely validates mocked legal-review tool calls before execution.
No real external action is allowed.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from pydantic import ValidationError

from contract import AgentState, ContractAnalysis

class InvalidToolCallException(Exception):
    """Raised when a requested tool call is unauthorized or malformed."""

def mock_propose_redline(
    clause_id: str,
    replacement_text: str,
    reason: str = "",
) -> Dict[str, Any]:
    """Return a proposed redline without modifying any real document."""
    return {
        "status": "mock_success",
        "tool": "propose_redline",
        "clause_id": clause_id,
        "replacement_text": replacement_text,
        "reason": reason,
        "external_action_performed": False,
    }

def mock_record_review_note(
    clause_id: str,
    note: str,
) -> Dict[str, Any]:
    """Record a review note without writing to any external system."""
    return {
        "status": "mock_success",
        "tool": "record_review_note",
        "clause_id": clause_id,
        "note": note,
        "external_action_performed": False,
    }

SAFE_TOOL_REGISTRY: Dict[str, Callable[..., Dict[str, Any]]] = {
    "propose_redline": mock_propose_redline,
    "record_review_note": mock_record_review_note,
}

TOOL_PERMISSION_MATRIX: Dict[str, Dict[str, Dict[str, type]]] = {
    "propose_redline": {
        "required": {
            "clause_id": str,
            "replacement_text": str,
        },
        "optional": {
            "reason": str,
        },
    },
    "record_review_note": {
        "required": {
            "clause_id": str,
            "note": str,
        },
        "optional": {},
    },
}

REDLINE_ALLOWED_RISK_LEVELS = {"high", "critical"}

def validate_analysis_state(state: AgentState) -> ContractAnalysis:
    """Ensure the Actor receives trusted Analyzer output."""
    if not state.is_validated:
        raise InvalidToolCallException(
            "Actor cannot run because the Analyzer output is not validated."
        )

    try:
        return ContractAnalysis.model_validate(state.analysis_payload)
    except ValidationError as exc:
        raise InvalidToolCallException(
            f"Analyzer payload does not match the frozen contract: {exc}"
        ) from exc


def find_clause(analysis: ContractAnalysis, clause_id: str):
    """Find the requested clause inside the validated analysis."""
    normalized_id = clause_id.strip().lower()

    for clause in analysis.clauses:
        if clause.clause_id.strip().lower() == normalized_id:
            return clause

    raise InvalidToolCallException(
        f"Clause {clause_id!r} does not exist in the validated analysis."
    )


def validate_parameters(
    tool_name: str,
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate required, optional, unknown, and incorrectly typed arguments."""
    if not isinstance(arguments, dict):
        raise InvalidToolCallException(
            f"Arguments for {tool_name!r} must be a dictionary."
        )

    rules = TOOL_PERMISSION_MATRIX[tool_name]
    required = rules["required"]
    optional = rules["optional"]

    missing = set(required) - set(arguments)
    if missing:
        raise InvalidToolCallException(
            f"Tool {tool_name!r} is missing required arguments: {sorted(missing)}"
        )

    allowed = set(required) | set(optional)
    unknown = set(arguments) - allowed
    if unknown:
        raise InvalidToolCallException(
            f"Tool {tool_name!r} received unauthorized arguments: {sorted(unknown)}"
        )

    expected_types = {**required, **optional}

    for argument_name, argument_value in arguments.items():
        expected_type = expected_types[argument_name]

        if not isinstance(argument_value, expected_type):
            raise InvalidToolCallException(
                f"Argument {argument_name!r} for tool {tool_name!r} "
                f"must be {expected_type.__name__}."
            )

        if isinstance(argument_value, str) and not argument_value.strip():
            raise InvalidToolCallException(
                f"Argument {argument_name!r} cannot be empty."
            )

    return arguments.copy()


def validate_tool_call(
    state: AgentState,
    analysis: ContractAnalysis,
    tool_call: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate one model-requested tool call before execution."""
    if not isinstance(tool_call, dict):
        raise InvalidToolCallException("Each tool call must be a dictionary.")

    tool_name = tool_call.get("name")
    arguments = tool_call.get("args")

    if not isinstance(tool_name, str) or not tool_name.strip():
        raise InvalidToolCallException(
            "Every tool call must contain a non-empty tool name."
        )

    if tool_name not in SAFE_TOOL_REGISTRY:
        raise InvalidToolCallException(
            f"Tool {tool_name!r} is not authorized."
        )

    validated_arguments = validate_parameters(tool_name, arguments)

    clause = find_clause(
        analysis,
        validated_arguments["clause_id"],
    )

    if (
        tool_name == "propose_redline"
        and clause.risk_level.value not in REDLINE_ALLOWED_RISK_LEVELS
    ):
        raise InvalidToolCallException(
            f"Redlining is not permitted for clause {clause.clause_id!r} "
            f"because its risk level is {clause.risk_level.value!r}."
        )

    return {
        "name": tool_name,
        "args": validated_arguments,
    }


def validate_tool_calls(
    state: AgentState,
    requested_tool_calls: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Validate every requested call before allowing any execution."""
    if not isinstance(requested_tool_calls, list):
        raise InvalidToolCallException(
            "Requested tool calls must be provided as a list."
        )

    analysis = validate_analysis_state(state)

    validated_calls: List[Dict[str, Any]] = []

    for tool_call in requested_tool_calls:
        validated_calls.append(
            validate_tool_call(
                state,
                analysis,
                tool_call,
            )
        )

    return validated_calls


def execute_validated_tool_calls(
    validated_calls: List[Dict[str, Any]],
) -> tuple[List[str], List[Dict[str, Any]]]:
    """Execute only calls that already passed every security check."""
    sanitized_calls: List[str] = []
    execution_results: List[Dict[str, Any]] = []

    for tool_call in validated_calls:
        tool_name = tool_call["name"]
        arguments = tool_call["args"]

        tool_function = SAFE_TOOL_REGISTRY[tool_name]
        result = tool_function(**arguments)

        sanitized_calls.append(
            f"{tool_name}({arguments})"
        )
        execution_results.append(result)

    return sanitized_calls, execution_results


def run_actor(
    state: AgentState,
    requested_tool_calls: List[Dict[str, Any]],
) -> AgentState:
    """Validate requested calls, then execute only approved mock tools."""
    updated_state = state.model_copy(deep=True)

    try:
        validated_calls = validate_tool_calls(
            updated_state,
            requested_tool_calls,
        )

        sanitized_calls, execution_results = execute_validated_tool_calls(
            validated_calls
        )

    except InvalidToolCallException as exc:
        reason = str(exc)

        updated_state.sanitized_tool_calls = []
        updated_state.execution_state = {
            "status": "blocked",
            "executed_count": 0,
            "results": [],
            "external_action_performed": False,
        }
        updated_state.error_log = f"BLOCKED: {reason}"
        updated_state.rejection_flag = True
        updated_state.rejection_reason_history.append(
            f"actor: {reason}"
        )

        return updated_state

    updated_state.sanitized_tool_calls = sanitized_calls
    updated_state.execution_state = {
        "status": "completed",
        "executed_count": len(execution_results),
        "results": execution_results,
        "external_action_performed": False,
    }
    updated_state.error_log = None
    updated_state.rejection_flag = False

    return updated_state


def build_requested_tool_calls(
    state: AgentState,
) -> List[Dict[str, Any]]:
    """Create mocked redline requests for high- and critical-risk clauses."""
    analysis = validate_analysis_state(state)
    requested_calls: List[Dict[str, Any]] = []

    for clause in analysis.clauses:
        if clause.risk_level.value in REDLINE_ALLOWED_RISK_LEVELS:
            requested_calls.append(
                {
                    "name": "propose_redline",
                    "args": {
                        "clause_id": clause.clause_id,
                        "replacement_text": (
                            "MOCK REDLINE: revise this clause to reduce "
                            "the identified contractual risk."
                        ),
                        "reason": clause.risk_rationale,
                    },
                }
            )

    return requested_calls


def actor_node(state: AgentState) -> AgentState:
    """LangGraph-compatible Actor node: AgentState -> AgentState."""
    requested_tool_calls = build_requested_tool_calls(state)

    return run_actor(
        state,
        requested_tool_calls,
    )


def mock_delete_contract(
    contract_id: str,
) -> Dict[str, Any]:
    """Simulate a dangerous action without deleting or modifying anything."""
    return {
        "status": "mock_dangerous_action",
        "tool": "delete_contract",
        "contract_id": contract_id,
        "message": "CRITICAL: CONTRACT DELETION TARGETED - MOCK EXECUTION ONLY",
        "external_action_performed": False,
    }


UNSAFE_TOOL_REGISTRY: Dict[str, Callable[..., Dict[str, Any]]] = {
    "propose_redline": mock_propose_redline,
    "record_review_note": mock_record_review_note,
    "delete_contract": mock_delete_contract,
}

def run_actor_NO_GUARDRAIL(
    state: AgentState,
    requested_tool_calls: List[Dict[str, Any]],
) -> AgentState:
    """Execute requested mock tools without checking permissions or parameters."""
    updated_state = state.model_copy(deep=True)

    results: List[Dict[str, Any]] = []
    executed_calls: List[str] = []

    for tool_call in requested_tool_calls:
        tool_name = tool_call["name"]
        arguments = tool_call["args"]

        tool_function = UNSAFE_TOOL_REGISTRY[tool_name]
        result = tool_function(**arguments)

        executed_calls.append(
            f"{tool_name}({arguments})"
        )
        results.append(result)

    updated_state.sanitized_tool_calls = executed_calls
    updated_state.execution_state = {
        "status": "completed_without_guardrail",
        "executed_count": len(results),
        "results": results,
        "external_action_performed": False,
    }

    return updated_state