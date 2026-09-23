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


def test_json_schema_is_inlined_without_refs_or_titles() -> None:
    schema = _schema_for(Hypotheses)
    dumped = str(schema)
    assert "$ref" not in dumped and "$defs" not in dumped and "'title'" not in dumped
    assert schema["properties"]["items"]["items"]["properties"]["tools_to_run"]["type"] == "array"


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
