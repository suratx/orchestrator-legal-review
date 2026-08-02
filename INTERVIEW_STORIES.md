# Interview Stories

Six quantified, professional descriptions — one per team member — of the
failure mode each person found and the guardrail they built to fix it.
~150 words each.

---

## Person 1 — Coordinator & Infinite Loop Guardrail

**Role:** Architecture, Coordinator, and System Integration
**System:** Multi-agent legal contract review orchestrator (LangGraph, Python)

While building the central Coordinator for a 5-node multi-agent contract-review
system, I found that an adversarial or persistently-malformed input could put
the Analyzer/Validator pair into an endless reject-and-retry cycle — the
Coordinator had no concept of "enough." Left unguarded, this pattern ran
past 500 iterations in testing with no natural exit, projecting roughly
$15+ in wasted LLM calls per incident with zero forward progress.

I fixed this with a deterministic, code-level guardrail: a `round_number`
counter enforced in the Coordinator's routing logic, hard-capped at 5. On
breach, the graph doesn't error — it short-circuits to a partial-output
report flagged for manual review. Post-fix, the same adversarial input
resolves in 6 bounded iterations at $0.18, with a 100% clean termination
rate instead of an unbounded runaway.

---

## Person 2 — Analyzer & Silent Hallucination Guardrail

**Role:** Contract & Analyzer (Worker A)
**System:** Multi-agent legal contract review orchestrator (LangGraph, Python)

Building Worker A of a five-node LangGraph contract-review orchestrator, I hit
the failure mode that doesn't announce itself. The Analyzer returned an
analysis with correct types, valid enums and well-formed section numbers — and
a twelve-month liability cap that was not in the contract, while omitting the
uncapped indemnity that was. `.with_structured_output()` accepted it without a
murmur, because a hallucination is structurally valid by construction.

So I moved the domain invariants into the frozen Pydantic contract itself:
every verbatim quote, clause ID and party name must occur in the source text,
and overall risk must equal the worst clause risk. On failure the node feeds
the validator's exact message back for exactly one automated self-correction,
then rejects to the Coordinator rather than guess.

Across a 12-analysis benchmark, defective analyses reaching the next agent fell
from 8/12 to 0/12 — zero correct analyses wrongly rejected — at a cost of one
extra model call. Live llama3.2 runs showed an 8.3% silent-failure rate the
schema alone could not see.

---

## Person 3 — Actor & Rogue Tool Execution Guardrail

**Role:** Actor & Tool Security (Worker B)
**System:** Multi-agent legal contract review orchestrator (LangGraph, Python)

TODO (Person 3): describe the unauthorized/malformed tool call you
reproduced in `student_3_rogue/test_failure.py`, the permission-matrix
interception + `InvalidToolCallException` guardrail you built, and the
quantified before/after metric (e.g. "unauthorized tool calls executed:
N → 0").

---

## Person 4 — Validator & Downstream Cascade Guardrail

**Role:** Validator & Testing (Worker C)
**System:** Multi-agent legal contract review orchestrator (LangGraph, Python)

While integrating Worker C for a LangGraph legal contract-review orchestrator,
I reproduced a downstream cascade failure: Worker B wrote a structurally
complete `execution_state` where `executed_count` was the string `"1"`, a
redline cited a fabricated `Section 99`, or the result cardinality lied. A
naive Reporter then crashed with TypeError, IndexError, ZeroDivisionError, or
RuntimeError — or would have approved a redline against a clause the Analyzer
never extracted.

I built an explicit Validation/Sanitization node between Actor and Reporter
that runs programmatic assertions against the frozen Pydantic contract:
required keys and types, count/list consistency, `external_action_performed=False`,
clause-ID grounding into `analysis_payload`, and high/critical-only redlines.
On failure the node sets `rejection_flag`, appends `validator: <reason>` to
`rejection_reason_history`, clears poisoned execution state, and forces
Coordinator rollback; identical reasons twice escalate to partial output.

Across four deterministic malformed payloads, downstream crashes fell from
4/4 (100%) to 0/4, with a 100% clean rejection rate and zero poisoned states
reaching the Reporter.

---

## Person 5 — Tracing Privacy Guardrail

**Role:** Global Graph Layer — Tracing & Privacy
**System:** Multi-agent legal contract review orchestrator (LangGraph, Python)

On a five-node LangGraph contract-review orchestrator, I owned the tracing layer
and found we were streaming entire contracts to LangSmith. A run over one seeded
agreement put 13 of 13 planted secrets — signatory SSN, escrow IBAN,
counter-party EIN, deal value and a production Postgres DSN — into telemetry,
549 times across 22 events.

I built a centralized State Redaction Interceptor on the graph-to-telemetry
boundary: a keyed HMAC fingerprint for contract body, a pattern registry plus
key denylist for identifiers, and entity replacement for party names,
fail-closed at depth caps and unknown types.

Three findings mattered. LangSmith's `_hide_run_error` consults only
`anonymizer`, so the intuitive `hide_inputs`/`hide_outputs` config ships
tracebacks in the clear. An env-var-driven `LangChainTracer` opens a second
upload route through an unredacted global client — my sink looked spotless while
data left in parallel. And redacting full legal names missed the short forms
contracts define for themselves, leaving 234 bare references to the parties in
the sentences that mattered most.

Result: 13/13 secrets, 549 occurrences and 234 short forms to zero; +18 ms per
run, 34% less egress, graph output and all 11 operational fields unchanged.

---

## Person 5 (second guardrail) — Context/Token Management

**Role:** Global Graph Layer — Context Manager
**System:** Multi-agent legal contract review orchestrator (LangGraph, Python)

TODO: `student_6_tokens/` — the context/token blowup reproduced, the
summarization + pruning guardrail, and the quantified before/after metric
(e.g. "token spend per failure event: $4.50 → $0.12"). Not yet started; the
tracing guardrail above is complete and measured.
