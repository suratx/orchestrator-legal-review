# Student 5 Metrics — Data Privacy Leak via Telemetry

## Evaluation overview

**Guardrail:** Centralized State Redaction Interceptor at the graph-to-telemetry boundary

**Reproduction commands:**

```bash
python student_5_trace/test_failure.py
pytest student_5_trace/ -q
```

The guardrail was evaluated using the fully integrated LangGraph with all six guardrails active. A synthetic contract containing 13 known secrets was processed twice using the same graph and input. The unguarded configuration recorded the complete telemetry payload, while the guarded configuration passed every telemetry event through the redaction interceptor.

Both configurations used an in-memory telemetry sink. No data was transmitted to LangSmith, written to disk, or sent to any external system.

The planted values included personal identifiers, party names, financial information, database credentials, internal infrastructure details, and API keys. Because all sensitive values were known in advance, leakage could be measured directly against a defined ground-truth corpus.

## Before-and-after results

| Metric | Without guardrail | With guardrail |
|---|---:|---:|
| **Unique planted secrets exposed** | **13 of 13** | **0 of 13** |
| **Total secret occurrences exposed** | **753** | **0** |
| **Standalone party aliases exposed** | **306** | **0** |
| Trace events retained | 28 | 28 |
| Telemetry payload size | 110,389 bytes | 79,524 bytes |
| Payload reduction | — | 28.0% |
| Operational fields preserved | 11 of 11 | 11 of 11 |
| Final graph output | Baseline | Identical to baseline |

The unique-secret count identifies how many distinct confidential values reached telemetry. The total-occurrence count measures how often those values appeared across node inputs, outputs, metadata, tags, and errors. The guardrail eliminated all measured leakage while retaining every trace event.

## Per-secret exposure

| Planted value | Without guardrail | With guardrail |
|---|---:|---:|
| Client name | 150 | 0 |
| Counterparty name | 147 | 0 |
| Internal hostname | 92 | 0 |
| Signatory email | 64 | 0 |
| Database connection string | 50 | 0 |
| Signatory phone number | 50 | 0 |
| Counterparty EIN | 43 | 0 |
| Contract value | 43 | 0 |
| Signatory SSN | 25 | 0 |
| Escrow IBAN | 25 | 0 |
| Signatory address | 25 | 0 |
| AWS access key | 25 | 0 |
| LangSmith API key | 14 | 0 |
| **Total** | **753** | **0** |

All planted values were synthetic. Emails used the reserved `.example` domain, telephone numbers used reserved test ranges, the AWS key was a published placeholder, and the SSN used an unissued range.

## Party-alias protection

Contracts frequently use shortened party names after defining the full legal entities. For example, `Acme Corporation` may later appear as `Acme`, while `Globex Industries Ltd` may appear as `Globex`. These aliases can expose party identities even when the complete legal names are removed.

| Standalone alias | Without guardrail | With guardrail |
|---|---:|---:|
| `Globex` | 139 | 0 |
| `Acme` | 167 | 0 |
| **Total** | **306** | **0** |

The interceptor identifies aliases by removing corporate suffixes from known party names and parsing aliases defined in the contract. Generic terms are excluded to reduce false positives. For example, `The Boeing Company` can produce the alias `Boeing`, while `First National Bank` produces no automatically derived alias because its individual terms are too generic.

Standalone aliases are counted separately from occurrences inside full legal names to avoid duplicate counting.

## Telemetry-channel coverage

The interceptor protects four telemetry channels because sensitive information may appear outside normal graph inputs and outputs.

| Telemetry channel | Sensitive values before | Sensitive values after | Protection method |
|---|---:|---:|---|
| Inputs and outputs | 11 | 0 | Recursive payload redaction |
| Metadata | 4 | 0 | Dedicated metadata transformation |
| Tags | 1 | 0 | Explicit tag redaction |
| Error messages | 2 | 0 | Exception-string redaction |

The implementation was verified against LangSmith 0.10.15. Inputs, outputs, and error messages are protected through the client anonymizer. Metadata uses its dedicated transformation hook, while tags are sanitized directly by the interceptor.

## Guardrail mechanisms

| Mechanism | Purpose |
|---|---|
| Regular-expression registry | Detects emails, phone numbers, SSNs, EINs, IBANs, payment cards, connection strings, internal hosts, financial values, and provider-specific API keys |
| Sensitive-key denylist | Removes values stored under fields such as `api_key`, `password`, `secret`, `ssn`, and `iban`, regardless of their format |
| Entity and alias replacement | Removes client names, counterparties, signatories, and contract-defined short forms |
| HMAC fingerprinting | Replaces complete contract text and verbatim quotations with keyed fingerprints and approximate length categories |
| Recursive payload traversal | Protects nested dictionaries, lists, tuples, and Pydantic models |
| Fail-closed handling | Withholds values that cannot be processed safely |
| Tracing-route audit | Prevents tracing when any detected telemetry route lacks redaction |

## Tracing-route protection

The route audit checks every configured path between the graph and telemetry.

| Tracing route | Detection | Protection |
|---|---|---|
| Explicit graph callback | Inspected before execution | `RedactingTracer` |
| Environment-enabled LangChain tracer | Detected through tracing configuration | Redacting global LangSmith client |
| Unknown third-party callback | Treated as unverified | Execution refused |

If any route is not confirmed to apply redaction, the configuration raises `UnredactedTracingRouteError` before graph execution. This ensures that no telemetry path can transmit an unredacted copy of the state.

