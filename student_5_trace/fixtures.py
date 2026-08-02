"""
fixtures.py -- Person 5 / Tracing Privacy: the planted-secret corpus.

Everything in this file is SYNTHETIC. No value here is a real credential, a
real person, a real bank account or a real host. `AKIAIOSFODNN7EXAMPLE` is
AWS's own published documentation placeholder; the IBAN is the ISO example
value; every phone number is in the ITU/NANP reserved 555-01xx test range.
Nothing is ever transmitted anywhere -- the telemetry sink is in-process.

WHY A FIXTURE FILE AT ALL
    The metric "leaked PII records: N -> 0" is only meaningful if N is
    counted against a known ground truth. `SECRET_CORPUS` *is* that ground
    truth: `snippet.scan_for_leaks()` counts how many of these exact strings
    survive into the telemetry stream. Without a planted corpus you can only
    say "we didn't see anything obvious", which is not a measurement.

THREE DISTINCT CORPORA, THREE DIFFERENT QUESTIONS
    SECRET_CORPUS       -- must NEVER reach telemetry. Measures recall.
    OBFUSCATED_SECRETS  -- non-standard formats. Measures the regex blind
                           spot honestly instead of pretending it is closed.
    BENIGN_LOOKALIKES   -- harmless operational strings that resemble
                           sensitive patterns. Measures false positives, i.e.
                           how much debuggability the guardrail costs.
"""

from __future__ import annotations

from typing import Any, Dict, List

from contract import AgentState

# ==========================================================================
# 1. THE PLANTED SECRETS -- ground truth for the leak count
# ==========================================================================

#: Personally-identifying / commercially-sensitive values that a real legal
#: contract-review system genuinely handles, plus the system secrets that sit
#: in the process environment alongside them.
PLANTED_SECRETS: Dict[str, str] = {
    # --- counter-party / signatory PII -------------------------------------
    "signatory_email": "j.okonkwo@globex-industries.example",
    "signatory_phone": "(713) 555-0182",
    "signatory_ssn": "412-88-7391",
    "signatory_address": "4820 Kirby Drive, Houston, TX 77098",
    "counterparty_ein": "76-4820193",
    "escrow_iban": "GB29NWBK60161331926819",
    # --- commercially sensitive deal terms ---------------------------------
    "deal_value": "$1,250,000.00",
    # --- party names (regex cannot catch these -- entity redaction does) ----
    "client_name": "Acme Corporation",
    "counterparty_name": "Globex Industries Ltd",
    # --- system secrets ----------------------------------------------------
    "langsmith_api_key": "lsv2_pt_a1b2c3d4e5f6a7b8c9d0e1f2_9f8e7d6c5b",
    "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
    "db_connection": (
        "postgresql://svc_review:Fak3P4ss@db-prod-01.acme.internal:5432/contracts"
    ),
    "internal_host": "db-prod-01.acme.internal",
}

#: Flat list used by `scan_for_leaks`.
SECRET_CORPUS: List[str] = list(PLANTED_SECRETS.values())


# ==========================================================================
# 2. THE POISONED CONTRACT -- what actually enters the graph as `raw_input`
# ==========================================================================

POISONED_CONTRACT = f"""MASTER SERVICES AGREEMENT

This Agreement is entered into by {PLANTED_SECRETS['client_name']} ("Acme") and
{PLANTED_SECRETS['counterparty_name']} ("Globex"). Thereafter the parties are
referred to by those short forms.

Client: {PLANTED_SECRETS['client_name']}
Counterparty: {PLANTED_SECRETS['counterparty_name']}
Counterparty Tax ID (EIN): {PLANTED_SECRETS['counterparty_ein']}
Total Contract Value: {PLANTED_SECRETS['deal_value']}
Escrow Account: {PLANTED_SECRETS['escrow_iban']}

Authorized Signatory: Jane Okonkwo
Email: {PLANTED_SECRETS['signatory_email']}
Direct Line: {PLANTED_SECRETS['signatory_phone']}
SSN (on file for background check): {PLANTED_SECRETS['signatory_ssn']}
Address: {PLANTED_SECRETS['signatory_address']}

Section 4.2 Indemnification
Globex Industries Ltd shall indemnify Acme Corporation for all losses arising
from breach, negligence, or misconduct without limitation.

Section 9.1 Governing Law
This Agreement shall be governed by and construed in accordance with the laws
of the State of Delaware without regard to its conflict of laws provisions.

-- INTERNAL PROCESSING NOTE (do not distribute) --
Reviewed against matter database {PLANTED_SECRETS['db_connection']}
Signed-copy artifact bucket credentials: {PLANTED_SECRETS['aws_access_key']}
"""

