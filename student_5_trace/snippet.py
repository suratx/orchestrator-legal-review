"""
snippet.py -- Student 5 / Global Graph Layer: Data Privacy Leak (Tracing)

Failure mode: the graph traces correctly to LangSmith, and in doing so ships
the entire contract -- signatory SSN, counter-party EIN, escrow IBAN, deal
value, and the production database URL sitting in the process environment --
into a cloud observability dashboard that has none of the access controls the
document repository has.

WHERE THIS SITS IN main_system.py
    Not inside any node. Nodes 0-4 are owned by Persons 1-4 and none of them
    should have to think about privacy. This layer sits on the *edge between
    the graph and the outside world*: every state transition LangGraph emits
    passes through a callback before it becomes a telemetry record.

        AgentState --(node returns)--> LangGraph callback dispatch
                                              |
                                    [ RedactingTracer ]  <-- this file
                                              |
                                     redact_payload(...)
                                       /              \\
                              local TelemetrySink   LangSmith Client
                                                    (anonymizer=redactor,
                                                     hide_metadata=redactor)

    Wired in via `redacted_trace_config()`, which main_system.py passes to
    `app.invoke(...)`. Zero changes to contract.py: this layer is read-only on
    state and adds no fields, exactly as ARCHITECTURE_DESIGN.md 4 specifies
    ("Tracing Redactor (P5) | full state (read-only) | nothing").

THE LOAD-BEARING CONSTRAINT: REDACT THE COPY, NEVER THE STATE
    Person 2's grounding validator checks every `verbatim_quote` against
    `state.raw_input`. If this layer redacted live state instead of the
    outbound telemetry copy, every clause would fail grounding and the graph
    would collapse into partial-output on every run. `redact_payload` is a
    pure function returning a deep copy; `test_integration.py` asserts the
    input object is byte-identical afterwards.

WHY REDACTION IS CENTRALIZED RATHER THAN PER-NODE
    A per-node redactor is only as good as the least careful node author, and
    it silently stops covering anything added later. One interceptor on the
    boundary covers all five nodes, plus every future node, by construction.

THE FOUR TELEMETRY CHANNELS -- ALL FOUR MUST BE COVERED
    Redacting `inputs`/`outputs` alone is a common and dangerous half-measure.
    Verified against langsmith 0.10.15:

      inputs/outputs -> Client._hide_run_inputs/_hide_run_outputs
      metadata       -> Client._hide_run_metadata, which does NOT consult the
                        anonymizer; it needs its own `hide_metadata=` hook
      error strings  -> Client._hide_run_error, which consults ONLY
                        `_anonymizer` and ignores hide_inputs/hide_outputs
                        entirely. Configuring hide_inputs+hide_outputs+
                        hide_metadata and no anonymizer ships tracebacks --
                        and any credential inside them -- in the clear.
      tags           -> not redacted by the SDK at all; handled here.

    So the correct client configuration is `anonymizer=` (covers inputs,
    outputs AND errors) *plus* `hide_metadata=`. Both are set below, and
    hide_inputs/hide_outputs are set too as defence in depth in case the
    documented precedence changes.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import logging
import os
import re
import secrets as _secrets
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import UUID

from pydantic import BaseModel

logger = logging.getLogger("trace_privacy")

Redactor = Callable[[Any], Any]


# ==========================================================================
# 1. REDACTION TOKENS AND LIMITS
# ==========================================================================

#: Depth cap for the recursive walk. Guards against pathological or malicious
#: nesting. See `_TOO_DEEP` for why hitting the cap must NOT return the value.
MAX_DEPTH = 12

#: Fail-closed sentinels. Every one of these is a *replacement*, never a
#: passthrough. The rule this file is built on: if the redactor cannot prove a
#: value is safe, the value does not leave the process.
_TOO_DEEP = "[REDACTED:MAX_DEPTH_EXCEEDED]"
_CYCLE = "[REDACTED:CYCLIC_REFERENCE]"
_UNSUPPORTED = "[REDACTED:UNSUPPORTED_TYPE]"
_SENSITIVE_KEY = "[REDACTED:SENSITIVE_KEY]"
_REDACTION_FAILED = "__redaction_error__"

#: Keys whose value is dropped wholesale on name alone, whatever it contains.
#: fnmatch patterns, tested against the lower-cased key.
SENSITIVE_KEY_PATTERNS: Tuple[str, ...] = (
    "*api_key*", "*apikey*", "*api-key*",
    "authorization", "*auth_token*", "*access_token*", "*refresh_token*",
    "*password*", "*passwd*", "*secret*", "*credential*", "*private_key*",
    "cookie", "*session_id*", "*ssn*", "*social_security*",
    "*bank_account*", "*account_number*", "*routing_number*", "*iban*",
    "*credit_card*", "*card_number*", "*cvv*",
)

#: Keys carrying verbatim contract body. Regex-scrubbing these is not enough:
#: the clause text itself is the confidential asset, and no pattern set
#: recognises "a paragraph of a private agreement". Replaced with a keyed
#: fingerprint (see `fingerprint`).
FINGERPRINT_KEY_PATTERNS: Tuple[str, ...] = (
    "raw_input", "verbatim_quote", "source_text", "document_text",
)

#: Keys whose *value* is a party name. Names are unreachable by regex, so they
#: are collected from the payload and replaced as literal entities.
ENTITY_KEYS: Tuple[str, ...] = (
    "counterparty", "client_name", "client", "signatory", "party_name",
)

#: Additional entities supplied by deployment config, comma-separated.
_ENV_ENTITIES = tuple(
    part.strip()
    for part in os.environ.get("TRACE_REDACT_ENTITIES", "").split(",")
    if part.strip()
)


# --------------------------------------------------------------------------
# SHORT FORMS OF PARTY NAMES
# --------------------------------------------------------------------------
#
# Redacting the full legal name alone is not enough. Contracts define short
# forms in their opening paragraph and then use them for the rest of the
# document:
#
#     ... between Acme Corporation ("Acme") and Globex Industries Ltd
#     ("Globex") ...
#
# after which every subsequent reference is the bare short form. Replacing
# only "Globex Industries Ltd" leaves "Globex breached its duty" untouched --
# the party is still identified, in the sentence that actually says something
# damaging about them.
#
# Two sources, mirroring how the full names are obtained:
#   1. DERIVED from the legal name by stripping corporate suffixes.
#   2. PARSED from the contract's own parenthesised definitions.
#
# Both are filtered, because over-redaction is its own failure: a short form
# that is a common word would blank out ordinary prose. "First National Bank"
# must NOT yield "First".

#: Trailing tokens that carry no identifying information.
_CORPORATE_SUFFIXES = frozenset({
    "ltd", "ltd.", "limited", "inc", "inc.", "incorporated", "llc", "l.l.c.",
    "llp", "lp", "plc", "corp", "corp.", "corporation", "co", "co.", "company",
    "gmbh", "ag", "nv", "n.v.", "sa", "s.a.", "bv", "b.v.", "pty", "pte",
    "srl", "spa", "oy", "ab", "as", "kk", "kg", "sas", "sarl",
})

#: Words too generic to use as a standalone party pseudonym. Redacting these
#: would destroy readable prose without protecting anybody.
_SHORT_FORM_STOPWORDS = frozenset({
    "the", "and", "for", "first", "second", "third", "national", "international",
    "global", "american", "european", "united", "general", "standard", "premier",
    "associated", "group", "holdings", "partners", "services", "systems",
    "solutions", "technologies", "technology", "industries", "enterprises",
    "bank", "trust", "capital", "management", "consulting", "associates",
    "company", "corporation", "limited", "brothers", "sons", "insurance",
})

#: A short form must be at least this long to be replaced at all.
MIN_SHORT_FORM_CHARS = 4

#: Matches a contract's own definition: `("Globex")` or `(the "Company")`.
_DEFINED_SHORT_FORM = re.compile(
    r"\(\s*(?:the\s+)?[\"“‘']([^\"”’']{2,40})[\"”’']\s*\)"
)


def _is_usable_short_form(candidate: str) -> bool:
    bare = candidate.strip().strip(".,")
    return (
        len(bare) >= MIN_SHORT_FORM_CHARS
        and bare.lower() not in _SHORT_FORM_STOPWORDS
    )


def derive_short_forms(name: str) -> List[str]:
    """Derive usable short forms from a full legal name.

    'Globex Industries Ltd' -> ['Globex Industries', 'Globex']
    'Acme Corporation'      -> ['Acme']
    'First National Bank'   -> []          (every token is too generic)
    """
    cleaned = name.strip().rstrip(".,")
    tokens = [token for token in re.split(r"\s+", cleaned) if token]
    if not tokens:
        return []

    core = list(tokens)
    while len(core) > 1 and core[-1].lower().strip(".,") in _CORPORATE_SUFFIXES:
        core.pop()

    forms: List[str] = []

    # the name minus its corporate suffix, e.g. "Globex Industries".
    # rstrip because dropping "Inc." off "Wayne Enterprises, Inc." otherwise
    # leaves the separating comma dangling on the end.
    if len(core) < len(tokens):
        trimmed = " ".join(core).rstrip(" ,.")
        if trimmed:
            forms.append(trimmed)

    # the first distinctive token, e.g. "Globex" -- skipping generic leaders
    # so "The Boeing Company" yields "Boeing", not "The".
    if len(core) > 1:
        for token in core:
            if _is_usable_short_form(token):
                forms.append(token.strip(".,"))
                break

    seen = {cleaned.lower()}
    unique: List[str] = []
    for form in forms:
        if form.lower() not in seen:
            seen.add(form.lower())
            unique.append(form)
    return unique


def parse_defined_short_forms(text: str) -> List[str]:
    """Extract short forms the document defines for itself.

    The contract states its own aliases in parentheses; reading them is more
    reliable than guessing, and catches aliases no derivation rule would
    produce (a code name, an acronym, a trading name).
    """
    return [
        match.group(1).strip().strip(".,")
        for match in _DEFINED_SHORT_FORM.finditer(text)
        if _is_usable_short_form(match.group(1))
    ]


def expand_entities(entities: Sequence[str]) -> List[str]:
    """Every entity, plus its derived short forms, deduplicated."""
    expanded: List[str] = []
    for entity in entities:
        expanded.append(entity)
        expanded.extend(derive_short_forms(entity))

    seen: set = set()
    unique: List[str] = []
    for entity in expanded:
        key = entity.strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(entity.strip())
    return unique


# ==========================================================================
# 2. PATTERN REGISTRY
# ==========================================================================
#
# Order is significant. Composite patterns must run before their components,
# or the component match destroys the evidence the composite needs -- e.g. the
# internal-hostname rule would eat `db-prod-01.acme.internal` out of a
# connection string and leave the password stranded and unmatched.

PatternRule = Tuple[str, re.Pattern, str]

REDACTION_PATTERNS: Tuple[PatternRule, ...] = (
    # --- composite first ---------------------------------------------------
    (
        "CONNECTION_STRING",
        re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:[^\s:/@]+@[^\s\"']+"),
        "[REDACTED:CONNECTION_STRING]",
    ),
    (
        "BEARER_TOKEN",
        re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{10,}"),
        "[REDACTED:BEARER_TOKEN]",
    ),
    # --- provider-specific credentials -------------------------------------
    (
        "LANGSMITH_KEY",
        re.compile(r"\blsv2_(?:pt|sk)_[A-Za-z0-9]{8,}_[A-Za-z0-9]{6,}\b"),
        "[REDACTED:API_KEY]",
    ),
    (
        "AWS_ACCESS_KEY",
        re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA)[0-9A-Z]{16}\b"),
        "[REDACTED:API_KEY]",
    ),
    (
        "OPENAI_STYLE_KEY",
        re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),
        "[REDACTED:API_KEY]",
    ),
    (
        "GITHUB_TOKEN",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        "[REDACTED:API_KEY]",
    ),
    # --- financial ---------------------------------------------------------
    (
        "IBAN",
        re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
        "[REDACTED:BANK_ACCOUNT]",
    ),
    (
        "CREDIT_CARD",
        re.compile(r"\b(?:\d{4}[ \-]?){3}\d{4}\b"),
        "[REDACTED:CARD_NUMBER]",
    ),
    (
        "MONEY",
        re.compile(r"[$\u20ac\u00a3]\s?\d{1,3}(?:,\d{3})+(?:\.\d{2})?\b"),
        "[REDACTED:DEAL_VALUE]",
    ),
    # --- government identifiers --------------------------------------------
    # SSN before EIN: 3-2-4 is strictly more specific than 2-7 and would
    # otherwise be partially consumed by it.
    (
        "SSN",
        re.compile(r"\b\d{3}[ \-]\d{2}[ \-]\d{4}\b"),
        "[REDACTED:SSN]",
    ),
    (
        "EIN",
        re.compile(r"\b\d{2}-\d{7}\b"),
        "[REDACTED:TAX_ID]",
    ),
    # --- contact details ---------------------------------------------------
    (
        "EMAIL",
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED:EMAIL]",
    ),
    (
        "PHONE",
        re.compile(
            r"(?:\+\d{1,3}[ \-.]?)?"
            r"(?:\(\d{2,4}\)|\b\d{2,4})[ \-.]\d{2,4}[ \-.]\d{3,4}\b"
        ),
        "[REDACTED:PHONE]",
    ),
    (
        "US_STREET_ADDRESS",
        re.compile(
            r"\b\d{1,6}\s+[A-Z][A-Za-z.\-]*(?:\s+[A-Z][A-Za-z.\-]*)*\s+"
            r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Road|Rd|Lane|Ln|"
            r"Way|Court|Ct|Plaza|Parkway|Pkwy)\b"
            r"(?:[,\s]+[A-Z][A-Za-z\s]*)?(?:,?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?)?",
        ),
        "[REDACTED:ADDRESS]",
    ),
    # --- infrastructure ----------------------------------------------------
    (
        "INTERNAL_HOSTNAME",
        re.compile(
            r"\b[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?"
            r"(?:\.[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?)*"
            r"\.(?:internal|local|corp|intranet|lan)\b",
            re.IGNORECASE,
        ),
        "[REDACTED:INTERNAL_HOST]",
    ),
)


# ==========================================================================
# 3. PSEUDONYMOUS FINGERPRINTING
# ==========================================================================
#
# Issue raised in review, and correct: a bare SHA-256 of the contract is
# PSEUDONYMIZATION, not anonymization. The digest is deterministic, so an
# adversary holding a candidate document can confirm a match, and identical
# documents are linkable across runs.
#
# Mitigations applied here:
#   1. HMAC with a process-local key rather than a bare hash, so the digest
#      cannot be recomputed by anyone who does not hold the key -- which
#      nobody outside this process does, because it is never logged, never
#      serialized and never sent to the sink.
#   2. Truncated output: 16 hex chars is ample for within-run correlation and
#      leaks less than a full digest.
#   3. Bucketed length rather than exact length. Exact character counts are
#      themselves a weak identifier of a known document.
#
# Trade-off, stated plainly: with an ephemeral key, fingerprints do not
# correlate across process restarts. Set TRACE_FINGERPRINT_KEY to a stable
# local secret if cross-run correlation is worth more than that property.

_FINGERPRINT_KEY: bytes = (
    os.environ.get("TRACE_FINGERPRINT_KEY", "").encode("utf-8")
    or _secrets.token_bytes(32)
)

_LENGTH_BUCKETS: Tuple[Tuple[int, str], ...] = (
    (256, "<256"),
    (1024, "256-1k"),
    (4096, "1k-4k"),
    (16384, "4k-16k"),
)


def _length_bucket(size: int) -> str:
    for limit, label in _LENGTH_BUCKETS:
        if size < limit:
            return label
    return ">16k"


def fingerprint(value: str) -> str:
    """Keyed, bucketed pseudonym for a block of confidential document text.

    Preserves the one property an on-call engineer actually needs from a
    trace -- "is this the same document as the run that failed?" -- while
    carrying none of the text.
    """
    digest = hmac.new(
        _FINGERPRINT_KEY, value.encode("utf-8", "replace"), hashlib.sha256
    ).hexdigest()[:16]
    return f"[REDACTED:TEXT hmac={digest} len={_length_bucket(len(value))}]"


# ==========================================================================
# 4. THE REDACTOR
# ==========================================================================


def _key_matches(key: str, patterns: Sequence[str]) -> bool:
    lowered = key.lower()
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in patterns)


def redact_string(text: str, entities: Sequence[str] = ()) -> str:
    """Apply every pattern rule, then every entity replacement, to one string."""
    for _name, pattern, replacement in REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)

    # Longest first, so "Globex Industries Ltd" is consumed before "Globex".
    for entity in sorted(entities, key=len, reverse=True):
        if not entity:
            continue
        text = re.sub(re.escape(entity), "[REDACTED:PARTY]", text, flags=re.IGNORECASE)

    return text


def collect_entities(payload: Any, depth: int = 0) -> List[str]:
    """Harvest party names from the payload so they can be replaced literally.

    Regexes cannot recognise "Globex Industries Ltd" as sensitive -- it is
    just capitalised words. But the payload tells us who the parties are:
    `analysis_payload.counterparty` is a party name by definition. Reading
    the names out of the data is what makes name redaction possible at all.
    """
    if depth >= MAX_DEPTH:
        return []

    found: List[str] = []
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="python")

    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str) and _key_matches(key, ENTITY_KEYS):
                if isinstance(value, str) and value.strip():
                    found.append(value.strip())
            # Read the document's own alias definitions before the body is
            # fingerprinted away. The aliases matter for OTHER fields --
            # messages, validation notes -- where the bare short form appears
            # with nothing to mark it as a party name.
            if (
                isinstance(key, str)
                and _key_matches(key, FINGERPRINT_KEY_PATTERNS)
                and isinstance(value, str)
            ):
                found.extend(parse_defined_short_forms(value))
            found.extend(collect_entities(value, depth + 1))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            found.extend(collect_entities(item, depth + 1))

    return found


def _redact(value: Any, entities: Sequence[str], depth: int, seen: set) -> Any:
    # --- FAIL CLOSED at the depth cap. -----------------------------------
    # Returning the raw value here would turn the recursion guard into an
    # exfiltration primitive: anything nested one level deeper than the cap
    # would be copied out verbatim, and nesting depth is attacker-controlled
    # whenever any node writes model output into state.
    if depth >= MAX_DEPTH:
        return _TOO_DEEP

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")

    # --- containers: cycle-guarded ---------------------------------------
    if isinstance(value, (dict, list, tuple)):
        marker = id(value)
        if marker in seen:
            return _CYCLE
        seen = seen | {marker}

    if isinstance(value, dict):
        result: Dict[Any, Any] = {}
        for key, item in value.items():
            safe_key = key if isinstance(key, str) else str(key)
            if _key_matches(safe_key, SENSITIVE_KEY_PATTERNS):
                result[safe_key] = _SENSITIVE_KEY
            elif _key_matches(safe_key, FINGERPRINT_KEY_PATTERNS) and isinstance(
                item, str
            ):
                result[safe_key] = fingerprint(item)
            else:
                result[safe_key] = _redact(item, entities, depth + 1, seen)
        return result

    if isinstance(value, (list, tuple)):
        return [_redact(item, entities, depth + 1, seen) for item in value]

    if isinstance(value, str):
        return redact_string(value, entities)

    # bool must be tested before int -- bool is a subclass of int.
    if value is None or isinstance(value, (bool, int, float)):
        return value

    # --- scalars that are safe to render as text, then scrub -------------
    if isinstance(value, (UUID, datetime, date, Decimal)):
        return redact_string(str(value), entities)

    if isinstance(value, Enum):
        return redact_string(str(value.value), entities)

    # --- everything else fails closed ------------------------------------
    # An arbitrary object's repr() is exactly where credentials hide (an HTTP
    # client whose repr includes its Authorization header, a DB session whose
    # repr includes its DSN). Emitting the type name is enough to debug with.
    return f"{_UNSUPPORTED}:{type(value).__name__}"


def redact_payload(payload: Any, extra_entities: Sequence[str] = ()) -> Any:
    """Return a redacted DEEP COPY of `payload`. Never mutates the argument.

    This is the single redaction function. Every telemetry route in the
    system -- the local sink, the LangSmith anonymizer, the metadata hook,
    the tag scrubber, the error formatter -- routes through this one call, so
    there is exactly one place where the policy lives and exactly one place
    that can be wrong.
    """
    entities = expand_entities(
        list(_ENV_ENTITIES) + list(extra_entities) + collect_entities(payload)
    )
    return _redact(payload, entities, 0, set())


def safe_redact(payload: Any, extra_entities: Sequence[str] = ()) -> Any:
    """`redact_payload` that cannot fail open.

    If redaction itself raises, the payload is discarded and replaced with the
    exception *type name only*. Not `str(exc)` -- an exception raised while
    walking a payload very often quotes the offending value back, which would
    reintroduce the exact leak this layer exists to prevent.
    """
    try:
        return redact_payload(payload, extra_entities)
    except Exception as exc:  # noqa: BLE001 -- fail-closed is the point
        logger.error("redaction failed (%s); payload dropped", type(exc).__name__)
        return {_REDACTION_FAILED: type(exc).__name__}


def redact_error(error: BaseException | str, entities: Sequence[str] = ()) -> str:
    """Redact an exception rendered as text.

    An exception message is a telemetry channel of its own, and the most
    dangerous one, because its contents were never in graph state and so were
    never subject to any node's own hygiene:

        RuntimeError("Connection failed: postgresql://admin:pw@db.internal/x")

    Redacting state and logging the raw exception leaks the password anyway.
    """
    rendered = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    try:
        return redact_string(rendered, entities)
    except Exception as exc:  # noqa: BLE001
        return f"[REDACTED:ERROR_REDACTION_FAILED:{type(exc).__name__}]"


def redact_tags(tags: Optional[Sequence[str]]) -> List[str]:
    """Tags are a fourth channel the SDK does not redact at all."""
    if not tags:
        return []
    return [redact_string(str(tag)) for tag in tags]


# ==========================================================================
# 5. TELEMETRY SINKS -- the local stand-in for LangSmith
# ==========================================================================


@dataclass
class TraceEvent:
    """One record as it would be shipped to the observability backend."""

    event: str
    name: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {"event": self.event, "name": self.name, "payload": self.payload},
            default=str,
            sort_keys=True,
        )


class InMemorySink:
    """Records what would have been uploaded. Nothing leaves the process."""

    def __init__(self) -> None:
        self.events: List[TraceEvent] = []

    def emit(self, event: TraceEvent) -> None:
        self.events.append(event)

    def as_json(self) -> str:
        return "\n".join(event.to_json() for event in self.events)

    @property
    def byte_size(self) -> int:
        return len(self.as_json().encode("utf-8"))

    def __len__(self) -> int:
        return len(self.events)


# NOTE ON THE SAFETY MANDATE
#     An earlier draft of this module also carried a `JsonlSink` that appended
#     trace events to a local file. It was removed rather than kept: the
#     assignment forbids file modifications "even inside your broken test
#     failure instances", and it was unused dead code besides. With it gone
#     this module touches no filesystem at all -- `InMemorySink` holds
#     everything in a list, so the reproduction can demonstrate a full leak
#     without a single byte being written or transmitted anywhere.


# ==========================================================================
# 6. THE INTERCEPTOR
# ==========================================================================

try:  # pragma: no cover -- import shape varies across langchain-core versions
    from langchain_core.callbacks.base import BaseCallbackHandler
except ImportError:  # pragma: no cover
    class BaseCallbackHandler:  # type: ignore[no-redef]
        """Offline fallback so this module imports without langchain-core."""


class RedactingTracer(BaseCallbackHandler):
    """The centralized State Redaction Interceptor.

    LangGraph dispatches a callback for every node entry, exit and error. This
    handler is the choke point: it redacts inputs, outputs, metadata, tags and
    error text before any of it becomes a telemetry record.
    """

    def __init__(
        self,
        sink: Optional[InMemorySink] = None,
        entities: Sequence[str] = (),
    ) -> None:
        self.sink = sink if sink is not None else InMemorySink()
        #: Party names known from deployment config rather than from the
        #: payload. The counter-party is discoverable (`analysis_payload.
        #: counterparty` names it by definition); our own client's name is
        #: not -- nothing in state is keyed "our client", so to both the
        #: regex engine and the entity harvester it is ordinary capitalised
        #: prose. Configured, because you always know who your client is.
        #: Expanded once here so error strings and tags -- which are redacted
        #: outside `redact_payload` -- get short-form coverage too.
        self.entities: Tuple[str, ...] = tuple(
            expand_entities(list(_ENV_ENTITIES) + list(entities))
        )

    # -- helpers ----------------------------------------------------------
    def _scrub(self, value: Any) -> Any:
        return safe_redact(value, self.entities)

    def _emit(self, event: str, name: str, payload: Dict[str, Any]) -> None:
        self.sink.emit(TraceEvent(event=event, name=name, payload=payload))

    def _envelope(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Redact the channels that travel alongside every callback."""
        return {
            "metadata": self._scrub(kwargs.get("metadata") or {}),
            "tags": [redact_string(str(tag), self.entities)
                     for tag in (kwargs.get("tags") or [])],
            "run_id": str(kwargs.get("run_id", "")),
        }

    @staticmethod
    def _name(serialized: Any, kwargs: Dict[str, Any]) -> str:
        if isinstance(serialized, dict):
            return str(serialized.get("name") or kwargs.get("name") or "unknown")
        return str(kwargs.get("name") or "unknown")

    # -- chain (= every LangGraph node) -----------------------------------
    def on_chain_start(self, serialized, inputs, **kwargs) -> None:
        self._emit(
            "chain_start",
            self._name(serialized, kwargs),
            {"inputs": self._scrub(inputs), **self._envelope(kwargs)},
        )

    def on_chain_end(self, outputs, **kwargs) -> None:
        self._emit(
            "chain_end",
            self._name(None, kwargs),
            {"outputs": self._scrub(outputs), **self._envelope(kwargs)},
        )

    def on_chain_error(self, error, **kwargs) -> None:
        self._emit(
            "chain_error",
            self._name(None, kwargs),
            {"error": redact_error(error, self.entities), **self._envelope(kwargs)},
        )

    # -- llm --------------------------------------------------------------
    def on_llm_start(self, serialized, prompts, **kwargs) -> None:
        self._emit(
            "llm_start",
            self._name(serialized, kwargs),
            {"prompts": self._scrub(list(prompts or [])), **self._envelope(kwargs)},
        )

    def on_llm_end(self, response, **kwargs) -> None:
        self._emit(
            "llm_end",
            self._name(None, kwargs),
            {"response": self._scrub(response), **self._envelope(kwargs)},
        )

    def on_llm_error(self, error, **kwargs) -> None:
        self._emit(
            "llm_error",
            self._name(None, kwargs),
            {"error": redact_error(error, self.entities), **self._envelope(kwargs)},
        )

    # -- tools ------------------------------------------------------------
    def on_tool_start(self, serialized, input_str, **kwargs) -> None:
        self._emit(
            "tool_start",
            self._name(serialized, kwargs),
            {"input": self._scrub(input_str), **self._envelope(kwargs)},
        )

    def on_tool_end(self, output, **kwargs) -> None:
        self._emit(
            "tool_end",
            self._name(None, kwargs),
            {"output": self._scrub(output), **self._envelope(kwargs)},
        )

    def on_tool_error(self, error, **kwargs) -> None:
        self._emit(
            "tool_error",
            self._name(None, kwargs),
            {"error": redact_error(error, self.entities), **self._envelope(kwargs)},
        )


