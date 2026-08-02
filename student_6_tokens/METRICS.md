# Person 5 Metrics — Context Window Explosion & Token Burn

**Guardrail:** Context Management Node at the head of every loop transition
**Reproduce:** `python student_6_tokens/benchmark.py` (all numbers below),
`python student_6_tokens/test_failure.py` (before/after narrative),
`pytest student_6_tokens/ -q` (29 tests).

---

## Read this first: what is measured, and what is projected

**No production node in this repository reads `state.messages`.** Person 2's
Analyzer builds its prompt from `raw_input`; the Coordinator and Validator are
deterministic and call no model at all. A context-node visit is therefore *not*
the same event as an LLM invocation.

That means "tokens at every graph transition" is a **projection**, not observed
spend, and it is labelled as such everywhere below. Two figures are reported
because they answer different questions:

| | What it is | Status |
|---|---|---|
| **(a)** Managed-window estimate summed over all graph transitions | What the window would cost if every transition fed history to a model | **Projection** |
| **(b)** Prompt tokens at a history-consuming agent's invocations | Measured at a stub (`HistoryConsumingAnalyzer`) that genuinely builds its prompt from the window | **The defensible figure** |

## Method, and why the comparison is fair

Both runs use the same history producer: `with_turn_recording()` wraps every
worker in `build_graph`, identically in both cases. The only variable is which
callable is injected as the context node — `context_manager_NO_GUARDRAIL`
(counts, prunes nothing) or `context_manager_node`. `test_both_runs_do_identical_work`
asserts both runs invoke the workers the same number of times.

**The round count is honest.** An always-rejecting Validator does *not* produce
a long run: Person 1's §5.3 rule escalates to `partial_output` when the same
rejection reason repeats, ending the graph after **one** round. The Validator
varies its reason instead — which is also what a real one does. `MAX_ROUNDS` was
not raised and no teammate's guardrail was disabled.

---

## Results

| Metric | Without guardrail | With guardrail |
|---|---:|---:|
| **Peak managed window** | **1,803 tokens** | **1,157** (−35.8%) |
| Windows breaching the 1,200 ceiling | 4 of 12 | **0 of 12** |
| **(b) Prompt tokens at consumer invocations** | **5,319** | **4,193** (−21.2%) |
| (a) Window estimate across all transitions *(projection)* | 11,136 | 8,884 (−20.2%) |
| Graph outcome (`final_report`) | identical | identical |

Window size at each of the 12 context-node visits:

```
unguarded  53 148 384 479 715 810 1046 1141 1377 1472 1708 1803   ← only grows
guarded    53 148 384 479 715 810 1046 1141  897  992 1062 1157   ← sawtooth
```

Prompt size at the six history-consuming agent invocations:

```
unguarded  59 390 721 1052 1383 1714
guarded    59 390 721 1052  903 1068
```

The unguarded series only grows — history is never compacted. The guarded series
climbs, hits the ceiling, compresses, climbs again.

### Latency: no claim

Both runs land at a median of **~14 ms**, with an interquartile range of roughly
0.4–0.7 ms over 60 timed runs after 5 warm-ups.

**No latency effect is claimed.** The guarded-minus-unguarded delta measured
**+0.07, +0.14, +0.18 and +0.27 ms** on four independent executions — an order
of magnitude smaller than the spread within a single run, and not stable between
runs. That instability *is* the finding: if the effect were real the delta would
reproduce, and it does not. Re-run `benchmark.py` and you will get a different
number in the same range.

Two earlier figures have been withdrawn for the same reason. A first draft
reported a "−0.76 ms saving" from untimed runs; a later one published "+0.07 ms"
as though it were a stable measurement. Neither survived repetition.

### Cost: projected, not observed

The team's model is local Ollama, which is free. Applying a hosted-API input
rate of **$R per 1M tokens** to figure (b):

| | Per 1,000 reviews | At $2.50/1M (illustrative) |
|---|---:|---:|
| Without guardrail | 5.32M tokens | $13.30 |
| With guardrail | 4.19M tokens | $10.48 |
| **Saved** | **1.13M tokens** | **$2.82** |

**Substitute your provider's current published rate** — the dollar column is an
illustration applied to measured token counts, not a bill anyone received and
not a quotation of any vendor's live pricing. The token column is the real
result.

---

## Node-level scaling *(projection)*

The graph stops at 5 rounds by design, so the figures above are bounded by that
ceiling. Running the compressor directly over synthetic histories shows what it
does to longer ones:

| History turns | Unguarded | Guarded | Reduction |
|---:|---:|---:|---:|
| 6 | 251 | 251 | 0.0% *(below threshold — no-op)* |
| 12 | 450 | 450 | 0.0% *(no-op)* |
| 24 | 852 | 852 | 0.0% *(no-op)* |
| 48 | 1,656 | 219 | 86.8% |
| 96 | 3,264 | 219 | **93.3%** |

The guarded column **flatlines at 219 tokens** regardless of input size:
unguarded growth is O(n), guarded is O(1). It also shows the node correctly
doing nothing below the threshold.

---

## The rolling summary is bounded by construction

Exactly **one** summary entry, **replaced** rather than appended — otherwise the
compressor becomes the leak. Replacement alone is insufficient: prose would
still concatenate on each merge. The summary carries a **fixed-schema aggregate**
and merging is addition.

| History turns | 12 | 24 | 48 | 96 | 192 |
|---|---:|---:|---:|---:|---:|
| Summary size (tokens) | 32 | 32 | 32 | 32 | **32** |

Exactly constant across a 16× range. Enforced by
`test_summary_size_does_not_grow_with_history_length`.

