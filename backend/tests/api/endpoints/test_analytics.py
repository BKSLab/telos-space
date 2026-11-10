from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from main import app
from src.dependencies.services import get_analytics_service
from src.exceptions.ai import AiProviderError
from src.exceptions.analytics import (
    AnalyticsEmptyScopeError,
    AnalyticsGenerationError,
    AnalyticsServiceError,
)
from src.exceptions.projects import ProjectNotFoundError
from src.schemas.analytics import (
    AnalyticsContextSchema,
    AnalyticsFindingKind,
    AnalyticsFindingSchema,
    AnalyticsHealth,
    AnalyticsReportSchema,
    AnalyticsScope,
    AnalyticsSeverity,
    AnalyticsSignalsSchema,
    AnalyticsTaskRefSchema,
)
from src.services.analytics import AnalyticsService


def report() -> AnalyticsReportSchema:
    """Возвращает свод, который отдаёт подменённый сервис."""
    return AnalyticsReportSchema(
        id=1,
        scope=AnalyticsScope.PROJECT,
        project_id=1,
        project_key="TEST",
        project_name="Тест",
        created_at=datetime.now(UTC),
        created_by="Тестов Тест",
        llm_model="test-model",
        duration_ms=1200,
        headline="Сроки держатся.",
        health=AnalyticsHealth.WATCH,
        health_note="Есть накопления в одном блоке.",
        findings=[
            AnalyticsFindingSchema(
                kind=AnalyticsFindingKind.OVERDUE,
                severity=AnalyticsSeverity.HIGH,
                title="Просрочена задача",
                detail="Срок прошёл неделю назад.",
                project_key="TEST",
                project_name="Тест",
                tasks=[
                    AnalyticsTaskRefSchema(
                        id=5,
                        key="TEST-5",
                        title="Интеграция",
                        project_key="TEST",
                        due_date=None,
                        is_overdue=True,
                    )
                ],
            )
        ],
        progress=[],
        recommendations=[],
        signals=AnalyticsSignalsSchema(
            total_tasks=10,
            done_tasks=4,
            overdue_tasks=1,
            due_soon_tasks=2,
            no_due_date_tasks=1,
            unassigned_tasks=0,
            stale_tasks=1,
            blocked_tasks=0,
            unplaced_tasks=2,
            milestones_at_risk=0,
        ),
        context=AnalyticsContextSchema(
            projects=1,
            tasks_total=10,
            tasks_included=10,
            comments_included=3,
            documents_included=1,
            stickers_included=2,
            wbs_nodes_included=4,
            milestones_included=1,
            activity_included=8,
            truncated=False,
            omitted=[],
        ),
    )


def override(service: AsyncMock) -> None:
    """Подменяет сервис аналитики на дублёра."""
    app.dependency_overrides[get_analytics_service] = lambda: service


@pytest.mark.asyncio
async def test_analytics_endpoint_maps_every_domain_error(api_client: AsyncClient) -> None:
    """Пустой срез — 409, чужой проект — 404, сбой генерации — 502, сбой провайдера — 503, прочий сбой сервиса — 500."""

    service = AsyncMock(spec=AnalyticsService)
    service.generate.side_effect = AnalyticsEmptyScopeError(error_details="нет задач")
    override(service)

    response = await api_client.post("/api/v1/dashboard/analytics", json={"project_id": 1})

    assert response.status_code == 409

    service = AsyncMock(spec=AnalyticsService)
    service.generate.side_effect = ProjectNotFoundError(project_id=99)
    override(service)

    response = await api_client.post("/api/v1/dashboard/analytics", json={"project_id": 99})

    assert response.status_code == 404

    service = AsyncMock(spec=AnalyticsService)
    service.generate.side_effect = AnalyticsGenerationError(error_details="мусор в ответе")
    override(service)

    response = await api_client.post("/api/v1/dashboard/analytics", json={"project_id": 1})

    assert response.status_code == 502

    service = AsyncMock(spec=AnalyticsService)
    service.generate.side_effect = AiProviderError("LLM недоступен")
    override(service)

    response = await api_client.post("/api/v1/dashboard/analytics", json={"project_id": 1})

    assert response.status_code == 503

    service = AsyncMock(spec=AnalyticsService)
    service.get_latest.side_effect = AnalyticsServiceError(error_details="сбой чтения")
    override(service)

    response = await api_client.get("/api/v1/dashboard/analytics?project_id=1")

    assert response.status_code == 500


@pytest.mark.asyncio
async def test_analytics_endpoint_returns_report_for_both_scopes(api_client: AsyncClient) -> None:
    """Отчёт по проекту и по портфелю, отсутствие отчёта отдаётся как null."""

    service = AsyncMock(spec=AnalyticsService)
    service.generate.return_value = report()
    override(service)

    response = await api_client.post("/api/v1/dashboard/analytics", json={"project_id": 1})

    assert response.status_code == 201
    body = response.json()
    assert body["health"] == "WATCH"
    assert body["findings"][0]["tasks"][0]["key"] == "TEST-5"
    assert body["signals"]["overdue_tasks"] == 1

    service = AsyncMock(spec=AnalyticsService)
    service.generate.return_value = report()
    override(service)

    response = await api_client.post("/api/v1/dashboard/analytics", json={})

    assert response.status_code == 201
    assert service.generate.await_args.kwargs["project_id"] is None

    service = AsyncMock(spec=AnalyticsService)
    service.get_latest.return_value = None
    override(service)

    response = await api_client.get("/api/v1/dashboard/analytics")

    assert response.status_code == 200
    assert response.json() is None
