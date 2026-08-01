# 2-Minute Demo Script — Student 2 / Silent Hallucination

Target: ~2:00. Screen recording, one terminal, no cuts needed.

**Before you hit record**

```bash
conda activate orchestrator-legal
cd orchestrator-legal-review
clear
```

Have three files open in tabs you can switch to: `contract.py`,
`student_2_silent/snippet.py`, `student_2_silent/fixtures.py`.

---

## 0:00–0:20 — The setup

> "I own Worker A, the Analyzer, in a LangGraph contract-review orchestrator.
> It turns raw contract text into structured clauses and risk tags. Everything
> downstream — the redline generator, the validator, the final report — trusts
> what this node produces. My failure mode is silent hallucination."

Show `fixtures.py`, scroll to `SAMPLE_CONTRACT`.

> "This is the contract under review. Note Section 4.2: an uncapped indemnity.
> And note what is *not* here — there is no limitation-of-liability cap
> anywhere in this document."

---

## 0:20–0:50 — The failure, unguarded

Scroll to `HALLUCINATED_ANALYSIS`.

> "Here's what the model produced. Correct types. Valid enum. Well-formed
> section number. Internally consistent overall risk. It passes
> `.with_structured_output()` without an error — and it is fiction. Section
> 12.4 does not exist. That liability cap does not exist. And the real uncapped
> indemnity in Section 4.2 has been dropped from the report entirely."

Run:

```bash
python -m pytest student_2_silent/test_failure.py::test_unguarded_analyzer_accepts_a_hallucination_silently -v
```

> "The unguarded node marks this validated, logs no error, raises nothing, and
> hands it to Worker B. A lawyer reading the report concludes liability is
> capped at twelve months of fees. It's unlimited. That's what 'silent' means —
> nothing failed, and everything is wrong."

---

## 0:50–1:25 — The guardrail

Switch to `contract.py`, show `_quotes_are_grounded`.

> "Structural validation cannot catch this, because a hallucination is
> structurally valid by construction. So the invariants live in the frozen
> contract: every verbatim quote, every clause ID and the counterparty name
> must actually occur in the source text. That's a substring assertion in
> Python — the model can't talk its way past it."

Switch to `snippet.py`, show the `try/except` in `run_analyzer`.

> "Three layers. Schema catches structure. Grounding catches invention. And on
> either failure the validator's exact message goes back to the model for
> exactly one self-correction — it's told *which field it invented*, not just
> 'try again'. If the retry also fails, the node clears the payload, sets
> `rejection_flag`, and hands control to the Coordinator. It never guesses."

Run:

```bash
python -m pytest student_2_silent/test_failure.py -v
```

> "Twelve tests. The same hallucination is now rejected, and a cooperative
> model repairs it inside one retry."

---

## 1:25–2:00 — The numbers

```bash
python student_2_silent/test_failure.py
```

Let the table render, then point at the headline:

> "Twelve analyses, eight defective. Before: eight reach Worker B, zero errors
> raised. After: zero reach Worker B — every one either repaired on the single
> retry or safely rejected. Zero correct analyses wrongly rejected, which
> matters just as much: a guardrail with false positives gets switched off.
> Cost is one extra model call per caught defect."

If you have time (~10s), show the live number:

> "And on real llama3.2 runs it caught the model inventing 'Section 7.1' in a
> contract that only has Section 7 — an 8.3% silent-failure rate that the
> schema alone could not see."

---

**Do not** run `benchmark_live.py` live on camera — it takes ~20 seconds per
call. Screenshot `live_benchmark_results.json` or the saved table instead.
