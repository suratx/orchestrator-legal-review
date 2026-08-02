"""
calibrate_tokens.py -- derive the token estimator's constants from llama3.2.

DESIGN_DOCS.md risk #14: "Token counter under/over-counts due to tokenizer
mismatch with the actual LLM." This script closes it by *fitting* the two
constants the estimator uses, rather than assuming them.

WHY /api/chat AND NOT /api/generate
    An earlier version of this script posted a single joined string to
    /api/generate, measured the difference between that and a one-character
    prompt, and called the difference "per-message overhead". That is not a
    valid experiment: /api/generate takes ONE prompt string, so it applies the
    chat template ONCE. A cost measured once cannot be multiplied by the number
    of messages in a history.

    /api/chat takes an actual message array, so the template is applied per
    message exactly as it is at runtime. The per-message cost is then the
    SLOPE of tokens against message count, which is what the estimator needs.

METHOD -- two one-variable fits, so each constant is separately identifiable:
    A. Hold content constant and tiny, vary the message count (1, 2, 4, 8, 16).
       Slope of prompt_eval_count vs. count = per-message overhead.
    B. Hold the message count at 1, vary content length.
       Slope of prompt_eval_count vs. characters = 1 / chars-per-token.

    Nothing is imported from `snippet.py` for the fit -- the constants are
    derived here and then compared to what the estimator currently uses, so
    the script cannot accidentally "measure" its own assumption.

SAFETY
    Live, opt-in, NEVER imported by the test suite -- the pattern Person 2
    established with `student_2_silent/benchmark_live.py`. Talks only to a
    local Ollama server, writes nothing, performs no destructive action.
    `pytest` stays fully offline.

Run:  ollama serve
      python student_6_tokens/calibrate_tokens.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
MODEL = "llama3.2"

FILLER = (
    "The indemnifying party shall not be liable for indirect or consequential "
    "damages arising out of or relating to this Agreement. "
)


def chat_prompt_tokens(messages: Sequence[dict], model: str = MODEL) -> int:
    """Tokens the model's own tokenizer produces for this message ARRAY."""
    payload = json.dumps(
        {
            "model": model,
            "messages": list(messages),
            "stream": False,
            "options": {"num_predict": 0},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA_CHAT_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return int(json.load(response)["prompt_eval_count"])


def linear_fit(points: Sequence[Tuple[float, float]]) -> Tuple[float, float]:
    """Ordinary least squares. Returns (slope, intercept)."""
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    slope = numerator / denominator
    return slope, mean_y - slope * mean_x


def fit_per_message_overhead() -> Tuple[float, List[Tuple[int, int]]]:
    """Vary message COUNT, hold content tiny and constant."""
    samples: List[Tuple[int, int]] = []
    for count in (1, 2, 4, 8, 16):
        messages = [{"role": "user", "content": "ok"} for _ in range(count)]
        samples.append((count, chat_prompt_tokens(messages)))
    slope, _ = linear_fit([(float(c), float(t)) for c, t in samples])
    return slope, samples


def fit_chars_per_token() -> Tuple[float, List[Tuple[int, int]]]:
    """Vary CONTENT length, hold the message count at one."""
    samples: List[Tuple[int, int]] = []
    for repeats in (1, 2, 4, 8, 16):
        content = FILLER * repeats
        samples.append(
            (len(content), chat_prompt_tokens([{"role": "user", "content": content}]))
        )
    slope, _ = linear_fit([(float(c), float(t)) for c, t in samples])
    return 1.0 / slope, samples


def main() -> None:
    print("=" * 74)
    print(f"TOKEN ESTIMATOR CALIBRATION vs {MODEL}  (DESIGN_DOCS.md risk #14)")
    print("=" * 74)

    try:
        chat_prompt_tokens([{"role": "user", "content": "warmup"}])
    except (urllib.error.URLError, OSError, KeyError) as exc:
        print(f"\nOllama not reachable ({exc}). Start it with:  ollama serve")
        raise SystemExit(1)

    print("\nA. PER-MESSAGE OVERHEAD  (vary message count, content held constant)")
    print(f"   {'messages':>9} {'prompt tokens':>14}")
    overhead, count_samples = fit_per_message_overhead()
    for count, tokens in count_samples:
        print(f"   {count:>9} {tokens:>14,}")
    print(f"   → fitted slope: {overhead:.2f} tokens per message")

    print("\nB. CHARACTERS PER TOKEN  (vary content length, one message)")
    print(f"   {'characters':>11} {'prompt tokens':>14}")
    chars_per_token, char_samples = fit_chars_per_token()
    for chars, tokens in char_samples:
        print(f"   {chars:>11,} {tokens:>14,}")
    print(f"   → fitted slope: {chars_per_token:.2f} characters per token")

    # Compare against what the estimator currently ships. Imported only AFTER
    # the fit, so it cannot influence it.
    from student_6_tokens.snippet import (
        PER_MESSAGE_OVERHEAD_TOKENS,
        HeuristicTokenCounter,
    )

    print("\nC. WHAT THE ESTIMATOR CURRENTLY USES")
    print(f"   {'constant':<28} {'in code':>9} {'fitted':>9} {'delta':>9}")
    rows = [
        ("PER_MESSAGE_OVERHEAD_TOKENS", PER_MESSAGE_OVERHEAD_TOKENS, overhead),
        ("chars_per_token", HeuristicTokenCounter.chars_per_token, chars_per_token),
    ]
    for name, in_code, fitted in rows:
        delta = 100 * (in_code - fitted) / fitted
        print(f"   {name:<28} {in_code:>9.2f} {fitted:>9.2f} {delta:>8.1f}%")

    print(
        "\nBoth constants above are fitted from message ARRAYS via /api/chat, so "
        "the per-message cost is a measured slope rather than a single-prompt "
        "template cost multiplied by a message count. Update the constants in "
        "snippet.py if the deltas are large, and record the run in METRICS.md."
    )


if __name__ == "__main__":
    main()
