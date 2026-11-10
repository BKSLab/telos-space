from unittest.mock import AsyncMock

import pytest

from main import app
from src.dependencies.services import get_risk_suggestion_service
from src.exceptions.access import ResourceNotAvailableError
from src.exceptions.ai import AiProviderError
from src.exceptions.auth import NotAuthenticatedError
from src.schemas.risk_suggestions import RiskSuggestionsSchema
from src.services.risk_suggestions import RiskSuggestionService


async def test_suggestions_pass_credentials_without_request_db_session(api_client):
    service = AsyncMock(spec=RiskSuggestionService)
    service.suggest.return_value = RiskSuggestionsSchema(suggestions=[])
    app.dependency_overrides[get_risk_suggestion_service] = lambda: service
    response = await api_client.post(
        "/api/v1/projects/1/risks/suggestions", headers={"Authorization": "Bearer telos_token_read"}
    )
    assert response.status_code == 200 and response.json() == {"suggestions": []}
    service.suggest.assert_awaited_once_with(
        project_id=1, session_token=None, bearer_secret="telos_token_read"
    )


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (NotAuthenticatedError(), 401),
        (ResourceNotAvailableError(resource="project", resource_id=2), 404),
        (AiProviderError("private provider data"), 503),
    ],
)
async def test_suggestions_translate_auth_access_and_provider_errors(api_client, error, status):
    service = AsyncMock(spec=RiskSuggestionService)
    service.suggest.side_effect = error
    app.dependency_overrides[get_risk_suggestion_service] = lambda: service
    response = await api_client.post("/api/v1/projects/2/risks/suggestions")
    assert response.status_code == status
    assert "private provider data" not in response.text