#: The short forms the contract defines for itself. Measured separately from
#: `SECRET_CORPUS` because each is a prefix of the full name it came from, so a
#: naive substring count would tally every full-name occurrence as a short-form
#: leak as well. `snippet.count_standalone_occurrences` counts only standalone
#: uses. Each entry is (short form, the full name it abbreviates).
SHORT_FORM_CORPUS: List[tuple] = [
    ("Globex", PLANTED_SECRETS["counterparty_name"]),
    ("Acme", PLANTED_SECRETS["client_name"]),
]

#: Party names that are *deployment configuration*, not discoverable from the
#: payload. The counter-party is discoverable -- `analysis_payload.counterparty`
#: names it by definition -- but nothing in the state is keyed "our client", so
#: "Acme Corporation" reads as ordinary capitalised prose to both the regex
#: engine and the entity harvester. You always know who your own client is, so
#: this belongs in config, and is passed to the tracer as `entities=`.
CLIENT_ENTITIES: List[str] = [PLANTED_SECRETS["client_name"]]

#: Invoke-time telemetry metadata. This is the payload that `hide_inputs` and
#: `hide_outputs` do NOT cover -- LangSmith routes it through a separate
#: `hide_metadata` hook that never consults the anonymizer.
POISONED_METADATA: Dict[str, Any] = {
    "client_name": PLANTED_SECRETS["client_name"],
    "database": PLANTED_SECRETS["internal_host"],
    "langsmith_api_key": PLANTED_SECRETS["langsmith_api_key"],
    "reviewer_email": PLANTED_SECRETS["signatory_email"],
    # benign operational metadata that MUST survive redaction
    "matter_type": "commercial_msa",
    "graph_version": "1.0.0",
}

#: Tags are a third telemetry channel, separate from inputs/outputs/metadata.
POISONED_TAGS: List[str] = [
    "env:prod",
    f"host:{PLANTED_SECRETS['internal_host']}",
    "team:legal-ops",
]

#: An exception whose *message* carries a credential the payload redactor
#: would never see, because it was never in graph state.
POISONED_ERROR_MESSAGE = (
    f"Connection failed: {PLANTED_SECRETS['db_connection']} "
    f"(retry with Bearer {PLANTED_SECRETS['langsmith_api_key']})"
)


# ==========================================================================
# 3. THE BLIND-SPOT CORPUS -- non-standard formats regexes are known to miss
# ==========================================================================

#: Each entry is (label, value, whether v1 patterns are expected to catch it).
#: The honest expectation is recorded here in code so METRICS.md reports a
#: measurement rather than an aspiration.
OBFUSCATED_SECRETS: List[Dict[str, Any]] = [
    {
        "label": "email_with_spelled_out_separators",
        "value": "j.okonkwo [at] globex-industries [dot] example",
        "expected_caught": False,
        "note": "MISS. Deliberate evasion defeats format matching entirely.",
    },
    {
        "label": "ssn_space_separated",
        "value": "SSN 412 88 7391",
        "expected_caught": True,
        "note": "CAUGHT. The SSN rule accepts spaces as well as hyphens.",
    },
    {
        "label": "uk_international_phone",
        "value": "+44 20 7946 0958",
        "expected_caught": True,
        "note": "CAUGHT. The phone rule has an optional +NN country prefix.",
    },
    {
        "label": "non_us_national_id",
        "value": "NHS 943 476 5919",
        "expected_caught": True,
        "note": (
            "CAUGHT INCIDENTALLY, and mislabelled. A UK NHS number is 3-3-4 "
            "digits, which is also the US phone shape, so it is redacted as "
            "[REDACTED:PHONE]. The value is protected -- that is what matters "
            "-- but the label is wrong, so anyone reading the trace draws the "
            "wrong conclusion about what kind of identifier was present. "
            "Correct coverage needs a real non-US identifier rule, not a "
            "lucky collision."
        ),
    },
    {
        "label": "base64_wrapped_key",
        "value": "bHN2Ml9wdF9hMWIyYzNkNGU1ZjZhN2I4YzlkMGUxZjI=",
        "expected_caught": False,
        "note": "MISS. Encoding changes the format, so the format rule fails.",
    },
]


# ==========================================================================
# 4. THE FALSE-POSITIVE CORPUS -- harmless strings shaped like secrets
# ==========================================================================

