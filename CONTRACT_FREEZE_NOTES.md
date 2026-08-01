# Contract Freeze Notes — `contract.py` v1.0.0

**Author:** Person 2 (Contract & Analyzer)
**Status:** Proposed for freeze — needs a thumbs-up from Persons 1, 3, 4, 5
**Basis:** Person 1's draft in `ARCHITECTURE_DESIGN.md` §6

This is the review record for the Contract Freeze Milestone: what changed from
the draft, why, and the three things I need the rest of the team to confirm or
push back on **before** we freeze. Once frozen, changing a field requires a
team-wide review.

---

## 1. Open question 1 — keep `rejection_reason_history`? → **KEPT**

`ARCHITECTURE_DESIGN.md` §5.3 specifies: *"If the same rejection_flag reason
repeats twice in a row, Coordinator escalates directly to `partial_output`."*

That rule cannot be implemented from a single `error_log` string — comparing a
reason to the previous one requires the previous one to still exist. Dropping
the field would silently delete a routing rule we already agreed on. Kept as
`List[str]`, append-only.

**Who appends:** any node that rejects. My Analyzer appends
`f"analyzer: {reason}"`. Person 4's Validator should use the same
`"<node>: <reason>"` convention so the Coordinator's "same reason twice" check
compares like with like.

## 2. Open question 2 — nested `ClauseRisk` model, or a loose dict? → **NESTED**

A loose `Dict[str, Any]` cannot be handed to `.with_structured_output()`, and
`.with_structured_output()` *is* the assignment's required guardrail for
failure mode 2. Without a real schema there is nothing for the model to be
constrained by and nothing for Pydantic to reject.

Resolution that costs no one any rework:

- `AgentState.analysis_payload` **stays `Dict[str, Any]`** — Person 1's
  Coordinator and Reporter are untouched, and LangGraph state merging stays
  simple.
- Two new models, `ClauseRisk` and `ContractAnalysis`, define what may go in
  it. The Analyzer may only write `ContractAnalysis.model_dump(mode="json")`,
  never a hand-built dict.

**Reading `analysis_payload` downstream** (Persons 3 and 4): it is a dict with
keys `contract_title`, `counterparty`, `overall_risk`, and `clauses` — a list
of dicts with `clause_id`, `clause_type`, `verbatim_quote`, `risk_level`,
`risk_rationale`. If you want it back as a typed object:
`ContractAnalysis.model_validate(state.analysis_payload)`.

## 3. Disagreement with §5.2 — the Analyzer retry should **not** cost a round

§5.2 says the structured-output retry and the loop retry both consume a
`round_number`, and invites me to flag it if I disagree. I disagree, on two
grounds:

1. **It never leaves the node.** The self-correction is an in-node loop: call
   model → validation fails → feed the validator's exact message back → call
   once more. Control never returns to the Coordinator, so there is no edge
   traversal to count.
2. **It would spend 40% of the loop budget on a bounded repair.** `MAX_ROUNDS`
   is 5. Charging a round to a retry that is already hard-capped at 1 by
   `MAX_ANALYZER_RETRIES` means one Analyzer hiccup costs a fifth of the
   graph's entire ability to recover from *everything else*.

The two guardrails stay independent and each stays hard-capped:
`MAX_ANALYZER_RETRIES = 1` inside the node, `MAX_ROUNDS = 5` across the graph.
Worst case per Analyzer visit is 2 LLM calls, and the Coordinator's ceiling is
unaffected.

For observability I added **`analysis_retry_count: int = 0`** so the retry is
still visible in state, in traces, and in Person 5's telemetry.

**Person 1: this is the one design decision I changed unilaterally. Say so if
you want it reverted — it is a two-line change either way.**

## 4. `rejection_flag` semantics clarified — set by **any** node, not just the Validator

The draft implied only the Validator sets it. My Analyzer also sets it, when
its one retry is exhausted, because that is the existing signal the Coordinator
already handles correctly (increment round, route back, eventually short-circuit
to a partial report). Documented in `contract.py` as:

> "The current state is not acceptable, roll back." Set by ANY node that cannot
> produce trustworthy output. Consumed (reset to False) by the Coordinator.

**Person 4: confirm this doesn't collide with how your Validator uses it.**

---

## 5. ⚠️ Integration bug found while writing this — for Person 1

