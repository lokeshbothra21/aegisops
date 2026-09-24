"""Schemas validate the way the plan promises; the router falls back; cassettes replay."""

import httpx
import pytest
from pydantic import ValidationError

from aegisops_agent.llm import (
    GeminiClient,
    GroqClient,
    LLMError,
    RecordedLLM,
    RouteConfig,
    Router,
    _schema_for,
)
from aegisops_agent.schemas import Hypotheses, RootCause, RootCauseCategory, Triage

CASSETTE = "packages/agent/tests/cassettes/s1_payment_failure.yaml"


def test_root_cause_confidence_and_category_are_constrained() -> None:
    RootCause(
        service="payment",
        category=RootCauseCategory.dependency_errors,
        statement="x",
        confidence=0.5,
    )
    with pytest.raises(ValidationError):
        RootCause(service="payment", category="made_up", statement="x", confidence=0.5)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RootCause(
            service="payment", category=RootCauseCategory.app_bug, statement="x", confidence=1.5
        )


def test_hypotheses_ids_unique_and_capped() -> None:
    with pytest.raises(ValidationError, match="unique"):
        Hypotheses.model_validate(
            {"items": [{"id": "H1", "statement": "a"}, {"id": "H1", "statement": "b"}]}
        )
    with pytest.raises(ValidationError):
        Hypotheses.model_validate(
            {"items": [{"id": f"H{i}", "statement": "a"} for i in range(1, 5)]}
        )


def test_json_schema_is_gemini_safe() -> None:
    """Gemini's responseSchema subset: no $ref/$defs, no additionalProperties/pattern/min-max
    keywords, Optional[X] becomes X + nullable (found live: 400 'Unknown name additionalProperties')."""
    from aegisops_agent.schemas import RootCause

    for model in (Hypotheses, RootCause):
        dumped = str(_schema_for(model))
        for bad in (
            "$ref",
            "$defs",
            "'title'",
            "additionalProperties",
            "'pattern'",
            "minLength",
            "maxLength",
            "'minimum'",
            "'maximum'",
            "anyOf",
        ):
            assert bad not in dumped, f"{model.__name__}: {bad}"
    h = _schema_for(Hypotheses)
    args = h["properties"]["items"]["items"]["properties"]["tools_to_run"]["items"]["properties"][
        "args"
    ]
    assert args["type"] == "object" and args["properties"]["window_minutes"] == {
        "type": "integer",
        "nullable": True,
    }
    rc = _schema_for(RootCause)
    assert rc["properties"]["evidence_refs"]["items"]["properties"]["claimed_value"] == {
        "type": "number",
        "nullable": True,
        "description": "a number the claim asserts, if any",
    }


async def test_recorded_llm_replays_in_order_then_repeats() -> None:
    llm = RecordedLLM.from_file(CASSETTE)
    t1 = await llm.complete("triage", "sys", "user", Triage)
    assert t1.value.service == "payment" and t1.model == "recorded" and t1.tokens_in == 800
    t2 = await llm.complete("triage", "sys", "user", Triage)
    assert t2.value == t1.value  # last response repeats
    with pytest.raises(LLMError, match="no recorded response"):
        await llm.complete("nope", "sys", "user", Triage)


def _gemini_ok(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7},
        },
    )


def _groq_ok(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        },
    )


TRIAGE_JSON = '{"service":"payment","symptom":"error_rate","window_minutes":15,"summary":"s"}'


