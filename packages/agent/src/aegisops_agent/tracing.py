"""Langfuse tracing for investigations (E8.1, PROJECT.md §13).

One run = one Langfuse trace. Inside it, every graph node is a span, every model call a
generation (prompt, structured output, model, tokens, cost) and every tool call a tool
observation, nested by the OpenTelemetry context:

    investigation                       (agent)    one per drive; a resumed run adds a second
    ├─ triage                           (chain)
    │  ├─ triage.llm                    (generation)
    │  └─ get_error_rate                (tool)
    ├─ verify_evidence                  (chain)    no model: pure code
    └─ approval                         (chain)    "paused for approval"

The trace id is derived from the run's checkpoint thread id, so the leg after an approval
lands in the same trace without storing anything extra.

Off unless both Langfuse keys are set: tests, CI and the CLI without keys pay nothing, and
every call site works the same with tracing off (null observations).

Langfuse gets its own OpenTelemetry TracerProvider instead of the global one: it should
only ever receive agent spans, and the API's own tracing (E8.2) will go elsewhere.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog
from langfuse import Langfuse, propagate_attributes
from langgraph.errors import GraphBubbleUp
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from pydantic import BaseModel

from aegisops_agent.llm import LLMClient, LLMResult

log = structlog.get_logger()
MAX_IO_CHARS = 8000  # per input/output field; prompts are ~5k, tool envelopes <= 4 KB
ObsType = Literal["agent", "chain", "generation", "tool", "span"]


def clip(value: Any, limit: int = MAX_IO_CHARS) -> Any:
    """Keep observations small: long strings are cut, other values pass if they serialise
    short enough, else become a cut JSON string."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"… [{len(value)} chars]"
    text = json.dumps(value, default=str)
    return value if len(text) <= limit else text[:limit] + f"… [{len(text)} chars]"


class _NoObservation:
    """Stands in for a Langfuse observation when tracing is off."""

    def update(self, **_: Any) -> None:
        return None


NO_OBSERVATION = _NoObservation()


@dataclass
class Tracing:
    client: Langfuse | None = None

    @property
    def enabled(self) -> bool:
        return self.client is not None

    @classmethod
    def create(
        cls,
        *,
        public_key: str | None,
        secret_key: str | None,
        base_url: str | None = None,
        environment: str | None = None,
        enabled: bool = True,
        exporter: SpanExporter | None = None,
    ) -> Tracing:
        if not enabled or not (public_key and secret_key):
            return cls()
        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            base_url=base_url,
            environment=environment,
            tracer_provider=TracerProvider(),  # not the global provider (see module doc)
            span_exporter=exporter,  # tests capture spans in memory; None = Langfuse cloud
        )
        log.info("tracing.enabled", base_url=base_url, environment=environment)
        return cls(client)

    @classmethod
    def from_env(cls, *, environment: str | None = None) -> Tracing:
        """CLI: the same variables the Langfuse SDK documents."""
        return cls.create(
            public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
            base_url=os.environ.get("LANGFUSE_BASE_URL"),
            environment=environment,
            enabled=os.environ.get("AEGIS_TRACING_ENABLED", "true").lower()
            not in ("0", "false", "no"),
        )

    @staticmethod
    def trace_id(seed: str) -> str:
        """Deterministic 32-hex trace id for a run's thread id (same id across legs)."""
        return Langfuse.create_trace_id(seed=seed)

    @contextmanager
    def observe(self, name: str, as_type: ObsType = "span", **kwargs: Any) -> Iterator[Any]:
        """A child of whatever observation is current; a null one when tracing is off."""
        if self.client is None:
            yield NO_OBSERVATION
            return
        with self.client.start_as_current_observation(name=name, as_type=as_type, **kwargs) as o:
            yield o

    @contextmanager
    def run(
        self,
        *,
        trace_id: str,
        name: str,
        input: Any,
        session_id: str | None = None,
        user_id: str | None = None,
        tags: list[str] | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> Iterator[Any]:
        """The root observation of one drive of a run, inside trace `trace_id`."""
        if self.client is None:
            yield NO_OBSERVATION
            return
        with (
            self.client.start_as_current_observation(
                trace_context={"trace_id": trace_id},
                name=name,
                as_type="agent",
                input=clip(input),
            ) as root,
            propagate_attributes(
                trace_name="investigation",
                session_id=session_id,
                user_id=user_id,
                tags=tags,
                metadata=dict(metadata or {}),
            ),
        ):
            yield root

    def score(self, trace_id: str, name: str, value: float, comment: str | None = None) -> None:
        if self.client is not None:
            self.client.create_score(trace_id=trace_id, name=name, value=value, comment=comment)

    async def flush(self) -> None:
        """Export what is buffered (a blocking HTTP call, so off the event loop). On Cloud
        Run the CPU is throttled once the request ends: flush while the run's request is
        still open rather than trusting the background exporter."""
        if self.client is not None:
            try:
                await asyncio.to_thread(self.client.flush)
            except Exception:  # tracing must never fail a run
                log.warning("tracing.flush_failed", exc_info=True)

    def shutdown(self) -> None:
        if self.client is not None:
            self.client.shutdown()


@dataclass
class TracedLLM:
    """LLMClient decorator: each `complete` becomes a generation under the current node."""

    inner: LLMClient
    tracing: Tracing
    prices: Mapping[str, Mapping[str, float]] = field(default_factory=dict)

    async def complete[T: BaseModel](
        self, node: str, system: str, user: str, schema: type[T]
    ) -> LLMResult[T]:
        if not self.tracing.enabled:
            return await self.inner.complete(node, system, user, schema)
        with self.tracing.observe(
            f"{node}.llm",
            "generation",
            input={"system": clip(system), "user": clip(user)},
            metadata={"schema": schema.__name__},
        ) as gen:
            result = await self.inner.complete(node, system, user, schema)
            price = self.prices.get(result.model) or {}
            gen.update(
                model=result.model,
                output=clip(result.value.model_dump(mode="json")),
                usage_details={"input": result.tokens_in, "output": result.tokens_out},
                cost_details={
                    "input": result.tokens_in / 1e6 * price.get("in", 0.0),
                    "output": result.tokens_out / 1e6 * price.get("out", 0.0),
                },
                metadata={"schema": schema.__name__, "fallback": result.fallback},
            )
            return result


def traced_node[S](
    name: str, fn: Callable[[S], Awaitable[Any]], tracing: Tracing
) -> Callable[[S], Awaitable[Any]]:
    """Wrap a graph node in a span. LangGraph pauses a run by raising from `interrupt()`;
    that is a pause, not an error, so it is recorded as such and re-raised outside the span."""

    async def node(state: S) -> Any:
        paused: GraphBubbleUp | None = None
        with tracing.observe(name, "chain") as span:
            try:
                out = await fn(state)
            except GraphBubbleUp as exc:
                span.update(status_message="paused for approval")
                paused = exc
            else:
                events = (out or {}).get("events") or []
                span.update(output={"keys": sorted(out or {}), "event": clip(events[-1:])})
                return out
        assert paused is not None
        raise paused

    node.__name__ = getattr(fn, "__name__", name)
    return node
