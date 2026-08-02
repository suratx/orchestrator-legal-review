"""
benchmark.py -- reproducible measurement for the context/token guardrail.

Every number in METRICS.md is produced here. Offline and deterministic: no
model server, no network, no file writes.

WHY THIS FILE EXISTS SEPARATELY
    An earlier draft reported a "-0.76 ms latency saving" from a handful of
    untimed runs. At that scale the difference is indistinguishable from
    scheduler noise, and a claim that cannot be reproduced with a stated method
    should not appear in a metrics document. This script uses warm-up runs,
    a fixed repeat count, and reports median with an interquartile range so the
    reader can see whether the difference is real.

THE TWO TOKEN NUMBERS, AND WHY BOTH ARE REPORTED
    (a) Managed-window estimate across graph transitions -- the window size at
        every context-node visit, summed. This is a PROJECTION: it assumes a
        history-consuming agent runs at every transition. In this system that
        is not true, because no production node reads `state.messages`.
    (b) Prompt tokens at history-consuming agent invocations -- measured at a
        stub that genuinely builds its prompt from the window. Fewer events,
        smaller numbers, and the defensible figure.

Run:  python student_6_tokens/benchmark.py
"""

from __future__ import annotations

import logging
import statistics
import sys
import time
from pathlib import Path
from typing import Callable, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_system import build_graph
from student_6_tokens.fixtures import (
    HistoryConsumingAnalyzer,
    actor_stub,
    analyzer_stub,
    initial_state,
    make_varying_validator,
    synthetic_history,
)
from student_6_tokens.snippet import (
    DEFAULT_COUNTER,
    MAX_CONTEXT_TOKENS,
    compress_history,
    context_manager_NO_GUARDRAIL,
    context_manager_node,
)

WARMUP_RUNS = 5
TIMED_RUNS = 60


class WindowMeter:
    """Records the managed-window size at every context-node visit."""

    def __init__(self, node: Callable):
        self.node = node
        self.windows: List[int] = []

    def __call__(self, state):
        result = self.node(state)
        self.windows.append(result.token_count)
        return result

    @property
    def cumulative(self) -> int:
        return sum(self.windows)

    @property
    def peak(self) -> int:
        return max(self.windows, default=0)


def run_once(context_node, *, consuming: bool = False):
    """One adversarial run. Returns (meter, analyzer)."""
    meter = WindowMeter(context_node)
    analyzer = HistoryConsumingAnalyzer() if consuming else analyzer_stub
    app = build_graph(
        analyzer=analyzer,
        actor=actor_stub,
        validator=make_varying_validator(),
        context_manager=meter,
    )
    app.invoke(initial_state(), config={"recursion_limit": 60})
    return meter, analyzer


def time_runs(context_node) -> Tuple[float, float, float]:
    """Warm up, then time. Returns (median_ms, p25, p75)."""
    for _ in range(WARMUP_RUNS):
        run_once(context_node)

    samples = []
    for _ in range(TIMED_RUNS):
        start = time.perf_counter()
        run_once(context_node)
        samples.append((time.perf_counter() - start) * 1000)

    samples.sort()
    return (
        statistics.median(samples),
        samples[len(samples) // 4],
        samples[3 * len(samples) // 4],
    )


def main() -> None:
    logging.disable(logging.CRITICAL)

    print("=" * 74)
    print("CONTEXT / TOKEN GUARDRAIL -- BENCHMARK")
    print(f"ceiling {MAX_CONTEXT_TOKENS} tokens | {WARMUP_RUNS} warm-up + "
          f"{TIMED_RUNS} timed runs")
    print("=" * 74)

    unguarded, _ = run_once(context_manager_NO_GUARDRAIL)
    guarded, _ = run_once(context_manager_node)

    print("\n1. MANAGED WINDOW AT EACH CONTEXT-NODE VISIT")
    print(f"   unguarded : {unguarded.windows}")
    print(f"   guarded   : {guarded.windows}")
    print(f"   peak      : {unguarded.peak:,} -> {guarded.peak:,} tokens "
          f"({100 * (unguarded.peak - guarded.peak) / unguarded.peak:.1f}% lower)")
    print(f"   breaches  : {sum(w > MAX_CONTEXT_TOKENS for w in unguarded.windows)}"
          f"/{len(unguarded.windows)} -> "
          f"{sum(w > MAX_CONTEXT_TOKENS for w in guarded.windows)}"
          f"/{len(guarded.windows)}")

    print("\n2. TOKEN TOTALS -- two different questions")
    print(f"   (a) managed-window estimate summed over ALL graph transitions")
    print(f"       -- a PROJECTION; no production node reads state.messages")
    print(f"       unguarded {unguarded.cumulative:,} -> guarded "
          f"{guarded.cumulative:,}  "
          f"({100 * (unguarded.cumulative - guarded.cumulative) / unguarded.cumulative:.1f}% lower)")

    _, u_agent = run_once(context_manager_NO_GUARDRAIL, consuming=True)
    _, g_agent = run_once(context_manager_node, consuming=True)
    print(f"   (b) prompt tokens at a history-consuming agent's invocations")
    print(f"       -- measured at real consumer events; the defensible figure")
    print(f"       unguarded prompts: {u_agent.prompt_tokens}")
    print(f"       guarded   prompts: {g_agent.prompt_tokens}")
    print(f"       unguarded {u_agent.cumulative:,} -> guarded "
          f"{g_agent.cumulative:,}  "
          f"({100 * (u_agent.cumulative - g_agent.cumulative) / u_agent.cumulative:.1f}% lower)")

    print("\n3. LATENCY")
    u_med, u_lo, u_hi = time_runs(context_manager_NO_GUARDRAIL)
    g_med, g_lo, g_hi = time_runs(context_manager_node)
    print(f"   unguarded : median {u_med:6.2f} ms   (IQR {u_lo:.2f}-{u_hi:.2f})")
    print(f"   guarded   : median {g_med:6.2f} ms   (IQR {g_lo:.2f}-{g_hi:.2f})")
    delta = g_med - u_med
    spread = max(u_hi - u_lo, g_hi - g_lo)
    verdict = (
        "WITHIN NOISE -- do not claim a latency effect"
        if abs(delta) < spread
        else f"outside the spread ({delta:+.2f} ms)"
    )
    print(f"   delta     : {delta:+.2f} ms  ->  {verdict}")

    print("\n4. NODE-LEVEL SCALING (projection, not an in-graph measurement)")
    print(f"   {'turns':>6} | {'unguarded':>10} | {'guarded':>8} | {'reduction':>9}")
    for turns in (6, 12, 24, 48, 96):
        history = synthetic_history(turns)
        raw = DEFAULT_COUNTER.count_messages(history)
        _, bounded, _ = compress_history(history)
        print(f"   {turns:>6} | {raw:>10,} | {bounded:>8,} | "
              f"{100 * (raw - bounded) / raw:>8.1f}%")


if __name__ == "__main__":
    main()
