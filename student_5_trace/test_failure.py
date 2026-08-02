"""
test_failure.py -- Person 5: Data Privacy Leak via Telemetry.

Reproduces the leak by running the REAL compiled LangGraph over a contract
carrying 13 planted secrets, with tracing configured the way an unconfigured
LangSmith integration configures it -- i.e. ship everything -- and then again
with the redaction interceptor in front.

Runs entirely offline. The "telemetry backend" is an in-process sink that
records exactly what would have been uploaded. Nothing is transmitted.

Run:  pytest student_5_trace/test_failure.py -v
      python student_5_trace/test_failure.py      # before/after table
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_system import build_graph
from student_5_trace.fixtures import (
    BENIGN_LOOKALIKES,
    CLIENT_ENTITIES,
    OBFUSCATED_SECRETS,
    PLANTED_SECRETS,
    POISONED_ERROR_MESSAGE,
    POISONED_METADATA,
    POISONED_TAGS,
    SECRET_CORPUS,
    poisoned_analyzer_stub,
    poisoned_initial_state,
)
from student_5_trace.snippet import (
    InMemorySink,
    RawTracer,
    RedactingTracer,
    redact_error,
    redact_string,
    redacted_trace_config,
    scan_for_leaks,
    unguarded_trace_config,
)


def _run(config_factory, sink):
    """Drive the real graph once with the given tracing configuration."""
    app = build_graph(analyzer=poisoned_analyzer_stub)
    config = config_factory(
        sink,
        metadata=dict(POISONED_METADATA),
        tags=list(POISONED_TAGS),
    )
    return app.invoke(poisoned_initial_state(), config=config)


def run_unguarded(sink: InMemorySink):
    return _run(unguarded_trace_config, sink)


def run_guarded(sink: InMemorySink):
    def factory(s, **kwargs):
        return redacted_trace_config(s, entities=CLIENT_ENTITIES, **kwargs)

    return _run(factory, sink)


# ==========================================================================
# THE FAILURE
# ==========================================================================


def test_unguarded_tracing_leaks_every_planted_secret():
    """Default tracing ships the whole contract to the observability backend."""
    sink = InMemorySink()
    run_unguarded(sink)
    report = scan_for_leaks(sink, SECRET_CORPUS)

    assert report.unique_count == len(SECRET_CORPUS), report.summary()
    assert report.total_occurrences > 100, report.summary()
    # the SSN and the production DSN are both in the stream verbatim
    assert report.per_secret[PLANTED_SECRETS["signatory_ssn"]] > 0
    assert report.per_secret[PLANTED_SECRETS["db_connection"]] > 0


# ==========================================================================
# THE GUARDRAIL
# ==========================================================================


def test_redaction_interceptor_eliminates_every_leak():
    sink = InMemorySink()
    run_guarded(sink)
    report = scan_for_leaks(sink, SECRET_CORPUS)

    assert report.unique_count == 0, f"still leaking: {report.unique_exposed}"
    assert report.total_occurrences == 0, report.summary()


def test_guardrail_still_produces_a_full_trace():
    """Redaction must not silence telemetry -- that would trade one failure
    for another. Same number of events either way."""
    raw_sink, safe_sink = InMemorySink(), InMemorySink()
    run_unguarded(raw_sink)
    run_guarded(safe_sink)

    assert len(safe_sink) == len(raw_sink) > 0


# ==========================================================================
# CHANNEL COVERAGE -- inputs/outputs are only one of four channels
# ==========================================================================


def test_metadata_channel_is_redacted():
    """Metadata travels beside inputs/outputs and needs its own hook.

    In the SDK this is `hide_metadata`, which never consults the anonymizer;
    an integration that sets only hide_inputs/hide_outputs leaks all of it.
    """
    sink = InMemorySink()
    run_guarded(sink)

    blob = sink.as_json()
    assert POISONED_METADATA["langsmith_api_key"] not in blob
    assert POISONED_METADATA["reviewer_email"] not in blob
    assert POISONED_METADATA["database"] not in blob
    assert POISONED_METADATA["client_name"] not in blob
    # benign operational metadata survives
    assert "commercial_msa" in blob


def test_tags_channel_is_redacted():
    """Tags are a third channel, and the SDK does not scrub them at all."""
    sink = InMemorySink()
    run_guarded(sink)

    blob = sink.as_json()
    assert PLANTED_SECRETS["internal_host"] not in blob
    assert "team:legal-ops" in blob  # non-sensitive tag preserved


def test_error_channel_is_redacted():
    """An exception message is the most dangerous channel: its contents were
    never in graph state, so no node's own hygiene ever touched them."""
    error = RuntimeError(POISONED_ERROR_MESSAGE)

    leaked = f"{type(error).__name__}: {error}"
    assert PLANTED_SECRETS["db_connection"] in leaked  # the raw form leaks

    redacted = redact_error(error)
    assert PLANTED_SECRETS["db_connection"] not in redacted
    assert PLANTED_SECRETS["langsmith_api_key"] not in redacted
    assert "Fak3P4ss" not in redacted
    assert "RuntimeError" in redacted  # the diagnosis survives


