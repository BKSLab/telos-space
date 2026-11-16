"""HTTP forwards the actual principal to both initial wait and polling."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from fastapi import FastAPI

from src.api.v1.endpoints.agent_requests import router
from src.dependencies.auth import get_principal
from src.dependencies.context_auth import get_context_principal
from src.dependencies.services import get_agent_requests_service
from src.dependencies.settings import get_settings
from src.schemas.agent_requests import AgentRequestResultSchema
from src.services.agent_requests import AgentRequestsService


async def test_external_result_transports_keep_current_scoped_identity(current_principal):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    service = AsyncMock(spec=AgentRequestsService)
    request_id = uuid4()
    service.ask_external.return_value = request_id
    service.wait_external_result.return_value = service.get_external_result.return_value = (
        AgentRequestResultSchema(
            request_id=request_id, status="completed", answer="Verified", write_allowed=False
        )
    )
    app.dependency_overrides[get_principal] = lambda: current_principal
    app.dependency_overrides[get_context_principal] = lambda: current_principal
    app.dependency_overrides[get_agent_requests_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        agent=SimpleNamespace(agent_ask_wait_seconds=0)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get(f"/api/v1/aris/requests/{request_id}")).status_code == 200
        assert service.get_external_result.await_args.kwargs["principal"] is current_principal
        response = await client.post("/api/v1/aris/requests", json={"text": "Question"})
        assert response.status_code == 200
        assert service.wait_external_result.await_args.kwargs["principal"] is current_principal
        assert service.ask_external.await_args.kwargs["user_id"] == current_principal.user_id
