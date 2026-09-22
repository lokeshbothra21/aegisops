"""Container watcher: Docker API snapshots -> deploy / restart / scale change events (E2.2)."""

import json
from typing import Any

import httpx
from sqlalchemy import select

from aegisops_api.db import create_engine, create_session_factory
from aegisops_api.jobs.container_watcher import (
    ACTOR,
    ContainerState,
    ContainerWatcher,
    ServiceState,
    diff_snapshots,
    snapshot_from_inspects,
)
from aegisops_api.models import ChangeEvent, ChangeType
from aegisops_api.settings import Settings

PROJECT = "otel-demo-test"


def _inspect(
    cid: str,
    service: str,
    image: str,
    image_id: str,
    started: str,
    restarts: int = 0,
    running: bool = True,
) -> dict[str, Any]:
    return {
        "Id": cid,
        "Image": image_id,
        "RestartCount": restarts,
        "State": {"StartedAt": started, "Running": running},
        "Config": {
            "Image": image,
            "Labels": {
                "com.docker.compose.project": PROJECT,
                "com.docker.compose.service": service,
            },
        },
    }


def test_snapshot_groups_by_compose_service_and_ignores_unlabelled() -> None:
    snap = snapshot_from_inspects(
        [
            _inspect("a", "payment", "demo:3.0.0-payment", "sha256:aaa", "2026-09-21T10:00:00Z"),
            _inspect("b", "cart", "demo:3.0.0-cart", "sha256:bbb", "2026-09-21T10:00:00Z"),
            _inspect("c", "cart", "demo:3.0.0-cart", "sha256:bbb", "2026-09-21T10:00:01Z"),
            {"Id": "x", "Config": {"Labels": {}}},  # not compose-managed
        ]
    )
    assert set(snap) == {"payment", "cart"}
    assert snap["cart"].replicas == 2
    assert snap["payment"].containers[0].image_id == "sha256:aaa"


def test_diff_classifies_deploy_restart_and_scale() -> None:
    c = ContainerState("demo:3.0.0-payment", "sha256:aaa", "2026-09-21T10:00:00Z", 0)
    before = {
        "payment": ServiceState((c,)),
        "cart": ServiceState((c, c)),
        "email": ServiceState((c,)),
        "gone": ServiceState((c,)),
    }
    after = {
        "payment": ServiceState(
            (ContainerState("demo:3.0.1-payment", "sha256:ccc", "2026-09-21T11:00:00Z", 0),)
        ),
        "cart": ServiceState((c,)),
        "email": ServiceState((ContainerState(c.image, c.image_id, "2026-09-21T10:05:00Z", 1),)),
        "new": ServiceState((c,)),
    }
    events = {(t, s): (b, a) for t, s, b, a in diff_snapshots(before, after)}
    assert events[(ChangeType.deploy, "payment")] == (
        {"image": "demo:3.0.0-payment", "image_id": "sha256:aaa"},
        {"image": "demo:3.0.1-payment", "image_id": "sha256:ccc"},
    )
    assert events[(ChangeType.scale, "cart")] == ({"replicas": 2}, {"replicas": 1})
    assert events[(ChangeType.restart, "email")][1] == {
        "started_at": "2026-09-21T10:05:00Z",
        "restart_count": 1,
    }
    assert events[(ChangeType.scale, "gone")] == ({"replicas": 1}, {"replicas": 0})
    assert events[(ChangeType.scale, "new")] == ({"replicas": 0}, {"replicas": 1})
    assert (ChangeType.restart, "payment") not in events  # a deploy is not also a restart


def test_dead_container_is_a_scale_down_and_its_return_a_scale_up() -> None:
    up = ContainerState("img", "id", "T0", 0, running=True)
    dead = ContainerState("img", "id", "T0", 0, running=False)
    back = ContainerState("img", "id", "T1", 0, running=True)
    assert diff_snapshots(
        {"currency": ServiceState((up,))}, {"currency": ServiceState((dead,))}
    ) == [(ChangeType.scale, "currency", {"replicas": 1}, {"replicas": 0})]
    events = diff_snapshots(
        {"currency": ServiceState((dead,))}, {"currency": ServiceState((back,))}
    )
    assert [e[0] for e in events] == [ChangeType.scale, ChangeType.restart]


def test_diff_is_empty_when_nothing_changed() -> None:
    c = ContainerState("img", "id", "t", 0)
    snap = {"payment": ServiceState((c,))}
    assert diff_snapshots(snap, snap) == []


class FakeDocker:
    """Minimal Docker Engine API: list + inspect, with a mutable container table."""

    def __init__(self) -> None:
        self.containers: dict[str, dict[str, Any]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/containers/json":
            return httpx.Response(200, json=[{"Id": cid} for cid in self.containers])
        cid = path.split("/")[2]
        return httpx.Response(200, json=self.containers[cid])


async def test_watcher_records_restart_and_deploy_events(settings: Settings) -> None:
    from uuid import uuid4

    svc = f"payment-{uuid4().hex[:8]}"
    fake = FakeDocker()
    fake.containers["p1"] = _inspect("p1", svc, "demo:3.0.0-payment", "sha256:aaa", "T0")
    watcher = ContainerWatcher(socket_path="/nonexistent.sock", project=PROJECT)
    watcher._client = httpx.AsyncClient(
        transport=httpx.MockTransport(fake.handler), base_url="http://docker"
    )
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as s:
            assert await watcher.tick(s) == 0  # baseline
            fake.containers["p1"] = _inspect(
                "p1", svc, "demo:3.0.0-payment", "sha256:aaa", "T1", restarts=1
            )
            assert await watcher.tick(s) == 1
            fake.containers["p1"] = _inspect("p1", svc, "demo:3.0.1-payment", "sha256:bbb", "T2")
            assert await watcher.tick(s) == 1
            assert await watcher.tick(s) == 0
            await s.commit()
            rows = (
                await s.scalars(
                    select(ChangeEvent).where(ChangeEvent.service == svc).order_by(ChangeEvent.id)
                )
            ).all()
            from sqlalchemy import delete

            await s.execute(
                delete(ChangeEvent).where(ChangeEvent.service == svc)
            )  # not real live changes
            await s.commit()
    finally:
        await watcher.aclose()
        await engine.dispose()
    assert [r.type for r in rows] == [ChangeType.restart, ChangeType.deploy]
    assert rows[0].actor == ACTOR and rows[0].after["restart_count"] == 1
    assert rows[1].after == {"image": "demo:3.0.1-payment", "image_id": "sha256:bbb"}


async def test_unreachable_docker_is_logged_not_fatal(settings: Settings) -> None:
    watcher = ContainerWatcher(socket_path="/definitely/not/a.sock", project=PROJECT)
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as s:
            assert await watcher.tick(s) == 0
    finally:
        await watcher.aclose()
        await engine.dispose()


def test_filters_param_targets_the_compose_project() -> None:
    watcher = ContainerWatcher(socket_path="/x.sock", project="demo")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[])

    watcher._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://docker"
    )
    import asyncio

    asyncio.run(watcher.fetch_snapshot())
    assert json.loads(httpx.URL(seen[0]).params["filters"]) == {
        "label": ["com.docker.compose.project=demo"]
    }
