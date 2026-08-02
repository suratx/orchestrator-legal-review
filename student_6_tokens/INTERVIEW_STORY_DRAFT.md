# Person 5 — Interview Story Draft (Context / Token Management)

**Role:** Global Graph Layer — Context & Token Management
**System:** Multi-agent legal contract review orchestrator (LangGraph, Python)

~150 words. Copied into the root `INTERVIEW_STORIES.md`.

---

I owned the context layer of a five-node LangGraph contract-review orchestrator.
Every loop appended turns to shared state, and any agent reading that history
re-sends all of it, so the window grew monotonically: an adversarial five-round
review peaked at 1,803 tokens against our 1,200 ceiling, breaching it on 4 of 12
transitions.

I built a Context Management Node at the head of each loop: a five-stage ladder
that digests bulky tool outputs, folds older turns into a single rolling
summary, then shrinks the recency window, recounting after every stage and
stopping at the first that fits.

The subtle part was the summary. Appending one per compression would make the
compressor the leak, so there is exactly one and it is replaced — and it stores
a fixed-schema aggregate rather than prose, so merging is addition. It measures
32 tokens whether it absorbed 12 turns or 192.

Peak window fell 35.8%, ceiling breaches to zero, and prompt tokens at a
history-consuming agent 21.2%, with the graph's output unchanged.

---

**Word count:** ~170. Trim the ladder sentence to hit 150 if the brief is strict.

## Numbers to have ready if they probe

- Peak window 1,803 → 1,157 tokens (−35.8%); ceiling breaches 4/12 → 0/12
- Prompt tokens at a history-consuming agent 5,319 → 4,193 (−21.2%)
- Rolling summary constant at **32 tokens** across a 16× range of history length
- Scaling projection: 93.3% reduction at 96 turns; guarded window flatlines at 219
- Latency: +0.07 ms, inside the IQR over 60 runs — **no latency claim made**
- Cost: projected only. 5.32M → 4.19M tokens per 1,000 reviews; apply your provider's current rate
- **Calibration:** fitted via /api/chat message arrays — 2 tokens per message
  plus a 24-token one-off prefix, 5.77 chars/token. My first attempt used
  /api/generate and reported 25/message; that measured a one-shot template
  cost and wrongly multiplied it per message
- 29 tests; zero changes to the frozen contract

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