The loop guardrail **does not fire** if a node fails without setting
`rejection_flag`. In `student_1_loop/snippet.py`, `round_number` is incremented
only inside the `if state.rejection_flag:` branch, but the `if not
state.is_validated:` branch below it also routes back to the Analyzer — without
incrementing anything.

Reproduced against the real Coordinator:

```python
state = AgentState(raw_input="x")
for _ in range(12):
    state = coordinator_node(state)
    state.is_validated = False      # a node that fails quietly
# -> NO TERMINATION after 12 visits: round_number=0, next_route=analyzer
```

`round_number` never leaves 0, so `round_number >= MAX_ROUNDS` is never true —
an infinite loop in the node whose whole job is preventing infinite loops. Only
LangGraph's own `recursion_limit` stops it, which is the failure mode, not the
guardrail.

My Analyzer always sets `rejection_flag=True` on terminal failure, so it does
not trigger this today. It is still a live hole for any node that doesn't, and
it is worth a regression test either way. Suggested fix — increment on *any*
backward route:

```python
if not state.is_validated:
    state.round_number += 1          # <-- add
    state.next_route = ROUTE_ANALYZER
    return state
```

Your call whether to take it — it's your node and your guardrail. Flagging it
rather than editing `student_1_loop/` myself.

---

## 6. Full change log vs. the draft

| # | Change | Impact on other nodes |
|---|---|---|
| 1 | Kept `rejection_reason_history` | none |
| 2 | Added `ClauseRisk`, `ContractAnalysis` | none — new models, no `AgentState` field changed |
| 3 | Added `analysis_retry_count: int = 0` | none — defaulted |
| 4 | Added `ClauseType` / `RiskLevel` enums, `RISK_ORDER`, `CLAUSE_ID_PATTERN`, `MIN_QUOTE_CHARS`, `MAX_ANALYZER_RETRIES`, `CONTRACT_VERSION` | none — additive |
| 5 | Added `normalize_text()`, `clause_reference()`, `validate_grounded()` | helpers, callable by anyone |
| 6 | Documented `rejection_flag` semantics | see §4 |

**No existing field was renamed, retyped or removed.** `ROUTE_*` constants,
`MAX_ROUNDS` and every original `AgentState` field are byte-identical in
meaning. Verified: the full pre-existing suite (Person 1's 15 tests) passes
unchanged against the new contract — `pytest -q` → **31 passed** (15 existing +
12 Analyzer unit/failure tests + 4 graph integration tests).

---

## 7. Fields NOT added — please claim yours before freeze

I deliberately did not invent fields for other people's nodes. If any of these
are needed, say so now; adding them after the freeze needs a team review.

- **Person 3 (Actor):** you have `sanitized_tool_calls: List[str]` and
  `execution_state: Dict[str, Any]`. Do you need a separate
  `blocked_tool_calls` list to *evidence* what the permission matrix refused?
  Right now a blocked call is only visible in `error_log`.
- **Person 4 (Validator):** you have `rejection_flag`,
  `rejection_reason_history` and `validation_notes: Optional[str]`. Is a
  single string enough, or do you want structured invariant results?
- **Person 5 (Privacy/Context):** you have `messages: List[Dict[str, Any]]`
  and `token_count: int`. Do you need a `history_summary` field to hold the
  condensed history separately from `messages`, or will you write the summary
  back into `messages` as a system entry?

---

## 8. Integration — the one-line swap for `main_system.py`

When Person 1 is ready, the Analyzer stub is a drop-in replacement (same
`AgentState -> AgentState` signature):

```python
# main_system.py
from student_2_silent.snippet import analyzer_node        # add

def build_graph(*, analyzer=analyzer_node, ...):          # was analyzer_stub
```

`analyzer_node` builds its own `ChatOllama` client and needs `ollama serve`
running. For tests that must stay offline, inject a scripted model instead:

```python
from student_2_silent.snippet import make_analyzer_node
from student_2_silent.fixtures import ScriptedStructuredLLM, GOOD_ANALYSIS

app = build_graph(analyzer=make_analyzer_node(ScriptedStructuredLLM([GOOD_ANALYSIS])))
```

Person 4: `make_analyzer_node` is the hook for your end-to-end tests — it lets
you drive the real graph deterministically without a model server.
