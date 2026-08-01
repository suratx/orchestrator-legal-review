# Metrics — Student 2 / Worker A (Analyzer)

**Failure mode:** Silent Hallucination & Structural Failure
**Guardrail:** `.with_structured_output(ContractAnalysis)` + source-grounding
invariants + exactly one automated self-correction retry
**Owner:** Person 2 · **Files:** `snippet.py`, `test_failure.py`, `benchmark_live.py`

---

## 1. What "silent" means in this domain

The contract under review (`fixtures.py:SAMPLE_CONTRACT`) contains an
**uncapped indemnity** in Section 4.2 and **no limitation-of-liability cap at
all**. The characteristic failure is not a crash — it is an analysis like this:

```json
{ "clause_id": "Section 12.4",
  "clause_type": "limitation_of_liability",
  "verbatim_quote": "Supplier's aggregate liability shall in no event exceed
                     the fees paid in the preceding twelve (12) months.",
  "risk_level": "low",
  "risk_rationale": "Liability is capped at twelve months of fees, which is
                     market standard and acceptable to the Client." }
```

Correct types. Valid enum. Well-formed section number. Internally consistent
overall risk. It passes `.with_structured_output()` without a murmur — and it
is fiction. Section 12.4 does not exist, the cap does not exist, and the real
uncapped indemnity has been dropped from the report. Worker B then redlines
against a contract that isn't the one on the table.

---

## 2. Deterministic benchmark — `python student_2_silent/test_failure.py`

Corpus: 12 Analyzer outputs, 8 defective (4 hallucinated clauses, 2 missing
`clause_id`, 1 understated overall risk, 1 wrong counterparty).

| Metric | Before (no guardrail) | After (guardrail active) |
|---|---|---|
| Defective analyses passed to Worker B | **8 / 12 (67%)** | **0 / 12 (0%)** |
| Errors raised on a defective analysis | 0 | 8 |
| Safe rejections routed to Coordinator | 0 | 8 |
| Defects repaired on the one retry *(cooperative model)* | 0 | 8 |
| LLM calls per 12 reviews | 12 | 20 (+67%) |
| Clean analyses wrongly rejected | 0 | **0** |

The last row matters as much as the first: the guardrail rejects every
defective analysis and **zero** correct ones.

Two "after" scenarios are measured because retry behaviour depends on the
model, and inventing a recovery rate would be dishonest:

- **Stubborn model** (repeats its bad answer): 8 defects → 8 safe rejections,
  0 escape. The node never guesses; it hands control back to the Coordinator
  with `rejection_flag=True`.
- **Cooperative model** (fixes itself once told which field it invented):
  8 defects → 8 repaired inside one retry, 0 escape, 0 rejections.

Either way the number that matters — silent defects reaching Worker B — is 0.

---

## 3. Live benchmark — `python student_2_silent/benchmark_live.py --runs 12`

Real `llama3.2` via Ollama, temperature 0.7, 12 runs. (Production runs at
temperature 0.0; 0.7 is used here to sample the model's error distribution
rather than count one deterministic behaviour twelve times.)

| Metric | Structure-only Analyzer | Full guardrail |
|---|---|---|
| Parsed and accepted | 10 / 12 | — |
| **Silent defects reaching Worker B** | **1 / 12 (8.3%)** | **0 / 12 (0%)** |
| Loud structural failures (visible, safe) | 2 / 12 | 2 / 12 |
| Median latency per LLM call | 19.09 s | — |
| Median latency per node invocation | — | 20.82 s |

**The real silent failure, run 6:** the model reported clauses `Section 7.1`
and `Section 9.1`. The contract has `Section 7` and `Section 9` — no
sub-sections exist. Every field was well-formed and every enum was valid, so
`.with_structured_output()` accepted it. The grounding invariant caught it:

```
UNGROUNDED OUTPUT (hallucination detected): clause_id 'Section 7.1' refers to
'Section 7.1', which does not exist in the source contract.
```

That is the entire thesis of this guardrail in one line: **structural
validation cannot detect a hallucination, because a hallucination is
structurally valid by construction.**

---

## 4. Cost of the guardrail

The model is local, so there is no per-token API bill — the cost is latency
and wasted graph work. Measured:

| | Caught at the Analyzer | Escaped to Worker B |
|---|---|---|
| Extra LLM calls | 1 retry (~19 s) | 0 |
| Downstream node executions wasted | 0 | 3 (Actor → Validator → Reporter) |
| Coordinator rounds consumed | 0 | 1–5 before the Validator catches it, if it catches it at all |
| Output | flagged for manual review | a confident, wrong legal opinion |

Catching a defect here costs **one** retry call. Letting it through costs
three downstream node executions, up to five Coordinator rounds, and produces
a report a lawyer might act on. On the deterministic corpus the guardrail adds
+67% LLM calls; on the live 12-run sample it added **0** retries, because the
model's first answer was already grounded in every accepted case.

---

## 5. False positives — a guardrail nobody trusts gets switched off

The first live run rejected 4 / 10 analyses. On inspection at least two were
**not** hallucinations: the model had reproduced the contract text faithfully
but substituted a typographic apostrophe (`’`) for the ASCII `'`, and in one
case emitted a stray JSON escape (`\'`). The grounding check was crying wolf.

Fix: `contract.normalize_text()` now folds Unicode punctuation variants
(smart quotes, en/em dashes, non-breaking spaces, stray escapes) to ASCII
before the substring comparison. Inventing a clause changes *words*;
re-punctuating one does not.

| | Before normalization | After |
|---|---|---|
| Grounding rejections per live batch | 4 / 10 | 0 / 12 |
| Of which attributable to typography | ≥ 2 | 0 |
| Genuine defects still caught | yes | yes (run 6 above) |

A second over-strictness was fixed the same way: `clause_id` originally had to
match `Section 4.2` exactly, so the perfectly reasonable
`"Section 4.2 Indemnification"` was rejected. `CLAUSE_ID_PATTERN` now accepts
an optional trailing heading and `clause_reference()` grounds on the numeric
reference only.

---

## 6. Known limitations (honest scope)

1. **Grounding is substring-based.** A quote that appears in the contract but
   is attributed to the wrong clause still passes. Catching that needs
   span-offset matching, which is out of scope for v1.
2. **`Section 1` grounds against `Section 11`** for the same reason — prefix
   collision. Low impact here; worth a word-boundary check if the team wants it.
3. **Role confusion is not caught.** An early live run returned the *Client*
   (`Northwind Analytics LLC`) as the `counterparty`. Both names are in the
   document, so the grounding check passes. This is mitigated by prompt rule 4
   ("`counterparty` is the party that is NOT our client"), which is prompt
   engineering, not a code guardrail — a real fix needs a party-role
   extraction step. Flagged for Person 4's Validator as a downstream invariant.
4. **Live numbers are a 12-run sample**, not a statistically tight estimate.
   The deterministic corpus is the reproducible number; the live run is
   evidence that the failure mode occurs with a real model.

---

## 7. Reproducing these numbers

```bash
conda activate orchestrator-legal

# Deterministic — no network, always identical
python student_2_silent/test_failure.py
pytest student_2_silent -v            # 12 tests

# Live — needs `ollama serve` and `ollama pull llama3.2`
python student_2_silent/benchmark_live.py --runs 12
```

Raw live results are written to `student_2_silent/live_benchmark_results.json`.
