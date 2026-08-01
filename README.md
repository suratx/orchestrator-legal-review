# Orchestrator — Legal Contract Review

A multi-agent LangGraph orchestrator that reviews legal contracts (clause
extraction → risk analysis → redline generation → counter-party
verification), built around a central Coordinator that dynamically routes
execution and enforces six code-level failure guardrails.

## Domain

Legal Contract Review. Input: raw contract text. Output: a structured
review report (extracted clauses, risk tags, proposed redlines,
validation notes) or, if a guardrail trips, a partial/manual-review
report.

## Stack

- **Language:** Python (single-language repo — no TypeScript, per the
  zero-tolerance multi-language rule)
- **Framework:** LangGraph + LangChain Core
- **Schema:** Pydantic
- **Observability:** LangSmith
- **Testing:** pytest

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install langgraph langchain-core pydantic langsmith pytest
```

Run the integrated graph:

```bash
python main_system.py
```

Run the full test suite:

```bash
pytest -v
```

## Team & Ownership

| # | Owner | Node / Layer | Critical failure mode | Folder |
|---|-------|---------------|------------------------|--------|
| 1 | Person 1 | Coordinator (Orchestrator) | Infinite Graph Loops | `student_1_loop/` |
| 2 | Person 2 | Worker A (Analyzer) | Silent Hallucination | `student_2_silent/` |
| 3 | Person 3 | Worker B (Actor) | Rogue Tool Execution | `student_3_rogue/` |
| 4 | Person 4 | Worker C (Validator) | Downstream Cascade Failure | `student_4_cascade/` |
| 5 | Person 5 | Global — Tracing & Privacy | Data Privacy Leak (Tracing) | `student_5_trace/` |
| 5 | Person 5 | Global — Context/Token Manager | Context Window Explosion | `student_6_tokens/` |

Person 1 also owns overall architecture (`ARCHITECTURE_DESIGN.docx`), the
Reporter node, and integration of all six guardrails into
`main_system.py`. Person 2 also owns `contract.py` (formalized from the
team-approved state design) once frozen. Person 4 leads unit,
integration, failure-mode, rollback, and end-to-end testing after
integration lands.

## Repository Structure

See `ARCHITECTURE_DESIGN.docx` Section 7 for the full layout. Top level:

```
/orchestrator-legal-review/
  README.md
  DESIGN_DOCS.md
  INTERVIEW_STORIES.md
  contract.py
  main_system.py
  student_1_loop/ .. student_6_tokens/
```

## Contract Freeze

`contract.py` is the mandatory shared state schema. It is currently a
**draft** (`AgentState`, see file header) built from
`ARCHITECTURE_DESIGN.docx` Section 6. No guardrail node may add fields
outside this contract without a team-wide review. Status: **not yet
frozen** — pending Person 2 review and commit.

## Status

Work in progress. See `ARCHITECTURE_DESIGN.docx` Section 8 ("Next Steps")
for the current stage of the team workflow.
