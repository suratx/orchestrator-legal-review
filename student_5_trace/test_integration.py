"""
test_integration.py -- Person 5: the properties that make the guardrail safe
to leave switched on.

Catching secrets is only half the job. A redaction layer also has to be
provably harmless: it must not alter what the graph computes, must not mutate
the state it inspects, must not destroy the operational fields the trace
exists for, and must never fail open. Those are the tests here.

Run:  pytest student_5_trace/test_integration.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract import AgentState
from main_system import build_graph
from student_5_trace.fixtures import (
    BENIGN_LOOKALIKES,
    CLIENT_ENTITIES,
    PLANTED_SECRETS,
    POISONED_METADATA,
    POISONED_TAGS,
    SECRET_CORPUS,
    SHORT_FORM_CORPUS,
    poisoned_analyzer_stub,
    poisoned_initial_state,
)
from student_5_trace.snippet import (
    MAX_DEPTH,
    InMemorySink,
    RedactingTracer,
    TracingRoute,
    UnredactedTracingRouteError,
    _client_is_redacting,
    assert_all_routes_redacted,
    audit_tracing_routes,
    build_redacting_client,
    count_standalone_occurrences,
    derive_short_forms,
    expand_entities,
    fingerprint,
    install_redacted_tracing,
    parse_defined_short_forms,
    redact_payload,
    redact_string,
    redacted_trace_config,
    safe_redact,
    scan_for_leaks,
)


# ==========================================================================
# 1. OPERATIONAL PARITY -- tracing must not change what the graph computes
# ==========================================================================
#
# Note the precise claim. It is NOT "state is unchanged" -- the graph's whole
# job is to change state, and a run that ended where it started would be a
# broken run. The claim is that the run WITH privacy tracing reaches the same
# operational outcome as the run WITHOUT it.


def _run(config):
    app = build_graph(analyzer=poisoned_analyzer_stub)
    return app.invoke(poisoned_initial_state(), config=config)


def test_traced_run_reaches_the_same_outcome_as_an_untraced_run():
    untraced = _run({"recursion_limit": 50})
    traced = _run(
        redacted_trace_config(
            InMemorySink(),
            entities=CLIENT_ENTITIES,
            metadata=dict(POISONED_METADATA),
            tags=list(POISONED_TAGS),
        )
    )

    for field in (
        "final_report",
        "round_number",
        "is_validated",
        "rejection_flag",
        "validation_notes",
        "analysis_payload",
        "execution_state",
        "sanitized_tool_calls",
        "error_log",
    ):
        assert traced[field] == untraced[field], f"{field} diverged under tracing"


def test_the_graph_does_advance_its_state():
    """Guards the test above from passing vacuously: if the graph were inert,
    'same outcome' would be trivially true and would prove nothing."""
    start = poisoned_initial_state()
    end = _run(redacted_trace_config(InMemorySink(), entities=CLIENT_ENTITIES))

    assert end["final_report"] is not None
    assert end["final_report"] != start.final_report
    assert end["is_validated"] is True
    assert end["execution_state"] != start.execution_state


# ==========================================================================
# 2. NON-MUTATION -- redact the telemetry copy, never the live state
# ==========================================================================
#
# This is the constraint that makes the whole design work. Person 2's
# grounding validator checks every verbatim_quote against state.raw_input; if
# redaction touched live state, every clause would fail grounding and the
# graph would degrade to partial-output on every single run.


def test_redaction_does_not_mutate_the_payload_it_inspects():
    state = poisoned_initial_state()
    before = state.model_dump_json()

    redacted = redact_payload(state)

    assert state.model_dump_json() == before, "redactor mutated the live state"
    assert PLANTED_SECRETS["signatory_ssn"] in state.raw_input
    assert PLANTED_SECRETS["signatory_ssn"] not in str(redacted)


def test_nested_containers_are_deep_copied():
    payload = {"outer": {"inner": ["keep", PLANTED_SECRETS["signatory_email"]]}}
    redacted = redact_payload(payload)

    assert payload["outer"]["inner"][1] == PLANTED_SECRETS["signatory_email"]
    assert redacted["outer"]["inner"][1] == "[REDACTED:EMAIL]"
    assert redacted["outer"]["inner"] is not payload["outer"]["inner"]


def test_grounding_still_works_after_a_traced_run():
    """The concrete consequence of non-mutation: Person 2's invariant holds."""
    from contract import validate_grounded

    state = poisoned_initial_state()
    result = _run(redacted_trace_config(InMemorySink(), entities=CLIENT_ENTITIES))

    # every quote in the analysis is still groundable against the source text
    validate_grounded(result["analysis_payload"], state.raw_input)