# ==========================================================================
# 7. ROUTE CONTROL -- closing the second, invisible tracing path
# ==========================================================================
#
# Attaching a RedactingTracer via `callbacks=[...]` protects the route you can
# see. It does nothing about the route you cannot:
#
#   LangChainTracer.__init__:  self.client = client or get_client()
#   langchain_core.tracers.langchain.get_client() -> run_trees.get_cached_client()
#   run_trees.get_cached_client(): global _CLIENT, built as a bare Client()
#
# With LANGSMITH_TRACING / LANGCHAIN_TRACING_V2 set, langchain-core attaches
# that tracer automatically, IN ADDITION to any callbacks you pass. The local
# sink would show a clean, fully-redacted stream while the unredacted state
# went to the cloud in parallel -- a guardrail that reports success precisely
# while it is failing.
#
# So the redacting client is seeded into the global singleton, making
# redaction unavoidable rather than opt-in, and `audit_tracing_routes()`
# enumerates every route so the property is asserted rather than assumed.

_TRACING_ENV_VARS = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_TRACING",
)


class UnredactedTracingRouteError(RuntimeError):
    """Raised when a telemetry route exists that does not redact."""


@dataclass(frozen=True)
class TracingRoute:
    name: str
    detail: str
    redacting: bool


def _env_tracing_enabled() -> bool:
    return any(
        os.environ.get(var, "").lower() in ("1", "true", "yes")
        for var in _TRACING_ENV_VARS
    )


