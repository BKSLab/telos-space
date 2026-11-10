"""Контракт HTTP при недоступности провайдера предложения ИСР."""

from httpx import AsyncClient

from main import app
from src.dependencies.services import get_wbs_suggestion_service
from src.exceptions.ai import AiProviderError
from src.exceptions.clients import ClientError
from tests.unit.services.test_wbs_suggestion_service import build_service, task


async def test_wbs_provider_failure_returns_safe_503_without_writes(api_client: AsyncClient) -> None:
    """Реальный сервис переводит отказ клиента в безопасный ответ, не записывая ИСР."""
    service, calls = build_service(tasks=[task(1)], llm_error=ClientError("private provider failure"))
    app.dependency_overrides[get_wbs_suggestion_service] = lambda: service

    response = await api_client.post("/api/v1/projects/1/wbs/suggestion")

    assert response.status_code == 503
    assert response.json() == {"detail": AiProviderError.detail}
    assert "private provider failure" not in response.text
    documented = app.openapi()["paths"]["/api/v1/projects/{project_id}/wbs/suggestion"]["post"]["responses"]
    assert "503" in documented
    calls["llm"].get_structured_response.assert_awaited_once()
    calls["unit_of_work"].commit.assert_not_awaited()
