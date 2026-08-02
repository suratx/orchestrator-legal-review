# Person 5 — Interview Story Draft (Tracing Privacy)

**Role:** Global Graph Layer — Tracing & Privacy
**System:** Multi-agent legal contract review orchestrator (LangGraph, Python)

~150 words. Copied into the root `INTERVIEW_STORIES.md`.

---

On a five-node LangGraph contract-review orchestrator, I owned the tracing layer
and found we were streaming entire contracts to LangSmith. A run over one seeded
agreement put 13 of 13 planted secrets — signatory SSN, escrow IBAN,
counter-party EIN, deal value and a production Postgres DSN — into telemetry,
753 times across 28 events.

I built a centralized State Redaction Interceptor on the graph-to-telemetry
boundary: a keyed HMAC fingerprint for contract body, a pattern registry plus
key denylist for identifiers, and entity replacement for party names,
fail-closed at depth caps and unknown types.

Three findings mattered. LangSmith's `_hide_run_error` consults only
`anonymizer`, so the intuitive `hide_inputs`/`hide_outputs` config ships
tracebacks in the clear. An env-var-driven `LangChainTracer` opens a second
upload route through an unredacted global client — my sink looked spotless while
data left in parallel. And redacting full legal names missed the short forms
contracts define for themselves, leaving 306 bare references to the parties in
the sentences that mattered most.

Result: 13/13 secrets, 753 occurrences and 306 short forms to zero; +34 ms per
run, 28% less egress, graph output and all 11 operational fields unchanged.

---

**Word count:** ~175. Trim the LangSmith-internals sentence to hit 150 if the
brief is strict — but it is the strongest technical detail in the story, so
prefer trimming the opening instead.

## Numbers to have ready if they probe

- 13 of 13 unique secrets → 0; 753 occurrences → 0; 306 bare party short forms → 0
- Four telemetry channels, not one: inputs/outputs, metadata, tags, error strings
- Overhead +34.16 ms/run (1.2 ms/event); payload 110,389 → 79,524 bytes (−28.0%)
- False-positive rate 1 of 7 (14.3%), an EIN-shaped part number — unfixable by
  tuning, published rather than hidden
- Non-standard-format recall 3 of 5, one of those mislabelled as PHONE
- 38 tests; zero changes to the frozen contract
- Short forms derived by suffix-stripping AND parsed from the contract's own
  `("Globex")` definitions; generic ones like "First" refused by design