def _client_is_redacting(client: Any) -> bool:
    """Structural check: does this client scrub all four channels?"""
    if client is None:
        return False
    anonymizer = getattr(client, "_anonymizer", None)
    hide_metadata = getattr(client, "_hide_metadata", False)
    # anonymizer covers inputs + outputs + errors; hide_metadata is separate
    # and must be either a callable or the hard `True` (drop everything).
    metadata_safe = hide_metadata is True or callable(hide_metadata)
    return callable(anonymizer) and metadata_safe


def build_redacting_client(
    redactor: Optional[Redactor] = None, **client_kwargs: Any
) -> Any:
    """Construct a LangSmith client that cannot ship an unredacted field.

    Lazy import and lazy construction: no API key and no network are needed
    unless a caller explicitly asks for a real client. Tests pass
    `auto_batch_tracing=False` to avoid spawning the SDK's uploader thread.
    """
    from langsmith import Client

    scrub: Redactor = redactor or safe_redact
    return Client(
        anonymizer=scrub,       # inputs + outputs + ERROR STRINGS
        hide_metadata=scrub,    # separate hook; never consults the anonymizer
        hide_inputs=scrub,      # defence in depth if precedence ever changes
        hide_outputs=scrub,
        **client_kwargs,
    )


def install_redacted_tracing(
    redactor: Optional[Redactor] = None, **client_kwargs: Any
) -> Any:
    """Make redaction the only possible route to LangSmith.

    Seeds the process-global client singleton that every implicitly-created
    `LangChainTracer` resolves through, then verifies the seed took.
    """
    from langsmith import run_trees

    client = build_redacting_client(redactor, **client_kwargs)
    run_trees._CLIENT = client  # documented singleton; see module note above

    installed = run_trees.get_cached_client()
    if installed is not client or not _client_is_redacting(installed):
        raise UnredactedTracingRouteError(
            "failed to seed the global LangSmith client; refusing to trace "
            "because an unredacted upload route would remain open."
        )
    logger.info("redacting LangSmith client installed as the global client")
    return client


