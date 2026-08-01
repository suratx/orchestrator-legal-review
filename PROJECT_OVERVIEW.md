# Project Overview — Legal Contract Review Orchestrator

A detailed, step-by-step account of what this project is, what has been
built so far, and what remains for each team member.

## 1. What this project is

A multi-agent orchestrator built on **LangGraph** that reviews legal
contracts end to end: it extracts clauses from raw contract text,
tags each with a risk category, proposes redlines (contract edits) via
mocked drafting tools, validates those redlines for structural
consistency, and produces a final report — or, if something goes
wrong, a "PARTIAL — MANUAL REVIEW REQUIRED" report instead of hanging
or crashing.

The system is not a linear pipeline. A central **Coordinator** node
reads the current state after every step and decides where to go next
— forward to the next worker, back to an earlier worker for
correction, or straight to a safe stop. This dynamic routing is what
makes the six failure modes below possible (and what makes six
code-level guardrails necessary).

## 2. The five roles and their failure modes

Five people, five nodes/layers, six required failure modes (Person 5
owns two):

| # | Owner | Node / Layer | Failure Mode | Guardrail |
|---|---|---|---|---|
| 1 | Person 1 | Coordinator | Infinite Graph Loops | `round_number >= 5` hard cap → short-circuit to partial output |
| 2 | Person 2 | Analyzer | Silent Hallucination | Structured output + schema validation + one auto-retry |
| 3 | Person 3 | Actor | Rogue Tool Execution | Tool-call permission matrix + `InvalidToolCallException` |
| 4 | Person 4 | Validator | Downstream Cascade Failure | Sanitization node + rejection flag + rollback |
| 5 | Person 5 | Tracing (global) | Data Privacy Leak | Redaction interceptor before LangSmith export |
| 6 | Person 5 | Context Manager (global) | Context Window Explosion | Token-threshold summarization + pruning |

Every guardrail must be **code**, not a prompt instruction — a plain
Python `if` on a state field that cannot be argued around by an LLM.

## 3. The mandatory contract

Before any guardrail code is written, the team must agree on one
shared state schema: `contract.py`, a Pydantic `AgentState` model. Every
node reads from and writes to this same object. Once the team reviews
and commits it, it is **frozen** — no node may add a field outside it
without a team-wide review (enforced in code via
`model_config = ConfigDict(extra="forbid")`).

Status: **not yet frozen**. A draft exists (built directly from
`ARCHITECTURE_DESIGN.md` Section 6) so Person 1 could develop and test
the Coordinator against a real schema ahead of the freeze meeting.
Person 2 owns turning this draft into the final, committed version.

## 4. Step by step — what Person 1 did

1. **Drafted the architecture** (`ARCHITECTURE_DESIGN.docx` /
   `ARCHITECTURE_DESIGN.md`) — graph topology, node interfaces
   (who reads/writes which state fields), routing/retry/rollback/
   termination rules for all four failure boundaries, the initial
   `AgentState` design, and the repository layout. This is the
   reference every other team member builds against.
2. **Built the Coordinator node** (`student_1_loop/snippet.py`) — a
   pure, LLM-free routing function. Every worker node returns control
   to it, which makes it the single choke point where a deterministic
   guardrail can catch a runaway loop no matter which upstream node
   caused it.
3. **Implemented the loop guardrail** — a `round_number` counter
   incremented on every backward route, hard-capped at 5. On breach,
   the Coordinator does not throw; it deterministically routes to
   `partial_output`.
4. **Reproduced the failure mode** (`student_1_loop/test_failure.py`)
   — simulated an adversarial upstream node that always rejects, and
   measured the unguarded coordinator running away (500+ iterations,
   no natural exit, ~$15+ projected API spend) versus the guarded one
   stopping cleanly at round 5 (6 iterations, $0.18, 100% clean
   termination).
5. **Unit-tested the guardrail** (`student_1_loop/test_coordinator.py`)
   — 11 pytest cases: normal routing, rejection routing, guardrail
   boundary conditions (below/at/above the ceiling), guardrail
   precedence over every other branch, guardrail not overwriting an
   existing error, and state immutability.
6. **Built the Reporter node** and wired both it and the Coordinator
   into a real LangGraph `StateGraph` in `main_system.py`, using
   injectable Analyzer/Actor/Validator stub nodes as placeholders.
7. **Integration-tested the compiled graph** (`test_main_system.py`)
   — proved the happy path produces a full report, and that an
   adversarial validator drives the guardrail to a clean partial
   output *inside the real compiled graph*, under a recursion limit
   small enough to prove the guardrail itself (not LangGraph's own
   recursion ceiling) is what stops the loop.
