from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from main import app
from src.dependencies.services import get_project_agent_service
from src.exceptions.ai import AgentAnswerError, AiProviderError
from src.schemas.knowledge import (
    KnowledgeAnswerSchema,
    KnowledgeSourceSchema,
    KnowledgeStatusSchema,
)
from src.services.project_agent import ProjectAgentService


@pytest.mark.asyncio
async def test_ask_returns_grounded_answer_and_sources(api_client: AsyncClient) -> None:
    service = AsyncMock(spec=ProjectAgentService)
    service.ask.return_value = KnowledgeAnswerSchema(
        answer="Задача находится **в работе**.",
        sources=[
            KnowledgeSourceSchema(
                source_id="task:7",
                entity_type="task",
                entity_id=7,
                task_id=7,
                title="TEST-7 · Индексация",
            )
        ],
    )
    app.dependency_overrides[get_project_agent_service] = lambda: service

    response = await api_client.post(
        "/api/v1/projects/1/knowledge/ask",
        json={"question": "Что в работе?", "history": []},
    )

    assert response.status_code == 200
    assert response.json()["sources"][0]["task_id"] == 7
    service.ask.assert_awaited_once()


@pytest.mark.asyncio
async def test_knowledge_endpoints_validate_input_and_hide_internals(api_client: AsyncClient) -> None:
    """Пустой вопрос отклоняется, сбой провайдера — 503, статус не раскрывает имя коллекции."""

    service = AsyncMock(spec=ProjectAgentService)
    app.dependency_overrides[get_project_agent_service] = lambda: service

    response = await api_client.post(
        "/api/v1/projects/1/knowledge/ask",
        json={"question": "   "},
    )

    assert response.status_code == 422
    service.ask.assert_not_called()

    service = AsyncMock(spec=ProjectAgentService)
    app.dependency_overrides[get_project_agent_service] = lambda: service

    for error, expected_status in (
        (AiProviderError("provider offline"), 503),
        (AgentAnswerError("private model response"), 502),
    ):
        service.ask.side_effect = error
        response = await api_client.post(
            "/api/v1/projects/1/knowledge/ask",
            json={"question": "Что в работе?"},
        )
        assert response.status_code == expected_status
        assert "private model response" not in response.json()["detail"]

    service.get_status.side_effect = AiProviderError("private provider details")
    response = await api_client.get("/api/v1/projects/1/knowledge/status")
    assert response.status_code == 503 and "private provider details" not in response.json()["detail"]

    service = AsyncMock(spec=ProjectAgentService)
    service.get_status.return_value = KnowledgeStatusSchema(
        enabled=True,
        ready=True,
        points_count=12,
        pending_jobs=0,
        processing_jobs=0,
        failed_jobs=0,
        last_error=None,
    )
    app.dependency_overrides[get_project_agent_service] = lambda: service

    response = await api_client.get("/api/v1/projects/1/knowledge/status")

    assert response.status_code == 200
    assert response.json()["points_count"] == 12
    assert "collection" not in response.json()
