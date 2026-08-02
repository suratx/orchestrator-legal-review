# Person 5 — Interview Story Draft (Context / Token Management)

**Role:** Global Graph Layer — Context & Token Management
**System:** Multi-agent legal contract review orchestrator (LangGraph, Python)

~150 words. Copied into the root `INTERVIEW_STORIES.md`.

---

I owned the context layer of a five-node LangGraph contract-review orchestrator.
Every loop appended turns to shared state and every turn re-sent the whole
history, so cost grew quadratically while the history grew linearly: an
adversarial five-round review burned 17,628 input tokens with the window peaking
at 2,880 — well past our 1,200 ceiling.

I built a Context Management Node at the head of each loop transition: a five-stage
ladder that digests bulky tool outputs, folds older turns into a single rolling
summary, then shrinks the recency window, recounting after every stage and
stopping at the first that fits.

The subtle part was the summary itself. Appending one per compression would make
the compressor the leak, so there is exactly one and it is replaced — and it
stores a fixed-schema aggregate, not prose, so merging is addition. It measures
64 tokens whether it has absorbed 12 turns or 192.

Cumulative burn fell 53.8%, peak window 61%, graph output unchanged.

---

**Word count:** ~170. Trim the ladder sentence to hit 150 if the brief is strict.

## Numbers to have ready if they probe

- Peak window 2,880 → 1,122 tokens; cumulative burn 17,628 → 8,140 (−53.8%)
- Rolling summary constant at **64 tokens** across a 16× range of history length
- Scaling projection: 93.9% reduction at 96 turns; guarded window flatlines at 381
- Latency −0.76 ms — the guardrail is a net *saving*, not a tax
- Cost at a published $2.50/1M input rate: $44.07 → $20.35 per 1,000 reviews
- **Calibration:** per-message overhead measured at 25 tokens, not the 4 I'd
  assumed — 6× off, and it dominates for windows of many short turns
- 25 tests; zero changes to the frozen contract

## The two things worth telling as engineering judgement

1. **The fair-comparison problem.** History didn't exist — no node wrote to
   `state.messages`. I made the graph produce it via wrappers rather than editing
   four teammates' nodes, and applied those wrappers to *both* sides so the only
   variable between runs is the context node itself.
2. **Not gaming the demo.** The obvious way to force a long run is an
   always-rejecting validator, but that trips a teammate's repeated-reason
   escalation and ends the graph after one round. I made the validator vary its
   reason — which is also what a real one does — rather than raise `MAX_ROUNDS`
   or disable anyone's guardrail to manufacture a more impressive number.