8. **Wrote the interview story** (`INTERVIEW_STORIES.md`, Person 1's
   entry) — a ~150-word, quantified account of the failure and fix.
9. **Scaffolded the rest of the repo** — empty, ready-to-use folders
   for Person 2–5 (`student_2_silent/` … `student_6_tokens/`), plus
   draft `README.md` and `DESIGN_DOCS.md` (19-risk analysis: the 6
   selected guardrails plus 13 additional risks considered, with
   status/rationale for each) so the team documents are not starting
   from zero.
10. **Published the repository** to GitHub as a public repo:
    https://github.com/suratx/orchestrator-legal-review

Verification at each stage: `pytest -v` → 15 passed, 0 failed
(11 coordinator unit tests + 2 failure-mode repro tests + 2 full-graph
integration tests).

## 5. What comes next — for the rest of the team

**Person 2 — Contract and Analyzer**
- Review the draft `AgentState` in `contract.py` / `ARCHITECTURE_DESIGN.md`
  Section 6, resolve the two open questions flagged there
  (`rejection_reason_history` scope, whether `analysis_payload` needs a
  nested `ClauseRisk` model), and commit the frozen version.
- Build the Analyzer using `.with_structured_output()`, catch schema
  validation errors programmatically, and give it exactly one automated
  self-correction retry (per Section 5.2 — this retry consumes one
  `round_number`, sharing the Coordinator's counter).
- Reproduce the silent-hallucination failure mode and its guardrail in
  `student_2_silent/` (`snippet.py` + `test_failure.py`), with
  before/after metrics.
- Fill in the README (per the original assignment text, README
  ownership sits with Person 2 — a starting draft already exists at
  the repo root).

**Person 3 — Actor and Tool Security**
- Build the Actor and every domain tool as safely mocked (no real
  destructive action possible, per the assignment's zero-tolerance
  safety mandate).
- Define the tool/parameter permission matrix, intercept requested
  tool calls before execution, and raise `InvalidToolCallException` on
  any unauthorized or malformed request (per Section 5.4 — this is not
  retryable; the Coordinator aborts straight to `partial_output`).
- Reproduce the rogue-tool-execution failure mode and its guardrail in
  `student_3_rogue/`.
- Lead the final safety review across the whole system.

**Person 4 — Validator and Testing**
- Build the Validator/sanitization node between Actor and Reporter,
  with structural and domain invariants checked via programmatic
  assertions.
- On invalid output, set `rejection_flag` and drive the rollback route
  back to the Analyzer (per Section 5.3 — including the "same reason
  twice → escalate immediately" rule).
- Reproduce the downstream-cascade failure mode and its guardrail in
  `student_4_cascade/`.
- Lead unit, integration, failure-mode, rollback, and end-to-end
  testing once all five nodes are integrated.

**Person 5 — Privacy and Context Management**
- Configure LangSmith tracing and build a redaction interceptor that
  scrubs PII/secrets from graph payload metadata before it is exported.
- Build the context manager: count tokens, and when a threshold is
  crossed, summarize history and prune intermediate tool outputs while
  preserving core state fields untouched.
- Reproduce both failure modes (privacy leak, context explosion) with
  guardrails in `student_5_trace/` and `student_6_tokens/`.

**Everyone, once the above lands**
- Replace the stub Analyzer/Actor/Validator in `main_system.py` with
  the real implementations (same `AgentState -> AgentState` signature,
  so this should be a drop-in swap).
- Fill in the remaining TODOs in `INTERVIEW_STORIES.md` and
  `DESIGN_DOCS.md`.
- Record each person's 2-minute failure/success demo video.
- Record the team's combined 5-minute end-to-end demo video, showing
  all six guardrails active and the graph managing conditional state
  changes dynamically.
- Add each team member as a GitHub collaborator (or accept
  fork+PR contributions — the repo is currently public and readable by
  anyone, but push access still requires an explicit invite).

## 6. Current status snapshot

| Deliverable | Status |
|---|---|
| Architecture design | Done |
| Contract (`contract.py`) | Draft only — not frozen |
| Coordinator + loop guardrail + Reporter | Done |
| `main_system.py` integration | Done, but still running on stubs for Analyzer/Actor/Validator |
| Analyzer (Person 2) | Not started |
| Actor (Person 3) | Not started |
| Validator (Person 4) | Not started |
| Tracing redaction + Context manager (Person 5) | Not started |
| `student_2_silent/` … `student_6_tokens/` | Empty, reserved |
| README / DESIGN_DOCS / INTERVIEW_STORIES | Drafted, partially complete (Person 1's parts done, others TODO) |
| Demo videos (individual + team) | Not started |
