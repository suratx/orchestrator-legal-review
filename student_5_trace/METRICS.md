# Person 5 Metrics — Data Privacy Leak via Telemetry

**Guardrail:** centralized State Redaction Interceptor on the graph→telemetry boundary
**Measured in the fully integrated graph** (all six guardrails active, so the
transition count includes the Context Manager). **Measured by:** `python student_5_trace/test_failure.py` (reproduction) and
`pytest student_5_trace/ -q` (42 tests). Every number below is produced by
running the real compiled LangGraph — no estimates.

**Method.** A contract carrying **13 planted secrets** (`fixtures.SECRET_CORPUS`)
is driven through the real graph twice: once with tracing configured the way an
unconfigured LangSmith integration configures it, once with the interceptor in
front. `scan_for_leaks()` then counts how many of those 13 exact strings survive
into the telemetry stream.

---

## Headline: before / after

| Metric | Without guardrail | With guardrail |
|---|---:|---:|
| **Unique planted secrets exposed** | **13 of 13** | **0 of 13** |
| **Total exposure occurrences** | **753** | **0** |
| **Bare party short forms exposed** | **306** | **0** |
| Trace events emitted | 28 | 28 |
| Telemetry payload shipped | 110,389 bytes | 79,524 bytes (−28.0%) |
| Operational fields preserved | 11 of 11 | 11 of 11 |
| Graph outcome (`final_report`) | identical | identical |

### Why two leak numbers, not one

They answer different questions and a single figure hides one of them.
**Unique secrets exposed** is the privacy question — *which* secrets are now in
the dashboard, i.e. what has to be rotated and who has to be notified. **Total
occurrences** is the blast-radius question — one API key appearing in 38 events
is one credential to rotate but 38 records to purge. An incident response needs
both.

### Per-secret exposure, unguarded

| Planted secret | Occurrences | Guarded |
|---|---:|---:|
| `Acme Corporation` (client name) | 150 | 0 |
| `Globex Industries Ltd` (counter-party) | 147 | 0 |
| `db-prod-01.acme.internal` (internal host) | 92 | 0 |
| `j.okonkwo@globex-industries.example` | 64 | 0 |
| `postgresql://svc_review:Fak3P4ss@…` (prod DSN) | 50 | 0 |
| `(713) 555-0182` (signatory phone) | 50 | 0 |
| `76-4820193` (counter-party EIN) | 43 | 0 |
| `$1,250,000.00` (deal value) | 43 | 0 |
| `666-88-7391` (signatory SSN) | 25 | 0 |
| `GB29NWBK60161331926819` (escrow IBAN) | 25 | 0 |
| `4820 Kirby Drive, Houston, TX 77098` | 25 | 0 |
| `AKIAIOSFODNN7EXAMPLE` (AWS key) | 25 | 0 |
| `lsv2_pt_…` (LangSmith API key) | 14 | 0 |
| **Total** | **753** | **0** |

### Party short forms — the leak the full-name rule misses

Redacting `Globex Industries Ltd` does nothing for `"Globex breached its duty
to Acme"`. Contracts define short forms in their opening paragraph —
`... Acme Corporation ("Acme") and Globex Industries Ltd ("Globex") ...` — and
use them for everything after, which is exactly where the damaging sentences
live. Nothing marks a short form as sensitive: no key holds it, no pattern
matches it, it is just a capitalised word.

| Short form | Standalone occurrences, unguarded | Guarded |
|---|---:|---:|
| `Globex` | 139 | 0 |
| `Acme` | 167 | 0 |
| **Total** | **306** | **0** |

Counted with `count_standalone_occurrences()`, which excludes hits inside the
full name — a derived short form is normally a prefix of the name it came from,
so a naive substring count would tally every full-name occurrence as a
short-form leak too and inflate both columns.

Short forms come from two places, mirroring how the full names are obtained:

1. **Derived** by stripping corporate suffixes — `Globex Industries Ltd` →
   `Globex Industries`, `Globex`; `Acme Corporation` → `Acme`.
2. **Parsed** from the contract's own parenthesised definitions, which catches
   aliases no derivation rule would produce: a code name, an acronym, a trading
   name.