**What the summary does not keep:** individual clause identifiers and the
reasoning behind each earlier decision. That is acceptable here because the
graph carries `analysis_payload`, `execution_state` and
`rejection_reason_history` as structured state alongside the window — the
summary is not the system's memory, only the conversational part of it. Note the
limit of the evidence: `test_compression_does_not_change_the_outcome` proves the
*deterministic* graph reaches the same result, and cannot prove an LLM would
reason equally well from the compressed window, because no production node
consumes it yet.

---

## The pruning ladder

| Stage | Action |
|---|---|
| 1 | Digest tool outputs outside the recency window |
| 2 | Fold older turns into the single rolling summary |
| 3 | Shrink the recency window one turn at a time |
| 4 | Digest the single retained turn |
| 5 | Truncate the summary itself |

**Recounts after every stage and stops at the first that fits** — never pruning
more than necessary, since lost context is a real cost.

### The ceiling is best-effort, not guaranteed

Stated precisely: **the guardrail holds tested workloads under the target, and
applies best-effort bounded compression when the minimum safe window itself
exceeds it.** Pinned instructions plus one retained turn can exceed a small
budget; the ladder then stops at a deterministic floor rather than looping or
emptying the window. An over-budget window is a cost problem; an empty one is a
correctness problem.

When that happens the node now **writes a pinned `over_budget` marker into
`messages`**, so the condition is visible in state and in telemetry rather than
only in a log line. It deliberately does **not** reroute the graph: deciding
that an over-budget window warrants partial output is a policy call for the
Coordinator's owner, not this node's to take unilaterally. Flagged as an open
item for the team.

---

## What `token_count` means

> **`token_count` = the estimated token size of `state.messages` after this node
> runs — the size of the managed window.**

It is a window size, not a running total, so it goes *down* after compression.
It is **not** "what the next model call pays" in this system, for the reason at
the top of this document.

---

## Tokenizer calibration — `DESIGN_DOCS.md` risk #14

`calibrate_tokens.py` fits the estimator's constants against llama3.2 through
Ollama's `/api/chat`, then reports the delta against whatever is in the code.
Nothing is imported from `snippet.py` before the fit, so the script cannot
measure its own assumption.

| Constant | Fitted | Method |
|---|---:|---|
| Conversation overhead | 24 tokens (once) | intercept of tokens vs. message count |
| Per-message overhead | 2.00 tokens | **slope** of tokens vs. message count |
| Characters per token | 5.77 | slope of tokens vs. content length |

### Correction: the earlier calibration was wrong

An earlier version of this file reported per-message overhead as **25 tokens**.
That figure came from posting a single joined string to `/api/generate`,
measuring the template cost once, and then applying it to every message.
`/api/generate` takes one prompt string, so the template is applied **once** — a
cost measured once cannot be multiplied by message count.

The corrected experiment varies the message count in a real array via
`/api/chat` and takes the **slope**: **2 tokens per message**, with the
remaining ~24 being a one-off conversation prefix. The old method overpriced a
35-turn window by roughly 800 tokens, which is why the headline reduction in the
earlier draft (53.8%) was inflated; the corrected figure is 21.2%.

The earlier claim that "both numbers are measured, not guessed" was also wrong
for the chars-per-token coefficient: the old script imported a counter with 5.25
already hardcoded and could only compare against it. It is now fitted (5.77).

---

## Core state preservation and contract compliance

The node touches **only** `messages` and `token_count`.
`test_context_node_touches_only_messages_and_token_count` iterates every other
field on `AgentState` and asserts byte-identity.

**No new state fields** — the rolling summary lives inside `messages` as a
pinned system entry, answering the context half of `CONTRACT_FREEZE_NOTES.md` §7.

`AgentState.messages` is typed `List[Dict[str, Any]]`, so nothing constrained a
turn's shape — and "prune intermediate tool outputs" is unimplementable unless a
turn can declare that it *is* one. This layer imposes and validates its own turn
schema inside that declared type. Anything writing to `messages` should use
`make_turn()` so its entries stay prunable.

---

## Bugs found in review and fixed

| Bug | Consequence | Regression test |
|---|---|---|
| Turn ids assigned as `len(messages)` | After compression a 4-entry window holding turns 0/47/48/49 restarted numbering at 4 — duplicate, non-chronological ids | `test_turn_ids_stay_unique_and_increasing_across_compression` |
| `body[:-recency_turns]` with `recency_turns=0` | `body[:-0]` is `body[:0]`, i.e. empty — the head/tail split silently inverted and skipped stages 1 and 2 | `test_recency_turns_zero_keeps_nothing_verbatim` |
| `floor_reached` logged only | Nothing reading state could tell the window was over budget | `test_over_budget_condition_is_visible_in_state` |
| Hardcoded token thresholds in tests | Recalibrating the constants silently invalidated two tests | limits now derived as fractions of measured size |

---

## Composition with the tracing layer

The rolling summary is new text manufactured from PII-bearing state — a carrier
that did not exist when the redaction rules were written.
`test_the_rolling_summary_is_still_redacted_before_telemetry` runs the graph
with both guardrails active and an explicit tight budget (so the summarization
stage definitely fires) and confirms zero party-name leaks.

---

## Safety mandate

| Requirement | Test |
|---|---|
| No network traffic | `test_no_network_traffic_during_compression` — `socket.connect` raises; the run still completes |
| No file modifications | `test_module_touches_no_filesystem_or_shell` |

`calibrate_tokens.py` is the only component that talks to anything; it reaches a
local Ollama server, writes nothing, and is never imported by tests.

```
pytest student_6_tokens/ -q   ->  29 passed
```
