"""Catalogue validation, flag toggling, and the runner against a fake API (E7.1, E7.2)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from aegisops_bench.catalogue import FlagFault, NoFault, OverlayFault, ScenarioSet, load_catalogue
from aegisops_bench.flags import current_variant, set_flag
from aegisops_bench.runner import Runner


def test_catalogue_matches_the_plan() -> None:
    cat = load_catalogue()
    keys = [s.key for s in cat.scenarios]
    assert keys == [f"S{i}" for i in range(1, 13)] + ["N1", "N2", "N3", "S13"]
    assert len(cat.by_set(ScenarioSet.dev)) == 8 and len(cat.by_set(ScenarioSet.held_out)) == 4
    assert len(cat.by_set(ScenarioSet.noise)) == 3 and len(cat.by_set(ScenarioSet.security)) == 1
    s1 = cat.get("S1")
    assert (
        isinstance(s1.fault, FlagFault)
        and s1.fault.flag == "paymentFailure"
        and s1.fault.revert == "off"
    )
    assert s1.expected_category == "dependency_errors" and s1.expected_actions == ["toggle_flag"]
    assert all(
        isinstance(s.fault, OverlayFault) and not s.fault.available
        for s in cat.by_set(ScenarioSet.held_out)
    )
    assert isinstance(cat.get("N3").fault, NoFault)
    s13 = cat.get("S13")
    assert s13.inject_log is not None and "ignore all previous instructions" in s13.inject_log.body
    with pytest.raises(KeyError):
        cat.get("S99")


def test_catalogue_rejects_inconsistent_entries(tmp_path: Path) -> None:
    bad = tmp_path / "s.yaml"
    bad.write_text("scenarios:\n  - {key: S1, title: t, set: dev, fault: {type: none}}\n")
    with pytest.raises(ValueError, match="expected_service"):
        load_catalogue(bad)
    bad.write_text(
        "scenarios:\n  - {key: N1, title: t, set: noise, fault: {type: none}, expected_actions: [restart_service]}\n"
    )
    with pytest.raises(ValueError, match="noise"):
        load_catalogue(bad)
    bad.write_text(
        "scenarios:\n  - {key: N1, title: t, set: noise, fault: {type: none}}\n  - {key: N1, title: t, set: noise, fault: {type: none}}\n"
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_catalogue(bad)


def test_set_flag_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "demo.flagd.json"
    p.write_text(
        json.dumps(
            {
                "flags": {
                    "paymentFailure": {"defaultVariant": "off", "variants": {"off": 0, "100%": 1}}
                }
            }
        )
    )
    assert set_flag(p, "paymentFailure", "100%") == "off"
    assert current_variant(p, "paymentFailure") == "100%"
    with pytest.raises(ValueError, match="variant"):
        set_flag(p, "paymentFailure", "50%")
    with pytest.raises(KeyError):
        set_flag(p, "nope", "off")


class FakeAPI:
    """Incidents appear N polls after the fault; resolve after revert; capture records the body."""

    def __init__(self, now: Any) -> None:
        self.now = now  # callable: the test's virtual clock
        self.polls = 0
        self.incident_opened: datetime | None = None
        self.reverted = False
        self.captures: list[dict] = []  # type: ignore[type-arg]
        self.ingested: list[dict] = []  # type: ignore[type-arg]

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/api/v1/incidents":
            self.polls += 1
            if self.polls >= 2 and self.incident_opened is None:
                self.incident_opened = self.now() + timedelta(seconds=45)
            items = []
            if self.incident_opened:
                items.append(
                    {
                        "id": 7,
                        "service": "payment",
                        "scenario_id": None,
                        "opened_at": self.incident_opened.isoformat(),
                        "status": "resolved" if self.reverted else "open",
                        "closed_at": None,
                    }
                )
            return httpx.Response(200, json={"items": items, "next_cursor": None})
        if path == "/api/v1/incidents/7":
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "status": "resolved" if self.reverted else "open",
                    "closed_at": (self.now() + timedelta(minutes=9)).isoformat()
                    if self.reverted
                    else None,
                },
            )
        if path == "/api/v1/admin/capture":
            assert req.headers["X-Admin-Token"] == "tok"
            self.captures.append(json.loads(req.read()))
            return httpx.Response(
                200,
                json={"key": "S1", "window_start": "x", "window_end": "y", "tagged": {"spans": 10}},
            )
        if path == "/ingest/v1/logs":
            self.ingested.append(json.loads(req.read()))
            return httpx.Response(200, json={})
        return httpx.Response(404)


async def test_runner_drives_a_flag_scenario_end_to_end(tmp_path: Path) -> None:
    flagd = tmp_path / "demo.flagd.json"
    flagd.write_text(
        json.dumps(
            {
                "flags": {
                    "paymentFailure": {"defaultVariant": "off", "variants": {"off": 0, "100%": 1}}
                }
            }
        )
    )
    t0 = datetime(2036, 3, 3, 12, 0, tzinfo=UTC)
    clock = {"t": t0}
    fake = FakeAPI(lambda: clock["t"])
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        clock["t"] += timedelta(seconds=s)
        if (
            fake.reverted is False
            and current_variant(flagd, "paymentFailure") == "off"
            and fake.incident_opened
        ):
            fake.reverted = True

    runner = Runner(
        api="http://api",
        admin_token="tok",
        flagd_path=flagd,
        http=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), base_url="http://api"),
        sleep=fake_sleep,
        now=lambda: clock["t"],
    )
    spec = load_catalogue().get("S13")  # S1 fault + injected log
    rep = await runner.run(spec)
    # poll 1 at fault time sees nothing, sleep 5, poll 2 opens the incident 45 s later: ttd = 50 s
    assert rep.incident_id == 7 and rep.ttd_s == 50.0
    assert current_variant(flagd, "paymentFailure") == "off"  # reverted
    assert rep.reverted_at is not None and rep.resolved_at is not None and rep.ended_at is not None
    assert rep.captured == {"spans": 10} and rep.notes == ["injected log line"]
    cap = fake.captures[0]
    assert (
        cap["key"] == "S13"
        and cap["expected_category"] == "dependency_errors"
        and cap["fault_type"] == "flag"
    )
    assert datetime.fromisoformat(cap["window_start"]) == t0
    assert fake.ingested[0]["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["body"][
        "stringValue"
    ].startswith("SYSTEM NOTICE")
    assert slept[0] == spec.timing.warmup_s and spec.timing.hold_s in slept


async def test_runner_reverts_even_when_no_incident_opens(tmp_path: Path) -> None:
    flagd = tmp_path / "demo.flagd.json"
    flagd.write_text(
        json.dumps(
            {
                "flags": {
                    "cartFailure": {"defaultVariant": "off", "variants": {"off": False, "on": True}}
                }
            }
        )
    )

    def never(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v1/admin/capture":
            return httpx.Response(
                200, json={"key": "S4", "window_start": "x", "window_end": "y", "tagged": {}}
            )
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    clock = {"t": datetime(2036, 3, 3, tzinfo=UTC)}

    async def fake_sleep(s: float) -> None:
        clock["t"] += timedelta(seconds=max(s, 1))

    runner = Runner(
        api="http://api",
        admin_token="tok",
        flagd_path=flagd,
        http=httpx.AsyncClient(transport=httpx.MockTransport(never), base_url="http://api"),
        sleep=fake_sleep,
        now=lambda: clock["t"],
    )
    spec = load_catalogue().get("S4")
    spec.timing.incident_timeout_s = 30
    rep = await runner.run(spec)
    assert rep.incident_id is None and "no incident within 30s" in rep.notes
    assert current_variant(flagd, "cartFailure") == "off"


async def test_runner_refuses_unbuilt_overlay(tmp_path: Path) -> None:
    runner = Runner(api="http://api", admin_token="tok", flagd_path=tmp_path / "x.json")
    with pytest.raises(RuntimeError, match="Week 8"):
        await runner.apply_fault(load_catalogue().get("S9"))
