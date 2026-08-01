"""
snippet.py — Student 4 / Worker C (Validator): Downstream Cascade Guardrail

FAILURE MODE
    Worker B (Actor) writes malformed or type-unsafe data into
    `execution_state` / `sanitized_tool_calls`. Worker C and the Reporter
    then treat that payload as trusted: they do arithmetic on
    `executed_count`, iterate `results`, and look up clause IDs. A string
    where an int is required, a missing key, or a redline that cites a
    clause the Analyzer never extracted becomes a TypeError / KeyError /
    ZeroDivisionError mid-graph — or worse, a silently wrong approval.

WHERE THIS SITS IN main_system.py
    Coordinator --(Route B)--> [ Worker B: Actor ]
                                      |
                                      | writes sanitized_tool_calls,
                                      | execution_state
                                      v
                               [ Worker C: Validator ]  <-- this file
                                      |
                                      | on success: validation_notes
                                      | on failure: rejection_flag + rollback
                                      v
                                 Coordinator (Route A or Route C)

THE GUARDRAIL — PROGRAMMATIC ASSERTIONS, NOT PROMPTS
    1. Parse Actor output against a fixed invariant checklist (types, keys,
       count consistency, clause-ID grounding against analysis_payload).
    2. On any invariant failure: set rejection_flag, append
       "validator: <reason>" to rejection_reason_history, populate
       validation_notes / error_log, clear execution_state so the next
       Actor pass cannot reuse poisoned data, and force is_validated=False
       so the Coordinator rolls back to the Analyzer (ARCHITECTURE §5.3).
    3. On success: normalize execution_state for the Reporter
       (redlines_proposed) and clear rejection_flag.

The unguarded helpers below intentionally crash on the same bad payloads —
used only by test_failure.py / the demo video.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from contract import AgentState, ContractAnalysis, clause_reference

logger = logging.getLogger("validator")

#: Keys every successful Actor execution_state must carry.
REQUIRED_EXECUTION_KEYS = (
    "status",
    "executed_count",
    "results",
    "external_action_performed",
)

#: Status values the Validator treats as a completed Actor pass.
SUCCESS_STATUSES = frozenset({"completed"})

#: Status values that mean the Actor already aborted — pass rejection through.
BLOCKED_STATUSES = frozenset({"blocked", "completed_without_guardrail"})


class CascadeValidationError(Exception):
    """Raised when Actor output fails a structural or domain invariant."""


# ==========================================================================
# INVARIANT CHECKS (pure functions — easy to unit-test in isolation)
# ==========================================================================


def _require_dict(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise CascadeValidationError(
            f"{label} must be a dict, got {type(value).__name__}."
        )
    return value


def _assert_execution_shape(execution_state: Dict[str, Any]) -> None:
    missing = [k for k in REQUIRED_EXECUTION_KEYS if k not in execution_state]
    if missing:
        raise CascadeValidationError(
            f"execution_state missing required key(s): {missing}."
        )

    status = execution_state["status"]
    if not isinstance(status, str) or not status.strip():
        raise CascadeValidationError("execution_state.status must be a non-empty string.")

    count = execution_state["executed_count"]
    if type(count) is not int:  # bool is a subclass of int — reject it
        raise CascadeValidationError(
            f"execution_state.executed_count must be int, got {type(count).__name__}."
        )
    if count < 0:
        raise CascadeValidationError("execution_state.executed_count cannot be negative.")

    results = execution_state["results"]
    if not isinstance(results, list):
        raise CascadeValidationError(
            f"execution_state.results must be a list, got {type(results).__name__}."
        )
    if count != len(results):
        raise CascadeValidationError(
            f"executed_count ({count}) does not match len(results) ({len(results)})."
        )

    external = execution_state["external_action_performed"]
    if not isinstance(external, bool):
        raise CascadeValidationError(
            "execution_state.external_action_performed must be bool."
        )
    if external is True:
        # Safety mandate: nothing in this system may claim a real write.
        raise CascadeValidationError(
            "execution_state claims external_action_performed=True — "
            "real infrastructure mutation is forbidden."
        )


def _assert_result_items(results: List[Any]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(results):
        if not isinstance(item, dict):
            raise CascadeValidationError(
                f"results[{index}] must be a dict, got {type(item).__name__}."
            )
        tool = item.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            raise CascadeValidationError(
                f"results[{index}].tool must be a non-empty string."
            )
        clause_id = item.get("clause_id")
        if not isinstance(clause_id, str) or not clause_id.strip():
            raise CascadeValidationError(
                f"results[{index}].clause_id must be a non-empty string."
            )
        if tool == "propose_redline":
            replacement = item.get("replacement_text")
            if not isinstance(replacement, str) or not replacement.strip():
                raise CascadeValidationError(
                    f"results[{index}].replacement_text must be a non-empty string "
                    "for propose_redline."
                )
        normalized.append(item)
    return normalized


def _assert_tool_calls_consistent(
    sanitized_tool_calls: List[Any],
    executed_count: int,
) -> None:
    if not isinstance(sanitized_tool_calls, list):
        raise CascadeValidationError(
            f"sanitized_tool_calls must be a list, got "
            f"{type(sanitized_tool_calls).__name__}."
        )
    for index, call in enumerate(sanitized_tool_calls):
        if not isinstance(call, str) or not call.strip():
            raise CascadeValidationError(
                f"sanitized_tool_calls[{index}] must be a non-empty string."
            )
    if len(sanitized_tool_calls) != executed_count:
        raise CascadeValidationError(
            f"len(sanitized_tool_calls) ({len(sanitized_tool_calls)}) does not "
            f"match executed_count ({executed_count})."
        )


def _analysis_clause_refs(analysis: ContractAnalysis) -> set[str]:
    return {
        clause_reference(clause.clause_id).strip().lower()
        for clause in analysis.clauses
    }


def _assert_redlines_grounded(
    results: List[Dict[str, Any]],
    analysis: ContractAnalysis,
) -> None:
    """Every Actor result must cite a clause the Analyzer actually extracted."""
    known = _analysis_clause_refs(analysis)
    for index, item in enumerate(results):
        ref = clause_reference(item["clause_id"]).strip().lower()
        if ref not in known:
            raise CascadeValidationError(
                f"results[{index}] cites clause_id {item['clause_id']!r}, which "
                "is not present in analysis_payload — refusing to approve a "
                "redline against an unknown clause."
            )


def _assert_redline_targets_risky_clauses(
    results: List[Dict[str, Any]],
    analysis: ContractAnalysis,
) -> None:
    """propose_redline is only meaningful for high/critical clauses."""
    risk_by_ref = {
        clause_reference(c.clause_id).strip().lower(): c.risk_level.value
        for c in analysis.clauses
    }
    for index, item in enumerate(results):
        if item.get("tool") != "propose_redline":
            continue
        ref = clause_reference(item["clause_id"]).strip().lower()
        risk = risk_by_ref.get(ref)
        if risk not in {"high", "critical"}:
            raise CascadeValidationError(
                f"results[{index}] proposes a redline for clause "
                f"{item['clause_id']!r} at risk_level={risk!r}; only high/"
                "critical clauses may be redlined."
            )


def _assert_high_risk_clauses_addressed(
    results: List[Dict[str, Any]],
    analysis: ContractAnalysis,
) -> None:
    """A 'completed' Actor pass must cover every high/critical clause.

    Otherwise the Reporter would stamp a full approval while the uncapped
    indemnity (etc.) never received a redline — a silent cascade of a
    different kind.
    """
    high_risk_refs = {
        clause_reference(c.clause_id).strip().lower()
        for c in analysis.clauses
        if c.risk_level.value in {"high", "critical"}
    }
    if not high_risk_refs:
        return

    addressed = {
        clause_reference(item["clause_id"]).strip().lower()
        for item in results
        if item.get("tool") == "propose_redline"
    }
    missing = sorted(high_risk_refs - addressed)
    if missing:
        raise CascadeValidationError(
            f"completed Actor pass left high/critical clause(s) unaddressed: "
            f"{missing}."
        )


def collect_invariant_violations(state: AgentState) -> List[str]:
    """Return every invariant failure as a human-readable reason string.

    Returns an empty list when the state is safe to forward to the Reporter.
    """
    violations: List[str] = []

    try:
        analysis = ContractAnalysis.model_validate(state.analysis_payload)
    except ValidationError as exc:
        return [f"analysis_payload does not match ContractAnalysis: {exc}"]

    if not state.is_validated:
        violations.append(
            "is_validated is False — Actor output cannot be approved without "
            "a grounded Analyzer pass."
        )

    try:
        execution_state = _require_dict(state.execution_state, "execution_state")
        _assert_execution_shape(execution_state)

        status = execution_state["status"]
        if status in BLOCKED_STATUSES:
            # Actor already refused; surface as a validation rejection so the
            # Coordinator's rollback / escalation path engages uniformly.
            violations.append(
                f"Actor reported status={status!r}; refusing to approve "
                "blocked or unguarded execution for reporting."
            )
            return violations

        if status not in SUCCESS_STATUSES:
            violations.append(
                f"execution_state.status={status!r} is not an approvable "
                f"status (expected one of {sorted(SUCCESS_STATUSES)})."
            )
            return violations

        results = _assert_result_items(execution_state["results"])
        _assert_tool_calls_consistent(
            state.sanitized_tool_calls,
            execution_state["executed_count"],
        )
        _assert_redlines_grounded(results, analysis)
        _assert_redline_targets_risky_clauses(results, analysis)
        _assert_high_risk_clauses_addressed(results, analysis)
    except CascadeValidationError as exc:
        violations.append(str(exc))

    return violations


# ==========================================================================
# GUARDED NODE
# ==========================================================================


def _reject(
    state: AgentState,
    reasons: List[str],
) -> AgentState:
    """Apply the §5.3 rollback contract onto state and return it."""
    reason = "; ".join(reasons)
    tagged = f"validator: {reason}"

    state.rejection_flag = True
    state.is_validated = False
    state.execution_state = {}
    state.sanitized_tool_calls = []
    state.validation_notes = reason
    state.error_log = tagged
    state.rejection_reason_history.append(tagged)

    logger.warning("validator rejected Actor output: %s", reason)
    return state


def _approve(state: AgentState, execution_state: Dict[str, Any]) -> AgentState:
    """Normalize successful Actor output for the Reporter and clear rejects."""
    redline_count = sum(
        1
        for item in execution_state.get("results", [])
        if isinstance(item, dict) and item.get("tool") == "propose_redline"
    )
    # Reporter (Person 1) historically reads redlines_proposed; keep both.
    normalized = dict(execution_state)
    normalized["redlines_proposed"] = redline_count

    state.execution_state = normalized
    state.rejection_flag = False
    state.validation_notes = (
        f"approved: {execution_state['executed_count']} tool result(s), "
        f"{redline_count} redline(s); structural and clause-grounding "
        "invariants passed."
    )
    # Do not clear error_log here if an earlier node left a non-blocking note;
    # a successful validation is the authority for the reporting path.
    if state.error_log and state.error_log.startswith("validator:"):
        state.error_log = None

    logger.info("validator approved Actor output")
    return state


def run_validator(state: AgentState) -> AgentState:
    """Validate / sanitize Actor output. AgentState -> AgentState."""
    state = state.model_copy(deep=True)

    if not state.execution_state:
        return _reject(
            state,
            ["execution_state is empty — nothing for the Validator to approve."],
        )

    violations = collect_invariant_violations(state)
    if violations:
        return _reject(state, violations)

    return _approve(state, dict(state.execution_state))


def validator_node(state: AgentState) -> AgentState:
    """LangGraph-compatible drop-in for main_system.build_graph(validator=...)."""
    return run_validator(state)


# ==========================================================================
# UNGUARDED DOWNSTREAM — reproduction only, never wired into main_system.py
# ==========================================================================


def downstream_report_NO_GUARDRAIL(state: AgentState) -> str:
    """Naive Reporter-style consumer that trusts Actor output blindly.

    This is the cascade: malformed types / missing keys become runtime
    exceptions instead of a clean rejection_flag. Used by test_failure.py.
    """
    execution = state.execution_state
    count = execution["executed_count"]
    results = execution["results"]

    # IndexError / wrong-length trust: believe executed_count over len(results).
    _ = results[count - 1] if count else None

    # TypeError if count is a string; ZeroDivisionError if count == 0.
    average_chars = (
        sum(len(r.get("replacement_text", "")) for r in results) / count
    )

    clause_ids = [r["clause_id"] for r in results]
    known = {c["clause_id"] for c in state.analysis_payload["clauses"]}
    orphan = [cid for cid in clause_ids if cid not in known]
    if orphan:
        # Silent corruption becomes a hard crash once a later stage notices.
        raise RuntimeError(
            f"CASCADE: reporter received redline(s) for unknown clause(s): {orphan}"
        )

    return (
        f"UNSAFE REPORT: {count} redlines, avg_chars={average_chars:.1f}, "
        f"orphan_clauses={orphan}"
    )


def run_validator_NO_GUARDRAIL(state: AgentState) -> AgentState:
    """Pass-through 'validator' that performs no checks — then hits the crash.

    Mimics wiring Actor output straight into a downstream consumer. On the
    happy path it stamps a note; on malformed data it raises (cascade).
    """
    state = state.model_copy(deep=True)
    # Touch the dangerous fields the same way a real downstream node would.
    _ = downstream_report_NO_GUARDRAIL(state)
    state.rejection_flag = False
    state.validation_notes = "unguarded: trusted Actor output without checks"
    return state


def build_malformed_actor_state(
    *,
    defect: str = "string_count",
    base: Optional[AgentState] = None,
) -> AgentState:
    """Factory for deterministic cascade fixtures used in tests and demos."""
    if base is None:
        base = AgentState(
            raw_input=(
                "MASTER SERVICES AGREEMENT\n"
                "Counterparty: Globex Industries Ltd\n\n"
                "Section 4.2 Indemnification\n"
                "Globex Industries Ltd shall indemnify Acme Corporation for all "
                "losses arising from breach, negligence, or misconduct without "
                "limitation.\n\n"
                "Section 7 Termination\n"
                "Either party may terminate this agreement for convenience with "
                "thirty days prior written notice to the other party."
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
                    },
                    {
                        "clause_id": "Section 7 Termination",
                        "clause_type": "termination",
                        "verbatim_quote": (
                            "Either party may terminate this agreement for "
                            "convenience with thirty days prior written notice "
                            "to the other party."
                        ),
                        "risk_level": "medium",
                        "risk_rationale": (
                            "Convenience termination is reciprocal and notice-"
                            "based; residual operational risk is moderate."
                        ),
                    },
                ],
            },
        )

    state = base.model_copy(deep=True)

    if defect == "string_count":
        # Classic cascade: arithmetic / division on a string count.
        state.sanitized_tool_calls = [
            "propose_redline({'clause_id': 'Section 4.2 Indemnification'})"
        ]
        state.execution_state = {
            "status": "completed",
            "executed_count": "1",  # wrong type
            "results": [
                {
                    "status": "mock_success",
                    "tool": "propose_redline",
                    "clause_id": "Section 4.2 Indemnification",
                    "replacement_text": "MOCK REDLINE: cap indemnity at direct damages.",
                    "reason": "uncapped indemnity",
                    "external_action_performed": False,
                }
            ],
            "external_action_performed": False,
        }
    elif defect == "orphan_clause":
        # Redline cites a clause the Analyzer never extracted.
        state.sanitized_tool_calls = [
            "propose_redline({'clause_id': 'Section 99 Fabricated'})"
        ]
        state.execution_state = {
            "status": "completed",
            "executed_count": 1,
            "results": [
                {
                    "status": "mock_success",
                    "tool": "propose_redline",
                    "clause_id": "Section 99 Fabricated",
                    "replacement_text": "MOCK REDLINE: invented clause edit.",
                    "reason": "hallucinated target",
                    "external_action_performed": False,
                }
            ],
            "external_action_performed": False,
        }
    elif defect == "count_mismatch":
        state.sanitized_tool_calls = ["propose_redline(...)"]
        state.execution_state = {
            "status": "completed",
            "executed_count": 3,  # lies about result cardinality
            "results": [
                {
                    "status": "mock_success",
                    "tool": "propose_redline",
                    "clause_id": "Section 4.2 Indemnification",
                    "replacement_text": "MOCK REDLINE: cap indemnity.",
                    "external_action_performed": False,
                }
            ],
            "external_action_performed": False,
        }
    elif defect == "zero_divide":
        state.sanitized_tool_calls = []
        state.execution_state = {
            "status": "completed",
            "executed_count": 0,
            "results": [],
            "external_action_performed": False,
        }
    elif defect == "healthy":
        state.sanitized_tool_calls = [
            "propose_redline({'clause_id': 'Section 4.2 Indemnification'})"
        ]
        state.execution_state = {
            "status": "completed",
            "executed_count": 1,
            "results": [
                {
                    "status": "mock_success",
                    "tool": "propose_redline",
                    "clause_id": "Section 4.2 Indemnification",
                    "replacement_text": "MOCK REDLINE: cap indemnity at direct damages.",
                    "reason": "uncapped indemnity",
                    "external_action_performed": False,
                }
            ],
            "external_action_performed": False,
        }
    else:
        raise ValueError(f"Unknown defect fixture: {defect!r}")

    return state


def summarize_cascade_metrics(
    *,
    crashes_without: int,
    crashes_with: int,
    rejections_with: int,
    trials: int,
) -> Dict[str, Any]:
    """Small helper so METRICS.md / demos share one number format."""
    return {
        "trials": trials,
        "downstream_crashes_without_guardrail": crashes_without,
        "downstream_crashes_with_guardrail": crashes_with,
        "clean_rejections_with_guardrail": rejections_with,
        "crash_rate_without": crashes_without / trials if trials else 0.0,
        "crash_rate_with": crashes_with / trials if trials else 0.0,
    }


def evaluate_fixture(defect: str) -> Tuple[bool, bool, Optional[str]]:
    """Run one fixture through unguarded + guarded paths.

    Returns (unguarded_crashed, guarded_rejected_cleanly, reject_reason).
    """
    malformed = build_malformed_actor_state(defect=defect)

    unguarded_crashed = False
    try:
        run_validator_NO_GUARDRAIL(malformed)
    except Exception:
        unguarded_crashed = True

    guarded = run_validator(malformed)
    rejected_cleanly = (
        guarded.rejection_flag is True
        and guarded.execution_state == {}
        and guarded.validation_notes is not None
    )
    return unguarded_crashed, rejected_cleanly, guarded.validation_notes
