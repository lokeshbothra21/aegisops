"""Langfuse tracing (E8.1): one trace per run, node spans, generations with usage and cost,
tool observations, approval recorded as a pause; and nothing at all without keys."""

import json
from typing import Any
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from aegisops_agent.llm import LLMError, LLMResult, RecordedLLM
from aegisops_agent.run import run_investigation
from aegisops_agent.tracing import NO_OBSERVATION, TracedLLM, Tracing, clip
from aegisops_tools.context import ToolContext
from aegisops_tools.testing import NOW, delete_scenario, seed_payment_failure

CASSETTE = "packages/agent/tests/cassettes/s1_payment_failure.yaml"
ALERT = "high-error-rate: error_rate for payment = 0.83 > 0.05 over 120s for 2 evaluations"
TYPE = "langfuse.observation.type"


def _tracing(exporter: InMemorySpanExporter) -> Tracing:
    # Langfuse keeps one client per public key in-process: a unique key per test keeps
    # each test's exporter its own
    return Tracing.create(
        public_key=f"pk-test-{uuid4().hex[:8]}",
        secret_key="sk-test",  # pragma: allowlist secret
        base_url="http://localhost:9",
        environment="test",
        exporter=exporter,
    )


def _attr(span: ReadableSpan, key: str) -> Any:
    raw = (span.attributes or {}).get(key)
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return raw


@pytest.fixture
async def scenario(engine: AsyncEngine):  # type: ignore[no-untyped-def]
    sc = f"T-{uuid4().hex[:8]}"
    await seed_payment_failure(engine, sc)
    try:
        yield sc
    finally:
        await delete_scenario(engine, sc)


async def test_a_run_becomes_one_nested_trace(engine: AsyncEngine, scenario: str) -> None:
    exporter = InMemorySpanExporter()
    tracing = _tracing(exporter)
    thread = f"t-{scenario}"
    rc, _ = await run_investigation(
        engine=engine,
        llm=RecordedLLM.from_file(CASSETTE),
        ctx=ToolContext(scenario_id=scenario, frozen_now=NOW),
        service="payment",
        alert_summary=ALERT,
        thread_id=thread,
        incident_id=42,
        tracing=tracing,
        prices={"recorded": {"in": 1.0, "out": 2.0}},
    )
    tracing.shutdown()
    spans = exporter.get_finished_spans()
    by_id = {s.context.span_id: s for s in spans}
    names = [s.name for s in spans]

    # one trace, the deterministic id of the thread
    assert {format(s.context.trace_id, "032x") for s in spans} == {Tracing.trace_id(thread)}
    (root,) = [s for s in spans if s.name == "investigation"]
    assert _attr(root, TYPE) == "agent" and root.parent is not None  # parent = trace context
    assert _attr(root, "session.id") == "incident-42"
    assert _attr(root, "langfuse.observation.output")["category"] == rc.category.value

    def parent(s: ReadableSpan) -> ReadableSpan | None:
        return by_id.get(s.parent.span_id) if s.parent else None

    # every node is a chain span directly under the run, deterministic nodes included
    for node in ("triage", "plan", "investigate", "verify_evidence", "remediate", "approval"):
        span = next(s for s in spans if s.name == node)
        assert _attr(span, TYPE) == "chain" and parent(span) is root, node

    # model calls are generations under their node, with tokens and cost from our prices
    gen = next(s for s in spans if s.name == "triage.llm")
    assert _attr(gen, TYPE) == "generation" and parent(gen).name == "triage"  # type: ignore[union-attr]
    assert _attr(gen, "langfuse.observation.model.name") == "recorded"
    assert _attr(gen, "langfuse.observation.usage_details") == {"input": 800, "output": 200}
    cost = _attr(gen, "langfuse.observation.cost_details")
    assert cost == {"input": pytest.approx(0.0008), "output": pytest.approx(0.0004)}
    assert "system" in _attr(gen, "langfuse.observation.input")

    # tool calls are tool observations under the node that made them
    tools = [s for s in spans if _attr(s, TYPE) == "tool"]
    assert tools and {parent(t).name for t in tools} <= {  # type: ignore[union-attr]
        "triage",
        "investigate",
        "plan",
        "correlate_changes",
    }
    assert "untrusted" in str(_attr(tools[0], "langfuse.observation.output"))
    assert names.count("investigation") == 1


