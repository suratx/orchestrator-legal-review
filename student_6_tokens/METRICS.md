# Person 5 Metrics — Context Window Explosion & Token Burn

**Guardrail:** Context Management Node at the head of every loop transition
**Measured by:** `python student_6_tokens/test_failure.py` and
`pytest student_6_tokens/ -q` (25 tests). Every number is produced by running
the real compiled LangGraph — no estimates.

---

## Method, and why the comparison is fair

Both runs use **the same history producer**. `with_turn_recording()` wraps every
worker in `build_graph`, so turns are appended by the graph identically in both
cases. The *only* variable is which callable is injected as the context node:

| | Context node | History producer |
|---|---|---|
| Unguarded | `context_manager_NO_GUARDRAIL` — counts, prunes nothing | identical wrappers |
| Guarded | `context_manager_node` — counts, prunes, summarizes | identical wrappers |

Verified by `test_both_runs_do_identical_work`, which asserts both runs invoke
the workers the same number of times (`analyzer` ×6, `actor` ×5, `validator` ×5).
If the two sides built their histories differently, the numbers below would be
comparing two workloads rather than measuring one guardrail.

### The round count is honest

The obvious way to force a long run is an always-rejecting Validator. **It does
not work**, and the reason is a guardrail rather than a bug: Person 1's
Coordinator escalates straight to `partial_output` when the same rejection
reason repeats (ARCHITECTURE_DESIGN.md §5.3), so a repetitive Validator ends the
graph after **one** round.

The fix is to make the Validator behave like a real one — a different defect each
pass — so §5.3 correctly does not fire and the run reaches Person 1's
`MAX_ROUNDS` ceiling of 5. **`MAX_ROUNDS` was not raised and no teammate's
guardrail was disabled to produce a longer demo.** Five rounds is the system
working as designed, and measuring inside that bound is the point.

---

## Headline: before / after

| Metric | Without guardrail | With guardrail |
|---|---:|---:|
| **Peak context window** | **2,880 tokens** | **1,122 tokens** (−61.0%) |
| **Cumulative input tokens** | **17,628** | **8,140** (−53.8%) |
| Final history length | 35 turns | 10 turns |
| Windows breaching the 1,200 ceiling | 7 of 12 | **0 of 12** |
| Latency per run | 14.17 ms | **13.41 ms** (−0.76 ms) |
| Rounds executed | 5 | 5 |
| Graph outcome (`final_report`) | identical | identical |

Window size at each of the 12 context-node visits:

```
unguarded  58  220  590  752 1122 1284 1654 1816 2186 2348 2718 2880   ← only grows
guarded    58  220  590  752 1122 1021  515  677 1047  946  515  677   ← sawtooth
```

The unguarded series is monotonically increasing — history is never compacted,
so it can only grow. The guarded series saws: it climbs, hits the ceiling,
compresses, and climbs again. **No guarded window ever exceeds 1,200.**

### Why cumulative burn is the real number

Peak window is what breaks the model. **Cumulative burn is what costs money**,
and it is the sum of the window at every turn — because turn N re-sends turns
1..N all over again. History grows linearly; spend grows quadratically. Reporting
only the final history length (35 → 10 turns) would describe the symptom and
miss the cost.

### The guardrail is not a latency tax

It is a small latency *saving*: −0.76 ms per run. Compression costs CPU, but a
smaller window means less state to copy and serialize on every subsequent hop,
and that dominates. On a live model path the effect is far larger, since input
tokens drive prefill time directly.

---

## Cost projection

The team's model is local Ollama, which is free, so a dollar figure has to be
constructed honestly rather than observed. **These are measured token counts
priced at a published hosted-API input rate of $2.50 per 1M tokens** — what this
same workload would cost on a commercial API, not a bill anyone received.

