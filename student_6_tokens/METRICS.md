# Student 6 Metrics — Context-Window Explosion and Token Burn

## Evaluation overview

**Guardrail:** Context Management Node at the beginning of each loop transition

**Reproduction commands:**

```bash
python student_6_tokens/benchmark.py
python student_6_tokens/test_failure.py
pytest student_6_tokens/ -q
```

The Context Management Node limits the growth of `state.messages` as the graph moves through repeated rounds of analysis, action, validation, and routing. It estimates the current context size and applies progressive compression when the configured 1,200-token threshold is exceeded.

The evaluation compares two executions of the same integrated graph:

- The unguarded configuration records and counts history without compressing it.
- The guarded configuration uses the same history producer but activates context compression.

Both executions use the same input, worker implementations, routing behavior, and five-round limit. The only difference is whether context compression is active. Tests confirm that both configurations invoke the workers the same number of times and produce identical operational results.

## Measurement scope

No production node in the current repository sends `state.messages` to a language model. The Analyzer builds its prompt from `raw_input`, while the Coordinator and Validator are deterministic. Therefore, context size at every graph transition represents a managed-window measurement rather than actual billed model usage.

Two token measurements are reported:

| Measurement | Meaning | Status |
|---|---|---|
| Managed-window estimate across graph transitions | Estimated context size at every Context Management Node visit | Projection |
| Prompt estimate at history-consuming agent invocations | Estimated prompt size when a deterministic consumer builds its prompt from `state.messages` | Consumer-level estimate |

`HistoryConsumingAnalyzer` is used to measure the second value. It receives the managed history at each Analyzer invocation and records the estimated size of the prompt it would submit. It remains deterministic and offline and does not call an external model.

## Before-and-after results

| Metric | Without guardrail | With guardrail |
|---|---:|---:|
| **Peak managed window** | **1,803 tokens** | **1,157 tokens** |
| Peak-window reduction | — | **35.8%** |
| Windows exceeding the 1,200-token threshold | 4 of 12 | **0 of 12** |
| **Estimated prompt tokens at consumer invocations** | **5,319** | **4,193** |
| Estimated prompt-token reduction | — | **21.2%** |
| Projected window total across all transitions | 11,136 | 8,884 |
| Projected transition-level reduction | — | 20.2% |
| Rounds executed | 5 | 5 |
| Final graph output | Baseline | Identical to baseline |

The guardrail kept every tested managed window below the 1,200-token target and preserved the graph’s final output.

## Window growth across graph transitions

Estimated window size at each of the 12 Context Management Node visits:

```text
Without guardrail:
53, 148, 384, 479, 715, 810, 1,046, 1,141, 1,377, 1,472, 1,708, 1,803

With guardrail:
53, 148, 384, 479, 715, 810, 1,046, 1,141, 897, 992, 1,062, 1,157
```

Without compression, the context grows monotonically and does not recover after crossing the threshold. With the guardrail active, the context is compressed when necessary and remains below the target.

## Prompt estimates at consumer invocations

Estimated prompt size at the six invocations of the history-consuming Analyzer:

```text
Without guardrail:
59, 390, 721, 1,052, 1,383, 1,714

With guardrail:
59, 390, 721, 1,052, 903, 1,068
```

The guarded and unguarded values remain identical while the history is below the threshold. Once compression becomes necessary, the guarded prompt estimate decreases while the unguarded estimate continues to grow.

The total estimated prompt size decreases from 5,319 to 4,193 tokens, representing a 21.2% reduction for the tested five-round workload.

## History production

The original graph state includes `messages`, but the Worker nodes do not write to it directly. History is produced centrally using `with_turn_recording()`, which wraps each graph component without modifying the individual files owned by other students.

Each recorded turn follows a validated structure:

```python
{
    "v": 1,
    "turn": 7,
    "node": "actor",
    "role": "tool",
    "kind": "tool_output",
    "content": "TOOL propose_redline -> ..."
}
```

The required fields are:

- `v`
- `turn`
- `node`
- `role`
- `kind`
- `content`

The `kind` field identifies whether an entry represents analysis, routing, validation, a tool output, a report, a system instruction, or a rolling summary. This allows the context manager to apply different retention priorities to different message types.

Malformed history entries are rejected rather than retained in an unmanageable form.