Derivation is filtered, because over-redaction is its own failure mode. A party
called `First National Bank` yields **no** short form — every token is too
generic, and blanking "First" out of every trace would destroy readable prose
while protecting nobody. `The Boeing Company` correctly skips the generic
leading token and yields `Boeing`. Enforced by
`test_derivation_refuses_generic_short_forms`.

---

## Channel coverage — inputs/outputs is only one of four

The common half-measure is to redact `inputs` and `outputs` and declare victory.
Verified against the installed `langsmith 0.10.15` source, that leaves two
channels wide open:

| Channel | SDK hook | Consults `anonymizer`? | Consequence of the half-measure |
|---|---|---|---|
| inputs / outputs | `_hide_run_inputs` / `_hide_run_outputs` | yes (takes precedence) | covered |
| **metadata** | `_hide_run_metadata` | **no** — needs its own `hide_metadata=` | client name + internal host uploaded in full |
| **error strings** | `_hide_run_error` | **only** `_anonymizer`; ignores `hide_inputs`/`hide_outputs` entirely | tracebacks and any credential inside them uploaded in full |
| tags | none | n/a — SDK does not scrub tags at all | `host:db-prod-01.acme.internal` uploaded in full |

So the correct client configuration is `anonymizer=` (which covers inputs,
outputs **and** errors) **plus** `hide_metadata=` — not the intuitive
`hide_inputs`+`hide_outputs`+`hide_metadata`, which ships every error message
in the clear. Tags are handled in the interceptor because the SDK will not.

| Channel | Secrets before | Secrets after |
|---|---:|---:|
| inputs / outputs | 11 | 0 |
| metadata | 4 | 0 |
| tags | 1 | 0 |
| error string | 2 | 0 |

---

## Closing the second upload route

Attaching a `RedactingTracer` protects the route you can see. With
`LANGSMITH_TRACING` set, `langchain-core` **also** auto-attaches a
`LangChainTracer`, and that tracer resolves its client through
`run_trees.get_cached_client()` — a bare, unredacted `Client()`:

```
LangChainTracer.__init__:  self.client = client or get_client()
get_client()            -> run_trees.get_cached_client()
get_cached_client()     -> global _CLIENT = Client()      # no redaction
```

The local sink would have shown a spotless, fully-redacted stream **while the
unredacted state went to the cloud in parallel** — a guardrail reporting success
precisely while it fails.

| Route | Detected by `audit_tracing_routes()` | Closed by |
|---|---|---|
| explicit `callbacks=[…]` | yes | `RedactingTracer` |
| implicit `LangChainTracer` (env-var driven) | yes | seeding the global `_CLIENT` with the redacting client |
| a third-party callback handler | yes — flagged, not trusted | run refused |

`redacted_trace_config()` audits before returning and raises
`UnredactedTracingRouteError` rather than hand back a config while any route
would upload in the clear. **Fail closed, not fail quiet.**

---

## Cost of the guardrail

| | Median per graph run |
|---|---:|
| Unguarded tracing | 3.01 ms |
| **Redacted tracing** | **37.17 ms** |
| Redaction overhead | **+34.16 ms/run** (1.2 ms per trace event) |

Reported honestly both ways: **large in relative terms** against a nearly-free
baseline, and **+34 ms absolute** on a run whose real-world cost is dominated by LLM calls
measured in seconds. On the live Ollama path a single Analyzer call is ~2–4 s,
so redaction is well under 1% of wall-clock. The relative figure looks alarming
only because the baseline it is measured against is nearly free.

Short-form expansion is part of that: each derived form is an additional
case-insensitive pass over every string in the payload. Cheap relative to what
it closes — 306 exposures.

Payload size *falls* 28.0%, because fingerprinting the contract body replaces
kilobytes of clause text with a 16-character digest — the guardrail reduces
egress volume as a side effect.

---

## What it costs in observability — measured, not asserted

A redactor that eats operational strings gets switched off, and a guardrail
that is switched off is a privacy failure by another route. So the false
positives are measured against a corpus of harmless strings shaped like
secrets (`fixtures.BENIGN_LOOKALIKES`):