| | Per review | Per 1,000 reviews | Per 100,000 reviews |
|---|---:|---:|---:|
| Without guardrail | $0.0441 | $44.07 | $4,407 |
| With guardrail | $0.0204 | $20.35 | $2,035 |
| **Saved** | **$0.0237** | **$23.72** | **$2,372** |

---

## Node-level scaling study *(projection — not an in-graph measurement)*

The graph stops at 5 rounds by design, so the in-graph numbers above are bounded
by that ceiling. To show what the same node does to longer histories, the
compressor is run directly over synthetic histories. **Labelled a projection
everywhere it appears and never blended with the measured numbers.**

| History turns | Unguarded window | Guarded window | Reduction |
|---:|---:|---:|---:|
| 6 | 446 | 446 | 0.0% *(below threshold — no-op)* |
| 12 | 834 | 834 | 0.0% *(below threshold — no-op)* |
| 24 | 1,610 | 381 | 76.3% |
| 48 | 3,162 | 381 | 88.0% |
| 96 | 6,266 | 381 | **93.9%** |

The guarded column **flatlines at 381 tokens** regardless of how much history
arrives. That constant is the whole design: unguarded growth is O(n), guarded is
O(1). It also shows the node correctly doing *nothing* below the threshold — a
guardrail that fires on every turn is one that gets switched off.

---

## The rolling summary is bounded by construction

There is exactly **one** summary entry and it is **replaced**, never appended.
Appending would make the compressor the thing that blows the window.

Replacement alone is not sufficient, though. If the summary were prose, merging
"old summary + newly evicted turns" would concatenate and the single entry would
still grow without bound. So the summary carries a **fixed-schema aggregate**
(counts, worst risk, latest rejection) and merging is *addition*:

```
old aggregate  +  aggregate(newly evicted turns)  →  one updated aggregate
```

Same keys, same size, no matter how many turns have been folded in.

| History turns | Summary size (tokens) |
|---:|---:|
| 12 | 64 |
| 24 | 64 |
| 48 | 64 |
| 96 | 64 |
| 192 | **64** |

Measured, not asserted: the summary is **exactly constant** across a 16× range of
history length. Enforced by `test_summary_size_does_not_grow_with_history_length`,
which fails if the spread exceeds 12 tokens — so the property is held in place
even if someone later adds a field to the aggregate.

---

## The pruning ladder — cheapest loss first, recount after every stage

| Stage | Action | Rationale |
|---|---|---|
| 1 | Digest tool outputs outside the recency window | Bulkiest and least reusable — the assignment names them specifically |
| 2 | Fold older turns into the single rolling summary | Retains the facts, drops the prose |
| 3 | Shrink the recency window one turn at a time | Recency is the last thing worth losing |
| 4 | Digest the single retained turn | Structure survives, detail does not |
| 5 | Truncate the summary itself | Last resort |

**The node recounts after every stage and stops at the first one that reaches the
target** — never pruning more than necessary, since lost context is a real cost.
Verified by `test_ladder_stops_at_the_first_stage_that_reaches_the_target`.

### When compression cannot reach the target

Pinned system turns plus one retained turn can still exceed a small ceiling. The
ladder then hits a **deterministic floor**: it logs, emits `floor_reached`, and
returns. It does not loop and does not empty the window. An over-budget window is
a cost problem; an empty one is a correctness problem, and this layer must not
trade the second for the first. Verified by
`test_fallback_ladder_terminates_when_the_target_is_unreachable`.

---

## What `token_count` means

Stated precisely, because an ambiguous metric is an unusable one:

> **`token_count` = the number of tokens in `state.messages` *after* the context
> node runs — exactly what the next model call pays to read the history.**

It is a **window size**, not a running total, so it can and does go *down* after
compression. Cumulative burn is a property of the run rather than of the state,
so the harness sums it; the contract is frozen and this layer adds no fields.
Both properties are asserted in `test_token_count_means_the_current_window_not_a_running_total`.

---

## Tokenizer calibration — closing `DESIGN_DOCS.md` risk #14

