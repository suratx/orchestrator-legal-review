# Design Docs — Failure Risk Analysis

**Status:** DRAFT — Person 1 scaffold, for team review and completion.
**System:** Legal Contract Review Orchestrator (LangGraph)

This document summarizes the failure risks the team considered while
designing the graph, per the assignment requirement of surveying 19
alternative failure risks. The six selected below are the ones each
student owns and guards against in code; the remaining risks were
considered and either deferred (out of scope for this assignment's
depth) or judged to be adequately covered by one of the six primary
guardrails. Each team member should confirm/refine the risks under
their own node and fill in any owner-specific detail marked TODO.

## Selected Guardrails (in scope, one per student)

| # | Failure Risk | Node / Layer | Owner | Guardrail Summary |
|---|---|---|---|---|
| 1 | Infinite Graph Loops | Coordinator | Person 1 | `round_number >= 5` hard ceiling in the conditional-edge function → short-circuit to `partial_output`. See `student_1_loop/`. |
| 2 | Silent Hallucination | Analyzer | Person 2 | `.with_structured_output()` + programmatic schema validation + one automated self-correction retry. See `student_2_silent/`. TODO (Person 2). |
| 3 | Rogue Tool Execution | Actor | Person 3 | Tool-call interception against a hardcoded permission matrix; raises `InvalidToolCallException`, no retry. See `student_3_rogue/`. TODO (Person 3). |
| 4 | Downstream Cascade Failure | Validator | Person 4 | Explicit sanitization node with programmatic assertions; sets `rejection_flag` and forces rollback. See `student_4_cascade/`. TODO (Person 4). |
| 5 | Data Privacy Leak (Tracing) | Global — Tracing | Person 5 | Redaction interceptor scrubs PII/secrets from payload metadata before LangSmith export. See `student_5_trace/`. TODO (Person 5). |
| 6 | Context Window Explosion / Token Burn | Global — Context Manager | Person 5 | Token-threshold check triggers summarization + pruning of `state.messages`. See `student_6_tokens/`. TODO (Person 5). |

## Additional Risks Considered (13)

These were discussed during architecture design but are not individually
guarded in this assignment's scope — most are mitigated as a side effect
of the six primary guardrails, or explicitly deferred. TODO: team to
review and adjust rationale/status as implementation reveals real
issues.

| # | Failure Risk | Where it could occur | Status / Rationale |
|---|---|---|---|
| 7 | Coordinator routes to a dead/unregistered node name | Coordinator → conditional edges | Mitigated: `next_route` is constrained to the four `ROUTE_*` constants in `contract.py`; LangGraph raises at graph-build time on an unmapped key. |
| 8 | Duplicate/out-of-order state writes from concurrent branches | Actor / Validator | Deferred — graph is currently strictly sequential (no fan-out), so not reachable in v1. Revisit if parallel workers are added. |
| 9 | Analyzer returns valid schema but empty `analysis_payload` | Analyzer | Partially covered by guardrail #2 (schema validation catches missing required fields); TODO (Person 2) confirm `ClauseRisk` model marks clause list as non-empty. |
| 10 | Actor calls a real (non-mocked) destructive tool by misconfiguration | Actor | Covered by Person 3's final safety review requirement — all domain tools must be verified mocked before integration. |
| 11 | Validator approves output that violates a business invariant not yet encoded | Validator | Deferred — invariant list is TODO (Person 4) to finalize; only structural checks are guaranteed in v1. |
| 12 | LangSmith outage blocks the whole graph on tracing calls | Global — Tracing | Deferred — assumed tracing calls are fire-and-forget / non-blocking; TODO (Person 5) confirm SDK behavior on network failure. |
| 13 | Redaction regex has false negatives on non-US PII formats | Global — Tracing | Deferred — v1 redaction patterns are US-centric; documented limitation, not a guardrail gap for this assignment. |
| 14 | Token counter under/over-counts due to tokenizer mismatch with the actual LLM | Global — Context Manager | Deferred — TODO (Person 5) confirm tokenizer matches the model in use. |
| 15 | Summarization step itself drops a field the Coordinator depends on (e.g. `round_number`) | Global — Context Manager | Mitigated by design: only `messages` is summarized/pruned; core state fields are explicitly preserved per `ARCHITECTURE_DESIGN.docx` Section requirements. |
| 16 | Same rejection reason oscillates without ever repeating identically (evades the 5.3 repeat-detection escalation) | Coordinator / Validator | Known gap — `ARCHITECTURE_DESIGN.docx` 5.3 escalates only on an *identical* repeated reason; a validator that varies wording each time would not trigger early escalation. Falls back to the `round_number` ceiling (guardrail #1) as the backstop. |
| 17 | `contract.py` schema drifts after freeze (a node adds an undeclared field) | All nodes | Mitigated: `AgentState.model_config = ConfigDict(extra="forbid")` — Pydantic rejects unknown fields at construction time. |
| 18 | Partial-output report is itself malformed (Reporter crashes while reporting a failure) | Reporter | Deferred — Reporter has no guardrail of its own in v1; a Reporter crash would surface as an uncaught exception rather than a graceful partial output. TODO (Person 1) — flag for review. |
| 19 | Adversarial input causes `round_number` guardrail and rogue-tool guardrail to trigger in the same round (ambiguous which error_log wins) | Coordinator | Mitigated: guardrail check order is fixed — `InvalidToolCallException` aborts immediately at the Actor (5.4) and never reaches the Coordinator's round-based check for that round. |

## Open Items for Team Review

- Item 18 (Reporter has no guardrail) — worth a small addition before
  final integration, or accepted as out of scope?
- Items 9, 11, 12, 14 need each respective owner to confirm status once
  their node is implemented.
