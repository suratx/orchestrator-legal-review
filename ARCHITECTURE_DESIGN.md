# Architecture Design Document

**Legal Contract Review Orchestrator**

- **Author:** Person 1 (Coordinator, Architecture & Integration)
- **Status:** DRAFT — for team review before Contract Freeze
- **Domain:** Legal Contract Review (Clause Extraction → Risk Analysis → Redline Generation → Counter-Party Verification)
- **Stack:** Python, LangGraph, LangChain Core, Pydantic, LangSmith

## 1. Purpose

This document defines the graph topology, node interfaces, routing rules, retry/rollback behavior, and termination conditions for the Generic Task Orchestrator instantiated in the Legal Contract Review domain. It is the reference all five team members build against. Once reviewed and approved, Person 2 will convert Section 6 (state design) into the frozen `contract.py`.

## 2. Domain Mapping

The generic 4-worker template maps onto the legal domain as follows:

| Generic Role | Legal Domain Instantiation | Owner |
|---|---|---|
| Coordinator | Task router / loop controller | Person 1 |
| Worker A — Analyzer | Clause Extractor + Risk Analyzer: parses raw contract text into structured clauses, tags each with a risk category | Person 2 |
| Worker B — Actor | Redline Generator: calls (mocked) drafting tools to propose contract edits based on flagged risks | Person 3 |
| Worker C — Validator | Counter-Party / Structural Validator: checks redlines are structurally valid and consistent with the original clause set before reporting | Person 4 |
| Global: Tracing/Privacy | LangSmith redaction layer (client names, deal terms, PII) | Person 5 |
| Global: Context Manager | Token/history pruning before each Coordinator loop | Person 5 |
| Worker D — Reporter | Final counter-party verification summary + report compiler | Person 1 |

> **Note on team size:** the assignment template assumes 6 owners (loop, silent-hallucination, rogue-tool, cascade, privacy, tokens). Your team has folded Reporter into Person 1's integration scope — confirm this is intentional before freeze, since the rubric grades "Global Graph Layer" work per-guardrail regardless of headcount.

## 3. Graph Topology

Coordinator routes to Analyzer on new tasks or retries (Route A), to Actor once analysis is validated (Route B), and to Reporter once validation succeeds (Route C). The Validator sits between Actor and Reporter and can force a rollback to the Coordinator with a rejection flag, incrementing `round_number`.

Terminal node: `partial_output` — reached only via forced short-circuit (Section 5.1) or successful Reporter completion.

```
                  ┌──────────────────────────────┐
                  ▼                              │ (Loop / Self-Correction)
               [ 0. Coordinator Node ] ──────────┼──────────────┐
                  │              ▲               │              │
                  │ (Route A)    │ (Error Flag)  │ (Route B)    │ (Route C)
                  ▼              │               ▼              ▼
     [ 1. Worker A: Analyzer ] ──┘     [ 2. Worker B: Actor ]   [ 4. Worker D: Reporter ]
                  │                              │
                  │ (Valid Schema)               │ (Execution State)
                  ▼                              ▼
     [ 5. Worker C: Validator ] ◄────────────────┘
```

## 4. Node Interfaces

Each node receives and returns the shared `AgentState` object (frozen contract, Section 6). No node may mutate state outside its declared fields — this is enforced by Person 4's rejection-flag logic at the Validator boundary, and by Pydantic validation on every node's return.

| Node | Reads | Writes | External calls |
|---|---|---|---|
| Coordinator | `round_number`, `is_validated`, `error_log`, `rejection_flag` | `round_number` (+1 on loop), `next_route` | none (pure routing) |
| Analyzer | `raw_input`, `error_log` (on retry) | `analysis_payload`, `is_validated`, `error_log` | LLM (`.with_structured_output`) |
| Actor | `analysis_payload` | `sanitized_tool_calls`, `execution_state` | LLM + mocked drafting tools |
| Validator | `sanitized_tool_calls`, `execution_state`, `analysis_payload` | `rejection_flag`, `validation_notes` | none (deterministic checks) |
| Reporter | `analysis_payload`, `execution_state`, `validation_notes` | `final_report` | none (template/LLM summary) |
| Context Manager (P5) | `messages` | pruned `messages`, `token_count` | none — runs before Coordinator re-entry |
| Tracing Redactor (P5) | full state (read-only) | nothing (side-channel to LangSmith) | LangSmith |