Risk #14 was open and assigned to Person 5: *"Token counter under/over-counts due
to tokenizer mismatch with the actual LLM."* It is now closed with a measurement.
`calibrate_tokens.py` asks llama3.2 itself, via Ollama's `prompt_eval_count`.

**Method note:** `prompt_eval_count` includes the model's chat template — BOS
marker and role headers — a fixed cost independent of the text. The first run
appeared to show an 81% estimator error on a 22-character string; that was
scaffolding, not error. Measuring the baseline once and subtracting it makes the
comparison content-only.

| Sample | Heuristic | Actual | Error |
|---|---:|---:|---:|
| Short routing turn | 6 | 7 | −14.3% |
| Clause quote | 34 | 25 | +36.0% |
| Full contract | 163 | 134 | +21.6% |
| History ×6 | 305 | 233 | +30.9% |
| History ×24 | 1,107 | 845 | +31.0% |
| History ×96 | 4,320 | 3,293 | +31.2% |

**Two findings, both applied to the code:**

1. **Per-message overhead is 25 tokens, not the 4 the estimator assumed** — more
   than six times higher. In a window made of many short turns that fixed cost
   dominates the content entirely, and guessing it low is exactly how a token
   budget silently overruns. `PER_MESSAGE_OVERHEAD_TOKENS` is now set from the
   measurement.
2. **llama3.2 averages ~5.25 characters per token**, not 4. `chars_per_token`
   updated accordingly.

Residual error after calibration drifts **high** on repetitive text (~+31%),
where BPE merges repeated phrases the estimator counts as fresh. **That bias is
conservative:** it compresses slightly earlier than strictly necessary, which is
the safe direction for a cost guardrail. The remaining exposure is the opposite
case — many very short turns, where the estimate runs ~14% low.

`calibrate_tokens.py` is live and opt-in, never imported by the test suite —
the same pattern Person 2 established with `benchmark_live.py`. `pytest` stays
fully offline.

---

## Core state preservation

The assignment requires "preserving the system's core state values." The node
touches **only** `messages` and `token_count`;
`test_context_node_touches_only_messages_and_token_count` asserts every other
field on `AgentState` is byte-identical after the node runs, and
`test_compression_does_not_change_the_outcome` asserts the guarded and unguarded
runs produce the same `final_report`, `round_number`, `rejection_reason_history`,
`analysis_payload` and `validation_notes`.

### Contract-freeze compliance

**No new state fields.** That answers the open question in
`CONTRACT_FREEZE_NOTES.md` §7 for the context half of Person 5's scope: the
rolling summary lives inside `messages` as a pinned system entry rather than in a
separate `history_summary` field, so the freeze holds.

`AgentState.messages` was typed loosely (`List[Dict[str, Any]]`), which means the
pruning policy had nothing to reason about — "prune intermediate tool outputs" is
only implementable if a turn can declare that it *is* a tool output. This layer
therefore imposes its own turn schema (`v`, `turn`, `node`, `role`, `kind`,
`content`) and validates every appended entry. A malformed turn raises rather
than sitting un-prunable in the window forever.

---

## Composition with the tracing layer

The rolling summary is **new text manufactured out of PII-bearing state** — it
did not exist when the redaction rules were written, so it is a genuinely new
carrier. `test_the_rolling_summary_is_still_redacted_before_telemetry` runs the
graph with both guardrails active and confirms a summarized history reaches the
telemetry sink with zero party-name leaks.

---

## Safety mandate

| Requirement | Test |
|---|---|
| No network traffic | `test_no_network_traffic_during_compression` — `socket.connect` raises; the run still completes |
| No file modifications | `test_module_touches_no_filesystem_or_shell` |

`calibrate_tokens.py` is the one component that talks to anything, and it talks
only to a local Ollama server, writes nothing, performs no destructive action,
and is never imported by tests.

---

## Test summary

```
pytest student_6_tokens/ -q   ->  25 passed
```
