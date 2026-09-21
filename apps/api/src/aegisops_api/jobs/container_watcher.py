"""Container watcher (E2.2, observation half): deploys, restarts and scale changes of the
target's containers become `change_events`.

Source: the Docker Engine HTTP API over its Unix socket (httpx `uds=` transport; no
Docker SDK). Every tick lists containers of one Compose project and inspects each
for image, image id, start time and restart count; the snapshot is keyed by Compose
service. Diffs against the previous snapshot:

  image or image id changed   -> deploy   (before/after: image, image_id)
  started_at moved / restarts -> restart  (before/after: started_at, restart_count)
  RUNNING container count     -> scale    (before/after: replicas)

`replicas` counts running containers, so a container that dies and is not restarted
(e.g. `docker kill` without a policy kicking in, or `docker stop`) is a scale 1 -> 0
event, and its return is 0 -> 1. Found live on 21 Sep: a killed container produced no
event at all when only image/start-time/restart-count were compared.

The first snapshot is the baseline. Actor is `docker` because we cannot see who ran
the command; the agent's own actions (Week 6) will write their events directly.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from aegisops_api.models import ChangeEvent, ChangeType

log = structlog.get_logger()
ACTOR = "docker"
LABEL_PROJECT = "com.docker.compose.project"
LABEL_SERVICE = "com.docker.compose.service"


@dataclass(frozen=True)
class ContainerState:
    image: str
    image_id: str
    started_at: str
    restart_count: int
    running: bool = True


@dataclass(frozen=True)
class ServiceState:
    containers: tuple[ContainerState, ...]

    @property
    def replicas(self) -> int:
        """Running containers only: a dead container is not a replica."""
        return sum(1 for c in self.containers if c.running)


type Snapshot = dict[str, ServiceState]


def diff_snapshots(
    before: Snapshot, after: Snapshot
) -> list[tuple[ChangeType, str, dict[str, Any], dict[str, Any]]]:
    """(type, service, before, after) for every change between two snapshots."""
    events: list[tuple[ChangeType, str, dict[str, Any], dict[str, Any]]] = []
    for service in sorted(set(before) | set(after)):
        b, a = before.get(service), after.get(service)
        if b is None or a is None:
            events.append(
                (
                    ChangeType.scale,
                    service,
                    {"replicas": b.replicas if b else 0},
                    {"replicas": a.replicas if a else 0},
                )
            )
            continue
        if b.replicas != a.replicas:
            events.append(
                (ChangeType.scale, service, {"replicas": b.replicas}, {"replicas": a.replicas})
            )
        # compare the first container of each side: the demo runs one per service
        bc, ac = b.containers[0], a.containers[0]
        if (bc.image, bc.image_id) != (ac.image, ac.image_id):
            events.append(
                (
                    ChangeType.deploy,
                    service,
                    {"image": bc.image, "image_id": bc.image_id},
                    {"image": ac.image, "image_id": ac.image_id},
                )
            )
        elif bc.started_at != ac.started_at or bc.restart_count != ac.restart_count:
            events.append(
                (
                    ChangeType.restart,
                    service,
                    {"started_at": bc.started_at, "restart_count": bc.restart_count},
                    {"started_at": ac.started_at, "restart_count": ac.restart_count},
                )
            )
    return events


def snapshot_from_inspects(inspects: list[dict[str, Any]]) -> Snapshot:
    """Build the per-service snapshot from `GET /containers/{id}/json` bodies."""
    grouped: dict[str, list[ContainerState]] = {}
    for c in inspects:
        service = c.get("Config", {}).get("Labels", {}).get(LABEL_SERVICE)
        if not service:
            continue
        grouped.setdefault(service, []).append(
            ContainerState(
                image=c.get("Config", {}).get("Image", ""),
                image_id=c.get("Image", ""),
                started_at=c.get("State", {}).get("StartedAt", ""),
                restart_count=int(c.get("RestartCount", 0)),
                running=bool(c.get("State", {}).get("Running", False)),
            )
        )
    return {
        s: ServiceState(tuple(sorted(cs, key=lambda x: x.started_at))) for s, cs in grouped.items()
    }


@dataclass
class ContainerWatcher:
    socket_path: str
    project: str
    _client: httpx.AsyncClient | None = field(default=None, repr=False)
    _snapshot: Snapshot | None = field(default=None, repr=False)

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(uds=self.socket_path),
                base_url="http://docker",  # host is ignored over a Unix socket
                timeout=5.0,
            )
        return self._client

    async def fetch_snapshot(self) -> Snapshot:
        client = self.client()
        r = await client.get(
            "/containers/json",
            params={"all": "true", "filters": f'{{"label":["{LABEL_PROJECT}={self.project}"]}}'},
        )
        r.raise_for_status()
        inspects = []
        for c in r.json():
            detail = await client.get(f"/containers/{c['Id']}/json")
            detail.raise_for_status()
            inspects.append(detail.json())
        return snapshot_from_inspects(inspects)

    async def tick(self, session: AsyncSession) -> int:
        try:
            current = await self.fetch_snapshot()
        except (httpx.HTTPError, OSError) as exc:
            log.warning("container_watcher.unreachable", socket=self.socket_path, error=repr(exc))
            return 0
        if self._snapshot is None:
            self._snapshot = current
            log.info("container_watcher.baseline", project=self.project, services=len(current))
            return 0
        events = diff_snapshots(self._snapshot, current)
        now = datetime.now(UTC)
        for kind, service, before, after in events:
            session.add(
                ChangeEvent(
                    ts=now, type=kind, service=service, before=before, after=after, actor=ACTOR
                )
            )
            log.info(f"change_event.{kind.value}", service=service, **{"from": before, "to": after})
        self._snapshot = current
        return len(events)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
