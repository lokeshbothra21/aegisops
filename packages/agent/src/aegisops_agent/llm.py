"""The only path to a language model (E3.5 router, ~100 lines of own code, ADR: no LiteLLM).

`LLMClient.complete()` takes a node name, system/user text and a Pydantic schema and
returns a validated instance plus token counts. Providers speak JSON mode:
  Gemini  generateContent with responseMimeType=application/json + responseSchema
  Groq    OpenAI-compatible chat.completions with response_format=json_schema
`Router` picks the model per node from config/models.yaml and falls back to the secondary
provider on 429/5xx/timeouts (logged as `model_fallback`). `RecordedLLM` replays canned
outputs for tests and CI (the plan's "cassettes").
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

import httpx
import structlog
import yaml
from pydantic import BaseModel, ValidationError

log = structlog.get_logger()
T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMResult[T: BaseModel]:
    value: T
    model: str
    tokens_in: int
    tokens_out: int
    fallback: bool = False


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMClient(Protocol):
    async def complete[T: BaseModel](
        self, node: str, system: str, user: str, schema: type[T]
    ) -> LLMResult[T]: ...


UNSUPPORTED_KEYWORDS = frozenset(
    {
        "title",
        "default",
        "additionalProperties",
        "pattern",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "$schema",
    }
)


def _schema_for(schema: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON Schema -> the subset Gemini's `responseSchema` accepts (Groq accepts it too).

    Inlines `$defs`, drops keywords Gemini rejects (validation still happens in Pydantic on
    our side), and turns `anyOf: [X, {type: null}]` into `X` + `nullable: true`.
    """
    raw = schema.model_json_schema()
    defs = raw.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, list):
            return [inline(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return inline(defs[node["$ref"].rsplit("/", 1)[-1]])
        if "anyOf" in node:
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            nullable = len(options) != len(node["anyOf"])
            if len(options) == 1:
                merged = {**{k: v for k, v in node.items() if k != "anyOf"}, **options[0]}
                out = inline(merged)
                if nullable:
                    out["nullable"] = True
                return out
        return {k: inline(v) for k, v in node.items() if k not in UNSUPPORTED_KEYWORDS}

    result: dict[str, Any] = inline(raw)
    return result


def _parse[T: BaseModel](schema: type[T], text: str) -> T:
    try:
        return schema.model_validate_json(text)
    except ValidationError as exc:
        raise LLMError(
            f"model output failed {schema.__name__} validation: {exc.errors()[0]['msg']}",
            retryable=True,
        ) from exc


# --- providers ----------------------------------------------------------------------


@dataclass
class GeminiClient:
    api_key: str
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    http: httpx.AsyncClient = field(default_factory=lambda: httpx.AsyncClient(timeout=60))

    async def complete[T: BaseModel](
        self, model: str, system: str, user: str, schema: type[T]
    ) -> LLMResult[T]:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _schema_for(schema),
                "temperature": 0.1,
            },
        }
        r = await self.http.post(
            f"{self.base_url}/models/{model}:generateContent",
            params={"key": self.api_key},
            json=body,
        )
        if r.status_code >= 400:
            raise LLMError(
                f"gemini {r.status_code}: {r.text[:200]}",
                retryable=r.status_code in (429, 500, 502, 503, 504),
            )
        data = r.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        usage = data.get("usageMetadata", {})
        return LLMResult(
            _parse(schema, text),
            model,
            int(usage.get("promptTokenCount", 0)),
            int(usage.get("candidatesTokenCount", 0)),
        )


@dataclass
class GroqClient:
    api_key: str
    base_url: str = "https://api.groq.com/openai/v1"
    http: httpx.AsyncClient = field(default_factory=lambda: httpx.AsyncClient(timeout=60))

    async def complete[T: BaseModel](
        self, model: str, system: str, user: str, schema: type[T]
    ) -> LLMResult[T]:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": _schema_for(schema)},
            },
            "temperature": 0.1,
        }
        r = await self.http.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
        )
        if r.status_code >= 400:
            raise LLMError(
                f"groq {r.status_code}: {r.text[:200]}",
                retryable=r.status_code in (429, 500, 502, 503, 504),
            )
        data = r.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return LLMResult(
            _parse(schema, text),
            model,
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
        )


