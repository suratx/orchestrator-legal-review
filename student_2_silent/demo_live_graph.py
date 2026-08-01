"""
demo_live_graph.py -- the real Ollama-backed Analyzer inside the real compiled
graph. This is the path the TEAM's 5-minute end-to-end demo takes.

    python student_2_silent/demo_live_graph.py

Deliberately NOT a pytest test: it needs `ollama serve`, and the repo
guarantees `pytest` never touches the network. Kept as a script so the team
can rehearse the demo and confirm the live path still works after each
person's node lands.

Verified working: full review in ~19s, 5 clauses extracted and grounded,
0 retries, 0 Coordinator rounds.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contract import AgentState  # noqa: E402
from main_system import build_graph  # noqa: E402
from student_2_silent.fixtures import SAMPLE_CONTRACT  # noqa: E402
from student_2_silent.snippet import analyzer_node  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="[%(name)s] %(message)s")

    app = build_graph(analyzer=analyzer_node)  # real node, real model

    print("Running the live graph (needs `ollama serve`)... ~20s\n")
    started = time.perf_counter()
    result = app.invoke(
        AgentState(raw_input=SAMPLE_CONTRACT), config={"recursion_limit": 50}
    )
    elapsed = time.perf_counter() - started

    print("=" * 66)
    print(" LIVE GRAPH RUN -- completed in %.1fs" % elapsed)
    print("=" * 66)
    print(" is_validated        :", result["is_validated"])
    print(" analyzer retries    :", result["analysis_retry_count"])
    print(" coordinator rounds  :", result["round_number"])

    payload = result["analysis_payload"]
    if payload:
        print(" counterparty        :", payload["counterparty"])
        print(" overall risk        :", payload["overall_risk"])
        print("\n Clauses extracted and grounded against the source contract:")
        for clause in payload["clauses"]:
            print(
                "   %-14s %-26s %s"
                % (clause["clause_id"], clause["clause_type"], clause["risk_level"])
            )
    else:
        print("\n No payload -- the guardrail rejected this analysis:")
        print(" ", result["error_log"])

    print("\n" + "=" * 66)
    print(result["final_report"].splitlines()[0])
    print("=" * 66)


if __name__ == "__main__":
    main()
