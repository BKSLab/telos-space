"""HTTP-граница не превращает model bool/query/url в разрешение человека."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from src.api.v1.endpoints.web_research import router
from src.db.models.api_tokens import ApiTokenScope
from src.dependencies.context_auth import get_context_principal
from src.dependencies.web_research import get_web_research_service
from src.exceptions.web_research import WebResearchConsentError
from src.schemas.web_research import WebResearchStatusSchema, WebTopicGrantSchema
from src.services.auth import Principal
from src.services.web_research import WebResearchService


@pytest.fixture
async def web_api():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    principal = Principal(
        user_id=5,
        username="reader",
        first_name="Тест",
        last_name="Человек",
        middle_name=None,
        scope=ApiTokenScope.READ,
        via_api_token=True,
    )
    service = AsyncMock(spec=WebResearchService)
    state = SimpleNamespace(principal=principal)
    app.dependency_overrides[get_context_principal] = lambda: state.principal
    app.dependency_overrides[get_web_research_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, service, state


@pytest.mark.parametrize(
    ("path", "body"),
    [("topics", {"query": "public topic"}), ("pages", {"url": "https://example.org/"})],
)
async def test_credential_cannot_issue_human_consent(web_api, path, body):
    client, service, _ = web_api
    response = await client.post(f"/api/v1/projects/3/web-research/{path}", json=body)
    assert response.status_code == 403
    service.approve_topic.assert_not_awaited()
    service.approve_url.assert_not_awaited()


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("search", {"query": "private raw context", "approved": True}),
        ("read", {"url": "https://example.org/private-query"}),
        ("search", {"topic_token": "signed", "query": "replacement private query"}),
    ],
)
async def test_read_endpoints_reject_unsigned_raw_input(web_api, path, body):
    client, service, _ = web_api
    response = await client.post(f"/api/v1/projects/3/web-research/{path}", json=body)
    assert response.status_code == 422
    service.search.assert_not_awaited()
    service.read.assert_not_awaited()


async def test_exact_human_topic_is_forwarded_with_server_identity(web_api):
    client, service, state = web_api
    principal = replace(state.principal, via_api_token=False)
    state.principal = principal
    service.approve_topic.return_value = WebTopicGrantSchema(
        public_topic="public topic", topic_token="signed", expires_at=datetime.now(UTC)
    )
    response = await client.post(
        "/api/v1/projects/3/web-research/topics", json={"query": "public topic"}
    )
    assert response.status_code == 200 and response.json()["public_topic"] == "public topic"
    call = service.approve_topic.await_args.kwargs
    assert call["principal"] is principal and call["project_id"] == 3
    assert call["public_topic"] == "public topic" and call["approval_id"]


async def test_status_and_service_error_keep_public_contract(web_api):
    client, service, _ = web_api
    service.status.return_value = WebResearchStatusSchema(
        reading_enabled=True, search_enabled=False
    )
    response = await client.get("/api/v1/projects/3/web-research/status")
    assert response.status_code == 200 and response.json() == {
        "reading_enabled": True,
        "search_enabled": False,
        "provider": "brave",
    }
    service.read.side_effect = WebResearchConsentError("internal-private-detail")
    response = await client.post(
        "/api/v1/projects/3/web-research/read", json={"page_token": "expired"}
    )
    assert response.status_code == 403 and "internal-private-detail" not in response.text