def audit_tracing_routes(
    callbacks: Optional[Sequence[Any]] = None,
) -> List[TracingRoute]:
    """Enumerate every route from graph state to an external endpoint."""
    routes: List[TracingRoute] = []

    for handler in callbacks or ():
        routes.append(
            TracingRoute(
                name=f"callback:{type(handler).__name__}",
                detail="explicitly attached callback handler",
                redacting=isinstance(handler, RedactingTracer),
            )
        )

    env_on = _env_tracing_enabled()
    if env_on:
        try:
            from langsmith import run_trees

            global_client = run_trees._CLIENT
        except Exception:  # noqa: BLE001
            global_client = None

        routes.append(
            TracingRoute(
                name="implicit:LangChainTracer",
                detail=(
                    "auto-attached because a LANGSMITH/LANGCHAIN tracing env "
                    "var is set; uploads via the global cached client"
                ),
                redacting=_client_is_redacting(global_client),
            )
        )

    return routes


def assert_all_routes_redacted(callbacks: Optional[Sequence[Any]] = None) -> None:
    """Fail closed if any enumerated route would upload unredacted data."""
    unsafe = [route for route in audit_tracing_routes(callbacks) if not route.redacting]
    if unsafe:
        raise UnredactedTracingRouteError(
            "unredacted telemetry route(s): "
            + "; ".join(f"{route.name} ({route.detail})" for route in unsafe)
        )


