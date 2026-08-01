"""
contract.py -- THE MANDATORY SHARED STATE CONTRACT

Owner:   Person 2 (Contract & Analyzer)
Status:  FROZEN -- v1.0.0
Source:  Formalized from ARCHITECTURE_DESIGN.md Section 6 (Person 1's draft),
         with the two open freeze questions resolved (see CONTRACT_FREEZE_NOTES.md).

FREEZE RULE (assignment: "Contract Freeze Milestone")
    Once this file is committed it is frozen. No node may add, rename, or
    retype a field here without a documented team-wide review. `AgentState`
    sets `extra="forbid"` so a node that tries to smuggle an undeclared field
    into the state fails loudly at runtime instead of silently corrupting
    downstream workers.

WHAT CHANGED FROM THE DRAFT (all four items reviewed by the team at freeze):
    1. KEPT `rejection_reason_history` (open question 1). It is not optional:
       ARCHITECTURE_DESIGN.md 5.3 requires "same rejection reason twice in a
       row -> escalate immediately", which is un-implementable from a single
       `error_log` string.
    2. ADDED nested models `ClauseRisk` / `ContractAnalysis` (open question 2).
       `analysis_payload` stays a `Dict[str, Any]` on AgentState so Person 1's
       Coordinator and Reporter are untouched -- but the Analyzer may only
       populate it via `ContractAnalysis.model_dump()`. A loose dict cannot be
       fed to `.with_structured_output()`, which is the entire guardrail for
       failure mode 2.
    3. ADDED `analysis_retry_count` so the Analyzer's one-shot self-correction
       is observable in state (see CONTRACT_FREEZE_NOTES.md for why the retry
       is in-node and does NOT consume a `round_number`, contra draft 5.2).
    4. CLARIFIED the semantics of `rejection_flag`: it means "the current state
       is not acceptable, roll back", set by ANY node -- not only the Validator.

WHY THE GROUNDING VALIDATORS LIVE HERE AND NOT IN THE ANALYZER
    Failure mode 2 is *silent* hallucination: output that is structurally
    perfect but factually invented. Structure alone (types, required fields)
    cannot catch it. So the contract itself carries the semantic invariants
    -- verbatim quotes must exist in the source contract, clause IDs must be
    real, overall risk must agree with the per-clause risks. Contract-first
    design means these checks are shared law, not one node's private opinion.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

CONTRACT_VERSION = "1.0.0"


# ==========================================================================
# 1. DOMAIN ENUMS -- the "critical domain identifiers" a hallucinating LLM
#    silently drops or invents. Free-text strings here would make failure
#    mode 2 undetectable, so both are closed sets.
# ==========================================================================


class ClauseType(str, Enum):
    """Closed set of reviewable clause categories for this domain."""

    INDEMNIFICATION = "indemnification"
    LIMITATION_OF_LIABILITY = "limitation_of_liability"
    TERMINATION = "termination"
    CONFIDENTIALITY = "confidentiality"
    GOVERNING_LAW = "governing_law"
    PAYMENT_TERMS = "payment_terms"
    IP_ASSIGNMENT = "ip_assignment"
    NON_COMPETE = "non_compete"
    WARRANTY = "warranty"
    DATA_PROTECTION = "data_protection"
    OTHER = "other"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


#: Ordering used for the "overall risk must equal the worst clause risk"
#: invariant. An LLM that flags a CRITICAL indemnification clause and then
#: summarises the contract as "low risk" is the classic silent failure.
RISK_ORDER: Dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

#: A clause identifier must *begin* with a real section reference. A trailing
#: heading is allowed ("Section 4.2 Indemnification") because that is how the
#: reference is printed in most contracts and rejecting it would be a false
#: positive, not a caught hallucination. Group `ref` is the part that gets
#: grounded against the source text.
CLAUSE_ID_PATTERN = re.compile(
    r"^(?P<ref>(?:§|Section|Sec\.|Clause|Article|Art\.)\s*\d+(?:\.\d+)*)(?:\b.*)?$",
    re.IGNORECASE,
)


def clause_reference(clause_id: str) -> str:
    """Extract the bare section reference from a clause_id.

    'Section 4.2 Indemnification' -> 'Section 4.2'. Used for grounding so a
    clause whose heading the model reworded is still checked on the part that
    actually identifies it.
    """
    match = CLAUSE_ID_PATTERN.match(clause_id.strip())
    return match.group("ref").strip() if match else clause_id.strip()

#: Quotes shorter than this are too generic to ground reliably against the
#: source text (e.g. "the Parties" appears everywhere).
MIN_QUOTE_CHARS = 20

#: Exactly one automated self-correction attempt, per the assignment text
#: ("route the error exception text back to the node once").
MAX_ANALYZER_RETRIES = 1


#: Unicode punctuation an LLM substitutes for ASCII when "quoting verbatim",
#: plus the stray backslash escapes that leak out of JSON generation. Measured
#: against live llama3.2 output: without this table the grounding check raises
#: false hallucination alarms on faithfully-quoted text (see METRICS.md,
#: "False positives").
_PUNCTUATION_EQUIVALENTS = {
    "‘": "'", "’": "'", "‚": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", "…": "...",
    "\\'": "'", '\\"': '"',
}


def normalize_text(text: str) -> str:
    """Whitespace/case/punctuation-insensitive form for grounding comparisons.

    LLMs re-wrap, re-capitalize and re-punctuate quoted text even when they
    are quoting faithfully. Normalizing keeps the guardrail from firing on
    cosmetic differences -- a guardrail with false positives gets switched
    off -- while still catching genuinely invented text, because inventing a
    clause changes words, not just typography.
    """
    for variant, canonical in _PUNCTUATION_EQUIVALENTS.items():
        text = text.replace(variant, canonical)
    return re.sub(r"\s+", " ", text).strip().lower()


# ==========================================================================
# 2. ANALYZER STRUCTURED-OUTPUT SCHEMA (Worker A / Person 2)
#    This is the object handed to `.with_structured_output(...)`.
# ==========================================================================


class ClauseRisk(BaseModel):
    """One extracted clause plus its risk assessment.

    Every field is required. There is deliberately no default anywhere in
    this model: a default would let the LLM omit a critical identifier and
    still produce a "valid" object, which is exactly failure mode 2.
    """

    clause_id: str = Field(
        description="The clause's section reference exactly as it appears in "
        "the contract, e.g. 'Section 4.2' or '§7'.",
    )
    clause_type: ClauseType = Field(
        description="Which category of clause this is. Must be one of the "
        "allowed values.",
    )
    # `min_length` is declared on the Field (not only in the validator below)
    # so it is exported into the JSON Schema that Ollama's structured-output
    # decoder actually constrains generation against. Constraints that live
    # only in a `field_validator` are invisible to the model and can only be
    # caught after the fact -- putting them in the schema stops the empty
    # string being generated in the first place.
    verbatim_quote: str = Field(
        min_length=MIN_QUOTE_CHARS,
        description="An exact, character-for-character quote of the clause "
        "text copied from the contract. Do not paraphrase or summarize.",
    )
    risk_level: RiskLevel = Field(
        description="Risk this clause poses to our client.",
    )
    risk_rationale: str = Field(
        min_length=MIN_QUOTE_CHARS,
        description="One or two sentences explaining why this clause carries "
        "that risk level. Must never be empty.",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("clause_id")
    @classmethod
    def _clause_id_looks_real(cls, value: str) -> str:
        value = value.strip()
        if not CLAUSE_ID_PATTERN.match(value):
            raise ValueError(
                f"clause_id {value!r} does not start with a valid section "
                "reference. Expected a form like 'Section 4.2', '§7' or "
                "'Article 3', optionally followed by the clause heading."
            )
        return value

    @field_validator("verbatim_quote")
    @classmethod
    def _quote_is_substantial(cls, value: str) -> str:
        value = value.strip()
        if len(value) < MIN_QUOTE_CHARS:
            raise ValueError(
                f"verbatim_quote is only {len(value)} characters; at least "
                f"{MIN_QUOTE_CHARS} are required so the quote can be grounded "
                "against the source contract."
            )
        return value

    @field_validator("risk_rationale")
    @classmethod
    def _rationale_is_substantial(cls, value: str) -> str:
        value = value.strip()
        if len(value) < MIN_QUOTE_CHARS:
            raise ValueError(
                "risk_rationale is too short to be a real justification."
            )
        return value


class ContractAnalysis(BaseModel):
    """Worker A's complete output for one contract."""

    contract_title: str = Field(
        description="The title of the agreement as written in the document.",
    )
    counterparty: str = Field(
        description="The other party to the agreement (not our client).",
    )
    clauses: List[ClauseRisk] = Field(
        min_length=1,
        description="Every risk-bearing clause found in the contract.",
    )
    overall_risk: RiskLevel = Field(
        description="Overall risk of the contract. Must equal the highest "
        "risk_level among the extracted clauses.",
    )

    model_config = ConfigDict(extra="forbid")

    # ---- Invariant A: internal consistency. Needs no external context, so
    #      it runs even inside `.with_structured_output()`.
    @model_validator(mode="after")
    def _overall_risk_matches_clauses(self) -> "ContractAnalysis":
        worst = max(RISK_ORDER[c.risk_level] for c in self.clauses)
        if RISK_ORDER[self.overall_risk] != worst:
            worst_label = next(
                level.value for level, rank in RISK_ORDER.items() if rank == worst
            )
            raise ValueError(
                f"overall_risk is {self.overall_risk.value!r} but the worst "
                f"clause risk is {worst_label!r}. The summary must not "
                "understate or overstate the clause-level findings."
            )
        return self

    # ---- Invariant B: duplicate clause IDs mean the model looped on itself.
    @model_validator(mode="after")
    def _clause_ids_unique(self) -> "ContractAnalysis":
        # Compare bare references so "Section 7" and "Section 7 Termination"
        # are recognised as the same clause reported twice.
        seen = [clause_reference(c.clause_id).lower() for c in self.clauses]
        duplicates = {cid for cid in seen if seen.count(cid) > 1}
        if duplicates:
            raise ValueError(
                f"duplicate clause_id(s) {sorted(duplicates)}: each clause may "
                "be reported only once."
            )
        return self

    # ---- Invariant C: GROUNDING. This is the anti-hallucination check.
    #      It only runs when the caller supplies the source contract text via
    #      validation context, so the model stays usable standalone by other
    #      students. The Analyzer node always supplies it.
    @model_validator(mode="after")
    def _quotes_are_grounded(self, info: ValidationInfo) -> "ContractAnalysis":
        context = info.context or {}
        source = context.get("source_text")
        if not source:
            return self

        haystack = normalize_text(source)
        problems: List[str] = []

        for clause in self.clauses:
            if normalize_text(clause.verbatim_quote) not in haystack:
                problems.append(
                    f"clause {clause.clause_id}: verbatim_quote does not appear "
                    f"in the source contract -- {clause.verbatim_quote[:60]!r}"
                )
            reference = clause_reference(clause.clause_id)
            if normalize_text(reference) not in haystack:
                problems.append(
                    f"clause_id {clause.clause_id!r} refers to {reference!r}, "
                    "which does not exist in the source contract."
                )

        if normalize_text(self.counterparty) not in haystack:
            problems.append(
                f"counterparty {self.counterparty!r} is not named anywhere in "
                "the source contract."
            )

        if problems:
            raise ValueError(
                "UNGROUNDED OUTPUT (hallucination detected): " + "; ".join(problems)
            )
        return self


