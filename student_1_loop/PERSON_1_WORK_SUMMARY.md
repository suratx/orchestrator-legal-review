# Person 1 — Work Summary

**Role:** Architecture, Coordinator, and Integration
**System:** Multi-agent legal contract review orchestrator (LangGraph, Python)

## What was built

**1. Architecture design (`ARCHITECTURE_DESIGN.docx`)**
Drafted the complete graph architecture for team review: graph topology
(Coordinator → Analyzer → Actor → Validator → Reporter, with rollback
edges), node interfaces (shared `AgentState` in/out, no field mutation
outside the contract), routing/retry/rollback/termination rules,
repository structure, and the initial state design later handed to
Person 2 to formalize into `contract.py`.

**2. Coordinator node + 5-round loop guardrail (`student_1_loop/snippet.py`)**
A pure, LLM-free routing function. It is the single choke point every
other node returns control to, which makes it the one place a
deterministic guardrail can catch a runaway loop regardless of which
upstream node caused it. The guardrail is a plain `round_number >= 5`
check in code (not a prompt instruction) that short-circuits routing to
a `partial_output` terminal state before any other branch can run.

**3. Failure-mode reproduction (`student_1_loop/test_failure.py`)**
A deterministic repro script simulating an adversarial upstream node
that always rejects, comparing an unguarded coordinator (runs away, no
natural exit) against the guarded one (stops cleanly at round 5).
Quantified results:

| Metric | Without guardrail | With guardrail |
|---|---|---|
| Iterations | 500+ (hits test-harness safety cap, uncapped otherwise) | 6 |
| Terminates cleanly | No | Yes |
| Estimated API spend | $15.00+ | $0.18 |

**4. Unit test suite (`student_1_loop/test_coordinator.py`)**
11 pytest cases covering normal routing, rejection routing with round
increment, guardrail boundary conditions (below/at/above the ceiling),
guardrail precedence over all other branches, guardrail not overwriting
an existing `error_log`, and state immutability.

**5. Reporter node + `main_system.py` integration**
The Reporter emits either a full report or, when the guardrail fires (or
`error_log` is set), a "PARTIAL -- MANUAL REVIEW REQUIRED" report. Wired
the Coordinator and Reporter into a real LangGraph `StateGraph` with
injectable Analyzer/Actor/Validator nodes (currently stubs, to be
replaced 1:1 by Person 2/3/4's real implementations behind the same
`contract.py` interface).

**6. Integration test (`test_main_system.py`)**
Two tests against the compiled graph itself (not just the isolated
coordinator function): the happy path produces a full report, and an
injected always-rejecting validator drives the guardrail inside the real
graph to a clean partial-output termination under a small
`recursion_limit`, proving the guardrail — not LangGraph's own recursion
ceiling — is what stops the loop.

**7. Interview story**
A ~150-word, quantified narrative in `INTERVIEW_STORIES.md` describing
the failure mode, the fix, and the before/after metrics.

**8. Repo scaffolding for the rest of the team**
Created `student_2_silent/`, `student_3_rogue/`, `student_4_cascade/`,
`student_5_trace/`, `student_6_tokens/` with placeholder docs describing
each owner's node, failure mode, and required deliverables, plus draft
`README.md` and `DESIGN_DOCS.md` (19-risk analysis: 6 selected
guardrails + 13 additional risks considered, with status/rationale).

## Verification

Full test suite: `pytest -v` → 15 passed, 0 failed (11 unit tests + 2
failure-mode repro tests + 2 integration tests).

## What is still open (not part of Person 1's individual scope)

- Contract freeze (Person 2)
- Analyzer, Actor, Validator real implementations (Person 2/3/4)
- LangSmith redaction + context manager (Person 5)
- Remaining 5 failure-mode folders' code, metrics, and videos
- Person 1's own 2-minute failure/success demo video (recording, not code)
- Team's combined 5-minute end-to-end demo video
