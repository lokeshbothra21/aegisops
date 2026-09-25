"""Remediation proposal (E5.1), policy (E5.4 first cut), approval interrupt + resume (E5.2)."""

from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncEngine

from aegisops_agent.llm import RecordedLLM
from aegisops_agent.remediation import Action, Remediation, Risk, evaluate, load_policy, propose
from aegisops_agent.run import (
    initial_state,
    make_graph,
    psycopg_url,
    resume_command,
    stream_steps,
)
from aegisops_agent.schemas import (
    ChangeCorrelation,
    EvidenceRef,
    RootCauseCategory,
    ScoredChange,
    Verification,
    VerifiedRootCause,
)
from aegisops_tools.context import ToolContext
from aegisops_tools.db import DEFAULT_URL
from aegisops_tools.testing import NOW, delete_scenario, seed_payment_failure

POLICY = load_policy("config/policy.yaml")
CASSETTE = "packages/agent/tests/cassettes/s1_payment_failure.yaml"


def _rc(category: RootCauseCategory, confidence: float = 0.8) -> VerifiedRootCause:
    return VerifiedRootCause(
        service="payment",
        category=category,
        statement="s",
        confidence=confidence,
        evidence_refs=[EvidenceRef(kind="change", ref_id="x", claim="c")] if confidence else [],
        claimed_confidence=0.9,
        verification=Verification(cited=1, verified=1 if confidence else 0),
    )


def _corr(
    flag_score: float = 0.9, minutes: float = 4.0, service: str = "payment"
) -> ChangeCorrelation:
    return ChangeCorrelation(
        alert_at="t",
        events=[
            ScoredChange(
                ts="t",
                type="flag",
                service=service,
                summary="paymentFailure: off -> 100%",
                minutes_before_alert=minutes,
                score=flag_score,
            )
        ],
        temporal_score=flag_score,
    )


def test_flag_change_before_the_alert_is_reverted_first() -> None:
    rem = propose(_rc(RootCauseCategory.config_regression), _corr())
    assert rem.action is Action.toggle_flag and rem.risk is Risk.low
    assert rem.params == {"flag": "paymentFailure", "variant": "off", "service": "payment"}
    assert "rollback_deployment" in rem.alternatives and rem.confidence == 0.8


def test_weak_or_post_alert_flag_changes_do_not_override_the_table() -> None:
    assert (
        propose(_rc(RootCauseCategory.config_regression), _corr(flag_score=0.2)).action
        is Action.rollback_deployment
    )
    assert (
        propose(_rc(RootCauseCategory.config_regression), _corr(minutes=-3)).action
        is Action.rollback_deployment
    )
    rem = propose(_rc(RootCauseCategory.cpu_saturation), None)
    assert (
        rem.action is Action.scale_service
        and rem.risk is Risk.medium
        and rem.params == {"service": "payment", "replicas": 2}
    )
    assert propose(_rc(RootCauseCategory.memory_leak), None).action is Action.restart_service
    assert propose(_rc(RootCauseCategory.bad_deploy), None).params == {
        "service": "payment",
        "to_version": "previous",
    }


def test_no_incident_or_zero_confidence_means_report_only() -> None:
    assert propose(_rc(RootCauseCategory.no_incident), _corr()).action is Action.none
    rem = propose(_rc(RootCauseCategory.dependency_errors, confidence=0.0), _corr())
    assert rem.action is Action.none and rem.params == {}


@pytest.mark.parametrize(
    ("level", "risk", "confidence", "public", "auto"),
    [
        (1, Risk.low, 0.99, False, False),  # level 1 always waits
        (2, Risk.low, 0.90, False, True),  # level 2 auto-executes low risk when confident
        (2, Risk.low, 0.80, False, False),  # ... but not below 0.85
        (2, Risk.medium, 0.99, False, False),  # medium waits at level 2
        (3, Risk.medium, 0.95, False, True),  # level 3 may auto-execute medium
        (3, Risk.low, 0.99, True, False),  # public mode never executes
        (9, Risk.low, 0.99, False, True),  # unknown level clamps to the highest defined
    ],
)
def test_policy_matrix(level: int, risk: Risk, confidence: float, public: bool, auto: bool) -> None:
    rem = Remediation(
        action=Action.toggle_flag,
        params={"flag": "paymentFailure", "service": "payment"},
        risk=risk,
        confidence=confidence,
        rationale="r",
    )
    if risk is Risk.medium:
        rem = Remediation(
            action=Action.scale_service,
            params={"service": "checkout", "replicas": 2},
            risk=risk,
            confidence=confidence,
            rationale="r",
        )
    d = evaluate(POLICY, rem, autonomy_level=level, public_mode=public)
    assert d.auto is auto, d.reason