def test_error_channel_is_redacted_through_the_interceptor():
    sink = InMemorySink()
    tracer = RedactingTracer(sink, CLIENT_ENTITIES)
    tracer.on_chain_error(RuntimeError(POISONED_ERROR_MESSAGE), name="actor")

    blob = sink.as_json()
    assert PLANTED_SECRETS["db_connection"] not in blob
    assert "Fak3P4ss" not in blob


def test_unguarded_tracer_leaks_the_error_message():
    """Confirms the failure is real and not a strawman."""
    sink = InMemorySink()
    RawTracer(sink).on_chain_error(RuntimeError(POISONED_ERROR_MESSAGE), name="actor")
    assert PLANTED_SECRETS["db_connection"] in sink.as_json()


# ==========================================================================
# KNOWN LIMITS -- measured, not assumed
# ==========================================================================


def test_obfuscated_formats_match_documented_expectations():
    """Regexes catch known formats. This records exactly which unusual ones
    they miss, so METRICS.md publishes a measurement instead of a claim."""
    for case in OBFUSCATED_SECRETS:
        redacted = redact_string(case["value"])
        caught = redacted != case["value"]
        assert caught == case["expected_caught"], (
            f"{case['label']}: expected caught={case['expected_caught']}, "
            f"got {caught!r} ({redacted!r})"
        )


def test_benign_lookalikes_are_preserved():
    """A redactor that eats operational strings costs debuggability, and a
    guardrail that destroys debuggability gets switched off."""
    for case in BENIGN_LOOKALIKES:
        redacted = redact_string(case["value"])
        preserved = redacted == case["value"]
        assert preserved == case["expected_preserved"], (
            f"{case['label']}: expected preserved={case['expected_preserved']}, "
            f"got {preserved!r} ({redacted!r})"
        )


# ==========================================================================
# DEMO
# ==========================================================================


def main() -> None:
    print("=" * 74)
    print("PERSON 5 -- DATA PRIVACY LEAK VIA TELEMETRY")
    print("=" * 74)

    raw_sink = InMemorySink()
    run_unguarded(raw_sink)
    before = scan_for_leaks(raw_sink, SECRET_CORPUS)

    print("\n1. WITHOUT GUARDRAIL  (default LangSmith tracing behaviour)")
    print(f"   trace events emitted          : {len(raw_sink)}")
    print(f"   payload shipped               : {raw_sink.byte_size:,} bytes")
    print(f"   unique planted secrets exposed: {before.unique_count} of {before.corpus_size}")
    print(f"   total exposure occurrences    : {before.total_occurrences}")
    print("   sample of what reached the dashboard:")
    for name in ("signatory_ssn", "db_connection", "escrow_iban", "deal_value"):
        value = PLANTED_SECRETS[name]
        print(f"      {name:18} x{before.per_secret[value]:<4} {value[:52]}")

    safe_sink = InMemorySink()
    run_guarded(safe_sink)
    after = scan_for_leaks(safe_sink, SECRET_CORPUS)

    print("\n2. WITH GUARDRAIL  (State Redaction Interceptor)")
    print(f"   trace events emitted          : {len(safe_sink)}")
    print(f"   payload shipped               : {safe_sink.byte_size:,} bytes")
    print(f"   unique planted secrets exposed: {after.unique_count} of {after.corpus_size}")
    print(f"   total exposure occurrences    : {after.total_occurrences}")
    print("   redaction rules that fired:")
    tokens = re.findall(r"\[REDACTED:[A-Z_]+", safe_sink.as_json())
    for token, count in sorted(Counter(tokens).items(), key=lambda kv: -kv[1]):
        print(f"      {token + ']':<34} x{count}")
    print("   operational fields still visible to an on-call engineer:")
    print("      round_number, next_route, is_validated, rejection_flag,")
    print("      clause_type, risk_level, node names, graph_version")

    print("\n3. RESULT")
    print(f"   unique planted secrets exposed: {before.unique_count} -> {after.unique_count}"
          f"  (of {before.corpus_size})")
    print(f"   total exposure occurrences    : {before.total_occurrences} -> "
          f"{after.total_occurrences}")
    reduction = 100 * (raw_sink.byte_size - safe_sink.byte_size) / raw_sink.byte_size
    print(f"   telemetry payload             : {raw_sink.byte_size:,} -> "
          f"{safe_sink.byte_size:,} bytes  ({reduction:.1f}% smaller)")
    print(f"   trace events retained         : {len(safe_sink)}/{len(raw_sink)} (100%)")


if __name__ == "__main__":
    main()
