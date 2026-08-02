# Design Docs — Failure Risk Analysis

**System:** Legal Contract Review Orchestrator (LangGraph, Python)

Nineteen failure risks were surveyed during architecture design. Six were
selected for individual code-level guardrails; thirteen were considered and
either judged covered by one of the six, or explicitly deferred with a reason.
Full rationale, measurements and reproductions live in each owner's
`student_*/METRICS.md`.

## Selected guardrails (6 — one per student)

| # | Failure risk | Layer | Owner | Guardrail, and the measured result |
|---|---|---|---|---|
| 1 | Infinite graph loops | Coordinator | P1 | `round_number >= 5` ceiling enforced in the conditional-edge function → `partial_output`; an identical rejection reason twice escalates early. Unbounded → 6 bounded iterations. |
| 2 | Silent hallucination | Analyzer | P2 | `.with_structured_output()` **plus** source-grounding invariants in the contract — every quote, clause ID and party name must occur in `raw_input` — then one in-node self-correction. Structure alone cannot catch a hallucination, because a hallucination is structurally valid by construction. Defective analyses reaching the next agent 8/12 → 0/12. |
| 3 | Rogue tool execution | Actor | P3 | Tool calls intercepted before execution and checked against a hardcoded permission matrix; `InvalidToolCallException` aborts the batch, no retry — a permission violation is not a transient failure. Unauthorized executions 1 → 0. |
| 4 | Downstream cascade failure | Validator | P4 | Sanitization node between Actor and Reporter: type/shape assertions, count↔results consistency, clause-ID grounding, high/critical-only redlines. On failure sets `rejection_flag` and forces rollback. Downstream crashes 4/4 → 0/4. |
| 5 | Data privacy leak (tracing) | Global — Tracing | P5 | Redaction interceptor on the graph→telemetry boundary covering **all four** channels (inputs/outputs, metadata, tags, error strings), keyed HMAC fingerprint for contract body, and a route audit that refuses to run while any upload path is unredacted. Fails closed everywhere. 13/13 secrets, 753 occurrences and 306 bare party short forms → 0. |
| 6 | Context explosion / token burn | Global — Context | P5 | Context node at each loop head: five-stage pruning ladder, recounting after every stage and stopping at the first that fits. One rolling summary, stored as a fixed-schema *aggregate* so merging is addition and it stays 32 tokens at any history length. Peak window 1,803 → 1,157 tokens and ceiling breaches 4/12 → 0/12; prompt tokens at a history-consuming agent −21.2%. |

## Additional risks considered (13)

| # | Failure risk | Status |
|---|---|---|
| 7 | Coordinator routes to an unregistered node | Mitigated — `next_route` is constrained to four `ROUTE_*` constants; LangGraph raises at build time. |
| 8 | Out-of-order writes from concurrent branches | Deferred — the graph is strictly sequential; unreachable in v1. |
| 9 | Valid schema but empty `analysis_payload` | Closed (P2) — `clauses` has `min_length=1` and no field has a default. Downstream must key off `is_validated`, not payload emptiness. |
| 10 | Actor calls a real destructive tool by misconfiguration | Covered by P3's safety review — every domain tool verified mocked before integration. |
| 11 | Validator approves an unencoded business invariant | Partially closed (P4) — structural invariants encoded; jurisdiction-specific legal rules deferred. |
| 12 | LangSmith outage blocks the graph | Closed (P5) — the SDK uploads from a background thread, and the interceptor never raises into the graph. It refuses *before* the run instead. |
| 13 | Redaction misses non-US PII formats | Partially closed, measured (P5) — recall 3/5 on awkward formats; an obfuscated email and a base64-wrapped key are missed. The key denylist and body fingerprint do not depend on format, and carry the bulk of the coverage. |
| 14 | Token counter mismatched to the real tokenizer | Closed with a measurement (P5) — constants fitted from real message arrays via `/api/chat` — 2 tokens per message plus a 24-token one-off conversation prefix, and 5.77 chars/token. An earlier `/api/generate` attempt reported 25 tokens per message; that measured a one-shot template cost and wrongly multiplied it per message. |
| 15 | Summarization drops a field the Coordinator needs | Closed (P5) — a test iterates every `AgentState` field and asserts all but `messages`/`token_count` are byte-identical after the node runs. |
| 16 | Rejection reason oscillates without repeating identically | Known gap — §5.3 escalates only on an *identical* repeat; falls back to the `round_number` ceiling. |
| 17 | Schema drift after the freeze | Mitigated — `extra="forbid"` rejects undeclared fields at construction. |
| 18 | Reporter crashes while reporting a failure | Deferred — the Reporter has no guardrail of its own in v1. |
| 19 | Two guardrails trigger in the same round | Mitigated — check order is fixed; the Actor aborts before the Coordinator's round check. |

## Open items for team review

- **#18** — give the Reporter a guardrail before final integration, or accept as out of scope? *(P1)*
- **Loop guardrail has a hole** — `round_number` increments only inside the `rejection_flag` branch, so a node failing quietly re-routes forever with the counter stuck at 0. Fix in `CONTRACT_FREEZE_NOTES.md` §5. Related to #16. *(P2)*
- **False positives are a real cost, not a cosmetic one** — a guardrail that cries wolf gets switched off, so both anti-hallucination and redaction publish their false-positive rates rather than tuning them away. Grounding FPs 4/10 → 0/12.