#: Operational values a debugger genuinely needs to see in a trace. If the
#: redactor eats these, it has cost real observability, and that cost has to
#: be measured and disclosed -- a guardrail that destroys debuggability gets
#: switched off, which is a privacy failure by another route.
BENIGN_LOOKALIKES: List[Dict[str, Any]] = [
    {"label": "clause_reference", "value": "Section 4.2", "expected_preserved": True},
    {"label": "iso_date", "value": "2026-08-01", "expected_preserved": True},
    {"label": "matter_id", "value": "MSA-001", "expected_preserved": True},
    {"label": "model_name", "value": "llama3.2", "expected_preserved": True},
    {"label": "round_counter", "value": "round 3 of 5", "expected_preserved": True},
    {"label": "contract_version", "value": "1.0.0", "expected_preserved": True},
    # Deliberate known false positive: an internal part number that is
    # digit-for-digit indistinguishable from a US EIN. Kept in the corpus so
    # the limitation is measured and published, not quietly dropped.
    {
        "label": "ein_shaped_part_number",
        "value": "PN 12-3456789",
        "expected_preserved": False,
    },
]


# ==========================================================================
# 5. GRAPH DRIVERS -- a poisoned initial state and a matching Analyzer stub
# ==========================================================================


def poisoned_initial_state() -> AgentState:
    """The state the graph starts from, carrying the poisoned contract.

    `messages` is pre-seeded because that list is what a real run accumulates
    and it is the largest single carrier of un-vetted text into telemetry.
    """
    return AgentState(
        raw_input=POISONED_CONTRACT,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are reviewing a contract for "
                    f"{PLANTED_SECRETS['client_name']}. Matter DB: "
                    f"{PLANTED_SECRETS['db_connection']}"
                ),
            },
            {
                "role": "user",
                "content": (
                    "Escalate questions to "
                    f"{PLANTED_SECRETS['signatory_email']} or "
                    f"{PLANTED_SECRETS['signatory_phone']}."
                ),
            },
            # Bare short forms, exactly as they appear after a contract's
            # opening paragraph defines them. Nothing here marks these as
            # party names -- no key, no format, just capitalised prose.
            {
                "role": "assistant",
                "content": (
                    "Globex breached its duty to Acme; recommend escalating "
                    "the Globex indemnity position before Acme countersigns."
                ),
            },
        ],
    )


#: Analyzer output for the poisoned contract. Written to satisfy every
#: `contract.py` invariant (grounded quotes, unique clause IDs, overall_risk
#: equal to the worst clause risk) so the graph reaches the Reporter cleanly
#: and the run measures *tracing*, not somebody else's guardrail firing.
POISONED_ANALYSIS: Dict[str, Any] = {
    "contract_title": "Master Services Agreement",
    "counterparty": PLANTED_SECRETS["counterparty_name"],
    "overall_risk": "critical",
    "clauses": [
        {
            "clause_id": "Section 4.2 Indemnification",
            "clause_type": "indemnification",
            "verbatim_quote": (
                "Globex Industries Ltd shall indemnify Acme Corporation for all "
                "losses arising from breach, negligence, or misconduct without "
                "limitation."
            ),
            "risk_level": "critical",
            "risk_rationale": (
                "The indemnity is uncapped and creates unlimited financial "
                "exposure for the client."
            ),
        },
        {
            "clause_id": "Section 9.1 Governing Law",
            "clause_type": "governing_law",
            "verbatim_quote": (
                "This Agreement shall be governed by and construed in accordance "
                "with the laws of the State of Delaware without regard to its "
                "conflict of laws provisions."
            ),
            "risk_level": "low",
            "risk_rationale": (
                "Delaware governing law is standard and acceptable for an "
                "agreement of this type."
            ),
        },
    ],
}


def poisoned_analyzer_stub(state: AgentState) -> AgentState:
    """Offline Analyzer that emits PII-bearing analysis for the fixture.

    Injected via `build_graph(analyzer=...)` so this file never has to touch
    Person 2's node, and so the whole reproduction runs without Ollama.
    """
    state = state.model_copy(deep=True)
    state.analysis_payload = POISONED_ANALYSIS
    state.is_validated = True
    state.messages = state.messages + [
        {
            "role": "assistant",
            "content": (
                "Extracted 2 clauses for "
                f"{PLANTED_SECRETS['counterparty_name']} "
                f"(EIN {PLANTED_SECRETS['counterparty_ein']}, deal value "
                f"{PLANTED_SECRETS['deal_value']})."
            ),
        }
    ]
    return state
