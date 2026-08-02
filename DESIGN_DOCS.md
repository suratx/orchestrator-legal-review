# Design Docs — Failure-Risk Analysis

**System:** Legal Contract Review Orchestrator  
**Architecture:** LangGraph Coordinator with four specialized workers and two global guardrail layers  
**Language:** Python 3.11  
**Shared contract:** Pydantic `AgentState` defined in `contract.py`

## 1. Design approach

The system was designed as a dynamically routed state machine rather than a linear prompt chain. A deterministic Coordinator examines the shared state after each worker transition and decides whether to continue, retry, roll back, report, or terminate with partial output. All workers exchange data through the frozen `AgentState` contract, which rejects undeclared fields and preserves structural consistency across the graph.

The six primary guardrails are implemented across the individual worker modules and global graph layers through executable validation and routing logic rather than prompt-only instructions. The Coordinator remains deterministic and LLM-free, Analyzer output is validated against the frozen contract, tool execution is restricted to mocked functions, malformed downstream state is rejected before reporting, telemetry is sanitized at the graph boundary, and context is compressed toward the configured threshold before Coordinator transitions.


## 2. Implemented guardrails

| # | Failure mode | Layer | Programmatic guardrail | Measured result |
|---:|---|---|---|---|
| 1 | Infinite graph loops | Coordinator | A deterministic `round_number` ceiling forces `partial_output` when five retry rounds are reached. Repeated identical rejection reasons escalate earlier. | An unguarded run reached the 500-iteration test-harness limit; the guarded graph terminated cleanly after five retry rounds. |
| 2 | Silent hallucination | Analyzer | `.with_structured_output(ContractAnalysis)` enforces structure, while source-grounding invariants verify clause references, quotations, party names, and overall-risk consistency. One in-node self-correction retry is allowed before rejection. | Defective analyses reaching the Actor decreased from 8 of 12 to 0 of 12. |
| 3 | Rogue tool execution | Actor | The complete requested tool-call array is validated against a hardcoded permission matrix before any call executes. Unauthorized tools, arguments, types, clauses, or risk levels raise `InvalidToolCallException` and abort the batch. | Unauthorized mocked executions decreased from 1 to 0 while valid mocked redlines continued to execute. |
| 4 | Downstream cascade failure | Validator | A Validation and Sanitization Node checks required keys, data types, result cardinality, clause grounding, allowed risk levels, and `external_action_performed=False`. Invalid state is cleared and returned with a rollback flag. | Downstream crashes decreased from 4 of 4 malformed payloads to 0 of 4. |
| 5 | Privacy leakage through telemetry | Global tracing layer | A centralized redaction interceptor sanitizes inputs, outputs, metadata, tags, and error strings. It combines sensitive-key removal, pattern matching, party-name replacement, HMAC fingerprinting, and tracing-route auditing. | Exposure decreased from 13 of 13 planted secrets, 753 occurrences, and 306 party aliases to zero. |
| 6 | Context-window explosion | Global context layer | A Context Management Node estimates message tokens and progressively digests tool output, folds older turns into one bounded rolling summary, and reduces the recent-message window when required. | Peak context decreased from 1,803 to 1,157 tokens; threshold breaches decreased from 4 of 12 to 0 of 12; estimated prompt tokens at a history-consuming agent decreased by 21.2%. |

## 3. Nineteen additional risks considered

The following risks are additional to the six required failure modes.

