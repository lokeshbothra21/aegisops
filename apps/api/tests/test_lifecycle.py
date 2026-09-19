"""Incident state machine: legal moves succeed, illegal ones raise, terminal states close (E2.4)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from aegisops_api.db import create_engine, create_session_factory
from aegisops_api.incidents.lifecycle import (
    ALLOWED,
    IllegalTransitionError,
    active_incident,
    open_incident,
    transition,
)
from aegisops_api.models import Incident
from aegisops_api.models import IncidentStatus as S
from aegisops_api.settings import Settings

NOW = datetime(2032, 1, 1, tzinfo=UTC)


def _inc(status: S) -> Incident:
    return Incident(opened_at=NOW, service="checkout", status=status, autonomy_level=1)


def test_happy_path_walks_every_state() -> None:
    i = _inc(S.open)
    for nxt in (S.investigating, S.awaiting_approval, S.remediating, S.resolved):
        transition(i, nxt, now=NOW)
    assert i.status is S.resolved
    assert i.closed_at == NOW


@pytest.mark.parametrize(
    ("frm", "to"),
    [
        (S.open, S.remediating),
        (S.resolved, S.open),
        (S.failed, S.investigating),
        (S.remediating, S.open),
    ],
)
def test_illegal_transitions_raise(frm: S, to: S) -> None:
    with pytest.raises(IllegalTransitionError) as e:
        transition(_inc(frm), to)
    assert e.value.current is frm and e.value.target is to


def test_terminal_states_have_no_exits() -> None:
    assert ALLOWED[S.resolved] == frozenset() and ALLOWED[S.failed] == frozenset()


def test_every_non_terminal_state_can_fail() -> None:
    for st in (S.open, S.investigating, S.awaiting_approval, S.remediating):
        assert S.failed in ALLOWED[st]


async def test_open_and_find_active_incident(settings: Settings) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    scenario = f"T-{uuid4().hex[:8]}"
    try:
        async with factory() as s:
            assert await active_incident(s, service="checkout", scenario_id=scenario) is None
            inc = await open_incident(
                s,
                service="checkout",
                alert_rule_id=None,
                summary="5xx > 5%",
                scenario_id=scenario,
                now=NOW,
            )
            await s.commit()
            found = await active_incident(s, service="checkout", scenario_id=scenario)
            assert found is not None and found.id == inc.id and found.status is S.open
            transition(found, S.resolved, now=NOW)
            await s.commit()
            assert await active_incident(s, service="checkout", scenario_id=scenario) is None
    finally:
        await engine.dispose()
