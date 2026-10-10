"""Runs over the API produce one Langfuse trace (E8.1): the start leg and the leg after the
approval share the trace id stored on the run, and the run is scored claimed vs verified."""

from typing import Any
from uuid import uuid4

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from aegisops_agent.tracing import Tracing
from tests.conftest import ADMIN_TOKEN
from tests.test_runs_api import _wait, agent_app  # noqa: F401 - fixture

HDR = {"X-Admin-Token": ADMIN_TOKEN}


async def test_both_legs_of_a_run_land_in_one_trace(agent_app) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    c, incident_id, _, _ = agent_app
    exporter = InMemorySpanExporter()
    tracing = Tracing.create(
        public_key=f"pk-test-{uuid4().hex[:8]}",
        secret_key="sk-test",  # pragma: allowlist secret
        base_url="http://localhost:9",
        environment="test",
        exporter=exporter,
    )
    scores: list[tuple[str, str, float]] = []
    tracing.score = lambda trace_id, name, value, comment=None: scores.append(  # type: ignore[method-assign]
        (trace_id, name, value)
    )
    manager = c._transport.app.state.runs
    manager.tracing = tracing

    run = (await c.post(f"/api/v1/incidents/{incident_id}/runs", json={}, headers=HDR)).json()
    await _wait(c, run["id"], "awaiting_approval")
    r = await c.post(f"/api/v1/runs/{run['id']}/approve", json={"by": "shreyas"}, headers=HDR)
    assert r.status_code == 200
    await _wait(c, run["id"], "succeeded")
    tracing.shutdown()

    expected = Tracing.trace_id(run["thread_id"])
    spans = exporter.get_finished_spans()
    assert {format(s.context.trace_id, "032x") for s in spans} == {expected}
    roots = {s.name: s for s in spans if s.name in ("investigation", "resume")}
    assert set(roots) == {"investigation", "resume"}
    attrs: dict[str, Any] = dict(roots["investigation"].attributes or {})
    assert attrs["session.id"] == f"incident-{incident_id}" and attrs["user.id"] == "admin"
    assert "execute" in [s.name for s in spans]  # the approved action ran in the resume leg

    async with manager.factory() as s:
        from aegisops_api.models import Run

        stored = await s.get(Run, run["id"])
    assert stored is not None and stored.langfuse_trace_id == expected
    assert (await c.get(f"/api/v1/runs/{run['id']}")).json()["langfuse_trace_id"] == expected
    # scored once (start leg), not again on resume
    assert scores == [
        (expected, "verified_confidence", 0.45),
        (expected, "claimed_confidence", 0.9),
    ]
