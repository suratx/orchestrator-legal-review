# Project Overview — Legal Contract Review Orchestrator

Orientation and current status. Not an assignment deliverable — the graded
documents are `README.md`, `DESIGN_DOCS.md`, `INTERVIEW_STORIES.md`,
`contract.py`, `main_system.py` and the six `student_*/` folders. This file
exists so anyone joining mid-project can see where things stand.

## 1. What this project is

A multi-agent orchestrator on **LangGraph** that reviews legal contracts end to
end: extract clauses from raw text, tag each with a risk level, propose redlines
through mocked drafting tools, validate those redlines, and emit a report — or,
if any guardrail trips, a `PARTIAL — MANUAL REVIEW REQUIRED` report instead of
hanging or crashing.

It is not a linear pipeline. A central **Coordinator** reads state after every
step and decides where to go next: forward, back for correction, or to a safe
stop. That dynamic routing is what makes the six failure modes reachable, and
what makes six code-level guardrails necessary.

## 2. Ownership

Five people, six failure modes — Person 5 owns two.

| # | Owner | Layer | Failure mode | Guardrail |
|---|---|---|---|---|
| 1 | Person 1 | Coordinator | Infinite graph loops | `round_number >= 5` → short-circuit to partial output |
| 2 | Person 2 | Analyzer | Silent hallucination | Structured output + source-grounding invariants + one retry |
| 3 | Person 3 | Actor | Rogue tool execution | Tool permission matrix + `InvalidToolCallException` |
| 4 | Person 4 | Validator | Downstream cascade failure | Sanitization node + rejection flag + rollback |
| 5 | Person 5 | Global — Tracing | Data privacy leak | Redaction interceptor on all four telemetry channels |
| 6 | Person 5 | Global — Context | Context window explosion | Context node: pruning ladder + bounded rolling summary |

Every guardrail is **code** — a plain Python `if` on a state field, not a prompt
instruction. An LLM can be talked out of an instruction; it cannot be talked out
of a `ValidationError`.

## 3. The contract

`contract.py` is the shared state schema every node reads from and writes to.

**Status: FROZEN at v1.0.0.** `extra="forbid"` means a node that tries to add an
undeclared field fails at construction rather than silently corrupting
downstream workers. The freeze review record — what changed from Person 1's
draft, the open questions and how each was resolved — is in
`CONTRACT_FREEZE_NOTES.md`. All five owners have now answered §7: no additional
fields were required by any layer.

## 4. Current status

| Deliverable | Status |
|---|---|
| Architecture design (`ARCHITECTURE_DESIGN.md`) | Done |
| Contract (`contract.py`) | **Frozen, v1.0.0** |
| Coordinator + loop guardrail + Reporter (P1) | Done |
| Analyzer + hallucination guardrail (P2) | Done |
| Actor + tool-permission guardrail (P3) | Done |
| Validator + cascade guardrail (P4) | Done |
| Tracing redaction (P5) | Done |
| Context/token manager (P5) | Done |
| `main_system.py` integration | Done — all six guardrails active |
| Test suite | 120 tests |
| `README.md` / `DESIGN_DOCS.md` | Complete |
| `INTERVIEW_STORIES.md` | 5 of 6 entries complete |
| Individual demo videos (2 min each) | 1 of 6 recorded |
| Team demo video (5 min) | Not started |

## 5. What remains

**Person 3** — the `INTERVIEW_STORIES.md` entry is still a TODO, and
`DESIGN_DOCS.md` row 3 could carry the quantified before/after figure the other
rows now have. The code and guardrail are done.

**Everyone** — the individual 2-minute failure/guardrail videos. Only
`student_2_silent/demo.mp4` exists so far. Each person's `test_failure.py`
prints a before/after table designed to carry the on-screen narrative:

```bash
python student_1_loop/test_failure.py      # infinite loop
python student_2_silent/test_failure.py    # silent hallucination
python student_3_rogue/test_failure.py     # rogue tool execution
python student_4_cascade/test_failure.py   # downstream cascade
python student_5_trace/test_failure.py     # PII leak to telemetry
python student_6_tokens/test_failure.py    # context explosion
```

**The team** — the combined 5-minute end-to-end demo, showing the graph managing
conditional state changes dynamically with all six guardrails active.
`python main_system.py` runs the integrated system.

**Housekeeping** — add each member as a GitHub collaborator, or continue via
fork + PR. The repo is public and readable, but push access needs an invite.

## 6. Running it

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt
python main_system.py    # integrated graph, all guardrails active
pytest -q                # full suite
```

Tests never touch the network — scripted models are injected everywhere, so
`pytest` passes with Ollama stopped. The two live scripts
(`student_2_silent/benchmark_live.py`, `student_6_tokens/calibrate_tokens.py`)
are opt-in and need `ollama serve`.

## 7. Correction to an earlier version of this document

An earlier draft stated that the Analyzer's self-correction retry consumes a
`round_number`, sharing the Coordinator's counter. **That is no longer true.**
Person 2 raised it during the contract freeze and the team took the other
option: the retry is an in-node loop that never returns to the Coordinator, so
there is no edge traversal to count, and charging it a round would spend 20% of
the loop budget on a repair already hard-capped at one attempt. The two
guardrails are independent and separately capped — `MAX_ANALYZER_RETRIES = 1`
inside the node, `MAX_ROUNDS = 5` across the graph. See
`CONTRACT_FREEZE_NOTES.md` §3.
