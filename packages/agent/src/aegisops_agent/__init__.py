"""aegisops-agent: the LangGraph investigation agent (PROJECT.md §8).

triage -> plan -> investigate -> root_cause, every node a pure function of state + tools
with a Pydantic output schema (E3.2), checkpointed in Postgres after each node (E3.1,
ADR-006), under hard budgets (E3.3). The model is reached only through `llm.Router`.
"""
