# Person 4 — Validator & Downstream Cascade Guardrail

While integrating Worker C for a LangGraph legal contract-review orchestrator, I reproduced a downstream cascade failure: Worker B wrote a structurally complete `execution_state` where `executed_count` was the string `"1"`, a redline cited a fabricated `Section 99`, or the result cardinality lied. A naive Reporter then crashed with TypeError, IndexError, ZeroDivisionError, or RuntimeError — or would have approved a redline against a clause the Analyzer never extracted.

I built an explicit Validation/Sanitization node between Actor and Reporter that runs programmatic assertions against the frozen Pydantic contract: required keys and types, count/list consistency, `external_action_performed=False`, clause-ID grounding into `analysis_payload`, and high/critical-only redlines. On failure the node sets `rejection_flag`, appends `validator: <reason>` to `rejection_reason_history`, clears poisoned execution state, and forces Coordinator rollback; identical reasons twice escalate to partial output.

Across four deterministic malformed payloads, downstream crashes fell from 4/4 (100%) to 0/4, with a 100% clean rejection rate and zero poisoned states reaching the Reporter.
