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

TODO (Person 2): describe the silent-hallucination failure you reproduced
in `student_2_silent/test_failure.py` (what did the unguarded Analyzer
output look like — structurally valid but wrong how?), the
`.with_structured_output()` + validation + one-shot self-correction
guardrail you built, and the quantified before/after metric (e.g. "N
malformed/incomplete clause extractions per 100 runs → 0").

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

TODO (Person 4): describe the downstream crash you reproduced in
`student_4_cascade/test_failure.py` from unvalidated Actor output, the
sanitization node + rejection-flag/rollback guardrail you built, and the
quantified before/after metric (e.g. "downstream crashes per 100 runs: N
→ 0").

---

## Person 5 — Tracing Privacy & Context/Token Management Guardrails

**Role:** Privacy and Context Management
**System:** Multi-agent legal contract review orchestrator (LangGraph, Python)

TODO (Person 5): this role covers two separate guardrails — cover both,
or split into two ~150-word entries if that reads better:

1. `student_5_trace/` — the PII/secret leak to LangSmith you reproduced,
   the redaction interceptor you built, and the quantified before/after
   metric (e.g. "leaked PII records: N → 0").
2. `student_6_tokens/` — the context/token blowup you reproduced, the
   summarization + pruning guardrail you built, and the quantified
   before/after metric (e.g. "token spend per failure event: $4.50 →
   $0.12").
