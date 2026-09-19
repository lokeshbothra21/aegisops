"""GET /api/v1/incidents list (filter, cursor) and detail (E2.4, §7)."""

from datetime import UTC, datetime
from uuid import uuid4

from httpx import AsyncClient

from aegisops_api.db import create_engine, create_session_factory
from aegisops_api.incidents.lifecycle import open_incident, transition
from aegisops_api.models import IncidentStatus as S
from aegisops_api.settings import Settings


async def _seed(settings: Settings, n: int) -> tuple[str, list[int]]:
    scenario = f"T-{uuid4().hex[:8]}"
    engine = create_engine(settings)
    ids = []
    try:
        async with create_session_factory(engine)() as s:
            for i in range(n):
                inc = await open_incident(
                    s,
                    service=f"svc-{scenario}",
                    alert_rule_id=None,
                    summary=f"#{i}",
                    scenario_id=scenario,
                    now=datetime(2033, 1, 1, i, tzinfo=UTC),
                )
                if i == 0:
                    transition(inc, S.resolved)
                ids.append(inc.id)
            await s.commit()
    finally:
        await engine.dispose()
    return scenario, ids


async def test_list_filters_by_status_and_paginates(
    client: AsyncClient, settings: Settings
) -> None:
    _, ids = await _seed(settings, 5)
    r = await client.get("/api/v1/incidents", params={"limit": 2})
    assert r.status_code == 200
    page = r.json()
    assert len(page["items"]) == 2 and page["next_cursor"] == page["items"][-1]["id"]
    assert page["items"][0]["id"] > page["items"][1]["id"]  # newest first
    r2 = await client.get("/api/v1/incidents", params={"limit": 2, "cursor": page["next_cursor"]})
    assert all(i["id"] < page["next_cursor"] for i in r2.json()["items"])
    r3 = await client.get("/api/v1/incidents", params={"status": "resolved", "limit": 100})
    assert ids[0] in {i["id"] for i in r3.json()["items"]}
    assert all(i["status"] == "resolved" for i in r3.json()["items"])


async def test_detail_and_404(client: AsyncClient, settings: Settings) -> None:
    _, ids = await _seed(settings, 1)
    r = await client.get(f"/api/v1/incidents/{ids[0]}")
    assert r.status_code == 200
    body = r.json()
    assert (
        body["status"] == "resolved"
        and body["closed_at"] is not None
        and body["autonomy_level"] == 1
    )
    r = await client.get("/api/v1/incidents/999999999")
    assert r.status_code == 404
    assert r.json()["title"] == "Incident not found"


async def test_bad_status_filter_is_422_problem(client: AsyncClient) -> None:
    r = await client.get("/api/v1/incidents", params={"status": "bogus"})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
