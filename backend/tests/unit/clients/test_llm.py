"""Политика повторов LLM-клиента и расчёт бюджета вызова.

Повтор неретраебельной ошибки не исправляет результат, но умножает
worst-case на число попыток. Разделение категорий проверяется здесь,
потому что именно оно определяет фактический timeout budget.
"""

import json
from unittest.mock import Mock

import httpx
import pytest
from pydantic import BaseModel

from src.clients.llm import LlmClient
from src.clients.retry import RetryDecision, classify, worst_case_seconds
from src.exceptions.clients import LlmClientError


class AnswerSchema(BaseModel):
    """Минимальная схема структурированного ответа."""

    answer: str


async def test_usage_metrics_include_tokens_but_not_document_text(monkeypatch):
    info = Mock()
    monkeypatch.setattr("src.clients.llm.logger.info", info)
    client = build_client(
        lambda request: httpx.Response(
            200,
            json={
                "id": "gen-test",
                "provider": "test-provider",
                "usage": {
                    "prompt_tokens": 321,
                    "completion_tokens": 12,
                    "prompt_tokens_details": {"cached_tokens": 100},
                    "completion_tokens_details": {"reasoning_tokens": 4},
                    "cost_rub": 0.01234,
                },
                "choices": [{"message": {"content": '{"answer":"секретный текст документа"}'}}],
            },
        )
    )
    try:
        await _ask(client)
        record = json.loads(info.call_args.args[1])
        assert record["prompt_tokens"] == 321 and record["cached_tokens"] == 100
        assert record["provider_request_id"] == "gen-test"
        assert record["reasoning_tokens"] == 4
        assert record["cost_rub"] == 0.01234
        assert record["provider"] == "test-provider"
        assert "секретный текст" not in info.call_args.args[1]
    finally:
        await client.http_client.aclose()


def build_client(handler, *, retries: int = 2) -> LlmClient:
    """Собирает клиент поверх httpx-транспорта с подменённым обработчиком."""
    return LlmClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        url="https://llm.test/v1/chat/completions",
        headers={"Authorization": "Bearer test"},
        model="test-model",
        timeout=5,
        retries=retries,
    )


def build_response(content: str) -> httpx.Response:
    """Ответ chat completions с заданным содержимым."""
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def _ask(client: LlmClient) -> AnswerSchema:
    """Выполняет типовой запрос структурированного ответа."""
    return await client.get_structured_response(
        system_prompt="Отвечай JSON.",
        content="Вопрос",
        schema=AnswerSchema,
    )


@pytest.mark.parametrize("host", ["polza.ai", "other.test", "polza.ai.other.test"])
async def test_repeated_prompts_and_retries_have_independent_polza_routing(host):
    requests = []
    route_labels = set()

    def reply(request):
        payload = json.loads(request.content)
        requests.append((request, payload))
        if host == "polza.ai":
            label = payload.get("prompt_cache_key")
            if label in route_labels:
                return httpx.Response(400, json={"error": "No endpoints found"})
            route_labels.add(label)
        if len(requests) == 1:
            return httpx.Response(503, json={"error": "temporary unavailable"})
        return build_response('{"answer":"готово"}')

    client = build_client(reply)
    client.url = f"https://{host}/v1/chat/completions"
    try:
        for _ in range(2):
            assert (await _ask(client)).answer == "готово"
    finally:
        await client.http_client.aclose()
    assert len(requests) == 3
    labels = [body.get("prompt_cache_key") for _, body in requests]
    if host == "polza.ai":
        assert len(set(labels)) == 3 and all(labels)
    else:
        assert labels == [None] * 3
    baseline = {k: v for k, v in requests[0][1].items() if k != "prompt_cache_key"}
    assert all(
        {k: v for k, v in body.items() if k != "prompt_cache_key"} == baseline
        and request.headers["authorization"] == "Bearer test"
        for request, body in requests
    )


