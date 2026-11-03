"""Одинаковые параметры модели в нативном и структурированном каналах Ариса."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import BaseModel, ValidationError
from qdrant_client import AsyncQdrantClient

from src.clients.model_options import agent_request_fields
from src.clients.vision import VisionClient
from src.core.settings import EmbeddingSettings, KnowledgeSettings, LlmSettings, get_settings
from src.exceptions.clients import LlmClientError
from src.knowledge.runtime import build_knowledge_runtime


class ProbeAnswer(BaseModel):
    answer: str


async def test_json_composition_preserves_model_options_and_keeps_vision_independent():
    """Роутер и WriteIntent получают те же параметры, vision остаётся отдельной моделью."""
    requests = []

    def reply(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"answer":"готово"}'}}]}
        )

    base = get_settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        qdrant = AsyncQdrantClient(location=":memory:")
        try:
            for name, enabled, effort, provider in [
                ("google/gemini-3.7-flash", None, None, ""),
                ("qwen/qwen3.8-flash", True, None, "alibaba"),
                ("qwen/qwen3.8-flash", False, None, "alibaba"),
                ("qwen/qwen3.5-flash-02-23", False, None, "alibaba"),
                ("openai/gpt-6-luna", False, "none", "openai"),
            ]:
                llm = LlmSettings(
                    _env_file=None,
                    llm_api_key="test",
                    llm_api_url="https://polza.ai/api/v1/chat/completions",
                    agent_model=name,
                    agent_reasoning_enabled=enabled,
                    agent_reasoning_effort=effort,
                    agent_provider=provider,
                    agent_request_timeout_seconds=None,
                    agent_request_attempts=None,
                    llm_retries=1,
                )
                runtime = build_knowledge_runtime(
                    settings=base.model_copy(update={"llm": llm}),
                    http_client=http,
                    qdrant_client=qdrant,
                )
                answer = await runtime.llm_client.get_structured_response(
                    system_prompt="Верни JSON.", content="Проверка", schema=ProbeAnswer
                )
                assert answer.answer == "готово"
                body = requests[-1]
                assert body["model"] == name
                assert body["response_format"] == {"type": "json_object"}
                assert body["max_completion_tokens"] == 4000
                assert bool(body.get("prompt_cache_key"))
                expected = agent_request_fields(llm)
                assert {
                    key: body[key] for key in ("provider", "reasoning") if key in body
                } == expected
                if enabled is not None:
                    assert body["reasoning"]["enabled"] is enabled
                if effort:
                    assert body["reasoning"]["effort"] == effort
                if provider:
                    assert body["provider"] == {"only": [provider], "allow_fallbacks": False}
                if hasattr(runtime.vision, "model"):
                    assert runtime.vision.model == llm.vision_model
                    assert not hasattr(runtime.vision, "request_fields")
        finally:
            await qdrant.close()


def test_agent_request_limits_validate_values_and_fall_back_independently():
    values = {
        "_env_file": None,
        "llm_api_key": "test",
        "llm_api_url": "https://llm.test/v1/chat/completions",
        "llm_timeout": 47,
        "llm_retries": 4,
        "agent_request_timeout_seconds": None,
        "agent_request_attempts": None,
    }
    for overrides, expected in [
        ({}, (47.0, 4)),
        ({"agent_request_timeout_seconds": 0.5}, (0.5, 4)),
        ({"agent_request_attempts": 1}, (47.0, 1)),
    ]:
        settings = LlmSettings(**{**values, **overrides})
        assert (settings.agent_timeout_seconds, settings.agent_attempts) == expected
    for field, invalid in [
        ("agent_request_timeout_seconds", 0),
        ("agent_request_timeout_seconds", -1),
        ("agent_request_attempts", 0),
        ("agent_request_attempts", -1),
    ]:
        with pytest.raises(ValidationError):
            LlmSettings(**{**values, field: invalid})


@pytest.mark.parametrize(
    ("agent_options", "read_timeout", "attempts"),
    [
        ({"agent_request_timeout_seconds": 30, "agent_request_attempts": 2}, 30, 2),
        ({}, 300, 3),
    ],
    ids=["agent-limits", "legacy-defaults"],
)
async def test_json_composition_limits_transport_retries_without_changing_vision_or_embeddings(
    monkeypatch, agent_options, read_timeout, attempts
):
    """Сетевой предел Ариса не меняет независимые таймауты vision и embeddings."""
    monkeypatch.delenv("AGENT_REQUEST_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("AGENT_REQUEST_ATTEMPTS", raising=False)
    monkeypatch.setattr("src.clients.llm.sleep_before_retry", AsyncMock())
    requests = []

    def reply(request):
        body = json.loads(request.content)
        requests.append((request, body))
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})
        if body["model"] == "test-vision":
            return httpx.Response(200, json={"choices": [{"message": {"content": "Текст"}}]})
        raise httpx.ReadTimeout("synthetic model timeout", request=request)

    llm = LlmSettings(
        _env_file=None,
        llm_api_key="test",
        llm_api_url="https://llm.test/v1/chat/completions",
        agent_model="openai/gpt-6-luna",
        agent_reasoning_enabled=False,
        agent_reasoning_effort="none",
        agent_provider="openai",
        llm_timeout=300,
        llm_retries=3,
        vision_model="test-vision",
        **agent_options,
    )
    embedding = EmbeddingSettings(
        _env_file=None,
        embedding_api_key="test",
        embedding_api_url="https://llm.test/v1/embeddings",
        embedding_timeout=120,
    )
    settings = get_settings().model_copy(
        update={
            "llm": llm,
            "embedding": embedding,
            "knowledge": KnowledgeSettings(_env_file=None, knowledge_vision_enabled=True),
        }
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply), timeout=300) as http:
        qdrant = AsyncQdrantClient(location=":memory:")
        try:
            runtime = build_knowledge_runtime(
                settings=settings, http_client=http, qdrant_client=qdrant
            )
            assert (runtime.llm_client.timeout, runtime.llm_client.retries) == (
                read_timeout,
                attempts,
            )
            with pytest.raises(LlmClientError):
                await runtime.llm_client.get_structured_response(
                    system_prompt="Верни JSON.", content="Проверка", schema=ProbeAnswer
                )
            model_requests = list(requests)
            assert len(model_requests) == attempts
            assert all(
                request.extensions["timeout"]["read"] == read_timeout
                for request, _ in model_requests
            )
            assert isinstance(runtime.vision, VisionClient)
            assert (runtime.vision.timeout, runtime.vision.retries) == (300, 3)
            assert (
                await runtime.vision.extract_image_text(filename="probe.png", content=b"image")
                == "Текст"
            )
            assert requests[-1][0].extensions["timeout"]["read"] == 300
            assert runtime.embedding_client.timeout == 120
            assert await runtime.embedding_client.get_embeddings(["Проверка"]) == [[0.1, 0.2]]
            assert requests[-1][0].extensions["timeout"]["read"] == 120
        finally:
            await qdrant.close()