async def test_router_uses_node_override_and_falls_back_on_429() -> None:
    seen: list[str] = []

    def gemini_handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        return httpx.Response(429, json={"error": "quota"})

    def groq_handler(req: httpx.Request) -> httpx.Response:
        seen.append("groq:" + req.read().decode()[:40])
        return _groq_ok(TRIAGE_JSON)

    gemini = GeminiClient(
        api_key="k", http=httpx.AsyncClient(transport=httpx.MockTransport(gemini_handler))
    )
    groq = GroqClient(
        api_key="k", http=httpx.AsyncClient(transport=httpx.MockTransport(groq_handler))
    )
    cfg = RouteConfig(
        primary="gemini:flash", secondary="groq:llama", nodes={"triage": "gemini:flash-lite"}
    )
    fallbacks: list[tuple[str, str]] = []
    router = Router(
        cfg, {"gemini": gemini, "groq": groq}, on_fallback=lambda n, m: fallbacks.append((n, m))
    )
    r = await router.complete("triage", "sys", "user", Triage)
    assert r.value.service == "payment" and r.fallback is True and r.model == "llama"
    assert seen[0].endswith("/models/flash-lite:generateContent")  # node override honoured
    assert fallbacks == [("triage", "groq:llama")]


async def test_router_does_not_fall_back_on_non_retryable_errors() -> None:
    gemini = GeminiClient(
        api_key="k",
        http=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(400, text="bad request"))
        ),
    )
    router = Router(
        RouteConfig(primary="gemini:flash", secondary="groq:llama"),
        {"gemini": gemini, "groq": GroqClient(api_key="k")},
    )
    with pytest.raises(LLMError, match="gemini 400"):
        await router.complete("plan", "sys", "user", Triage)


async def test_invalid_model_json_is_a_retryable_error() -> None:
    gemini = GeminiClient(
        api_key="k",
        http=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: _gemini_ok('{"service": 1}'))
        ),
    )
    with pytest.raises(LLMError, match="validation") as e:
        await gemini.complete("flash", "sys", "user", Triage)
    assert e.value.retryable is True


def test_router_from_env_needs_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEGIS_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AEGIS_GROQ_API_KEY", raising=False)
    router = Router.from_env(RouteConfig(primary="gemini:flash"))
    with pytest.raises(LLMError, match="AEGIS_GEMINI_API_KEY"):
        router._split("gemini:flash")


async def test_router_alternates_providers_with_backoff_and_retry_after() -> None:
    """Gemini 503, Groq 429 with Retry-After 2, Gemini 503, Groq OK: four attempts, three waits."""
    calls: list[str] = []
    waits: list[float] = []

    def gemini_handler(req: httpx.Request) -> httpx.Response:
        calls.append("gemini")
        return httpx.Response(503, json={"error": "high demand"})

    def groq_handler(req: httpx.Request) -> httpx.Response:
        calls.append("groq")
        if calls.count("groq") == 1:
            return httpx.Response(429, json={"error": "tpm"}, headers={"retry-after": "2"})
        return _groq_ok(TRIAGE_JSON)

    async def fake_sleep(s: float) -> None:
        waits.append(s)

    router = Router(
        RouteConfig(primary="gemini:flash", secondary="groq:llama"),
        {
            "gemini": GeminiClient(
                api_key="k", http=httpx.AsyncClient(transport=httpx.MockTransport(gemini_handler))
            ),
            "groq": GroqClient(
                api_key="k", http=httpx.AsyncClient(transport=httpx.MockTransport(groq_handler))
            ),
        },
        sleep=fake_sleep,
    )
    r = await router.complete("plan", "sys", "user", Triage)
    assert r.value.service == "payment" and r.fallback is True and router.fallbacks == 1
    assert calls == ["gemini", "groq", "gemini", "groq"]
    assert len(waits) == 3 and waits[1] == 2.0 and 1.5 <= waits[0] <= 2.5 and 4.5 <= waits[2] <= 5.5


async def test_router_gives_up_after_max_attempts() -> None:
    from aegisops_agent.llm import MAX_ATTEMPTS

    async def no_sleep(s: float) -> None:
        pass

    down = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(503, text="down"))
    )
    router = Router(
        RouteConfig(primary="gemini:flash", secondary="groq:llama"),
        {
            "gemini": GeminiClient(api_key="k", http=down),
            "groq": GroqClient(api_key="k", http=down),
        },
        sleep=no_sleep,
    )
    with pytest.raises(LLMError, match=f"all {MAX_ATTEMPTS} attempts failed") as e:
        await router.complete("plan", "sys", "user", Triage)
    assert e.value.retryable is False