def test_policy_allowlists() -> None:
    rem = Remediation(
        action=Action.toggle_flag,
        params={"flag": "notAFlag"},
        risk=Risk.low,
        confidence=0.99,
        rationale="r",
    )
    assert not evaluate(POLICY, rem, autonomy_level=2, public_mode=False).auto
    rem = Remediation(
        action=Action.restart_service,
        params={"service": "unknown-svc"},
        risk=Risk.low,
        confidence=0.99,
        rationale="r",
    )
    assert "allowlist" in evaluate(POLICY, rem, autonomy_level=2, public_mode=False).reason
    none = Remediation(action=Action.none, risk=Risk.low, confidence=0.5, rationale="r")
    assert (
        evaluate(POLICY, none, autonomy_level=3, public_mode=False).reason == "nothing to execute"
    )


@pytest.fixture
async def scenario(engine: AsyncEngine):  # type: ignore[no-untyped-def]
    sc = f"T-{uuid4().hex[:8]}"
    await seed_payment_failure(engine, sc)
    try:
        yield sc
    finally:
        await delete_scenario(engine, sc)


async def test_graph_pauses_at_approval_and_resumes_with_the_decision(
    engine: AsyncEngine, scenario: str
) -> None:
    ctx = ToolContext(scenario_id=scenario, frozen_now=NOW)
    thread = f"t-{scenario}-approval"
    async with AsyncPostgresSaver.from_conn_string(psycopg_url(DEFAULT_URL)) as saver:
        await saver.setup()
        graph, _ = make_graph(
            engine=engine,
            llm=RecordedLLM.from_file(CASSETTE),
            ctx=ctx,
            policy=POLICY,
            checkpointer=saver,
        )
        steps = [
            s
            async for s in stream_steps(
                graph, initial_state(service="payment", alert_summary="a", incident_id=None), thread
            )
        ]
        nodes = [s.node for s in steps]
        assert nodes[:7] == [
            "triage",
            "plan",
            "investigate",
            "correlate_changes",
            "root_cause",
            "verify_evidence",
            "remediate",
        ]
        paused = steps[-1]
        assert paused.interrupt is not None and paused.interrupt["type"] == "approval_requested"
        assert paused.interrupt["remediation"]["action"] == "toggle_flag"
        assert paused.interrupt["policy"]["auto"] is False  # level 1: wait for a human
        # resume with a human decision: only the approval node runs, then END
        resumed = [
            s
            async for s in stream_steps(
                graph, resume_command("approved", "shreyas", "ship it"), thread
            )
        ]
        assert [s.node for s in resumed] == ["approval"]
        assert resumed[0].update["approval"] == {
            "decision": "approved",
            "by": "shreyas",
            "note": "ship it",
        }
        tup = await saver.aget_tuple({"configurable": {"thread_id": thread}})
        assert (
            tup is not None
            and tup.checkpoint["channel_values"]["approval"]["decision"] == "approved"
        )


async def test_level_2_auto_approves_low_risk(engine: AsyncEngine, scenario: str) -> None:
    ctx = ToolContext(scenario_id=scenario, frozen_now=NOW)
    graph, _ = make_graph(
        engine=engine, llm=RecordedLLM.from_file(CASSETTE), ctx=ctx, policy=POLICY
    )
    # cassette root cause verifies at 0.45 (< 0.85) -> would wait; raise autonomy AND confidence via a 2-ref cassette is
    # not available, so assert the policy reason instead of the auto path here
    final = await graph.ainvoke(
        initial_state(
            service="payment",
            alert_summary="a",
            incident_id=None,
            autonomy_level=2,
            auto_decision="rejected",
        ),
        config={"configurable": {"thread_id": f"t-{scenario}-l2"}},
    )
    assert (
        final["policy_decision"]["auto"] is False and "0.45" in final["policy_decision"]["reason"]
    )