# ==========================================================================
# 3. FAIL-CLOSED BEHAVIOUR -- every escape hatch returns a sentinel
# ==========================================================================


def test_depth_cap_redacts_rather_than_returning_the_value():
    """The recursion guard must not become an exfiltration primitive.

    If hitting MAX_DEPTH returned the unexamined value, anything nested one
    level deeper than the cap would be copied out verbatim -- and nesting
    depth is attacker-controlled the moment any node writes model output
    into state.
    """
    secret = PLANTED_SECRETS["langsmith_api_key"]

    payload: object = secret
    for _ in range(MAX_DEPTH + 5):
        payload = {"nested": payload}

    redacted = redact_payload(payload)
    rendered = str(redacted)

    assert secret not in rendered, "secret escaped past the depth cap"
    assert "[REDACTED:MAX_DEPTH_EXCEEDED]" in rendered


def test_cycles_fail_closed():
    payload: dict = {"name": "loop"}
    payload["self"] = payload

    redacted = redact_payload(payload)
    assert "[REDACTED:CYCLIC_REFERENCE]" in str(redacted)


def test_unknown_object_types_fail_closed():
    """An arbitrary object's repr is exactly where credentials hide -- an HTTP
    client whose repr includes its Authorization header, a DB session whose
    repr includes its DSN."""

    class Connection:
        def __repr__(self) -> str:
            return f"<Connection dsn={PLANTED_SECRETS['db_connection']}>"

    redacted = redact_payload({"conn": Connection()})

    assert PLANTED_SECRETS["db_connection"] not in str(redacted)
    assert redacted["conn"] == "[REDACTED:UNSUPPORTED_TYPE]:Connection"


def test_a_redactor_crash_drops_the_payload_instead_of_passing_it_through():
    class Exploding:
        def __repr__(self) -> str:
            raise RuntimeError(f"boom {PLANTED_SECRETS['signatory_ssn']}")

    class ExplodingDict(dict):
        def items(self):
            raise RuntimeError(f"boom {PLANTED_SECRETS['signatory_ssn']}")

    result = safe_redact(ExplodingDict(a=1))

    # the payload is gone, and the exception MESSAGE is not substituted for it
    # -- an exception raised while walking a payload routinely quotes the
    # offending value straight back.
    assert result == {"__redaction_error__": "RuntimeError"}
    assert PLANTED_SECRETS["signatory_ssn"] not in str(result)


def test_sensitive_keys_are_dropped_on_name_alone():
    """Belt to the regex braces: a key called `api_key` is dropped whatever
    its value looks like, including a format no pattern would match."""
    redacted = redact_payload({"api_key": "totally-unrecognisable-format-42"})
    assert redacted["api_key"] == "[REDACTED:SENSITIVE_KEY]"


# ==========================================================================
# 4. ROUTE CONTROL -- the second, invisible upload path
# ==========================================================================
#
# Attaching a RedactingTracer protects the route you can see. With a tracing
# env var set, langchain-core ALSO auto-attaches a LangChainTracer that
# resolves its client through run_trees.get_cached_client() -- a bare,
# unredacted Client. The local sink would look spotless while the real state
# went to the cloud in parallel.


@pytest.fixture
def clean_tracing_env(monkeypatch):
    for var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING"):
        monkeypatch.delenv(var, raising=False)
    yield monkeypatch


def test_the_langsmith_client_covers_all_four_channels():
    """Verified against the SDK internals, not assumed:
    `_hide_run_error` consults ONLY `_anonymizer`, and `_hide_run_metadata`
    never consults it. Setting hide_inputs/hide_outputs alone ships error
    strings in the clear."""
    client = build_redacting_client(auto_batch_tracing=False)

    assert callable(client._anonymizer), "errors would be uploaded unredacted"
    assert callable(client._hide_metadata), "metadata would be uploaded unredacted"
    assert callable(client._hide_inputs)
    assert callable(client._hide_outputs)
    assert _client_is_redacting(client)


def test_a_bare_client_is_recognised_as_unsafe():
    from langsmith import Client

    assert not _client_is_redacting(Client(auto_batch_tracing=False))
    assert not _client_is_redacting(None)


