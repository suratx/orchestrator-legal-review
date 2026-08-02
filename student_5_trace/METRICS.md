# Student 5 Metrics — Data Privacy Leak via Telemetry

## Evaluation overview

**Guardrail:** Centralized State Redaction Interceptor at the graph-to-telemetry boundary

**Reproduction commands:**

```bash
python student_5_trace/test_failure.py
pytest student_5_trace/ -q
```

The evaluation uses the fully integrated LangGraph with all six guardrails active. A synthetic contract containing 13 known secrets is processed twice using the same graph and input. The unguarded run records raw telemetry, while the guarded run passes every telemetry event through the redaction interceptor. Both runs use an in-memory sink, so no data is written to disk or transmitted externally.

The planted values include personal identifiers, party names, financial information, database credentials, internal infrastructure details, and API keys. Because the complete secret corpus is known in advance, leakage can be measured directly rather than assessed through visual inspection.

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

The unique-secret count identifies how many distinct confidential values reached telemetry. Total occurrences measure the broader exposure because the same secret may appear in several node inputs, outputs, metadata fields, or errors. The guardrail eliminated both forms of leakage while retaining every trace event.

## Per-secret exposure

| Planted value | Unguarded occurrences | Guarded occurrences |
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

All values are synthetic. Emails use the reserved `.example` domain, telephone numbers use reserved test ranges, the AWS key is a published placeholder, and the SSN uses an unissued range.

## Party-alias protection

Removing a full legal name does not protect shorter references such as `Acme` or `Globex`. These aliases are common in contracts and may appear independently throughout the document.

| Standalone alias | Unguarded occurrences | Guarded occurrences |
|---|---:|---:|
| `Globex` | 139 | 0 |
| `Acme` | 167 | 0 |
| **Total** | **306** | **0** |

The interceptor obtains aliases by removing corporate suffixes from known party names and parsing aliases defined in the contract. Generic words are rejected to limit over-redaction. For example, `The Boeing Company` can yield `Boeing`, whereas `First National Bank` produces no automatically derived alias because its individual terms are too generic.

Standalone occurrences are counted separately from appearances inside full legal names to avoid inflating the results.

## Telemetry-channel coverage

Protecting only inputs and outputs is insufficient because sensitive information may also appear in metadata, tags, and exception messages.

| Telemetry channel | Sensitive values before | Sensitive values after | Protection method |
|---|---:|---:|---|
| Inputs and outputs | 11 | 0 | Recursive payload redaction |
| Metadata | 4 | 0 | Dedicated metadata transformation |
| Tags | 1 | 0 | Explicit tag redaction |
| Error messages | 2 | 0 | Exception-string redaction |

The implementation was verified against LangSmith 0.10.15. Inputs, outputs, and errors are protected through the client anonymizer, while metadata uses its separate transformation hook. Tags are sanitized directly by the interceptor.

## Guardrail mechanisms

| Mechanism | Purpose |
|---|---|
| Regular-expression registry | Detects emails, phone numbers, SSNs, EINs, IBANs, cards, connection strings, internal hosts, financial values, and provider-specific API keys |
| Sensitive-key denylist | Removes values under fields such as `api_key`, `password`, `secret`, `ssn`, and `iban`, regardless of their format |
| Entity and alias replacement | Removes client names, counterparties, signatories, and contract-defined short forms |
| HMAC fingerprinting | Replaces complete contract text and verbatim quotations with keyed fingerprints and approximate length buckets |
| Recursive payload traversal | Protects nested dictionaries, lists, tuples, and Pydantic models |
| Fail-closed handling | Withholds values when the payload cannot be safely processed |
| Tracing-route audit | Refuses to run if any detected telemetry route lacks redaction |

## Tracing-route protection

An explicitly attached redacting callback is not sufficient when environment-based LangSmith tracing automatically creates another tracer. The route audit therefore checks both explicit and implicit tracing paths.

| Tracing route | Detection | Protection |
|---|---|---|
| Explicit graph callback | Inspected before execution | `RedactingTracer` |
| Automatically attached LangChain tracer | Detected through tracing configuration | Redacting global LangSmith client |
| Unknown third-party callback | Treated as unverified | Execution refused |

If any route is not confirmed safe, `UnredactedTracingRouteError` is raised before tracing begins. This prevents a clean local trace from hiding a simultaneous unredacted upload path.

## Operational preservation

The interceptor sanitizes a copy created for telemetry and never modifies the live graph state. This is necessary because the Analyzer verifies that each extracted quotation appears verbatim in the original contract. Redacting the operational state would cause valid clauses to fail grounding.

The guarded and unguarded executions produced identical values for the final report and all operational fields used by the graph. The following 11 fields remained available for debugging:

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

