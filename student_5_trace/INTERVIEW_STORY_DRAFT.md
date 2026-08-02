# Person 5 — Interview Story Draft (Tracing Privacy)

**Role:** Global Graph Layer — Tracing & Privacy
**System:** Multi-agent legal contract review orchestrator (LangGraph, Python)

~150 words. Copied into the root `INTERVIEW_STORIES.md`.

---

On a five-node LangGraph contract-review orchestrator, I owned the tracing
layer and found we were streaming entire contracts to LangSmith. A run over one
seeded agreement put 13 of 13 planted secrets — signatory SSN, escrow IBAN,
counter-party EIN, deal value and a production Postgres DSN — into telemetry,
511 times across 22 events.

I built a centralized State Redaction Interceptor on the graph-to-telemetry
boundary: a keyed HMAC fingerprint for contract body, a pattern registry plus
key denylist for identifiers, and entity replacement for party names, all
fail-closed at depth caps and unknown types.

Two findings mattered most. LangSmith's `_hide_run_error` consults only
`anonymizer`, so the intuitive `hide_inputs`/`hide_outputs` config ships
tracebacks in the clear. And an env-var-driven `LangChainTracer` opens a second
upload route through an unredacted global client — my sink looked spotless while
data left in parallel. I seeded that singleton and added a route audit that
refuses to run otherwise.

Result: 13/13 secrets and 511 occurrences to zero, +15 ms per run, 34% less
egress, with all 11 operational fields and the graph's output unchanged.

---

**Word count:** ~175. Trim the LangSmith-internals sentence to hit 150 if the
brief is strict — but it is the strongest technical detail in the story, so
prefer trimming the opening instead.

## Numbers to have ready if they probe

- 13 of 13 unique secrets exposed → 0; 511 occurrences → 0
- Four telemetry channels, not one: inputs/outputs, metadata, tags, error strings
- Overhead +14.87 ms/run (0.68 ms/event); payload 61,825 → 40,769 bytes (−34.1%)
- False-positive rate 1 of 7 (14.3%), an EIN-shaped part number — unfixable by
  tuning, published rather than hidden
- Non-standard-format recall 3 of 5, one of those mislabelled as PHONE
- 32 tests; zero changes to the frozen contract
