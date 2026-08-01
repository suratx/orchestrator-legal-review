"""
contract.py — SHARED STATE CONTRACT (DRAFT, NOT YET FROZEN)

Owner: Person 2 (Contract & Analyzer). This file is Person 1's working draft,
built directly from ARCHITECTURE_DESIGN.md Section 6, so the Coordinator can
be developed against a real schema before the team freeze meeting.

DO NOT treat this as final. Once Person 2 reviews, adjusts, and commits the
frozen version, this file must be replaced with theirs and no node should
add fields outside of it without a team-wide review (see assignment: Contract
Freeze Milestone).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentState(BaseModel):
    # --- task identity ---
    task_domain: str = "legal_contract_review"
    raw_input: str

    # --- loop / routing control (Student 1 / Coordinator scope) ---
    round_number: int = 0
    next_route: Optional[str] = None  # "analyzer" | "actor" | "reporter" | "partial_output"

    # --- validation & rejection state ---
    is_validated: bool = False
    rejection_flag: bool = False
    rejection_reason_history: List[str] = Field(default_factory=list)
    error_log: Optional[str] = None

    # --- payloads produced by workers ---
    analysis_payload: Dict[str, Any] = Field(default_factory=dict)      # Analyzer output (clauses + risk tags)
    sanitized_tool_calls: List[str] = Field(default_factory=list)       # Actor output (approved redline actions)
    execution_state: Dict[str, Any] = Field(default_factory=dict)       # Actor execution result
    validation_notes: Optional[str] = None                              # Validator output
    final_report: Optional[str] = None                                  # Reporter output

    # --- context management (Student 6 / Person 5 scope) ---
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    token_count: int = 0

    # Contract-first design: reject unknown fields so no node can silently
    # smuggle state outside the agreed schema.
    model_config = ConfigDict(extra="forbid")


# --- Coordinator routing constants (kept here so all nodes agree on the same labels) ---
ROUTE_ANALYZER = "analyzer"
ROUTE_ACTOR = "actor"
ROUTE_REPORTER = "reporter"
ROUTE_PARTIAL_OUTPUT = "partial_output"

MAX_ROUNDS = 5