## Context Management Node

The Context Management Node runs before the Coordinator enters or re-enters a routing cycle. It reads `messages`, estimates the current token count, and updates only:

- `messages`
- `token_count`

When the context remains below 1,200 tokens, the node performs no compression. When the threshold is exceeded, it applies a five-stage pruning process.

## Pruning process

| Stage | Action | Purpose |
|---|---|---|
| 1 | Shorten bulky tool outputs outside the recent-message window | Removes the largest and least reusable intermediate content first |
| 2 | Fold older turns into one rolling summary | Preserves important aggregate information while removing detailed prose |
| 3 | Reduce the recent-message window one turn at a time | Retains recent context for as long as possible |
| 4 | Shorten the final retained turn | Provides an additional deterministic fallback |
| 5 | Truncate the summary | Applies a final bounded reduction when necessary |

The node recalculates the estimated token count after each stage and stops as soon as the context fits within the threshold. This prevents unnecessary removal of information.

## Rolling-summary behavior

The guardrail maintains only one rolling summary. Each compression cycle updates and replaces the existing summary rather than adding another summary entry.

The summary stores a fixed set of structured values:

```python
{
    "turns_compressed": 26,
    "node_counts": {
        "actor": 8,
        "analyzer": 5
    },
    "clauses_analyzed": 2,
    "max_risk": "critical",
    "redlines_proposed": 8,
    "rejections": 5,
    "latest_rejection": "..."
}
```

Newly compressed history is merged through arithmetic updates to these fields. This prevents the summary from growing through repeated prose concatenation.

### Summary-size evaluation

| History length | Summary size |
|---:|---:|
| 12 turns | 32 tokens |
| 24 turns | 32 tokens |
| 48 turns | 32 tokens |
| 96 turns | 32 tokens |
| 192 turns | 32 tokens |

The measured summary size remained constant across a sixteenfold increase in history length.

The summary retains aggregate conversational information, while detailed operational information remains available in structured graph fields such as:

- `analysis_payload`
- `execution_state`
- `rejection_reason_history`
- `validation_notes`

## Node-level scaling projection

The integrated graph stops after five rounds, so a separate deterministic scaling evaluation applies the same compression function to longer synthetic histories.

| History length | Without guardrail | With guardrail | Reduction |
|---:|---:|---:|---:|
| 6 turns | 251 tokens | 251 tokens | 0.0% |
| 12 turns | 450 tokens | 450 tokens | 0.0% |
| 24 turns | 852 tokens | 852 tokens | 0.0% |
| 48 turns | 1,656 tokens | 219 tokens | 86.8% |
| 96 turns | 3,264 tokens | 219 tokens | 93.3% |

The guardrail performs no compression while the history remains below the threshold. Once compression is activated, the managed size remains bounded as additional history is introduced.

These scaling results are node-level projections and are reported separately from the measurements obtained through the integrated five-round graph.

## Token-counter calibration

The offline token estimator uses constants calibrated against Llama 3.2 through Ollama’s `/api/chat` endpoint.

The estimator follows:

```text
estimated tokens =
    conversation overhead
    + per-message overhead × number of messages
    + content characters ÷ characters per token
```

| Constant | Calibrated value |
|---|---:|
| Conversation overhead | 24 tokens per conversation |
| Per-message overhead | 2 tokens per message |
| Average characters per token | 5.77 |

The calibration uses real message arrays rather than a single concatenated prompt. Message count and content length are varied separately to estimate the corresponding components.

The production guardrail uses this dependency-free estimator so that tests remain deterministic and offline. The reported token values are calibrated estimates rather than vendor billing records.

## Projected token cost

The local Ollama model does not create a hosted API charge. However, the estimated prompt-token reduction can be translated into a provider-specific cost projection.

For 1,000 reviews:

| Metric | Without guardrail | With guardrail | Reduction |
|---|---:|---:|---:|
| Estimated prompt tokens | 5.319 million | 4.193 million | 1.126 million |

If a hosted provider charges \(R\) dollars per one million input tokens, the projected saving for 1,000 equivalent reviews is:

```text
Projected saving = 1.126 × R dollars
```

This is a cost projection based on estimated input tokens and is not an observed charge.

## Latency evaluation