def redacted_trace_config(
    sink: Optional[InMemorySink] = None,
    *,
    entities: Sequence[str] = (),
    recursion_limit: int = 50,
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """The config main_system.py passes to `app.invoke(...)`.

    Attaches the interceptor, and if ambient tracing is switched on in the
    environment, installs the redacting client first so the implicit route is
    closed too. Then audits, and refuses to return a config while any route
    would still upload in the clear.
    """
    tracer = RedactingTracer(sink, entities)

    if _env_tracing_enabled():
        install_redacted_tracing()

    assert_all_routes_redacted([tracer])

    config: Dict[str, Any] = {
        "callbacks": [tracer],
        "recursion_limit": recursion_limit,
    }
    if metadata is not None:
        config["metadata"] = metadata
    if tags is not None:
        config["tags"] = list(tags)
    return config


# ==========================================================================
# 8. MEASUREMENT
# ==========================================================================


@dataclass
class LeakReport:
    """Two different numbers, because they answer two different questions."""

    #: How many distinct planted secrets escaped at least once. This is the
    #: privacy question: "which secrets are now in the dashboard?"
    unique_exposed: List[str]
    #: How many times any secret appeared across the whole stream. This is the
    #: blast-radius question: one key in five events is one exposed secret but
    #: five records to purge, and both matter to an incident response.
    total_occurrences: int
    per_secret: Dict[str, int]
    corpus_size: int

    @property
    def unique_count(self) -> int:
        return len(self.unique_exposed)

    def summary(self) -> str:
        return (
            f"unique planted secrets exposed: {self.unique_count} of "
            f"{self.corpus_size} | total exposure occurrences: "
            f"{self.total_occurrences}"
        )


def count_standalone_occurrences(
    blob: str, short_form: str, full_name: Optional[str] = None
) -> int:
    """Count a short form only where it stands alone, not inside the full name.

    Needed because a derived short form is usually a prefix of the name it came
    from ("Globex" ⊂ "Globex Industries Ltd"). A naive substring count would
    tally every occurrence of the full name as a short-form leak too, and
    inflate the before/after numbers in both directions.
    """
    pattern = r"\b" + re.escape(short_form) + r"\b"
    if full_name and full_name.lower().startswith(short_form.lower()):
        remainder = full_name[len(short_form):].strip()
        if remainder:
            pattern += r"(?!\s+" + re.escape(remainder) + r")"
    return len(re.findall(pattern, blob, re.IGNORECASE))


def scan_for_leaks(
    events: Iterable[TraceEvent] | InMemorySink,
    corpus: Sequence[str],
) -> LeakReport:
    """Count planted secrets surviving into the telemetry stream."""
    if isinstance(events, InMemorySink):
        events = events.events

    blob = "\n".join(event.to_json() for event in events)

    per_secret: Dict[str, int] = {}
    total = 0
    exposed: List[str] = []
    for secret in corpus:
        # json.dumps escapes nothing in these values except quotes/slashes;
        # count both the literal and the JSON-escaped rendering.
        count = blob.count(secret) + (
            blob.count(json.dumps(secret)[1:-1]) if '"' in secret or "\\" in secret else 0
        )
        per_secret[secret] = count
        if count:
            exposed.append(secret)
            total += count

    return LeakReport(
        unique_exposed=exposed,
        total_occurrences=total,
        per_secret=per_secret,
        corpus_size=len(corpus),
    )


# ==========================================================================
# 9. THE UNGUARDED TWIN -- test_failure.py only, never wired into the graph
# ==========================================================================


class RawTracer(BaseCallbackHandler):
    """What LangSmith tracing does out of the box: ship everything.

    This is not a strawman. It is the default behaviour of an unconfigured
    `Client()`, which is what you get by setting LANGSMITH_TRACING=true and
    nothing else.
    """

    def __init__(self, sink: Optional[InMemorySink] = None) -> None:
        self.sink = sink if sink is not None else InMemorySink()

    def _emit(self, event: str, name: str, payload: Dict[str, Any]) -> None:
        self.sink.emit(TraceEvent(event=event, name=name, payload=payload))

    @staticmethod
    def _plain(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        return value

    def on_chain_start(self, serialized, inputs, **kwargs) -> None:
        self._emit(
            "chain_start",
            str((serialized or {}).get("name", "unknown"))
            if isinstance(serialized, dict)
            else "unknown",
            {
                "inputs": self._plain(inputs),
                "metadata": kwargs.get("metadata") or {},
                "tags": list(kwargs.get("tags") or []),
            },
        )

    def on_chain_end(self, outputs, **kwargs) -> None:
        self._emit(
            "chain_end",
            "unknown",
            {
                "outputs": self._plain(outputs),
                "metadata": kwargs.get("metadata") or {},
                "tags": list(kwargs.get("tags") or []),
            },
        )

    def on_chain_error(self, error, **kwargs) -> None:
        self._emit(
            "chain_error",
            "unknown",
            {
                "error": f"{type(error).__name__}: {error}",
                "metadata": kwargs.get("metadata") or {},
                "tags": list(kwargs.get("tags") or []),
            },
        )


def unguarded_trace_config(
    sink: Optional[InMemorySink] = None,
    *,
    recursion_limit: int = 50,
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Mirror of `redacted_trace_config` with the guardrail removed."""
    config: Dict[str, Any] = {
        "callbacks": [RawTracer(sink)],
        "recursion_limit": recursion_limit,
    }
    if metadata is not None:
        config["metadata"] = metadata
    if tags is not None:
        config["tags"] = list(tags)
    return config