def test_implicit_tracing_route_is_detected_and_refused(clean_tracing_env):
    """With ambient tracing on and an unredacted global client, the audit must
    see the second route and refuse to hand back a config."""
    from langsmith import Client, run_trees

    clean_tracing_env.setenv("LANGSMITH_TRACING", "true")
    clean_tracing_env.setattr(
        run_trees, "_CLIENT", Client(auto_batch_tracing=False), raising=False
    )

    routes = audit_tracing_routes([RedactingTracer(InMemorySink())])
    implicit = [r for r in routes if r.name == "implicit:LangChainTracer"]
    assert implicit and implicit[0].redacting is False

    with pytest.raises(UnredactedTracingRouteError):
        assert_all_routes_redacted([RedactingTracer(InMemorySink())])


def test_installing_the_redacting_client_closes_the_implicit_route(clean_tracing_env):
    from langsmith import Client, run_trees

    clean_tracing_env.setenv("LANGSMITH_TRACING", "true")
    clean_tracing_env.setattr(
        run_trees, "_CLIENT", Client(auto_batch_tracing=False), raising=False
    )

    install_redacted_tracing(auto_batch_tracing=False)

    assert _client_is_redacting(run_trees.get_cached_client())
    assert_all_routes_redacted([RedactingTracer(InMemorySink())])  # no raise

    # and the config factory performs that remediation for you
    clean_tracing_env.delenv("LANGSMITH_TRACING", raising=False)
    config = redacted_trace_config(InMemorySink(), entities=CLIENT_ENTITIES)
    assert config["callbacks"]


def test_no_env_tracing_means_no_implicit_route(clean_tracing_env):
    routes = audit_tracing_routes([RedactingTracer(InMemorySink())])
    assert all(r.name != "implicit:LangChainTracer" for r in routes)
    assert all(r.redacting for r in routes)


def test_a_foreign_callback_handler_is_flagged(clean_tracing_env):
    """Any handler that is not ours is, by definition, not known to redact."""

    class SomeoneElsesTracer:
        pass

    with pytest.raises(UnredactedTracingRouteError):
        assert_all_routes_redacted([SomeoneElsesTracer()])


# ==========================================================================
# 4b. SHORT FORMS OF PARTY NAMES
# ==========================================================================
#
# Redacting the full legal name alone leaves the party identified everywhere
# the contract uses its defined short form -- which, after the opening
# paragraph, is everywhere that matters.


def test_short_forms_are_derived_from_legal_names():
    assert derive_short_forms("Globex Industries Ltd") == [
        "Globex Industries",
        "Globex",
    ]
    assert derive_short_forms("Acme Corporation") == ["Acme"]
    assert derive_short_forms("Initech LLC") == ["Initech"]
    # a generic leading token is skipped in favour of the distinctive one
    assert "Boeing" in derive_short_forms("The Boeing Company")
    # dropping the suffix must not leave dangling punctuation
    assert "Wayne Enterprises" in derive_short_forms("Wayne Enterprises, Inc.")


def test_derivation_refuses_generic_short_forms():
    """Over-redaction is its own failure. A party called 'First National Bank'
    must not cause the word 'First' to be blanked out of every trace."""
    assert derive_short_forms("First National Bank") == []
    assert parse_defined_short_forms('X Ltd (the "Company") and Y (the "Bank")') == []


def test_contract_defined_short_forms_are_parsed():
    """More reliable than guessing, and catches aliases no derivation rule
    would produce -- a code name, an acronym, a trading name."""
    assert set(parse_defined_short_forms(poisoned_initial_state().raw_input)) == {
        "Acme",
        "Globex",
    }


def test_bare_short_forms_are_redacted_in_a_real_graph_run():
    """The regression this closes: 'Globex breached its duty to Acme' is
    capitalised prose. No key marks it, no pattern matches it."""
    sink = InMemorySink()
    _run(redacted_trace_config(sink, entities=CLIENT_ENTITIES))
    blob = sink.as_json()

    for short_form, full_name in SHORT_FORM_CORPUS:
        assert count_standalone_occurrences(blob, short_form, full_name) == 0, (
            f"bare short form {short_form!r} survived into telemetry"
        )


