import json
import logging
import re
from copy import deepcopy
from time import perf_counter
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from src.clients.request_routing import request_routing_fields
from src.clients.retry import (
    RetryDecision,
    classify,
    log_attempt,
    sleep_before_retry,
    worst_case_seconds,
)
from src.exceptions.clients import LlmClientError

logger = logging.getLogger(__name__)
PydanticModel = TypeVar("PydanticModel", bound=BaseModel)
CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.IGNORECASE)
API_NAME = "LLM API"


class LlmClient:
    """OpenAI Chat Completions client со structured output и retry."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        model: str,
        timeout: float,
        retries: int,
        request_fields: dict[str, Any] | None = None,
    ) -> None:
        self.http_client = http_client
        self.url = url
        self.headers = headers
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.request_fields = deepcopy(request_fields or {})

    @property
    def worst_case_seconds(self) -> float:
        """Верхняя оценка длительности одного вызова с учётом повторов."""
        return worst_case_seconds(timeout=self.timeout, attempts=self.retries)

    async def get_structured_response(
        self,
        *,
        system_prompt: str,
        content: str,
        schema: type[PydanticModel],
        max_completion_tokens: int = 4000,
    ) -> PydanticModel:
        """Запрашивает JSON и валидирует его Pydantic-схемой.

        Повторяются только сеть, таймаут, 429 и 5xx, а также неразобранный
        ответ модели. Обычные 4xx завершаются сразу: повтор их не исправит,
        а бюджет вызова умножит на число попыток.

        Args:
            system_prompt: Системная инструкция модели.
            content: Пользовательская часть запроса.
            schema: Схема, которой должен соответствовать ответ.
            max_completion_tokens: Предел длины ответа модели.

        Returns:
            Разобранный и провалидированный ответ модели.

        Raises:
            LlmClientError: Если API недоступен или ответ не разобран.
        """
        payload = {
            **self.request_fields,
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "max_completion_tokens": max_completion_tokens,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            started = perf_counter()
            try:
                response = await self.http_client.post(
                    self.url,
                    headers=self.headers,
                    content=json.dumps(
                        {**payload, **request_routing_fields(self.url)}, ensure_ascii=False
                    ),
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("Ответ LLM API должен быть объектом.")
                usage = data.get("usage")
                usage = usage if isinstance(usage, dict) else {}
                details = usage.get("prompt_tokens_details")
                details = details if isinstance(details, dict) else {}
                completion_details = usage.get("completion_tokens_details")
                completion_details = (
                    completion_details if isinstance(completion_details, dict) else {}
                )
                logger.info(
                    "Метрики LLM: %s",
                    json.dumps(
                        {
                            "event": "llm.request",
                            "model": self.model,
                            "schema": schema.__name__,
                            "provider_request_id": data.get("id"),
                            "attempt": attempt,
                            "duration_ms": round((perf_counter() - started) * 1000, 3),
                            "prompt_tokens": usage.get("prompt_tokens"),
                            "completion_tokens": usage.get("completion_tokens"),
                            "cached_tokens": details.get("cached_tokens"),
                            "reasoning_tokens": completion_details.get("reasoning_tokens"),
                            "cost_rub": usage.get("cost_rub", usage.get("cost")),
                            "provider": data.get("provider"),
                        },
                        ensure_ascii=False,
                    ),
                )
                content_value = data["choices"][0]["message"]["content"]
                if not isinstance(content_value, str) or not content_value.strip():
                    raise ValueError("LLM вернул пустой content.")
                normalized = CODE_FENCE.sub("", content_value.strip()).strip()
                return schema.model_validate_json(normalized)
            except (
                httpx.HTTPError,
                KeyError,
                IndexError,
                TypeError,
                ValueError,
                ValidationError,
            ) as error:
                last_error = error
                decision = classify(error)
                log_attempt(
                    api_name=API_NAME,
                    decision=decision,
                    attempt=attempt,
                    attempts=self.retries,
                    error=error,
                )
                if decision is RetryDecision.FAIL_FAST:
                    break
                if attempt < self.retries:
                    await sleep_before_retry(attempt)
        raise LlmClientError(str(last_error))
