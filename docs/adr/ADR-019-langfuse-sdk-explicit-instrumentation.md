# ADR-019: Langfuse tracing via the official SDK, instrumented at three seams, on its own TracerProvider

**Status:** Accepted, 10 Oct 2026

## Context
E8.1 asks for one Langfuse trace per run with node spans, tokens and cost. Our model calls do not go through LangChain: the router is our own (~100 lines, §8.4), and tools go through `ToolRunner`. The API will also get its own OpenTelemetry tracing (E8.2), exported to our collector, not to Langfuse. The Langfuse Python SDK (v4.17) is itself built on OpenTelemetry and, by default, installs its TracerProvider as the process-wide global one.

## Decision
- Use the official `langfuse` SDK (MIT).
- Instrument three seams, not the node bodies:
  - `TracedLLM` wraps any `LLMClient`: each `complete` is a **generation** (prompt, structured output, model, usage, cost from our `models.yaml` prices).
  - `ToolRunner.call` is a **tool** observation (args, untrusted envelope, WARNING on denied/failed calls).
  - `traced_node` wraps each graph node in a **chain** span; LangGraph's `interrupt()` is recorded as "paused for approval", not as an error.
  Nesting comes from the OpenTelemetry context, which follows `await` within a task.
- **Trace id = `create_trace_id(seed=thread_id)`**: the leg after an approval joins the same trace with no extra state; the id is also stored on `runs.langfuse_trace_id`.
- Give Langfuse a **dedicated `TracerProvider`**, never the global one.
- **Off unless both keys are set**; null observations keep call sites identical. API tests force it off because `.env` may hold real keys.
- Scores per run: `verified_confidence` and `claimed_confidence`.

## Alternatives considered
- **LangChain/LangGraph `CallbackHandler`.** Captures graph nodes but not our own router's HTTP calls, so no generations, tokens or cost: the part that matters.
- **Raw ingestion REST API with httpx.** No dependency, but Langfuse now centres its SDK on OpenTelemetry; we would be maintaining a client for an endpoint it may retire.
- **`@observe` decorators on node functions.** Spreads tracing through agent logic; the seams keep `graph.py` free of tracing code apart from one line in `build_graph`.
- **Sharing the global TracerProvider.** E8.2's FastAPI/SQLAlchemy spans would flow to Langfuse (noise, quota), and whichever library initialises first would decide the other's sampling.

## Consequences
- Router retries are one generation (the attempt that answered) with `fallback: true`, not one per failed attempt; fallbacks remain in logs. A later change can trace attempts.
- Flushing is explicit at the end of each drive (off the event loop): on Cloud Run the CPU is throttled once no request is open, so a background exporter cannot be trusted to finish.
- Langfuse caches one client per public key in-process; tests use a unique key each so their in-memory exporters do not collide.
