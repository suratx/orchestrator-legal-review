# Design Docs — Alternative Failure-Risk Analysis

**System:** Legal Contract Review Orchestrator  
**Scope:** Nineteen risks considered in addition to the six required failure modes

The team evaluated the following risks during system design. These decisions supplement the six implemented guardrails without repeating their architecture, implementation details, or measured results.

| # | Alternative risk considered | Design decision |
|---:|---|---|
| 1 | The Coordinator selects an unregistered route | Routing uses shared `ROUTE_*` constants and a conditional-edge map containing only registered graph destinations. |
| 2 | Concurrent branches overwrite shared state | The current graph remains sequential. Future parallel execution would require explicit state reducers, conflict-resolution rules, and concurrency tests. |
| 3 | The shared schema changes after contract freeze | `AgentState` uses Pydantic `extra="forbid"`. Undeclared fields fail validation, and contract changes require documented team review. |
| 4 | The Analyzer returns a structurally valid but empty analysis | `ContractAnalysis.clauses` requires at least one item, every `ClauseRisk` field is mandatory, and downstream routing depends on `is_validated`. |
| 5 | The Analyzer reports the same clause more than once | Clause references are normalized and duplicate identifiers are rejected, including differently worded identifiers referring to the same section. |
| 6 | The Analyzer confuses the client with the counterparty | Source grounding verifies that the reported party appears in the contract. Explicit party-role extraction remains a future enhancement. |
| 7 | A destructive tool is accidentally exposed to the Actor | The runtime registry contains only mocked functions, and every result must report `external_action_performed=False`. |
| 8 | A valid tool call executes before a later call in the same batch is rejected | The complete requested tool-call array is validated before any function executes. One invalid request aborts the entire batch. |
| 9 | An authorized tool targets an invalid clause or inappropriate risk level | Middleware confirms that the clause exists in the validated analysis and permits redlining only for high- or critical-risk clauses. |
| 10 | The Validator omits a jurisdiction-specific legal rule | The Validator enforces structural and cross-node invariants. Jurisdiction-specific legal interpretation remains subject to qualified human review. |
| 11 | The Reporter fails while producing a failure report | The Reporter is deterministic, performs no external actions, and uses a minimal template for both completed and partial reports. |
| 12 | Telemetry processing changes operational graph behavior | Redaction operates on a deep copy of telemetry data. Integration tests verify that tracing does not modify working state or the final report. |
| 13 | Environment-enabled tracing creates a second unprotected upload route | A tracing-route audit detects implicit LangChain tracers and refuses execution when any active route cannot be confirmed as redacting. |
| 14 | Sensitive information escapes through overlooked telemetry channels | Redaction independently covers inputs, outputs, metadata, tags, error messages, and serialized state values. |
| 15 | Redaction misses unusual sensitive formats or removes harmless values | Difficult formats and benign lookalikes are evaluated separately. Pattern coverage can be expanded without changing graph state or worker logic. |
| 16 | The offline token estimator differs from the model tokenizer | Estimator constants are calibrated against Llama 3.2 chat-message arrays, while live calibration remains available for model or template changes. |
| 17 | Rolling summaries become another source of context growth | The Context Manager retains exactly one fixed-schema summary and replaces it rather than appending additional summaries. |
| 18 | Context compression removes information needed by workers | Compression modifies only `messages` and `token_count`; routing, analysis, execution, validation, rejection, and reporting fields remain unchanged. |
| 19 | Rejection reasons vary or alternate and bypass repeated-reason escalation | Exact repeated reasons terminate early, while nonidentical or oscillating failures remain bounded by the independent graph-round ceiling. |

These decisions favor deterministic validation, bounded execution, explicit state ownership, fail-closed telemetry handling, and conservative human escalation. Graph topology and routing are documented in [`ARCHITECTURE_DESIGN.md`](ARCHITECTURE_DESIGN.md), while implementation results are reported in the individual `METRICS.md` files and summarized in [`README.md`](README.md).
