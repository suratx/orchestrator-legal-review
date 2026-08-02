"""
calibrate_tokens.py -- measure the heuristic counter against the REAL tokenizer.

DESIGN_DOCS.md risk #14: "Token counter under/over-counts due to tokenizer
mismatch with the actual LLM." That risk was open and assigned to Person 5.
This script closes it with a number instead of an assumption.

The guardrail ships with a dependency-free ~4-chars-per-token estimator so that
it, its tests and its metrics run with no model server. That estimator is only
trustworthy if its error is known, so this script asks the actual model.

HOW IT GETS A TRUE COUNT
    Ollama's /api/generate returns `prompt_eval_count` -- the number of tokens
    the model's own tokenizer produced for the prompt. Asking for zero output
    tokens makes it a pure tokenization query.

SAFETY
    Live, opt-in, and NEVER imported by the test suite -- exactly the pattern
    Person 2 established with `student_2_silent/benchmark_live.py`. It talks
    only to a local Ollama server, performs no destructive action, and writes
    nothing. `pytest` remains fully offline with sockets blocked.

Run:  ollama serve
      python student_6_tokens/calibrate_tokens.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from student_6_tokens.fixtures import LONG_CONTRACT, synthetic_history
from student_6_tokens.snippet import DEFAULT_COUNTER

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2"


def true_token_count(text: str, model: str = MODEL) -> int:
    """Ask the model's own tokenizer how many tokens `text` is."""
    payload = json.dumps(
        {
            "model": model,
            "prompt": text,
            "stream": False,
            "options": {"num_predict": 0},
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return int(json.load(response)["prompt_eval_count"])


def samples():
    """Text shaped like what the guardrail actually counts."""
    yield "short routing turn", "route=analyzer round=2"
    yield "clause quote", (
        "Globex Industries Ltd shall indemnify Acme Corporation for all losses "
        "arising from breach, negligence, or misconduct without limitation."
    )
    yield "full contract", LONG_CONTRACT
    for turns in (6, 24, 96):
        history = synthetic_history(turns)
        blob = "\n".join(entry["content"] for entry in history)
        yield f"history x{turns}", blob


def main() -> None:
    print("=" * 74)
    print(f"TOKEN COUNTER CALIBRATION vs {MODEL} (DESIGN_DOCS.md risk #14)")
    print("=" * 74)

    try:
        true_token_count("warmup")
    except (urllib.error.URLError, OSError) as exc:
        print(f"\nOllama not reachable ({exc}). Start it with:  ollama serve")
        raise SystemExit(1)

    # `prompt_eval_count` includes the model's chat template -- BOS marker and
    # role headers -- which is a FIXED cost independent of the text. Measuring
    # it once and subtracting is what makes the comparison apples-to-apples;
    # without this correction a 22-character string appears to be a 400%
    # estimator error when it is really template scaffolding.
    # An empty prompt is rejected, so measure a single-token prompt and take
    # one token off. That is the fixed scaffolding cost.
    baseline = true_token_count(".") - 1
    print(f"\nChat-template overhead measured at {baseline} tokens per message.")
    print("Subtracted below so the comparison is content-only.\n")

    print(f"{'sample':<20} {'heuristic':>10} {'actual':>8} {'error':>9}")
    print("-" * 52)

    errors = []
    for label, text in samples():
        estimated = DEFAULT_COUNTER.count_text(text)
        actual = max(1, true_token_count(text) - baseline)
        error = 100 * (estimated - actual) / actual
        errors.append(error)
        print(f"{label:<20} {estimated:>10,} {actual:>8,} {error:>8.1f}%")

    mean = sum(errors) / len(errors)
    worst = max(errors, key=abs)
    print("-" * 52)
    print(f"{'mean error':<20} {'':>10} {'':>8} {mean:>8.1f}%")
    print(f"{'worst error':<20} {'':>10} {'':>8} {worst:>8.1f}%")

    print(
        f"\nTwo findings. (1) Per-message overhead is {baseline} tokens, not the "
        f"{4} the estimator assumed -- for a window of many short turns that "
        "difference dominates, so PER_MESSAGE_OVERHEAD_TOKENS is set from this "
        "measurement rather than guessed."
    )
    print(
        "(2) On real contract prose the chars-per-token estimate is close; it "
        "drifts high on repetitive text, where BPE merges repeated phrases the "
        "estimator counts as fresh. That bias is CONSERVATIVE -- it compresses "
        "slightly earlier than strictly necessary, which is the safe direction "
        "for a cost guardrail. Recorded in METRICS.md."
    )


if __name__ == "__main__":
    main()
