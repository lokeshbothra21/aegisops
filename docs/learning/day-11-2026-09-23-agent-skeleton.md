# Day 11 · 23 Sep 2026 · The agent skeleton: graph, schemas, budgets, router (PR #21, E3.1, E3.2, E3.3, E3.5)

## What we did
The first version of the investigation agent, in `packages/agent`, built so that no model key is needed until the very last step:
- **Graph (E3.1):** LangGraph `StateGraph` with four nodes, triage → plan → investigate → root_cause; conditional edges jump to `root_cause` on any budget breach; state checkpointed to Postgres after every node with `AsyncPostgresSaver` (ADR-006).
- **Structured outputs (E3.2):** every node returns a Pydantic model (`Triage`, `Hypotheses`, `Findings`, `RootCause`); the model is asked in JSON mode with the schema attached and the answer is validated. Free text only inside fields.
- **Budgets (E3.3):** 15 tool calls, 60k tokens, 180 s, enforced in the tool runner and after every model call; a breach short-circuits to a `partial=true` root cause.
- **Router (E3.5):** ~100 lines of our own code. `config/models.yaml` names a primary (`gemini:gemini-2.5-flash`), a secondary (`groq:llama-3.3-70b-versatile`) and per-node overrides (triage on the cheaper flash-lite). Providers are talked to over plain httpx (Gemini `generateContent`, Groq's OpenAI-compatible endpoint), both in JSON-schema mode; 429/5xx/timeouts fall back to the secondary and are logged as `model_fallback`.
- **Recorded model ("cassettes"):** `RecordedLLM` replays canned outputs per node from a YAML file; the S1 cassette drives the end-to-end test and the CLI.
- **Node-scoped tool authorization (first cut of E9.2):** `NODE_TOOLS` says which tools each node may call; denied and unknown tools still count against the budget.
- **CLI:** `uv run aegis-investigate --service payment --alert "..." [--scenario S1 --frozen-now ...] [--recorded cassette.yaml]` prints one JSON document; logs go to stderr.
- 97 tests, 95 %.

## Live result
End to end on the seeded S1 scenario with the recorded model: 4 model calls, 10 tool calls, tokens 3,200 in / 800 out, root cause `dependency_errors` at confidence 0.9, `partial=false`, six checkpoints written for the thread. Week 3's exit criterion ("end-to-end on S1 producing a RootCause JSON") is met with recorded outputs; the same command with `AEGIS_GEMINI_API_KEY` set and no `--recorded` will make the first real model run.

## How a run flows
`run_investigation()` builds a `ToolRunner` (session factory + `ToolContext` + budget), the graph with `Deps(llm, tools, budget)`, and invokes it with the incident's service and alert text under a `thread_id`. **triage** pulls a 5-minute snapshot (error rate, latency, top error logs) and asks the model for service/symptom/window. **plan** fetches the dependency graph (depth 2) and the last hour of change events, shows the model the catalogue of tools it may use, and gets ≤ 3 hypotheses each with tool requests. **investigate** executes those requests (arguments filtered to the tool's real parameters), collects the untrusted envelopes, and asks once for findings with evidence references. **root_cause** turns everything into the final `RootCause`, marking `partial` if a budget tripped. Every node appends an event to `state.events` (the future SSE stream) and updates `usage`.

## Terms introduced

**LangGraph StateGraph / node / edge / conditional edge.** A graph whose nodes are functions from state to a partial state update, joined by edges; conditional edges pick the next node from the state. Ours has a fixed topology, which is what makes "the only edge into `execute` is from `approval`" testable later.

**TypedDict state.** LangGraph state is a dict with declared keys; nodes return only the keys they change and LangGraph merges. Values must be JSON-serialisable so the checkpointer can store them, hence `model_dump()` everywhere.

**Checkpointer / thread_id.** After each node the whole state is written to Postgres under a `thread_id`. A crashed or interrupted run resumes from the last checkpoint; the approval interrupt (Week 5) relies on this. Tables `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` are created by `saver.setup()` and belong to LangGraph, not Alembic.

**Structured output / JSON mode / response schema.** Asking the model for JSON that conforms to a schema, and validating it on our side. Gemini takes `responseSchema`; Groq takes `response_format: json_schema`. Pydantic's `model_json_schema()` output is inlined (no `$ref`) because providers do not resolve references.

**Cassette (recorded model).** Canned model outputs replayed per node. Makes the agent testable in CI and deterministic in the public replay demo. The plan called these "VCR-style cassettes".

**Budget wrapper.** Counting tool calls, tokens and wall time at the two places they are spent (tool runner, after each model call) and turning a breach into a state flag that routes to `root_cause`. The partial report is still produced: an incident never ends with nothing.

**Model router / fallback.** One place that knows provider names, models and keys; per-node overrides for cost; automatic fallback on quota or outage. Interviewers call this "provider abstraction without a framework".

**Retryable vs non-retryable errors.** 429/5xx/timeouts → try the secondary; 400/401/403 → fail fast (a bad request will fail on any provider too). Invalid model JSON is retryable: another sample may validate.

**Plan-then-execute (ADR-016).** The planner names tools and arguments; execution is deterministic; one model call interprets results. Four model calls per run regardless of tool count.

**Node-scoped tool authorization.** A table of which tools each node may call, enforced at execution. Defence in depth with the untrusted envelope: even a hijacked planner cannot make `triage` read change events or any node reach an action.

**Argument filtering.** Only the tool's real parameters are passed; anything else the model invents is dropped rather than causing an exception.

**Prompt versioning.** Prompts are files under `prompts/` and `PROMPT_VERSION` is recorded in state, so a benchmark result can be tied to the exact prompts that produced it.

**Alembic `include_object`.** Hook to exclude tables Alembic must not manage; the drift test and autogenerate now skip `checkpoint*`. Found because the checkpointer test created tables and the drift test then wanted to drop them.

**Logs to stderr, data to stdout.** A CLI whose stdout is one JSON document composes with pipes; structlog is pointed at stderr.

## Interview questions
1. *Why a fixed graph rather than letting the model drive?* Bounded cost (4 model calls), testable topology, and authorization by structure. (ADR-016)
2. *How do you make an LLM agent testable in CI?* Structured outputs plus recorded per-node responses; the graph and tools run for real against Postgres, only the model is replayed.
3. *What happens when the budget runs out mid-investigation?* The state flag routes to `root_cause`, which produces a `partial=true` report with lowered confidence; nothing is lost, and the UI will say so.
4. *How does provider fallback work and when does it not?* Retryable errors (quota, 5xx, timeout) retry once on the secondary; 4xx client errors fail fast. Logged as `model_fallback` for the cost dashboard.
5. *Where does the checkpoint live and who owns those tables?* Postgres, same database, tables created by LangGraph's saver; Alembic is told to ignore them.
6. *How do you keep the model from calling a tool it should not?* Node-scoped allowlist enforced at execution, arguments filtered to the real signature, and denied calls still burn budget.