def test_short_forms_do_leak_without_the_guardrail():
    """Confirms the gap was real rather than hypothetical."""
    from student_5_trace.snippet import unguarded_trace_config

    sink = InMemorySink()
    _run(unguarded_trace_config(sink))
    blob = sink.as_json()

    assert all(
        count_standalone_occurrences(blob, short, full) > 0
        for short, full in SHORT_FORM_CORPUS
    )


def test_short_form_redaction_does_not_eat_ordinary_prose():
    """The cost side: only the configured/derived parties are replaced."""
    text = "Acme escalated the matter to an arbitrator in Delaware on 2026-08-01."
    redacted = redact_string(text, expand_entities(["Acme Corporation"]))

    assert "[REDACTED:PARTY]" in redacted
    assert "arbitrator in Delaware" in redacted
    assert "2026-08-01" in redacted


# ==========================================================================
# 5. PSEUDONYMIZATION, NOT ANONYMIZATION
# ==========================================================================


def test_fingerprint_carries_no_document_text():
    text = poisoned_initial_state().raw_input
    printed = fingerprint(text)

    assert PLANTED_SECRETS["signatory_ssn"] not in printed
    assert "Indemnification" not in printed
    assert printed.startswith("[REDACTED:TEXT hmac=")


def test_fingerprint_is_stable_within_a_process_and_keyed_across_them():
    """Correlation without disclosure: the same document gives the same
    pseudonym inside one process, but the digest cannot be recomputed by
    anyone who does not hold the key -- which is nobody, since the key is
    never logged, serialized or sent to the sink."""
    import student_5_trace.snippet as trace

    text = "CONFIDENTIAL SETTLEMENT AGREEMENT between two named parties."
    first = fingerprint(text)
    assert fingerprint(text) == first

    original_key = trace._FINGERPRINT_KEY
    try:
        trace._FINGERPRINT_KEY = b"a-different-process-key"
        assert fingerprint(text) != first
    finally:
        trace._FINGERPRINT_KEY = original_key


def test_fingerprint_buckets_the_length_instead_of_publishing_it():
    """An exact character count is itself a weak identifier of a known
    document, so the length is coarsened to a bucket."""
    short, long = fingerprint("x" * 100), fingerprint("x" * 5000)

    assert "len=<256" in short
    assert "len=4k-16k" in long
    assert "100" not in short and "5000" not in long


# ==========================================================================
# 6. THE COST OF THE GUARDRAIL -- debuggability that survives
# ==========================================================================


def test_operational_fields_survive_redaction():
    """What the trace is FOR. If routing state does not survive, the privacy
    layer has bought confidentiality by destroying observability."""
    sink = InMemorySink()
    _run(
        redacted_trace_config(
            sink,
            entities=CLIENT_ENTITIES,
            metadata=dict(POISONED_METADATA),
            tags=list(POISONED_TAGS),
        )
    )
    blob = sink.as_json()

    for marker in (
        "round_number",
        "next_route",
        "is_validated",
        "rejection_flag",
        "analysis_retry_count",
        "legal_contract_review",   # task_domain
        "coordinator",             # node names
        "indemnification",         # clause_type enum
        "critical",                # risk_level enum
        "commercial_msa",          # benign metadata
        "graph_version",
    ):
        assert marker in blob, f"redaction destroyed operational field {marker!r}"

    assert scan_for_leaks(sink, SECRET_CORPUS).unique_count == 0


def test_documented_false_positive_rate():
    """One known false positive, deliberately kept in the corpus and
    published rather than quietly tuned away: an internal part number that
    is digit-for-digit indistinguishable from a US EIN."""
    preserved = sum(
        1 for c in BENIGN_LOOKALIKES if redact_string(c["value"]) == c["value"]
    )
    total = len(BENIGN_LOOKALIKES)

    assert preserved == total - 1, "false-positive count changed; update METRICS.md"
    assert redact_string("PN 12-3456789") == "PN [REDACTED:TAX_ID]"


def test_contract_is_untouched():
    """Contract-freeze compliance: this layer adds no state fields."""
    fields = set(AgentState.model_fields)
    assert "trace_id" not in fields
    assert "redaction_log" not in fields
    # and the interceptor never writes to state at all
    state = poisoned_initial_state()
    snapshot = state.model_dump_json()
    RedactingTracer(InMemorySink(), CLIENT_ENTITIES).on_chain_start(
        {"name": "coordinator"}, state, metadata={}, tags=[]
    )
    assert state.model_dump_json() == snapshot