async def test_approval_pause_is_not_an_error(engine: AsyncEngine, scenario: str) -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    from aegisops_agent.run import psycopg_url
    from aegisops_tools.db import DEFAULT_URL

    exporter = InMemorySpanExporter()
    tracing = _tracing(exporter)
    async with AsyncPostgresSaver.from_conn_string(psycopg_url(DEFAULT_URL)) as saver:
        await saver.setup()
        await run_investigation(
            engine=engine,
            llm=RecordedLLM.from_file(CASSETTE),
            ctx=ToolContext(scenario_id=scenario, frozen_now=NOW),
            service="payment",
            alert_summary=ALERT,
            thread_id=f"t-{scenario}-cp",
            checkpointer=saver,
            tracing=tracing,
        )
    tracing.shutdown()
    approvals = [s for s in exporter.get_finished_spans() if s.name == "approval"]
    # paused once (interrupt), then run again on resume: two spans, neither an error
    assert len(approvals) == 2
    assert _attr(approvals[0], "langfuse.observation.status_message") == "paused for approval"
    assert all(_attr(s, "langfuse.observation.level") != "ERROR" for s in approvals)


async def test_a_failed_model_call_is_an_error_generation() -> None:
    exporter = InMemorySpanExporter()
    tracing = _tracing(exporter)

    class Down:
        async def complete(self, node: str, system: str, user: str, schema: Any) -> Any:
            raise LLMError("all providers down", retryable=False)

    with pytest.raises(LLMError):
        await TracedLLM(Down(), tracing).complete("triage", "s", "u", BaseModel)
    tracing.shutdown()
    (gen,) = exporter.get_finished_spans()
    assert gen.name == "triage.llm" and gen.status.status_code.name == "ERROR"


async def test_tracing_off_without_keys_changes_nothing() -> None:
    off = Tracing.create(public_key=None, secret_key=None)
    assert not off.enabled
    placeholder = "sk"  # pragma: allowlist secret
    assert not Tracing.create(public_key="pk", secret_key=placeholder, enabled=False).enabled
    with off.observe("x") as obs, off.run(trace_id="0" * 32, name="r", input={}) as root:
        assert obs is NO_OBSERVATION and root is NO_OBSERVATION
        obs.update(output="ignored")
    off.score("0" * 32, "verified_confidence", 0.5)
    await off.flush()
    off.shutdown()

    class Echo:
        async def complete(self, node: str, system: str, user: str, schema: Any) -> Any:
            return LLMResult(schema(), "m", 1, 1)

    class Out(BaseModel):
        ok: bool = True

    result = await TracedLLM(Echo(), off).complete("triage", "s", "u", Out)
    assert result.model == "m"


def test_from_env_reads_the_sdk_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    assert not Tracing.from_env().enabled
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", f"pk-env-{uuid4().hex[:6]}")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("AEGIS_TRACING_ENABLED", "false")
    assert not Tracing.from_env().enabled


def test_trace_id_is_deterministic_and_clip_bounds_size() -> None:
    assert Tracing.trace_id("inc-1-abc") == Tracing.trace_id("inc-1-abc")
    assert len(Tracing.trace_id("x")) == 32 and Tracing.trace_id("x") != Tracing.trace_id("y")
    assert clip("short") == "short"
    assert clip("a" * 10, limit=4) == "aaaa… [10 chars]"
    assert clip({"k": "v"}) == {"k": "v"}
    assert clip({"k": "v" * 50}, limit=10).endswith("chars]")
