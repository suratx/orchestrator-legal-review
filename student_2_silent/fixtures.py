"""
fixtures.py -- Deterministic test material for Student 2 / Worker A.

Contains:
  * SAMPLE_CONTRACT      -- one realistic MSA used by every test and demo.
  * ScriptedStructuredLLM -- a stand-in for
        `ChatOllama(...).with_structured_output(ContractAnalysis)`
    that replays a fixed list of responses. The assignment requires a
    *deterministic* reproduction script; a live llama3.2 call is not
    deterministic, so the failure repro drives the exact same code path with
    scripted model output. `snippet.py` runs against the real model.
  * Canned analyses covering the good case and three distinct ways the
    Analyzer can fail.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence, Union

from contract import ContractAnalysis

# ==========================================================================
# The contract under review
# ==========================================================================

SAMPLE_CONTRACT = """MASTER SERVICES AGREEMENT

This Master Services Agreement (the "Agreement") is entered into as of
14 March 2025 by and between Northwind Analytics LLC ("Client") and
Globex Industries Ltd ("Supplier").

Section 1. Services
Supplier shall provide the data engineering services described in Schedule A.

Section 4.2 Indemnification
Supplier shall indemnify, defend and hold harmless Client from and against
any and all claims, losses and liabilities of any kind whatsoever arising out
of or relating to this Agreement, without limitation as to amount.

Section 5.1 Limitation of Liability
Except for the indemnity obligations in Section 4.2, neither party shall be
liable for indirect or consequential damages.

Section 7. Termination
Client may terminate this Agreement for convenience upon one hundred eighty
(180) days prior written notice. Supplier may terminate immediately upon any
late payment.

Section 9. Governing Law
This Agreement shall be governed by the laws of the Cayman Islands and the
parties submit to the exclusive jurisdiction of its courts.