| # | Additional risk | Design decision |
|---:|---|---|
| 7 | Coordinator selects an unregistered route | The Coordinator assigns routes only through the shared `ROUTE_*` constants, and the graph’s conditional-edge mapping contains only registered destination nodes. |
| 8 | Concurrent branches overwrite shared state out of order | Deferred because the current graph is deliberately sequential. Parallel execution would require explicit reducers, conflict-resolution rules, and concurrency tests. |
| 9 | State-schema drift after the contract freeze | `AgentState` uses Pydantic `extra="forbid"`, so undeclared fields fail validation instead of silently entering shared state. Contract changes require documented team review. |
| 10 | Structurally valid but empty analysis payload | `ContractAnalysis.clauses` requires at least one item, and every `ClauseRisk` field is mandatory. Downstream routing uses `is_validated` rather than payload truthiness alone. |
| 11 | Duplicate clause identifiers | `ContractAnalysis` normalizes clause references and rejects duplicate identifiers, including differently worded labels that refer to the same section. |
| 12 | Incorrect party-role attribution | Grounding confirms that a party name occurs in the contract but cannot always distinguish the client from the counterparty. The prompt specifies the role, while explicit party-role extraction is retained as a future enhancement. |
| 13 | A real destructive tool is accidentally registered | The runtime registry contains only mocked functions, all results explicitly report `external_action_performed=False`, and safety tests verify that no destructive or external operation occurs. |
| 14 | A valid call executes before a later call in the same batch is rejected | The complete tool-call array is validated before execution begins. If any request fails, the entire batch is aborted and no call is executed. |
| 15 | An authorized tool targets an invalid clause or inappropriate risk level | Tool middleware verifies that the clause exists in the validated analysis and permits redlining only for high- or critical-risk clauses. |
| 16 | The Validator omits an important legal business rule | Structural and cross-node invariants are encoded in the current Validator. Jurisdiction-specific legal interpretation remains outside the deterministic v1 guardrail and requires qualified human review. |
| 17 | The Reporter fails while producing a failure report | The Reporter is deterministic and performs no external action, reducing its failure surface. A separate Reporter guardrail was considered but deferred because it is outside the six assigned failure modes. |
| 18 | Telemetry processing alters or interrupts graph execution | Submission tests use an in-memory telemetry sink and verify that enabling redacted tracing produces the same operational state and final report as an untraced execution. |
| 19 | Environment-enabled tracing creates an unnoticed second upload route | The tracing-route audit detects implicitly created LangChain tracers and refuses execution when any telemetry route cannot be confirmed as redacting. |
| 20 | Sensitive data escapes through metadata, tags, or exception messages | Redaction covers four distinct channels: inputs and outputs, metadata, tags, and error strings. Tests verify each channel separately. |
| 21 | Redaction misses unusual formats or removes harmless identifiers | Non-standard-format recall and benign-lookalike false positives are measured explicitly. The current evaluation detected 3 of 5 difficult formats and preserved 6 of 7 benign lookalikes. |
| 22 | The offline token estimator differs from the model tokenizer | Estimator constants were calibrated against Llama 3.2 using real `/api/chat` message arrays. The model uses a 24-token conversation prefix, two tokens per message, and approximately 5.77 characters per token. |
| 23 | Rolling summaries accumulate and become a new source of context growth | Exactly one summary entry is retained and replaced. Its fixed-schema aggregate is merged arithmetically rather than through growing prose concatenation. |
| 24 | Context compression removes information required by other nodes | The Context Management Node modifies only `messages` and `token_count`. Core routing, analysis, execution, rejection, and reporting fields remain unchanged and are verified through integration tests. |
| 25 | Rejection reasons vary or oscillate, bypassing identical-reason escalation | Identical reasons escalate immediately. Non-identical or oscillating reasons remain bounded by the independent five-round Coordinator ceiling. |

## 4. Final architectural decisions

All six primary guardrails operate through executable validation and routing logic rather than prompt-only instructions. The Coordinator remains deterministic and LLM-free, worker outputs are validated against the frozen contract, tool execution is restricted to mocked functions, malformed downstream state is rejected before reporting, telemetry is sanitized at the graph boundary, and context is bounded before each Coordinator transition.

When a trustworthy full result cannot be produced, the system fails safely by emitting `PARTIAL -- MANUAL REVIEW REQUIRED` rather than hanging, crashing, executing an unauthorized action, exposing sensitive information, or forwarding unvalidated data.
