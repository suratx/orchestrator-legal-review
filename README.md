# Orchestrator — Legal Contract Review

A multi-agent [LangGraph](https://langchain-ai.github.io/langgraph/) system
that reviews legal contracts end to end — **clause extraction → risk analysis
→ redline generation → counter-party verification** — built around a central
Coordinator that routes dynamically and six **code-level** guardrails that stop
the six critical multi-agent failure modes.

Every guardrail here is a plain Python `if` on a state field, not a line of
prompt text. An LLM can be talked out of an instruction; it cannot be talked
out of a `ValidationError`.

---

## The domain, and why it was chosen

Legal contract review is high-stakes in a way that makes each failure mode
concrete rather than academic:

| Failure mode | What it costs in this domain |
|---|---|
| Infinite loop | Budget drains while a contract sits unreviewed |
| **Silent hallucination** | A liability cap that doesn't exist is reported as real, and someone signs |
| Rogue tool execution | An unreviewed redline is written into a live document |
| Cascade failure | A malformed redline crashes verification, or worse, passes it |
| Privacy leak | Client names and deal terms land in a cloud telemetry dashboard |
| Context explosion | Long reviews blow the context window and the token budget with it |

**Input:** raw contract text. **Output:** a structured review report — extracted
clauses, risk tags, proposed redlines, validation notes — or, if any guardrail
trips, a `PARTIAL — MANUAL REVIEW REQUIRED` report. The system is designed to
fail loudly and stop, never to fail quietly and continue.

---

## Architecture

```
                      ┌──────────────────────────────────────────────┐
                      ▼                                              │ (Loop / Self-Correction)
 entry ──► [ Context Manager ] ──► [ 0. Coordinator Node ] ──────────┤
             global layer #6           │          │          │       │
                      ▲                │ (Route A)│ (Route B)│ (Route C)
                      │                ▼          ▼          ▼
                      │   [ Worker A: Analyzer ]  [ Worker B: Actor ]  [ Worker D: Reporter ]
                      │                │          │                            │
                      │  (Error Flag)  │          │ (Execution State)          ▼
                      │                │          ▼                           END
                      │                │   [ Worker C: Validator ]
                      └────────────────┴──────────┘
                         both return through the Context Manager

 ── global layer #5 ──────────────────────────────────────────────────────────
 every state transition above ──► [ Redaction Interceptor ] ──► telemetry
```

Not a linear pipeline: no worker decides what happens next. Each one returns
control upward — through the Context Manager, then to the Coordinator — which
reads state flags and decides whether to go forward, roll back, or stop. That
single choke point is what makes a deterministic loop guardrail possible, and
putting the Context Manager immediately in front of it means every routing
decision is made against a context window that has already been bounded.

The two global layers are not workers. The **Context Manager** (#6) sits at the
head of every loop transition, so no worker can hand the model a context window
it has not bounded. The **Redaction Interceptor** (#5) sits on the
graph→telemetry boundary rather than inside any node, so it covers all six
nodes — and any node added later — by construction.

Node interfaces and the routing/retry/rollback rules are in
[`ARCHITECTURE_DESIGN.md`](ARCHITECTURE_DESIGN.md) — the design record, written
before the two global layers were built, so its §3 diagram shows the worker
topology only. The diagram above is the graph as assembled in
[`main_system.py`](main_system.py), which is the authority on what actually
runs.

---

## Stack

| | |
|---|---|
| Language | Python 3.11 — **single-language repo**, per the zero-tolerance rule |
| Framework | LangGraph + LangChain Core |
| Schema | Pydantic v2 (the frozen contract) |
| LLM | Local **Ollama / llama3.2** — free, offline, no API key to leak |
| Observability | LangSmith (with a redaction interceptor in front of it) |
| Testing | pytest |

The LLM is local by design: it keeps the whole team able to run the system
without sharing an API key, and it makes the privacy guardrail (Person 5) a
real exercise rather than a hypothetical one.

---

## Setup

```bash
conda create -n orchestrator-legal python=3.11 -y
conda activate orchestrator-legal
pip install -r requirements.txt
```

For anything that calls the model (the Analyzer, the integrated graph, the
live benchmark), you also need Ollama running:

```bash
ollama pull llama3.2
ollama serve
```

Optional environment variables (sensible defaults are built in):

| Variable | Default |
|---|---|
| `OLLAMA_MODEL` | `llama3.2` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` |

**The test suite never touches the network.** Every test injects a scripted
model, so `pytest` passes with Ollama stopped.

---

## Running it

```bash
# The integrated graph, end to end
python main_system.py

# Everything
pytest -v                                  # 124 tests

# One person's failure-mode reproduction + metrics table
python student_1_loop/test_failure.py      # infinite loop
python student_2_silent/test_failure.py    # silent hallucination
python student_5_trace/test_failure.py     # PII/secret leak to telemetry
python student_6_tokens/test_failure.py    # context explosion / token burn

# Reproducible measurement (offline)
python student_6_tokens/benchmark.py       # window sizes, token totals, latency

# Live measurement against the real model (needs ollama serve)
python student_2_silent/benchmark_live.py --runs 12
python student_6_tokens/calibrate_tokens.py       # token counter vs real llama3.2
```

---

## Team & ownership

Five people, six failure modes — Person 5 owns two.

| # | Owner | Node / Layer | Failure mode | Guardrail | Folder |
|---|---|---|---|---|---|
| 1 | Person 1 | Coordinator | Infinite graph loops | `round_number >= 5` → short-circuit to partial output | [`student_1_loop/`](student_1_loop/) |
| 2 | Person 2 | Worker A — Analyzer | **Silent hallucination** | `.with_structured_output()` + source-grounding invariants + 1 retry | [`student_2_silent/`](student_2_silent/) |
| 3 | Person 3 | Worker B — Actor | Rogue tool execution | Permission matrix + `InvalidToolCallException` | [`student_3_rogue/`](student_3_rogue/) |
| 4 | Person 4 | Worker C — Validator | Downstream cascade failure | Sanitization node + rejection flag + rollback | [`student_4_cascade/`](student_4_cascade/) |
| 5 | Person 5 | Global — Tracing | Data privacy leak | Redaction interceptor on all four telemetry channels + tracing-route audit | [`student_5_trace/`](student_5_trace/) |
| 6 | Person 5 | Global — Context | Context window explosion | Context Management Node: token-threshold ladder + bounded rolling summary | [`student_6_tokens/`](student_6_tokens/) |

Person 1 also owns the architecture, the Reporter node, and integration into
`main_system.py`. Person 2 owns `contract.py` and this README. Person 3 leads
the final safety review. Person 4 leads end-to-end testing.

---

## The contract

[`contract.py`](contract.py) is the mandatory shared state schema — one
Pydantic `AgentState` that every node reads from and writes to. It sets
`extra="forbid"`, so a node that tries to smuggle in an undeclared field fails
loudly instead of silently corrupting downstream workers.

**Status: v1.0.0, proposed for freeze.** The review record — what changed from
Person 1's draft, the two open questions resolved, one design disagreement, and
an integration bug found along the way — is in
[`CONTRACT_FREEZE_NOTES.md`](CONTRACT_FREEZE_NOTES.md). Persons 1, 3, 4 and 5
should read §7 there and claim any fields they need **before** we freeze.

Beyond the state model, the contract also carries the domain invariants
(`ClauseRisk`, `ContractAnalysis`, `validate_grounded()`) — because "is this
analysis trustworthy?" is shared law, not one node's private opinion.

---

## Repository structure

```
orchestrator-legal-review/
├── README.md                    # this file
├── ARCHITECTURE_DESIGN.md       # topology, interfaces, routing rules
├── CONTRACT_FREEZE_NOTES.md     # freeze review record
├── DESIGN_DOCS.md               # 19 alternative failure risks considered
├── INTERVIEW_STORIES.md         # six ~150-word interview narratives
├── PROJECT_OVERVIEW.md          # status snapshot (not a graded deliverable)
├── contract.py                  # THE FROZEN CONTRACT
├── main_system.py               # the integrated graph, all guardrails active
├── requirements.txt
├── test_main_system.py
└── student_{1..6}_*/
      ├── snippet.py             # where the guardrail sits in the graph
      ├── test_failure.py        # reproduction: the failure, unguarded
      ├── METRICS.md             # before/after numbers
      └── demo.mp4               # 2-minute demo (recorded per person)
```

---

## Safety

Per the assignment's safety mandate, **every action touching external
infrastructure is mocked**. No file writes outside this repo, no deletions, no
network calls to anything but the local Ollama server — including inside the
deliberately-broken reproduction scripts. Person 3 owns the final audit.
