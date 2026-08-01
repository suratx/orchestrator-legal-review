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
| 2 | Silent Hallucination | Analyzer | Person 2 | Three code layers: (a) `.with_structured_output(ContractAnalysis)` forces the model through a Pydantic schema; (b) `contract.validate_grounded()` re-validates against the source text — every `verbatim_quote`, `clause_id` and `counterparty` must occur in `raw_input`, and `overall_risk` must equal the worst clause risk; (c) on either failure the validator's exact message is fed back for exactly one in-node self-correction (`MAX_ANALYZER_RETRIES = 1`), then the node sets `rejection_flag` rather than guess. Layer (b) is the load-bearing one: structural validation *cannot* catch a hallucination, because a hallucination is structurally valid by construction. See `student_2_silent/METRICS.md`. |
| 3 | Rogue Tool Execution | Actor | Person 3 | Tool-call interception against a hardcoded permission matrix; raises `InvalidToolCallException`, no retry. See `student_3_rogue/`. TODO (Person 3). |
| 4 | Downstream Cascade Failure | Validator | Person 4 | Explicit sanitization node between Actor and Reporter: programmatic assertions on `execution_state` types/keys, `executed_count`↔`results` consistency, `external_action_performed=False`, clause-ID grounding into `analysis_payload`, and high/critical-only redlines. On failure sets `rejection_flag`, appends `validator: <reason>` to `rejection_reason_history`, clears poisoned execution state, and forces Coordinator rollback (§5.3); identical reasons twice escalate to `partial_output`. See `student_4_cascade/METRICS.md` (crashes 4/4 → 0/4). |
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
| 9 | Analyzer returns valid schema but empty `analysis_payload` | Analyzer | **Confirmed closed (Person 2).** `ContractAnalysis.clauses` is declared `Field(min_length=1)`, so a zero-clause analysis fails at parse time. `ClauseRisk` has no default on any field — a default would let the model omit a critical identifier and still produce a "valid" object, which is the failure mode itself. `verbatim_quote` and `risk_rationale` also carry `min_length` on the *Field* (not only in a validator) so the constraint is exported into the JSON Schema that Ollama's decoder constrains generation against; live runs showed llama3.2 emitting empty `risk_rationale` strings until this was added. Note the deliberate remaining gap: an empty payload is also written on purpose when the Analyzer rejects (`analysis_payload = {}` alongside `rejection_flag = True`) — downstream nodes must key off `is_validated`, not payload emptiness. |
| 10 | Actor calls a real (non-mocked) destructive tool by misconfiguration | Actor | Covered by Person 3's final safety review requirement — all domain tools must be verified mocked before integration. |
| 11 | Validator approves output that violates a business invariant not yet encoded | Validator | **Partially closed (Person 4).** v1 invariants now include type/shape checks, count consistency, no real external actions, clause-ID grounding against `analysis_payload`, high/critical-only redlines, and “every high/critical clause must be addressed on a completed Actor pass.” Remaining business rules (e.g. jurisdiction-specific indemnification language) stay deferred. |
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
- Items 11, 12, 14 need each respective owner to confirm status once their
  node is implemented. Item 9 is closed — see the row above.
- **New risk surfaced during implementation (Person 2):** the loop guardrail
  (#1) does not fire when a node fails *without* setting `rejection_flag` —
  `round_number` is incremented only inside the `rejection_flag` branch of the
  Coordinator, so an `is_validated=False` failure re-routes to the Analyzer
  forever with `round_number` stuck at 0. Reproduced against the real
  Coordinator; write-up and one-line fix in `CONTRACT_FREEZE_NOTES.md` §5.
  Person 1 to decide. Related to item 16 — both are cases where the round
  ceiling is the assumed backstop but does not actually engage.
- **Grounding false positives (Person 2):** an anti-hallucination check that
  cries wolf gets switched off, so it is a real risk, not a cosmetic one.
  Live llama3.2 output substitutes typographic apostrophes and stray JSON
  escapes into otherwise-faithful quotes; `contract.normalize_text()` folds
  these to ASCII before comparison. Measured: 4/10 grounding rejections before
  the fix (≥2 typographic), 0/12 after, with genuine defects still caught.