| Benign value | Preserved? |
|---|---|
| `Section 4.2` (clause reference) | ✅ |
| `2026-08-01` (ISO date) | ✅ |
| `MSA-001` (matter ID) | ✅ |
| `llama3.2` (model name) | ✅ |
| `round 3 of 5` | ✅ |
| `1.0.0` (contract version) | ✅ |
| `PN 12-3456789` (internal part number) | ❌ **known false positive** |

**False-positive rate: 1 of 7 (14.3%).** The failure is not fixable by tuning:
a US EIN is `NN-NNNNNNN` and so is that part number — they are digit-for-digit
identical, and no regex can separate them without context. It is kept in the
corpus and published rather than quietly tuned away, and
`test_documented_false_positive_rate` fails if the rate changes without the
table being updated.

Everything an on-call engineer actually needs still reaches the dashboard:
`round_number`, `next_route`, `is_validated`, `rejection_flag`,
`analysis_retry_count`, `task_domain`, node names, `clause_type`, `risk_level`,
and non-sensitive metadata. Asserted by `test_operational_fields_survive_redaction`.

---

## Known blind spots — measured against deliberately awkward formats

Regexes match formats, so anything that changes the format evades them
(`fixtures.OBFUSCATED_SECRETS`):

| Variant | Caught? |
|---|---|
| `SSN 666 88 7391` (spaces not hyphens) | ✅ |
| `+44 20 7946 0958` (international phone) | ✅ |
| `NHS 943 476 5919` (UK national ID) | ⚠️ **caught incidentally, mislabelled** |
| `j.okonkwo [at] globex-industries [dot] example` | ❌ missed |
| `bHN2Ml9wdF9…` (base64-wrapped key) | ❌ missed |

**Recall on non-standard formats: 3 of 5 (60%), with 1 of those 3 mislabelled.**
The NHS case is worth stating plainly: a UK NHS number is 3-3-4 digits, which is
also the US phone shape, so it is redacted as `[REDACTED:PHONE]`. The value is
protected — which is what matters — but anyone reading the trace draws the wrong
conclusion about what kind of identifier was present. Real non-US coverage needs
a dedicated rule, not a lucky collision.

Three structural defences exist precisely because pattern matching is not
sufficient on its own, and none of them depend on recognising a format:

1. **Key-name denylist** — `api_key`, `password`, `ssn`, `iban` and friends are
   dropped on the key name whatever the value looks like.
2. **Field fingerprinting** — `raw_input` and `verbatim_quote` are replaced
   wholesale, so the contract body never has to be pattern-matched at all. (This
   is why `[REDACTED:SSN]` and `[REDACTED:BANK_ACCOUNT]` do not appear in the
   demo's fired-rule list: those values live inside `raw_input`, which is
   fingerprinted before any regex runs.)
3. **Fail-closed defaults** — depth cap, cycles, unknown object types and
   redactor crashes all return a sentinel, never the value.

---

## Fail-closed behaviour

| Escape hatch | Behaviour | Test |
|---|---|---|
| Nesting deeper than `MAX_DEPTH` (12) | `[REDACTED:MAX_DEPTH_EXCEEDED]` | `test_depth_cap_redacts_rather_than_returning_the_value` |
| Cyclic reference | `[REDACTED:CYCLIC_REFERENCE]` | `test_cycles_fail_closed` |
| Arbitrary object (`repr` may hold a DSN or auth header) | `[REDACTED:UNSUPPORTED_TYPE]:<Class>` | `test_unknown_object_types_fail_closed` |
| The redactor itself raises | `{"__redaction_error__": "<ExcType>"}` — type name only, never `str(exc)` | `test_a_redactor_crash_drops_the_payload_instead_of_passing_it_through` |
| An unredacted tracing route exists | `UnredactedTracingRouteError`, run refused | `test_implicit_tracing_route_is_detected_and_refused` |

The depth cap deserves the emphasis: returning the unexamined value at the cap
would turn the recursion guard into an exfiltration primitive, since nesting
depth is attacker-controlled the moment any node writes model output into state.
The exception-message rule matters for the same reason — an exception raised
while walking a payload routinely quotes the offending value straight back, so
substituting `str(exc)` for a dropped payload would reintroduce the exact leak.

---

## Pseudonymization, not anonymization

