"""
test_failure.py -- Person 5 (Context/Token layer): context window explosion.

Runs the REAL compiled LangGraph twice over the same adversarial contract
review. The two runs are identical in every respect except which callable is
injected as the context node:

    unguarded -> context_manager_NO_GUARDRAIL   (counts, prunes nothing)
    guarded   -> context_manager_node           (counts, prunes, summarizes)

The turn-recording wrappers are applied in BOTH runs, so the histories are
produced by the same code. A comparison where each side built its history
differently would be measuring the fixture rather than the guardrail.

Fully offline: no model server, no network, no file writes.

Run:  pytest student_6_tokens/test_failure.py -v
      python student_6_tokens/test_failure.py     # before/after table
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract import MAX_ROUNDS
from main_system import build_graph
from student_6_tokens.fixtures import (
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
    is_summary,
)


class BurnMeter:
    """Records the window size at every context-node visit.

    Cumulative burn is the SUM of those values: each turn re-sends the whole
    window, so the run's real input-token cost is the sum over turns, not the
    size of the final history. That distinction is the entire failure mode.
    """

    def __init__(self, node):
        self.node = node
        self.windows: list[int] = []

    def __call__(self, state):
        result = self.node(state)
        self.windows.append(result.token_count)
        return result

    @property
    def cumulative(self) -> int:
        return sum(self.windows)

    @property
    def peak(self) -> int:
        return max(self.windows) if self.windows else 0


class Counter:
    """Counts node invocations, to prove both runs did the same work."""

    def __init__(self, node):
        self.node = node
        self.calls = 0

    def __call__(self, state):
        self.calls += 1
        return self.node(state)


def run(context_node):
    """One full adversarial run. Returns (result, meter, invocation counts)."""
    meter = BurnMeter(context_node)
    analyzer = Counter(analyzer_stub)
    actor = Counter(actor_stub)
    validator = Counter(make_varying_validator())

    app = build_graph(
        analyzer=analyzer,
        actor=actor,
        validator=validator,
        context_manager=meter,
    )
    result = app.invoke(initial_state(), config={"recursion_limit": 60})
    counts = {
        "analyzer": analyzer.calls,
        "actor": actor.calls,
        "validator": validator.calls,
    }
    return result, meter, counts


# ==========================================================================
# THE FAILURE
# ==========================================================================


def test_unguarded_window_grows_past_the_ceiling():
    result, meter, _ = run(context_manager_NO_GUARDRAIL)

    assert result["round_number"] == MAX_ROUNDS
    assert meter.peak > MAX_CONTEXT_TOKENS, (
        f"peak window {meter.peak} never breached the {MAX_CONTEXT_TOKENS} "
        "ceiling; the fixture is not exercising the failure"
    )
    # history is never compacted: it only ever grows
    assert meter.windows == sorted(meter.windows)


def test_unguarded_burn_is_superlinear():
    """Each turn pays for every earlier turn again, so cumulative cost grows
    faster than the history itself."""
    _, meter, _ = run(context_manager_NO_GUARDRAIL)

    assert meter.cumulative > meter.peak * 2


# ==========================================================================
# THE GUARDRAIL
# ==========================================================================


def test_guardrail_holds_every_window_under_the_ceiling():
    result, meter, _ = run(context_manager_node)

    assert result["round_number"] == MAX_ROUNDS
    assert meter.peak <= MAX_CONTEXT_TOKENS, (
        f"window reached {meter.peak}, ceiling is {MAX_CONTEXT_TOKENS}"
    )


def test_guardrail_reduces_cumulative_burn():
    _, unguarded, _ = run(context_manager_NO_GUARDRAIL)
    _, guarded, _ = run(context_manager_node)

    assert guarded.cumulative < unguarded.cumulative
    assert guarded.peak < unguarded.peak


# ==========================================================================
# FAIRNESS -- the two runs must differ in exactly one variable
# ==========================================================================


def test_both_runs_do_identical_work():
    """Same producer, same workers, same number of invocations. If these
    diverged, the before/after numbers would be comparing two different
    workloads."""
    _, _, unguarded_counts = run(context_manager_NO_GUARDRAIL)
    _, _, guarded_counts = run(context_manager_node)

    assert unguarded_counts == guarded_counts


def test_compression_does_not_change_the_outcome():
    """Pruning history must not change what the graph decides or reports."""
    unguarded, _, _ = run(context_manager_NO_GUARDRAIL)
    guarded, _, _ = run(context_manager_node)

    for field in (
        "final_report",
        "round_number",
        "is_validated",
        "rejection_flag",
        "rejection_reason_history",
        "analysis_payload",
        "validation_notes",
        "error_log",
    ):
        assert guarded[field] == unguarded[field], f"{field} diverged under pruning"


# ==========================================================================
# DEMO
# ==========================================================================


def _fmt(n: int) -> str:
    return f"{n:,}"


def main() -> None:
    print("=" * 74)
    print("PERSON 5 (CONTEXT LAYER) -- CONTEXT WINDOW EXPLOSION & TOKEN BURN")
    print("=" * 74)

    unguarded_result, unguarded, counts = run(context_manager_NO_GUARDRAIL)
    guarded_result, guarded, _ = run(context_manager_node)

    print(f"\nWorkload (identical in both runs): {counts}")
    print(f"Rounds executed: {unguarded_result['round_number']} "
          f"(Person 1's MAX_ROUNDS ceiling -- not raised for this demo)")

    print("\n1. WITHOUT GUARDRAIL")
    print(f"   window at each turn         : {unguarded.windows}")
    print(f"   peak window                 : {_fmt(unguarded.peak)} tokens "
          f"(ceiling {MAX_CONTEXT_TOKENS})")
    print(f"   cumulative input tokens     : {_fmt(unguarded.cumulative)}")
    print(f"   final history length        : {len(unguarded_result['messages'])} turns")

    print("\n2. WITH GUARDRAIL")
    print(f"   window at each turn         : {guarded.windows}")
    print(f"   peak window                 : {_fmt(guarded.peak)} tokens")
    print(f"   cumulative input tokens     : {_fmt(guarded.cumulative)}")
    print(f"   final history length        : {len(guarded_result['messages'])} turns")
    summaries = [m for m in guarded_result["messages"] if is_summary(m)]
    print(f"   rolling summaries in window : {len(summaries)} (must be <= 1)")
    if summaries:
        print(f"   summary content             : {summaries[0]['content'][:96]}...")

    saved = unguarded.cumulative - guarded.cumulative
    pct = 100 * saved / unguarded.cumulative
    print("\n3. RESULT")
    print(f"   peak window       : {_fmt(unguarded.peak)} -> {_fmt(guarded.peak)} tokens")
    print(f"   cumulative burn   : {_fmt(unguarded.cumulative)} -> "
          f"{_fmt(guarded.cumulative)} tokens  ({pct:.1f}% reduction)")
    print(f"   graph output      : identical "
          f"({guarded_result['final_report'] == unguarded_result['final_report']})")

    print("\n4. NODE-LEVEL SCALING STUDY (projection -- not an in-graph measurement)")
    print("   The graph stops at 5 rounds by design. This shows what the same")
    print("   guardrail does to longer histories, measured on the node alone.")
    print(f"   {'turns':>6} | {'unguarded':>10} | {'guarded':>8} | {'reduction':>9}")
    for turns in (6, 12, 24, 48, 96):
        history = synthetic_history(turns)
        raw = DEFAULT_COUNTER.count_messages(history)
        _, bounded, _ = compress_history(history)
        print(f"   {turns:>6} | {raw:>10,} | {bounded:>8,} | "
              f"{100 * (raw - bounded) / raw:>8.1f}%")


if __name__ == "__main__":
    main()