Section 11. Confidentiality
Each party shall keep the other's Confidential Information secret for a period
of two (2) years following termination.
"""


# ==========================================================================
# Canned analyses
# ==========================================================================

#: Fully correct: every quote, clause_id and party name is really in the
#: contract, and overall_risk equals the worst clause risk.
GOOD_ANALYSIS: Dict[str, Any] = {
    "contract_title": "Master Services Agreement",
    "counterparty": "Globex Industries Ltd",
    "overall_risk": "critical",
    "clauses": [
        {
            "clause_id": "Section 4.2",
            "clause_type": "indemnification",
            "verbatim_quote": (
                "Supplier shall indemnify, defend and hold harmless Client from "
                "and against any and all claims, losses and liabilities of any "
                "kind whatsoever arising out of or relating to this Agreement, "
                "without limitation as to amount."
            ),
            "risk_level": "critical",
            "risk_rationale": (
                "The indemnity is uncapped and covers claims of any kind, so the "
                "Client's exposure is unlimited."
            ),
        },
        {
            "clause_id": "Section 7",
            "clause_type": "termination",
            "verbatim_quote": (
                "Supplier may terminate immediately upon any late payment."
            ),
            "risk_level": "high",
            "risk_rationale": (
                "Termination rights are asymmetric: the Client needs 180 days "
                "notice while the Supplier can walk away instantly."
            ),
        },
        {
            "clause_id": "Section 9",
            "clause_type": "governing_law",
            "verbatim_quote": (
                "This Agreement shall be governed by the laws of the Cayman "
                "Islands"
            ),
            "risk_level": "high",
            "risk_rationale": (
                "An offshore forum makes enforcement slow and expensive for the "
                "Client relative to its home jurisdiction."
            ),
        },
    ],
}


#: THE SILENT HALLUCINATION.
#: Structurally flawless: every required field present, every enum valid,
#: clause IDs well-formed, overall_risk consistent with the clause risks. It
#: passes `.with_structured_output()` without complaint.
#:
#: It is also legally catastrophic. The model has:
#:   (a) invented a "Section 12.4" liability cap that does not exist, and
#:   (b) omitted the real uncapped indemnity in Section 4.2.
#: A downstream reviewer reading this payload concludes liability is capped at
#: twelve months of fees, when it is in fact unlimited.
HALLUCINATED_ANALYSIS: Dict[str, Any] = {
    "contract_title": "Master Services Agreement",
    "counterparty": "Globex Industries Ltd",
    "overall_risk": "high",
    "clauses": [
        {
            "clause_id": "Section 12.4",
            "clause_type": "limitation_of_liability",
            "verbatim_quote": (
                "Supplier's aggregate liability shall in no event exceed the "
                "fees paid in the preceding twelve (12) months."
            ),
            "risk_level": "low",
            "risk_rationale": (
                "Liability is capped at twelve months of fees, which is market "
                "standard and acceptable to the Client."
            ),
        },
        {
            "clause_id": "Section 7",
            "clause_type": "termination",
            "verbatim_quote": (
                "Supplier may terminate immediately upon any late payment."
            ),
            "risk_level": "high",
            "risk_rationale": (
                "Termination rights are asymmetric: the Client needs 180 days "
                "notice while the Supplier can walk away instantly."
            ),
        },
    ],
}


#: STRUCTURAL failure: `clause_id` -- the critical domain identifier -- is
#: missing entirely. Caught at parse time by `.with_structured_output()`.
MISSING_CLAUSE_ID_ANALYSIS: Dict[str, Any] = {
    "contract_title": "Master Services Agreement",
    "counterparty": "Globex Industries Ltd",
    "overall_risk": "critical",
    "clauses": [
        {
            "clause_type": "indemnification",
            "verbatim_quote": (
                "Supplier shall indemnify, defend and hold harmless Client from "
                "and against any and all claims"
            ),
            "risk_level": "critical",
            "risk_rationale": (
                "The indemnity is uncapped, so the Client's exposure is "
                "unlimited."
            ),
        }
    ],
}


#: CONSISTENCY failure: flags a critical clause, then summarizes the whole
#: contract as "low" risk. Every field is present and correctly typed.
UNDERSTATED_RISK_ANALYSIS: Dict[str, Any] = {
    **GOOD_ANALYSIS,
    "overall_risk": "low",
}


#: A counterparty that is plausible but not the one in the document -- the
#: kind of one-word drift that survives every structural check.
WRONG_COUNTERPARTY_ANALYSIS: Dict[str, Any] = {
    **GOOD_ANALYSIS,
    "counterparty": "Globex International Holdings",
}


# ==========================================================================
# Deterministic model stand-in
# ==========================================================================

Response = Union[Dict[str, Any], Exception]


class ScriptedStructuredLLM:
    """Replays a fixed sequence of model responses.

    Mirrors the real runnable returned by
    `ChatOllama(...).with_structured_output(ContractAnalysis)`:

      * it validates each scripted payload against `ContractAnalysis`, so a
        structurally broken script raises `ValidationError` here exactly as
        the real chain would at parse time;
      * it does NOT run the grounding invariant (that needs the source text
        via validation context), so hallucinated-but-well-formed payloads
        sail through -- which is precisely the failure being reproduced.

    Once the script is exhausted the last response repeats, so a scripted
    model that never corrects itself keeps failing forever.
    """

    def __init__(self, responses: Sequence[Response]) -> None:
        if not responses:
            raise ValueError("ScriptedStructuredLLM needs at least one response")
        self._responses: List[Response] = list(responses)
        self.call_count: int = 0
        self.prompts: List[Any] = []

    def invoke(self, messages: Any, *_args: Any, **_kwargs: Any) -> ContractAnalysis:
        self.prompts.append(messages)
        index = min(self.call_count, len(self._responses) - 1)
        self.call_count += 1
        response = self._responses[index]
        if isinstance(response, Exception):
            raise response
        return ContractAnalysis.model_validate(response)

    @property
    def last_prompt_text(self) -> str:
        """Flattened text of the most recent prompt -- lets tests assert that
        the validation error really was fed back on the retry."""
        return _flatten(self.prompts[-1]) if self.prompts else ""


class ScriptedRawLLM:
    """Replays raw model replies with NO schema attached.

    This is what the Analyzer looks like before any guardrail: a chat model
    that returns a JSON-ish blob of text which the caller trusts. Used only
    by the unguarded path in `snippet.py` / `test_failure.py`.
    """

    def __init__(self, payloads: Sequence[Dict[str, Any]]) -> None:
        self._payloads = list(payloads)
        self.call_count = 0
        self.prompts: List[Any] = []

    def invoke(self, messages: Any, *_args: Any, **_kwargs: Any) -> "_RawReply":
        self.prompts.append(messages)
        index = min(self.call_count, len(self._payloads) - 1)
        self.call_count += 1
        return _RawReply(json.dumps(self._payloads[index]))


class _RawReply:
    """Minimal stand-in for a LangChain `AIMessage`."""

    def __init__(self, content: str) -> None:
        self.content = content


def _flatten(messages: Any) -> str:
    parts = []
    for message in messages:
        parts.append(str(getattr(message, "content", message)))
    return "\n".join(parts)