All 28 trace events were retained, demonstrating that the guardrail preserves observability rather than disabling tracing.

## Performance

| Measurement | Result |
|---|---:|
| Median unguarded tracing time | 3.01 ms per graph run |
| Median guarded tracing time | 37.17 ms per graph run |
| Absolute redaction overhead | 34.16 ms per graph run |
| Approximate overhead per trace event | 1.2 ms |
| Telemetry payload reduction | 28.0% |

The privacy layer adds approximately 34 ms per run. This is large relative to the nearly cost-free local tracing baseline but small compared with model calls measured in seconds. Fingerprinting complete contract fields also reduces the amount of telemetry data transmitted.

## False positives and limitations

### Benign-lookalike evaluation

| Benign value | Preserved |
|---|---|
| `Section 4.2` | Yes |
| `2026-08-01` | Yes |
| `MSA-001` | Yes |
| `llama3.2` | Yes |
| `round 3 of 5` | Yes |
| `1.0.0` | Yes |
| `PN 12-3456789` | No |

The measured false-positive rate is **1 of 7, or 14.3%**. The part number has the same `NN-NNNNNNN` structure as a US EIN, so format matching alone cannot reliably distinguish the two.

### Non-standard-format evaluation

| Variant | Detection result |
|---|---|
| Space-separated SSN | Detected |
| International telephone number | Detected |
| UK NHS number | Detected but misclassified as a phone number |
| Email written using `[at]` and `[dot]` | Missed |
| Base64-encoded API key | Missed |

Recall on these deliberately difficult formats is **3 of 5, or 60%**, with one detected value assigned the wrong category. The key denylist and complete-field fingerprinting reduce dependence on regex detection, but arbitrary encoding and previously unseen international identifiers remain limitations.

## Fail-closed behavior

| Failure condition | Safe response |
|---|---|
| Nesting exceeds `MAX_DEPTH` | `[REDACTED:MAX_DEPTH_EXCEEDED]` |
| Cyclic reference detected | `[REDACTED:CYCLIC_REFERENCE]` |
| Unsupported object type encountered | `[REDACTED:UNSUPPORTED_TYPE]` |
| Redaction function raises an exception | Payload withheld; only the exception type is retained |
| Unprotected tracing route detected | Execution refused with `UnredactedTracingRouteError` |

The interceptor never returns raw data as a fallback. Exception messages are also sanitized because they may contain database addresses, passwords, or authorization headers.

## Pseudonymization of contract text

Complete fields such as `raw_input` and `verbatim_quote` are replaced with:

```text
[REDACTED:TEXT hmac=<16-hex-digest> len=<bucket>]
```

The implementation uses HMAC-SHA-256 with a process-local random key, truncates the digest, and reports only an approximate length category. This allows repeated processing of the same document to be recognized within a process without exposing its contents.

This mechanism is pseudonymization rather than complete anonymization. Identical documents remain linkable within the same process, and the length category reveals their approximate size. A deployment may provide `TRACE_FINGERPRINT_KEY` when stable cross-process correlation is required.

## Contract and safety compliance

The privacy layer adds no state fields and does not modify `contract.py`. It operates as a read-only telemetry layer and preserves the frozen shared-state contract.

All external behavior is mocked:

| Safety requirement | Verification |
|---|---|
| No network traffic during the guarded run | Socket connections are blocked in testing |
| No network traffic during the unguarded reproduction | The leak is captured only by `InMemorySink` |
| No file modifications | File and shell-writing primitives are prohibited |
| No real LangSmith upload | No create, update, batch-ingest, or flush method is invoked |
| No real credentials or personal information | All planted values are synthetic placeholders |

A LangSmith client is constructed only to verify its redaction configuration. It is never used to transmit telemetry.

## Test summary

```text
pytest student_5_trace/ -q
42 passed
```

The tests cover:

- Deterministic reproduction of the unguarded privacy leak
- Elimination of all planted secrets
- Inputs, outputs, metadata, tags, and error messages
- Explicit and implicit tracing-route protection
- Party-name and short-form removal
- False positives and difficult formats
- Recursive and fail-closed behavior
- Operational-state preservation
- HMAC fingerprinting
- Frozen-contract compliance
- Network, filesystem, and external-upload safety

## Conclusion

The unguarded graph exposed all 13 planted secrets across 753 telemetry occurrences and leaked 306 standalone party aliases. The State Redaction Interceptor reduced all measured leakage to zero while retaining all 28 trace events, preserving all 11 operational fields, and producing an identical final report. The guardrail therefore prevents telemetry-based privacy leakage without disabling observability or changing the graph’s behavior.
