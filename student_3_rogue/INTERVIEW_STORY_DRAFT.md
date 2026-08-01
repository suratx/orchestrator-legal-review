# Person 3 - Actor and Rogue Tool Execution Guardrail

While developing Worker B for a LangGraph-based legal contract review system, I reproduced a rogue tool execution failure in which an unguarded Actor accepted a model-generated `delete_contract` request. Although the destructive tool was safely mocked, the test demonstrated that an unauthorized call could pass directly into the execution layer without permission checks.

I implemented a code-level middleware that validates the complete tool-call array before any function runs. The guardrail checks the requested tool against a hardcoded permission matrix, validates required and optional parameters, rejects unknown arguments, verifies parameter types, confirms that the clause exists in the frozen contract analysis, and permits redlining only for high- or critical-risk clauses. Invalid requests raise `InvalidToolCallException` and update the graph state with a rejection flag.

In deterministic testing, unauthorized mock executions fell from 1 to 0, producing a 100% block rate while authorized mocked redline calls continued to execute successfully. No real external actions were performed.