`raw_input` and `verbatim_quote` are replaced with
`[REDACTED:TEXT hmac=<16 hex> len=<bucket>]`. Stated precisely, this is
**pseudonymization**: a digest is deterministic, so it still reveals whether two
runs saw the same document, and an adversary holding a candidate document could
confirm a match against a plain hash.

Three mitigations, and the residual risk:

| | Choice | Why |
|---|---|---|
| Algorithm | **HMAC**-SHA-256, not bare SHA-256 | the digest cannot be recomputed without the key |
| Key | process-local, 32 random bytes; never logged, serialized or sent to the sink | nobody outside the process can brute-force candidate documents |
| Length | bucketed (`<256`, `256-1k`, `1k-4k`, `4k-16k`, `>16k`) | an exact character count is itself a weak identifier of a known document |

**Residual risk, disclosed:** identical documents remain linkable *within* a
process, and the bucket still reveals the contract's rough size. That is the
deliberate trade — it is exactly the property that lets an on-call engineer ask
"is this the same document as the run that failed?" without the text ever
leaving the process. `TRACE_FINGERPRINT_KEY` can supply a stable local key when
cross-restart correlation is worth more than key ephemerality.

---

## Contract-freeze compliance

Zero changes to `contract.py`. This layer adds **no state fields** — the answer
to the open question in `CONTRACT_FREEZE_NOTES.md` §7 for the tracing half of
Person 5's scope. It is read-only on state and side-channels to telemetry,
exactly as `ARCHITECTURE_DESIGN.md` §4 specifies.

That is not merely tidiness. Person 2's grounding validator checks every
`verbatim_quote` against `state.raw_input`; a redactor that scrubbed live state
instead of the outbound copy would make every clause fail grounding and collapse
the graph into partial-output on every run. `redact_payload()` is a pure
function returning a deep copy, and
`test_redaction_does_not_mutate_the_payload_it_inspects` plus
`test_grounding_still_works_after_a_traced_run` hold that line.

---

## Test summary

```
pytest student_5_trace/ -q     ->  42 passed
```

Covering the reproduction against the real compiled graph, all four telemetry
channels, tracing-route control, party short forms, every fail-closed path, the
pseudonymization properties, the measured cost in observability, and the safety
mandate below.

---

## Safety mandate compliance

> *"All actions interacting with external infrastructure must be mocked... even
> inside your broken test failure instances."*

This layer is the one most exposed to that rule, since its entire subject is
shipping data to a third-party cloud service. So the property is **tested, not
promised**:

| Requirement | How it is enforced | Test |
|---|---|---|
| No network traffic | `socket.connect` monkeypatched to raise; the full guarded run still completes | `test_no_network_traffic_during_a_traced_run` |
| ...including in the broken path | same, over the *unguarded* reproduction — the "leak" goes to an in-process list | `test_no_network_traffic_during_the_unguarded_reproduction` |
| No file modifications | the module is asserted to contain no `open(`, `os.remove`, `os.system`, `shutil.`, `subprocess` | `test_module_touches_no_filesystem` |
| No real telemetry upload | no `create_run` / `update_run` / `batch_ingest` / `.flush(` call exists anywhere | `test_no_real_langsmith_upload_path_is_invoked` |

Two deliberate design decisions behind those tests:

- **`InMemorySink` only.** An earlier draft carried a `JsonlSink` that appended
  trace events to a local file. It was **removed** rather than kept: it wrote a
  file, and it would have written to disk exactly the data this layer exists to
  keep out of storage. The reproduction demonstrates a total PII leak without a
  single byte being written or transmitted.
- **A LangSmith `Client` is constructed, never used to transmit.** It exists so
  its redaction configuration can be *verified* (`anonymizer` and
  `hide_metadata` both set). Construction opens no connection — confirmed with
  sockets blocked — and no upload method is ever called.

Every planted value is synthetic and drawn from a reserved range wherever one
exists: `AKIAIOSFODNN7EXAMPLE` is AWS's published documentation placeholder, the
IBAN is the ISO example value, phone numbers are in the NANP `555-01xx` and
Ofcom `020 7946 0xxx` reserved test ranges, the SSN is in the `666-xx-xxxx`
block the SSA has never issued, and the email uses the reserved `.example` TLD.
