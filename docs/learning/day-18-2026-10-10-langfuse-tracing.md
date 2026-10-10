# Day 18 · 10 Oct 2026 · Seeing inside a run: Langfuse tracing (PR #39, E8.1)

## Why
Until today, a run left behind its final JSON, the run events and logs. When the agent said something surprising ("claimed 0.66, verified 0.22"), we could not see *which prompt* produced *which claim*, which model actually answered, or where the 112 seconds went. Tracing records every step of a run as a tree you can open and read.

## What we did
- **`aegisops_agent/tracing.py`**, about 200 lines:
  - `Tracing`: the Langfuse client, or nothing. It is off unless `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are both set, so tests, CI and a keyless CLI pay nothing.
  - `TracedLLM`: wraps the model client. Each call becomes a **generation** with the prompt, the structured answer, the model that answered, tokens and cost (from our `models.yaml` prices).
  - `traced_node`: wraps each graph node in a span. An approval pause is recorded as "paused for approval", not as an error.
  - `ToolRunner.call` records each tool call (arguments, the untrusted envelope it returned, a warning when denied or failed).
- **One trace per run**, with the trace id computed from the run's thread id. The resume leg after an approval lands in the same trace, and the id is stored on `runs.langfuse_trace_id` and returned by `GET /runs/{id}`.
- **Scores:** each run records `claimed_confidence` and `verified_confidence`, so Langfuse can chart how much the verifier cuts, run after run.
- **Production:** two new secrets (`aegis-langfuse-public-key`, `aegis-langfuse-secret-key`) created by piping from `.env` (no value printed, lengths checked). The deploy mounts them, plus `LANGFUSE_BASE_URL`.
- The CLI (`aegis-investigate`) traces too when the keys are in the environment.
- ADR-019. 181 tests.

## Live result
One real investigation of the captured S1 scenario through the CLI, then the trace read back through Langfuse's API:
- **27 observations in one tree:** 1 agent root, 8 node spans, 5 generations, 13 tool calls.
- Every generation is nested under its node, with model, tokens and cost: `triage.llm` on gemini-3.5-flash-lite (696 in / 71 out), `plan.llm` and `root_cause.llm` on gemini-3.5-flash, and **both `investigate.llm` calls on openai/gpt-oss-120b**, meaning Gemini failed there and the router fell back to Groq twice.
- Total **$0.0051** at list prices, latency **112 s**.

The trace answered a question on its first day: most of the 112 s was the investigate node waiting on fallbacks. Without the trace, that was one line in a log among hundreds.

## How the tree is built
```
investigation (agent)                     ← tracing.run(trace_id = f(thread_id))
├─ triage (chain)                         ← traced_node
│  ├─ get_error_rate (tool)               ← ToolRunner.call
│  └─ triage.llm (generation)             ← TracedLLM.complete
├─ plan (chain) ─ plan.llm
├─ investigate (chain) ─ 9 tools, 2 generations
├─ correlate_changes (chain)              ← deterministic, still timed
├─ root_cause (chain) ─ root_cause.llm
├─ verify_evidence (chain)                ← no model: pure code
├─ remediate (chain)
└─ approval (chain)  "paused for approval"
resume (agent)                            ← same trace id, after the human decides
└─ approval → execute
```
Nobody passes a "parent" around. OpenTelemetry keeps the *current span* in a context variable that follows `await` within a task. `traced_node` makes the node span current, so the generation that `TracedLLM` opens inside it automatically becomes its child.

## Terms introduced

**Observability.** Being able to answer new questions about a running system from what it records, without adding code first. Logs are lines; metrics are numbers over time; **traces** are trees of timed steps for one request or, here, one run.

**Trace, span, observation.** A **trace** is the whole story of one run. A **span** is one timed step inside it, with a start, an end, a parent, and attributes. Langfuse calls its spans **observations** and gives them types: *agent* (the run), *chain* (a node), *generation* (a model call), *tool* (a tool call).

**Generation.** Langfuse's observation for one model call: the prompt in, the answer out, the model name, token usage and cost. This is what makes Langfuse an *LLM* observability tool rather than generic tracing.

**OpenTelemetry (OTel).** The open standard for producing traces, metrics and logs, independent of vendor. Langfuse v4 is built on it: our spans are OTel spans with Langfuse attributes (`langfuse.observation.type = generation` and so on).

**Context propagation.** How a child span finds its parent without being handed it. OTel stores the current span in a Python `contextvar`; it survives `await`, so anything opened inside the node's `with` block nests under the node.

**TracerProvider (global vs dedicated).** The OTel object that creates spans and sends them to exporters. A process has one *global* provider that libraries use by default. Langfuse would install itself there; we give it a **dedicated** one, so only agent spans reach Langfuse and the API's own tracing (E8.2) can go to our collector.

**Exporter and flush.** Spans are buffered and sent in batches by an *exporter* on a background thread. **Flush** forces the buffer out now. We flush at the end of each run leg, because Cloud Run throttles the CPU once no request is open, and a background thread might never finish.

**Deterministic trace id.** `create_trace_id(seed=thread_id)` always gives the same 32-hex id for the same run. The leg after an approval, possibly on another Cloud Run instance, joins the same trace without any lookup.

**Decorator pattern (`TracedLLM`).** Wrap an object in another with the same interface that adds behaviour (here: recording) and delegates the real work. The graph cannot tell the difference, and the router stays tracing-free.

**Null object pattern.** When tracing is off, `observe()` yields an object whose `update()` does nothing. Call sites never write `if tracing:` branches.

**Score.** A number attached to a trace in Langfuse, chartable across runs. Ours are claimed vs verified confidence.

## Decisions
- ADR-019: the official SDK, three seams (model client, tool runner, node wrapper), and a dedicated TracerProvider. Rejected: the LangChain callback handler (it cannot see our own router's calls, so no tokens or cost), raw REST ingestion (maintaining a client for an endpoint Langfuse is moving away from), decorators inside node bodies, and sharing the global provider.

## Honest gaps
- A router retry shows as one generation (the attempt that answered, `fallback: true`), not one per failed attempt. The failures are in the logs.
- Production traces start with the first production run, after the S1 import.

## Interview questions
1. *How do you debug a wrong answer from your agent?* Open the run's trace. Each node shows the exact prompt, the structured answer and the model that produced it; the verifier's span shows what it dropped; the scores show claimed vs verified.
2. *How do child spans find their parent in async Python?* The OTel context lives in a `contextvar` that follows `await`; whatever is current when a span starts becomes its parent.
3. *Why not the LangChain callback handler?* Our model calls go through our own router, which the handler cannot see: we would get node timings but no prompts, tokens or cost.
4. *Why a dedicated TracerProvider?* Isolation: Langfuse should receive agent spans only, not every HTTP and SQL span the API will emit, and neither library should configure the other's sampling.
5. *Why flush explicitly on Cloud Run?* CPU is throttled outside requests, so buffered spans might never be sent; flush while the run's request is still open.