The benchmark uses five warm-up runs followed by 60 timed runs for each configuration. Both guarded and unguarded executions have a median runtime of approximately 14 ms, with overlapping interquartile ranges.

No meaningful latency difference is claimed because the measured variation between repeated benchmark executions is greater than the guarded-versus-unguarded difference. The guardrail’s demonstrated benefit is reduced context size rather than execution-time improvement in the offline graph.

## Threshold floor

The 1,200-token value is a management target. If pinned system instructions, one retained turn, and the minimum summary still exceed a configured threshold, the node stops at a deterministic floor rather than looping indefinitely or deleting all essential context.

When the minimum safe context remains over budget, the node adds a pinned `over_budget` marker to `messages`. This makes the condition visible in graph state and telemetry.

The guardrail therefore guarantees deterministic termination and bounded compression behavior. The tested integrated workload remained below the threshold after compression.

## Core-state preservation

The Context Management Node modifies only:

```text
messages
token_count
```

Every other field in `AgentState` remains unchanged. Tests compare each field before and after context management and verify that the following operational values are preserved:

- `round_number`
- `next_route`
- `is_validated`
- `rejection_flag`
- `rejection_reason_history`
- `analysis_payload`
- `execution_state`
- `validation_notes`
- `error_log`
- `final_report`

The guarded and unguarded graph executions produce identical final reports and routing outcomes.

## Contract compliance

The context guardrail adds no fields to `AgentState` and does not modify `contract.py`.

The rolling summary is stored as a validated system entry within the existing `messages` list. The current estimated context size is stored in the existing `token_count` field.

This preserves the frozen shared-state contract.

## Integration with the privacy guardrail

The rolling summary may contain information derived from confidential graph state. Therefore, the integrated evaluation also passes summarized history through Student 5’s State Redaction Interceptor.

The cross-layer test confirms that:

- Context summarization is activated.
- The summary is included in telemetry.
- Party names are removed from the telemetry copy.
- No planted party-name leaks remain.
- The graph’s operational state remains unchanged.

This verifies that the context and privacy guardrails operate together correctly.

## Limitations

The evaluation has the following limitations:

1. No production node currently sends `state.messages` to an LLM. Prompt-token results are therefore estimated at a deterministic history-consuming test agent.
2. The token counter is a calibrated heuristic and may differ from exact counts produced by other models or tokenizers.
3. The fixed-schema summary does not preserve every clause identifier or every earlier reasoning step.
4. The 1,200-token threshold is a configured management target rather than a universal model limit.
5. If the minimum safe context exceeds the target, the node reports the condition instead of deleting all essential context.
6. Identical graph output demonstrates state preservation in the current deterministic workflow but does not establish that every future LLM would reason identically from compressed history.

## Safety compliance

The guardrail performs no external actions during normal operation or automated testing.

| Safety requirement | Verification |
|---|---|
| No network traffic during compression | Socket connections are blocked during the integrated test |
| No file modifications | The context-management module contains no filesystem- or shell-writing operations |
| No external model call during tests | All test agents and token measurements are deterministic and offline |
| No destructive operations | The guardrail only reads and updates graph state |

`calibrate_tokens.py` is an optional calibration utility that communicates only with a local Ollama server. It is not imported or executed by the automated test suite and does not write files or perform destructive actions.

## Test summary

```text
pytest student_6_tokens/ -q
29 passed
```

The tests cover:

- Unguarded context growth
- Threshold breaches
- Guarded context compression
- Before-and-after prompt estimates
- Equivalent worker invocations
- Identical graph output
- Turn-schema validation
- Unique and increasing turn identifiers
- Tool-output pruning
- Rolling-summary replacement
- Bounded summary size
- Token recounting after compression
- Deterministic floor behavior
- Configurable context budgets
- Core-state preservation
- Frozen-contract compliance
- Integration with the privacy interceptor
- Network and filesystem safety

## Conclusion

Without the guardrail, the managed context grew to 1,803 estimated tokens and exceeded the 1,200-token target during four of twelve graph transitions. With the Context Management Node active, the peak window decreased to 1,157 tokens, all threshold breaches were eliminated, and the estimated prompt size at history-consuming agent invocations decreased by 21.2%.

The guarded and unguarded executions completed the same five-round workload and produced identical final reports. The implementation passed 29 tests, preserved every operational state field, and required no changes to the frozen contract.