## 5. Routing, Retry, Rollback, Termination Rules

### 5.1 Termination — Infinite Loop Guardrail (Person 1)

- `state.round_number` increments every time the Coordinator routes back upstream (Route A after a rejection or validation error).
- Hard limit: `round_number >= 5`. On breach, the Coordinator does not raise an exception — it deterministically routes to `partial_output`, setting `error_log` with the last known failure reason, and the Reporter emits a "PARTIAL — MANUAL REVIEW REQUIRED" report instead of a full one.
- This check lives in the conditional-edge function, not in a prompt. It must be a plain Python `if` on the state field.

### 5.2 Retry (Person 2's scope, Coordinator enforces the count)

- Analyzer gets exactly one self-correction retry on a schema validation failure. The Coordinator does not distinguish "structured-output retry" from "loop retry" in round-counting — both consume a round. This is a deliberate design choice to keep the loop guardrail simple and universal; flag this in review if Person 2 disagrees.

### 5.3 Rollback (Person 4's scope, Coordinator handles the edge)

- If Validator sets `rejection_flag = True`, Coordinator routes back to Analyzer (Route A) with `error_log` populated from `validation_notes`, not back to Actor directly — this avoids re-running a redline against clauses that may themselves need re-extraction.
- If the same rejection_flag reason repeats twice in a row, Coordinator escalates directly to `partial_output` regardless of `round_number`, to avoid burning rounds on a deterministically unfixable error.

### 5.4 Rogue Tool Abort (Person 3's scope)

- If Actor raises `InvalidToolCallException`, this is not treated as a retryable error. Coordinator routes straight to `partial_output` with `error_log = "BLOCKED: unauthorized tool call"`. No retry — a permission violation is not a transient failure.

## 6. Initial State Design

*(draft — for Person 2 to formalize into `contract.py`)*

```python
class AgentState(BaseModel):
    task_domain: str = "legal_contract_review"
    raw_input: str
    round_number: int = 0
    is_validated: bool = False
    rejection_flag: bool = False
    rejection_reason_history: List[str] = Field(default_factory=list)
    error_log: Optional[str] = None
    analysis_payload: Dict[str, Any] = Field(default_factory=dict)   # clauses + risk tags
    sanitized_tool_calls: List[str] = Field(default_factory=list)    # approved redline actions
    execution_state: Dict[str, Any] = Field(default_factory=dict)    # Actor output
    validation_notes: Optional[str] = None
    final_report: Optional[str] = None
    messages: List[Dict[str, Any]] = Field(default_factory=list)     # pruned by Context Manager
    token_count: int = 0
```

**Open questions for freeze review:**

- Should `rejection_reason_history` be in scope for v1, or is a single `error_log` string enough for the escalation rule in 5.3?
- Does Analyzer's clause/risk output need its own nested Pydantic model (`ClauseRisk`) inside `analysis_payload`, or stay as a loose dict for v1 speed?

## 7. Repository Structure

```
/orchestrator-legal-review/
  ├── README.md
  ├── DESIGN_DOCS.md
  ├── INTERVIEW_STORIES.md
  ├── contract.py                # frozen after team review
  ├── main_system.py             # Person 1 integrates here
  ├── [student_1_loop]/
  │     ├── snippet.py           # Coordinator + round_number guardrail
  │     └── test_failure.py      # infinite loop repro (guardrail disabled)
  ├── [student_2_silent]/
  │     ├── snippet.py
  │     └── test_failure.py
  ├── [student_3_rogue]/
  │     ├── snippet.py
  │     └── test_failure.py
  ├── [student_4_cascade]/
  │     ├── snippet.py
  │     └── test_failure.py
  ├── [student_5_trace]/
  │     ├── snippet.py
  │     └── test_failure.py
  └── [student_6_tokens]/         # or folded into student_5 if team stays at 5
        ├── snippet.py
        └── test_failure.py
```

## 8. Next Steps

- Team review of Sections 4–6 (interfaces + state) — flag disagreements before Contract Freeze.
- Person 2 formalizes Section 6 into `contract.py`, commits, freezes.
- Parallel build begins on individual guardrail folders.
- Person 1 builds Coordinator, loop guardrail, Reporter, then integrates all layers into `main_system.py`.
- Person 4 leads end-to-end testing once integration lands.
