"""Один курсор private истории и передача around_message_id в общий сервис."""

from unittest.mock import AsyncMock

import pytest

from main import app
from src.dependencies.services import get_agent_conversations_service
from src.schemas.agent_conversations import AgentMessagePageSchema
from src.services.agent_conversations import AgentConversationsService


@pytest.mark.parametrize("project_id", [None, 1], ids=["global", "project"])
async def test_private_history_around_cursor_is_exclusive_and_uses_server_owner(
    api_client, project_id
):
    service = AsyncMock(spec=AgentConversationsService)
    service.get_messages.return_value = AgentMessagePageSchema(items=[], next_before_id=None)
    app.dependency_overrides[get_agent_conversations_service] = lambda: service
    base = (
        "/api/v1/aris/conversations"
        if project_id is None
        else f"/api/v1/projects/{project_id}/aris/conversations"
    )
    response = await api_client.get(f"{base}/7/messages?around_message_id=42&limit=5")
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_before_id": None}
    expected = dict(user_id=1, conversation_id=7, before_id=None, around_message_id=42, limit=5)
    if project_id is not None:
        expected["project_id"] = project_id
    service.get_messages.assert_awaited_once_with(**expected)
    assert (
        await api_client.get(f"{base}/7/messages?around_message_id=42&before_id=50")
    ).status_code == 422
    assert (await api_client.get(f"{base}/7/messages?around_message_id=0")).status_code == 422
    assert service.get_messages.await_count == 1
