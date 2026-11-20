"""Публичные маршруты/пагинация и typed ошибки производной карты."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from fastapi import FastAPI

from src.api.v1.endpoints.project_knowledge import router
from src.dependencies.context_auth import get_context_principal
from src.dependencies.project_knowledge import get_project_knowledge_service
from src.exceptions.project_knowledge import ProjectKnowledgeAccessError, ProjectKnowledgeStaleError
from src.schemas.project_knowledge import (
    ProjectKnowledgeMapSchema,
    ProjectKnowledgeObservationSchema,
    ProjectKnowledgeStatusSchema,
)
from src.services.project_knowledge import ProjectKnowledgeService


async def test_map_http_contract_bounds_read_and_refresh_errors(current_principal):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    service = AsyncMock(spec=ProjectKnowledgeService)
    app.dependency_overrides[get_context_principal] = lambda: current_principal
    app.dependency_overrides[get_project_knowledge_service] = lambda: service
    service.read_map.return_value = ProjectKnowledgeMapSchema(
        project_id=7,
        generation=0,
        status="empty",
        items=[],
        observation=ProjectKnowledgeObservationSchema(project_id=7, generation=0),
        total=0,
        offset=0,
        limit=20,
        has_more=False,
    )
    service.get_status.return_value = ProjectKnowledgeStatusSchema(
        project_id=7, generation=0, status="empty"
    )
    service.request_refresh.return_value = ProjectKnowledgeStatusSchema(
        project_id=7, generation=0, status="pending"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/projects/7/knowledge/map?limit=20&offset=0&category=goal&exclude_category=facts&q=delivery"
        )
        assert response.status_code == 200 and response.json()["observation"]["generation"] == 0
        assert service.read_map.await_args.kwargs == {
            "project_id": 7,
            "principal": current_principal,
            "limit": 20,
            "offset": 0,
            "category": "goal",
            "exclude_category": "facts",
            "query": "delivery",
        }
        assert (await client.get("/api/v1/projects/7/knowledge/map/status")).status_code == 200
        assert (await client.post("/api/v1/projects/7/knowledge/map/refresh")).status_code == 202
        for query in ("limit=51", "offset=-1", "category=arbitrary", "q=%00"):
            assert (
                await client.get("/api/v1/projects/7/knowledge/map?" + query)
            ).status_code == 422
        service.read_map.assert_awaited_once()
        service.get_claim.side_effect = ProjectKnowledgeStaleError()
        assert (
            await client.get(f"/api/v1/projects/7/knowledge/map/claims/{uuid4()}")
        ).status_code == 409
        service.get_status.side_effect = ProjectKnowledgeAccessError(
            status_code=403, detail="Нет права читать карту."
        )
        response = await client.get("/api/v1/projects/7/knowledge/map/status")
        assert response.status_code == 403 and response.json() == {
            "detail": "Нет права читать карту."
        }
