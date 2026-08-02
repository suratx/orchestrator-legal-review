# Person 5 — 2-Minute Demo Video Script

The assignment requires a screen recording showing the failure mode running,
then the guardrail trapping it. I cannot record it for you — this is the shot
list and the exact commands, sequenced so the terminal output tells the story
without editing.

**Setup before recording:** `source venv/bin/activate`, terminal at the repo
root, font large enough to read at 720p, and clear the scrollback.

---

## Shot 1 — the stakes (0:00–0:15)

Open `student_5_trace/fixtures.py`, scroll to `PLANTED_SECRETS`.

> "This is a legal contract review orchestrator. The contract it reviews carries
> a signatory's SSN, an escrow IBAN, the counter-party's tax ID, the deal value,
> and the production database DSN. Thirteen secrets, all synthetic. Now watch
> where they end up."

## Shot 2 — the failure (0:15–0:50)

```bash
python student_5_trace/test_failure.py
```

Let it print. Pause on section 1 and point at the numbers.

> "That's the real graph with tracing configured the way an unconfigured
> LangSmith integration configures it — ship everything. Thirteen of thirteen
> secrets exposed, 511 times across 22 trace events. The SSN is right there. So
> is the production connection string, password included. This is a cloud
> dashboard with none of the access controls the document repository has."

## Shot 3 — the guardrail (0:50–1:20)

Point at section 2 of the same output.

> "Same graph, same contract, with the State Redaction Interceptor on the
> telemetry boundary. Zero of thirteen. Zero occurrences. And look what it's
> replacing them with — the contract body becomes a keyed HMAC fingerprint, and
> every rule that fired is labelled. Twenty-two events out of twenty-two are
> still there. Round number, routing decisions, clause types, risk levels: an
> on-call engineer can still debug this. That's the point — a privacy layer that
> destroys observability gets switched off."

## Shot 4 — the part that isn't obvious (1:20–1:45)

```bash
pytest student_5_trace/test_integration.py -v -k "route or error_channel"
```

> "Two things nearly defeated this. LangSmith's error hook consults only the
> `anonymizer` callback, so the intuitive `hide_inputs`/`hide_outputs` config
> ships every traceback in the clear — and tracebacks are where connection
> strings hide. Worse, with a tracing env var set, LangChain auto-attaches a
> second tracer that resolves an unredacted global client. My local sink would
> have looked perfectly clean while the real state uploaded in parallel. So the
> layer seeds that client and audits every route — if one can't be proven to
> redact, it refuses to run."

## Shot 5 — full system + honesty (1:45–2:00)

```bash
python main_system.py
pytest student_5_trace/ -q
```

> "Wired into the integrated graph, 32 tests green, zero changes to the frozen
> contract. And it's not perfect — there's one documented false positive, a part
> number identical in shape to a tax ID, and obfuscated formats still evade the
> patterns. Both are measured and published in METRICS.md rather than tuned
> away, because a guardrail you've measured is worth more than one you trust."

---

## If a command must be re-run mid-take

Every script is deterministic except the HMAC fingerprint, whose key is
regenerated per process — the digest will differ between takes. Set
`TRACE_FINGERPRINT_KEY=demo-key-do-not-ship` to pin it for a clean re-record.

## Do not show

Nothing here touches the network or a real LangSmith project, so there is no
credential on screen at any point. Every "secret" in the output is synthetic:
the AWS key is Amazon's published documentation placeholder, the IBAN is the ISO
example value, and the phone numbers are in the reserved 555-01xx test range.