def validate_grounded(payload: Any, source_text: str) -> ContractAnalysis:
    """Re-validate an already-parsed analysis against the source contract.

    `.with_structured_output()` validates without context, so invariant C
    above is skipped at parse time. The Analyzer calls this immediately
    afterwards to run the grounding check. Raises `pydantic.ValidationError`
    on any ungrounded field.
    """
    if isinstance(payload, BaseModel):
        payload = payload.model_dump()
    return ContractAnalysis.model_validate(
        payload, context={"source_text": source_text}
    )


# ==========================================================================
# 3. THE SHARED GRAPH STATE
# ==========================================================================


class AgentState(BaseModel):
    """The single object every node reads from and writes to."""

    # --- task identity ---
    task_domain: str = "legal_contract_review"
    raw_input: str

    # --- loop / routing control (Person 1, Coordinator) ---
    round_number: int = 0
    next_route: Optional[str] = None  # one of the ROUTE_* constants below

    # --- validation & rejection state ---
    #: Set by the Analyzer once its output passed BOTH structural and
    #: grounding validation. The Coordinator treats it as "safe to proceed".
    is_validated: bool = False
    #: "The current state is not acceptable, roll back." Set by ANY node that
    #: cannot produce trustworthy output -- the Validator on a failed
    #: invariant, and the Analyzer when its one self-correction retry is
    #: exhausted. Consumed (reset to False) by the Coordinator.
    rejection_flag: bool = False
    #: Append-only. Required by ARCHITECTURE_DESIGN.md 5.3 so the Coordinator
    #: can detect the same reason twice in a row and escalate early.
    rejection_reason_history: List[str] = Field(default_factory=list)
    error_log: Optional[str] = None

    # --- payloads produced by workers ---
    #: Analyzer output. MUST be `ContractAnalysis.model_dump()` -- never a
    #: hand-built dict. Kept as a plain dict so LangGraph state merging and
    #: Person 1's Reporter stay simple.
    analysis_payload: Dict[str, Any] = Field(default_factory=dict)
    #: Number of in-node self-correction attempts the Analyzer used on the
    #: current contract. Capped at MAX_ANALYZER_RETRIES.
    analysis_retry_count: int = 0

    sanitized_tool_calls: List[str] = Field(default_factory=list)  # Actor (P3)
    execution_state: Dict[str, Any] = Field(default_factory=dict)  # Actor (P3)
    validation_notes: Optional[str] = None  # Validator (P4)
    final_report: Optional[str] = None  # Reporter (P1)

    # --- context management (Person 5) ---
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    token_count: int = 0

    # Contract-first design: reject unknown fields so no node can silently
    # smuggle state outside the agreed schema.
    model_config = ConfigDict(extra="forbid")


# --- Coordinator routing constants (all nodes must use these labels) ---
ROUTE_ANALYZER = "analyzer"
ROUTE_ACTOR = "actor"
ROUTE_REPORTER = "reporter"
ROUTE_PARTIAL_OUTPUT = "partial_output"

MAX_ROUNDS = 5
