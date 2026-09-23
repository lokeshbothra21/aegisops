"""The graph end to end with recorded model outputs against a seeded scenario (W3 exit criterion)."""

from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncEngine

from aegisops_agent.llm import RecordedLLM
from aegisops_agent.run import psycopg_url, run_investigation
from aegisops_agent.schemas import Budget, RootCauseCategory
from aegisops_agent.tools import NODE_TOOLS, ToolRunner, tool_catalogue
from aegisops_tools.context import ToolContext
from aegisops_tools.db import DEFAULT_URL, session_factory
from aegisops_tools.testing import NOW, delete_scenario, seed_payment_failure

CASSETTE = "packages/agent/tests/cassettes/s1_payment_failure.yaml"
ALERT = "high-error-rate: error_rate for payment = 0.83 > 0.05 over 120s for 2 evaluations"


@pytest.fixture
async def scenario(engine: AsyncEngine):  # type: ignore[no-untyped-def]
    sc = f"T-{uuid4().hex[:8]}"
    await seed_payment_failure(engine, sc)
    try:
        yield sc
    finally:
        await delete_scenario(engine, sc)


async def test_s1_end_to_end_with_recorded_model(engine: AsyncEngine, scenario: str) -> None:
    llm = RecordedLLM.from_file(CASSETTE)
    rc, state = await run_investigation(
        engine=engine,
        llm=llm,
        ctx=ToolContext(scenario_id=scenario, frozen_now=NOW),
        service="payment",
        alert_summary=ALERT,
        thread_id=f"t-{scenario}",
    )
    assert rc.category is RootCauseCategory.dependency_errors and rc.service == "payment"
    # the cassette cites one real change and one PLACEHOLDER trace id: the verifier keeps the
    # change, drops the fabricated span, and halves the model's confidence
    assert rc.claimed_confidence == 0.9 and rc.confidence == pytest.approx(0.45)
    assert rc.verification.cited == 2 and rc.verification.verified == 1
    assert "not a trace id" in rc.verification.dropped[0].reason
    assert rc.partial is False
    corr = state["correlation"]
    assert corr["events"][0]["summary"] == "paymentFailure: off -> 100%"
    assert corr["temporal_score"] > 0.8
    assert [e["node"] for e in state["events"] if e["type"] == "output"] == [
        "triage",
        "plan",
        "investigate",
        "correlate_changes",
        "root_cause",
        "verify_evidence",
    ]
    calls = [c[0] for c in llm.calls]
    assert calls == ["triage", "plan", "investigate", "root_cause"]
    records = state["tool_records"]  # type: ignore[typeddict-item]
    assert {r["tool"] for r in records if r["hypothesis_id"] == "H1"} == {
        "get_recent_changes",
        "get_error_traces",
    }
    assert all(r["ok"] for r in records), [r for r in records if not r["ok"]]
    usage = state["usage"]
    assert (
        usage["llm_calls"] == 4
        and usage["tool_calls"] == 3 + 2 + 5
        and usage["tokens_in"] == 4 * 800
    )
    plan_input = next(u for n, u in llm.calls if n == "plan")
    assert 'untrusted=\\"true\\"' in plan_input or 'untrusted="true"' in plan_input
    assert "paymentFailure" in plan_input  # the flag flip reached the planner
    rc_input = next(u for n, u in llm.calls if n == "root_cause")
    assert "change_correlation" in rc_input and "paymentFailure: off -> 100%" in rc_input


