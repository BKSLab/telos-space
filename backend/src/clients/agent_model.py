"""Модель PydanticAI для агента Ариса поверх текущих настроек LLM.

Тот же провайдер (polza.ai, OpenAI-совместимый API), ключ и модель
AGENT_MODEL, что и у прежнего JSON-цикла; меняется только способ вызова:
chat completions с нативными инструментами вместо JSON-режима.

Модель ещё и меряет себя: каждый запрос к шлюзу попадает в журнал текущего
ответа (``record_requests``) с длительностью и токенами — по нему видно, на
каком шаге ушло время.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Any

import httpx
from openai import AsyncOpenAI
from openai.types import chat
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from src.clients.model_options import agent_request_fields
from src.clients.request_routing import request_routing_fields
from src.core.settings import LlmSettings

#: Журнал запросов текущего ответа; None — никто не записывает.
_REQUESTS: ContextVar[list[dict[str, Any]] | None] = ContextVar("agent_requests", default=None)
_TOOLS_OFFERED: ContextVar[int] = ContextVar("agent_tools_offered", default=0)


@contextmanager
def record_requests() -> Iterator[list[dict[str, Any]]]:
    """Собирает запросы модели внутри блока: длительность и токены каждого шага."""
    journal: list[dict[str, Any]] = []
    token = _REQUESTS.set(journal)
    try:
        yield journal
    finally:
        _REQUESTS.reset(token)


class GatewayChatModel(OpenAIChatModel):
    """Chat completions шлюза с честным учётом токенов и замером каждого запроса.

    PydanticAI извлекает usage через каталог genai-prices, а шлюз polza в нём не
    опознаётся: входные и выходные токены приходили нулями. Числа берутся прямо
    из ответа, если библиотека их не нашла.
    """

    def prepare_request(
        self,
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> tuple[ModelSettings | None, ModelRequestParameters]:
        settings, parameters = super().prepare_request(model_settings, model_request_parameters)
        routing = request_routing_fields(self.base_url)
        if routing:
            request_settings: ModelSettings = {
                **(settings or {}),
                "extra_body": {**((settings or {}).get("extra_body") or {}), **routing},
            }
            settings = request_settings
        # Native deferred capabilities keep hidden definitions in function_tools.
        # Count the schemas actually declared after public visibility resolution.
        _TOOLS_OFFERED.set(len(parameters.declared_function_tools))
        return settings, parameters

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        started = perf_counter()
        token = _TOOLS_OFFERED.set(0)
        try:
            response = await super().request(messages, model_settings, model_request_parameters)
            tools_offered = _TOOLS_OFFERED.get()
        finally:
            _TOOLS_OFFERED.reset(token)
        journal = _REQUESTS.get()
        if journal is not None:
            usage = response.usage
            journal.append(
                {
                    "ms": round((perf_counter() - started) * 1000, 1),
                    "input_tokens": usage.input_tokens,
                    "cached_tokens": usage.cache_read_tokens,
                    "output_tokens": usage.output_tokens,
                    "reasoning_tokens": usage.details.get("reasoning_tokens", 0),
                    "tools_offered": tools_offered,
                    "tool_calls": len(response.tool_calls),
                    "provider_request_id": response.provider_response_id,
                    "model": response.model_name,
                    "provider": (response.provider_details or {}).get("gateway_provider"),
                    "cost_rub": (response.provider_details or {}).get("cost_rub"),
                }
            )
        return response

    def _process_response(self, response: chat.ChatCompletion | str) -> ModelResponse:
        result = super()._process_response(response)
        raw = getattr(response, "usage", None)
        if raw is not None:
            cost_rub = getattr(raw, "cost_rub", None)
            if cost_rub is None:
                cost_rub = getattr(raw, "cost", None)
            result.provider_details = {
                **(result.provider_details or {}),
                "cost_rub": cost_rub,
                "gateway_provider": getattr(response, "provider", None),
            }
        if raw is not None and not result.usage.input_tokens:
            details = getattr(raw, "prompt_tokens_details", None)
            result.usage = RequestUsage(
                input_tokens=raw.prompt_tokens or 0,
                output_tokens=raw.completion_tokens or 0,
                cache_read_tokens=(getattr(details, "cached_tokens", None) or 0),
                details=result.usage.details,
            )
        return result


def build_agent_model(settings: LlmSettings, http_client: httpx.AsyncClient) -> OpenAIChatModel:
    """Создаёт модель агента на общем HTTP-клиенте приложения.

    Таймаут и число HTTP-попыток Ариса настраиваются отдельно от vision;
    незаданные параметры сохраняют ``LLM_TIMEOUT``/``LLM_RETRIES``.
    SDK повторяет только HTTP-запрос модели при сети, timeout, 429 и 5xx.

    Args:
        settings: Ключ, адрес и модель LLM.
        http_client: Общий пул соединений внешних API; закрывает его lifespan.
    Returns:
        Модель для ``pydantic_ai.Agent``.
    """
    client = AsyncOpenAI(
        base_url=settings.base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        http_client=http_client,
        timeout=settings.agent_timeout_seconds,
        max_retries=max(0, settings.agent_attempts - 1),
    )
    return GatewayChatModel(
        settings.agent_model,
        provider=OpenAIProvider(openai_client=client),
        settings=ModelSettings(extra_body=agent_request_fields(settings)),
    )