type Provider = GeminiClient | GroqClient


# --- router ---------------------------------------------------------------------------


class RouteConfig(BaseModel):
    primary: str  # "gemini:gemini-2.5-flash"
    secondary: str | None = None  # "groq:llama-3.3-70b-versatile"
    nodes: dict[str, str] = {}  # node -> "provider:model" override for the primary


def load_models_config(path: str | Path) -> RouteConfig:
    with Path(path).open() as f:
        return RouteConfig.model_validate(yaml.safe_load(f))


@dataclass
class Router:
    """Chooses provider+model per node; retries once on the secondary if the primary is down."""

    config: RouteConfig
    providers: dict[str, Provider]
    on_fallback: Callable[[str, str], None] | None = None

    @classmethod
    def from_env(cls, config: RouteConfig) -> Router:
        providers: dict[str, Provider] = {}
        if key := os.environ.get("AEGIS_GEMINI_API_KEY"):
            providers["gemini"] = GeminiClient(api_key=key)
        if key := os.environ.get("AEGIS_GROQ_API_KEY"):
            providers["groq"] = GroqClient(api_key=key)
        return cls(config, providers)

    def _split(self, spec: str) -> tuple[str, str]:
        provider, _, model = spec.partition(":")
        if not model or provider not in self.providers:
            raise LLMError(
                f"no client for {spec!r} (set AEGIS_{provider.upper()}_API_KEY)", retryable=False
            )
        return provider, model

    async def complete[T: BaseModel](
        self, node: str, system: str, user: str, schema: type[T]
    ) -> LLMResult[T]:
        primary = self.config.nodes.get(node, self.config.primary)
        provider, model = self._split(primary)
        try:
            return await self.providers[provider].complete(model, system, user, schema)
        except (LLMError, httpx.HTTPError) as exc:
            retryable = getattr(exc, "retryable", True)
            if not retryable or not self.config.secondary:
                raise
            log.warning(
                "model_fallback",
                node=node,
                **{"from": primary, "to": self.config.secondary},
                error=str(exc)[:200],
            )
            if self.on_fallback:
                self.on_fallback(node, self.config.secondary)
            provider2, model2 = self._split(self.config.secondary)
            result = await self.providers[provider2].complete(model2, system, user, schema)
            result.fallback = True
            return result


# --- recorded (cassettes) ------------------------------------------------------------


@dataclass
class RecordedLLM:
    """Replays canned outputs per node: `responses[node]` is a list consumed in order (the
    last one repeats). Used by tests, CI and the public replay demo's smoke runs."""

    responses: dict[str, list[dict[str, Any]]]
    tokens_per_call: tuple[int, int] = (800, 200)
    calls: list[tuple[str, str]] = field(default_factory=list, repr=False)
    _cursor: dict[str, int] = field(default_factory=dict, repr=False)

    @classmethod
    def from_file(cls, path: str | Path) -> RecordedLLM:
        with Path(path).open() as f:
            data = yaml.safe_load(f) if str(path).endswith((".yml", ".yaml")) else json.load(f)
        return cls(responses={k: (v if isinstance(v, list) else [v]) for k, v in data.items()})

    async def complete[T: BaseModel](
        self, node: str, system: str, user: str, schema: type[T]
    ) -> LLMResult[T]:
        self.calls.append((node, user))
        options = self.responses.get(node)
        if not options:
            raise LLMError(f"no recorded response for node {node!r}", retryable=False)
        i = min(self._cursor.get(node, 0), len(options) - 1)
        self._cursor[node] = i + 1
        value = schema.model_validate(options[i])
        return LLMResult(value, "recorded", *self.tokens_per_call)