## Operational-state preservation

The interceptor creates a sanitized telemetry copy and does not modify the live graph state. This is necessary because the Analyzer verifies that every extracted quotation appears verbatim in the original contract. Modifying the operational state would interfere with this grounding validation.

The guarded and unguarded executions produced identical final reports and operational state. The following 11 fields remained available for debugging:

- `round_number`
- `next_route`
- `is_validated`
- `rejection_flag`
- `analysis_retry_count`
- `task_domain`
- Node names
- `clause_type`
- `risk_level`
- Matter type
- Graph version

All 28 trace events were retained. The guardrail therefore preserved observability while removing sensitive content.

## Performance

| Measurement | Result |
|---|---:|
| Median unguarded tracing time | 3.01 ms per graph run |
| Median guarded tracing time | 37.17 ms per graph run |
| Absolute redaction overhead | 34.16 ms per graph run |
| Approximate overhead per trace event | 1.2 ms |
| Telemetry payload reduction | 28.0% |

The interceptor adds approximately 34 ms per graph run. The telemetry payload decreases by 28.0% because complete contract fields are replaced with compact HMAC fingerprints.

## False-positive evaluation

The false-positive evaluation used seven harmless values with formats similar to potentially sensitive information.

| Benign value | Preserved |
|---|---|
| `Section 4.2` | Yes |
| `2026-08-01` | Yes |
| `MSA-001` | Yes |
| `llama3.2` | Yes |
| `round 3 of 5` | Yes |
| `1.0.0` | Yes |
| `PN 12-3456789` | No |

The measured false-positive rate was **1 of 7, or 14.3%**. The part number has the same `NN-NNNNNNN` format as a US EIN, so the two cannot be distinguished using format matching alone.

## Non-standard-format evaluation

The interceptor was also evaluated using five deliberately difficult or non-standard representations.

| Variant | Detection result |
|---|---|
| Space-separated SSN | Detected |
| International telephone number | Detected |
| UK NHS number | Detected but classified as a telephone number |
| Email written using `[at]` and `[dot]` | Missed |
| Base64-encoded API key | Missed |

Recall on these non-standard formats was **3 of 5, or 60%**, with one detected value assigned the wrong category. The sensitive-key denylist and complete-field fingerprinting reduce dependence on format recognition, but encoded credentials and previously unseen international identifiers remain limitations.

## Fail-closed behavior

| Failure condition | Safe response |
|---|---|
| Nesting exceeds `MAX_DEPTH` | `[REDACTED:MAX_DEPTH_EXCEEDED]` |
| Cyclic reference detected | `[REDACTED:CYCLIC_REFERENCE]` |
| Unsupported object encountered | `[REDACTED:UNSUPPORTED_TYPE]` |
| Redaction function raises an exception | Payload withheld; only the exception type is retained |
| Unprotected tracing route detected | Execution refused with `UnredactedTracingRouteError` |

The interceptor never returns the original value when it cannot confirm that the value is safe. Exception messages are also sanitized because they may contain database addresses, passwords, connection strings, or authorization headers.

## Pseudonymization of contract text

Complete fields such as `raw_input` and `verbatim_quote` are replaced with the following structure:

```text
[REDACTED:TEXT hmac=<16-hex-digest> len=<bucket>]
```

The implementation uses HMAC-SHA-256 with a process-local random key. The digest is truncated to 16 hexadecimal characters, and the exact document length is replaced with an approximate category.

This allows repeated processing of the same document to be recognized within a process without exposing the contract text. The mechanism is pseudonymization rather than complete anonymization because identical documents remain linkable within the same process and the length category reveals their approximate size.

A stable local key may be provided through `TRACE_FINGERPRINT_KEY` when cross-process correlation is required.

## Contract compliance

The privacy layer adds no graph-state fields and does not modify `contract.py`. It operates as a read-only layer between the graph and telemetry, preserving the frozen shared-state contract.

The graph’s operational output remains unchanged because redaction is applied only to the telemetry copy.

## Safety compliance

All telemetry behavior is mocked and remains local.

| Safety requirement | Verification |
|---|---|
| No network traffic during the guarded run | Socket connections are blocked during testing |
| No network traffic during the unguarded reproduction | The leak is captured only by `InMemorySink` |
| No file modifications | File-writing and shell-execution primitives are prohibited |
| No real LangSmith upload | No create, update, batch-ingest, or flush operation is invoked |
| No real credentials or personal information | All planted values are synthetic placeholders |

The LangSmith client is instantiated only to verify its redaction configuration. It is not used to transmit telemetry.

## Test summary

```text
pytest student_5_trace/ -q
42 passed
```

The tests cover:

- Deterministic reproduction of the unguarded privacy leak
- Elimination of all planted secrets
- Inputs, outputs, metadata, tags, and error messages
- Explicit and environment-enabled tracing routes
- Party names and standalone aliases
- False positives and non-standard formats
- Recursive and fail-closed behavior
- Operational-state preservation
- HMAC fingerprinting
- Frozen-contract compliance
- Network, filesystem, and external-upload safety

## Conclusion

Without the guardrail, the graph exposed all 13 planted secrets across 753 telemetry occurrences and leaked 306 standalone party aliases. With the State Redaction Interceptor active, all measured leakage decreased to zero while all 28 trace events and all 11 operational fields remained available. The guarded graph produced an identical final report and required no modifications to the frozen contract.