async def test_follow_up_round_runs_once_and_is_budgeted(
    engine: AsyncEngine, scenario: str
) -> None:
    data = {k: v[0] for k, v in RecordedLLM.from_file(CASSETTE).responses.items()}
    first = dict(data["investigate"])
    first["follow_up"] = [
        {
            "tool": "compare_windows",
            "args": {"service": "payment", "after_minutes": 5, "before_minutes": 30},
        }
    ]
    second = dict(data["investigate"])  # no follow_up -> loop ends
    llm = RecordedLLM(
        responses={**{k: [v] for k, v in data.items()}, "investigate": [first, second]}
    )
    rc, state = await run_investigation(
        engine=engine,
        llm=llm,
        ctx=ToolContext(scenario_id=scenario, frozen_now=NOW),
        service="payment",
        alert_summary=ALERT,
        thread_id=f"t-{scenario}-fu",
    )
    inv = [c for c in llm.calls if c[0] == "investigate"]
    assert len(inv) == 2 and "follow_up_round" in inv[1][1]
    assert (
        "follow-up" in state["tool_results"]
        and state["tool_results"]["follow-up"][0]["tool"] == "compare_windows"
    )
    assert state["usage"]["llm_calls"] == 5 and state["usage"]["tool_calls"] == 3 + 2 + 5 + 1
    assert next(e for e in state["events"] if e["node"] == "investigate")["follow_up_rounds"] == 1
    assert rc.category is RootCauseCategory.dependency_errors


async def test_budget_breach_short_circuits_to_a_partial_root_cause(
    engine: AsyncEngine, scenario: str
) -> None:
    llm = RecordedLLM.from_file(CASSETTE)
    rc, state = await run_investigation(
        engine=engine,
        llm=llm,
        ctx=ToolContext(scenario_id=scenario, frozen_now=NOW),
        service="payment",
        alert_summary=ALERT,
        thread_id=f"t-{scenario}-b",
        budget=Budget(max_tool_calls=4),
    )
    assert state["budget_exceeded"] == "tool_calls"
    assert rc.partial is True
    nodes = [c[0] for c in llm.calls]
    assert "investigate" not in nodes and nodes[-1] == "root_cause"  # plan's tools tripped the cap
    assert "verified_root_cause" in state  # the verifier still runs on a partial report


async def test_node_scoped_authorization_and_budget_accounting(
    engine: AsyncEngine, scenario: str
) -> None:
    runner = ToolRunner(
        factory=session_factory(engine),
        ctx=ToolContext(scenario_id=scenario, frozen_now=NOW),
        budget=Budget(max_tool_calls=3),
    )
    denied = await runner.call(
        "triage", "-", "get_recent_changes", {}
    )  # triage may not read changes
    assert "not available to node" in denied and runner.usage.tool_calls == 1
    unknown = await runner.call("investigate", "H1", "drop_tables", {})
    assert (
        "not available" in unknown and runner.usage.tool_calls == 2
    )  # unknown tools are denied the same way
    ok = await runner.call(
        "investigate",
        "H1",
        "get_error_rate",
        {"service": "payment", "window_minutes": 5, "bogus": 1},
    )
    assert '"error_rate"' in ok and runner.records[-1].args == {
        "service": "payment",
        "window_minutes": 5,
    }
    from aegisops_agent.tools import BudgetExceededError

    with pytest.raises(BudgetExceededError):
        await runner.call("investigate", "H1", "get_error_rate", {"service": "payment"})
    assert "search_similar_incidents" not in NODE_TOOLS["investigate"]
    assert {t["tool"] for t in tool_catalogue("plan")} == NODE_TOOLS["plan"]


async def test_checkpointer_persists_state_per_thread(engine: AsyncEngine, scenario: str) -> None:
    thread = f"t-{scenario}-cp"
    async with AsyncPostgresSaver.from_conn_string(psycopg_url(DEFAULT_URL)) as saver:
        await saver.setup()
        _rc, _state = await run_investigation(
            engine=engine,
            llm=RecordedLLM.from_file(CASSETTE),
            ctx=ToolContext(scenario_id=scenario, frozen_now=NOW),
            service="payment",
            alert_summary=ALERT,
            thread_id=thread,
            checkpointer=saver,
        )
        tup = await saver.aget_tuple({"configurable": {"thread_id": thread}})
    assert tup is not None
    values = tup.checkpoint["channel_values"]
    assert values["verified_root_cause"]["category"] == "dependency_errors"
    assert values["triage"]["service"] == "payment"
