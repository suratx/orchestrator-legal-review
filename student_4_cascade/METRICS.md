# Person 4 Metrics — Downstream Cascade Failure

## Deterministic Fixture Suite

Four malformed Actor payloads were run through:

1. `run_validator_NO_GUARDRAIL` — naive downstream consumer (no assertions)
2. `run_validator` — Person 4 sanitization node (programmatic invariants)

| Defect | Without Guardrail | With Guardrail |
|---|---|---|
| `string_count` (`executed_count="1"`) | TypeError crash | Clean `rejection_flag` |
| `orphan_clause` (Section 99 Fabricated) | RuntimeError crash | Clean `rejection_flag` |
| `count_mismatch` (count=3, results=1) | IndexError crash | Clean `rejection_flag` |
| `zero_divide` (count=0 with critical clause) | ZeroDivisionError crash | Clean `rejection_flag` |

## Before / After

| Metric | Without Guardrail | With Guardrail |
|---|---:|---:|
| Downstream crashes per 4 malformed payloads | 4 | 0 |
| Crash rate | 100% | 0% |
| Clean rejection + rollback signals | 0 | 4 |
| Poisoned `execution_state` reaching Reporter | 4 | 0 |
| Healthy Actor payload approval rate | n/a | 100% (1/1) |

## Integration (compiled LangGraph)

| Scenario | Result |
|---|---|
| Healthy Actor → Validator → Reporter | Full report; `redlines_proposed=1` |
| First-pass poison → reject → retry healthy | Completes after `round_number >= 1`; no crash |
| Identical rejection reason twice | Escalates to `PARTIAL -- MANUAL REVIEW REQUIRED` before `MAX_ROUNDS` |

## Result

The sanitization node converted a 100% downstream crash rate on malformed Actor output into a 100% clean rejection rate, and forced Coordinator rollback via `rejection_flag` + `rejection_reason_history` without deadlocking the graph.