async def test_answer_is_validated_and_unwrapped() -> None:
    """Ответ проходит валидацию схемой, обёртка из тройных кавычек снимается."""
    # Корректный JSON разбирается схемой.
    client = build_client(lambda request: build_response(json.dumps({"answer": "готово"})))

    assert (await _ask(client)).answer == "готово"
    # Модель нередко оборачивает JSON в ```json — обёртка снимается.
    payload = "```json\n" + json.dumps({"answer": "готово"}) + "\n```"
    client = build_client(lambda request: build_response(payload))

    assert (await _ask(client)).answer == "готово"


async def test_retryable_statuses_are_retried() -> None:
    """429 и 5xx повторяются: причина сбоя на стороне сервера."""
    for status_code in (429, 500, 503):
        attempts = {"count": 0}

        def handler(
            request: httpx.Request,
            code: int = status_code,
            counter: dict = attempts,
        ) -> httpx.Response:
            counter["count"] += 1
            if counter["count"] == 1:
                return httpx.Response(code, json={"error": "retry"})
            return build_response(json.dumps({"answer": "готово"}))

        assert (await _ask(build_client(handler, retries=3))).answer == "готово"
        assert attempts["count"] == 2, f"{status_code} не повторён"


async def test_worst_case_budget_is_derived_from_timeout_and_attempts() -> None:
    """Бюджет худшего случая считается из timeout и попыток, ноль попыток недопустим, продовые значения дают известное число."""
    # Бюджет вызова доступен как свойство клиента.
    client = build_client(lambda request: build_response("{}"), retries=3)

    assert client.worst_case_seconds == pytest.approx(worst_case_seconds(timeout=5, attempts=3))
    # Ноль попыток — некорректный бюджет, а не мгновенный вызов.
    with pytest.raises(ValueError):
        worst_case_seconds(timeout=5, attempts=0)
    # Синхронный LlmClient по умолчанию имеет worst case не менее 900 секунд.
    # Ограничение HTTP и отдельный deadline очереди Ариса описаны в docs/agent.md.
    assert worst_case_seconds(timeout=300, attempts=3) >= 900


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (400, RetryDecision.FAIL_FAST),
        (401, RetryDecision.FAIL_FAST),
        (403, RetryDecision.FAIL_FAST),
        (404, RetryDecision.FAIL_FAST),
        (408, RetryDecision.RETRY_TRANSPORT),
        (429, RetryDecision.RETRY_TRANSPORT),
        (500, RetryDecision.RETRY_TRANSPORT),
        (503, RetryDecision.RETRY_TRANSPORT),
    ],
)
def test_status_classification(status_code: int, expected: RetryDecision) -> None:
    """Классификация HTTP-статусов зафиксирована явно."""
    request = httpx.Request("POST", "https://llm.test")
    error = httpx.HTTPStatusError(
        "ошибка",
        request=request,
        response=httpx.Response(status_code, request=request),
    )

    assert classify(error) is expected


def test_non_http_error_is_content_failure() -> None:
    """Ошибка разбора относится к содержимому, а не к транспорту."""
    assert classify(ValueError("плохой JSON")) is RetryDecision.RETRY_CONTENT


async def test_transient_failures_are_retried_and_client_errors_are_not() -> None:
    """Ошибка клиента не повторяется, сбой транспорта и нарушение схемы — повторяются."""
    # Обычная 4xx завершается сразу и не тратит остальные попытки.
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(LlmClientError):
        await _ask(build_client(handler, retries=3))

    assert attempts["count"] == 1
    # Сетевой сбой повторяется как транспортная ошибка.
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ConnectError("нет соединения", request=request)
        return build_response(json.dumps({"answer": "готово"}))

    assert (await _ask(build_client(handler, retries=3))).answer == "готово"
    assert attempts["count"] == 2
    # Ответ, не прошедший схему, повторяется отдельной категорией.
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return build_response(json.dumps({"wrong_field": 1}))
        return build_response(json.dumps({"answer": "готово"}))

    assert (await _ask(build_client(handler, retries=3))).answer == "готово"
    assert attempts["count"] == 2
