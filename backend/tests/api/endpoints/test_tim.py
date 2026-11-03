"""HTTP-контур Ариса: виджет без проекта и решение по действию из чата проекта."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from main import app
from src.dependencies.services import (
    get_agent_actions_service,
    get_agent_conversations_service,
    get_agent_files_service,
)
from src.exceptions.agent_conversations import (
    AgentConversationBusyError,
    AgentConversationNotFoundError,
    AgentConversationsServiceError,
    AgentMessageConflictError,
)
from src.schemas.agent_conversations import (
    AgentConversationListSchema,
    AgentConversationSchema,
    AgentMessageAcceptedSchema,
    AgentMessagePageSchema,
    AgentMessageSchema,
)
from src.schemas.agent_files import AgentFileSchema
from src.schemas.agent_tools import AgentToolRunSchema
from src.services.agent_actions import AgentActionsService
from src.services.agent_files import AgentFilesService
from src.services.tim_widget import TimWidgetService


async def test_action_decision_uses_server_actor_and_does_not_accept_changed_arguments(api_client):
    actions = AsyncMock(spec=AgentActionsService)
    action_id = uuid4()
    actions.decide.return_value = AgentToolRunSchema(
        id=action_id,
        tool_name="delete_task",
        title="Удалить задачу",
        arguments={"task_id": 7},
        status="completed",
        result={"deleted": True},
        created_at=datetime.now(UTC),
    )
    app.dependency_overrides[get_agent_actions_service] = lambda: actions
    path = f"/api/v1/tim/conversations/7/actions/{action_id}/decision"
    assert (
        await api_client.post(path, json={"decision": "approve", "arguments": {"task_id": 8}})
    ).status_code == 422
    actions.decide.assert_not_awaited()
    response = await api_client.post(path, json={"decision": "approve"})
    assert response.status_code == 200 and response.json()["status"] == "completed"
    call = actions.decide.await_args.kwargs
    # Проект действия виджета берётся из самого действия, а не из маршрута.
    assert call["context"].user_id == 1 and call["context"].project_id is None
    assert call["conversation_id"] == 7 and call["action_id"] == action_id
    actions.decide_in_chat.return_value = actions.decide.return_value
    chat_path = f"/api/v1/projects/1/tim/actions/{action_id}/decision"
    response = await api_client.post(chat_path, json={"decision": "reject"})
    assert response.status_code == 200
    call = actions.decide_in_chat.await_args.kwargs
    assert call["context"].project_id == 1 and call["context"].user_id == 1
    assert call["decision"] == "reject" and call["action_id"] == action_id


async def test_file_upload_uses_multipart_and_server_owned_conversation(api_client):
    files = AsyncMock(spec=AgentFilesService)
    files.max_file_size = 10 * 1024 * 1024
    file_id = uuid4()
    files.upload.return_value = AgentFileSchema(
        id=file_id,
        original_name="plan.md",
        content_type="text/markdown",
        size=4,
        created_at=datetime.now(UTC),
    )
    app.dependency_overrides[get_agent_files_service] = lambda: files
    path = "/api/v1/tim/conversations/7/files"
    response = await api_client.post(path, files={"file": ("plan.md", b"plan", "text/markdown")})
    assert response.status_code == 201 and response.json()["id"] == str(file_id)
    assert "storage_key" not in response.json()
    assert files.upload.await_args.kwargs == dict(
        user_id=1,
        conversation_id=7,
        file_name="plan.md",
        content_type="text/markdown",
        content=b"plan",
    )
    assert (await api_client.delete(f"{path}/{file_id}")).status_code == 204
    files.delete_draft.assert_awaited_once_with(user_id=1, conversation_id=7, file_id=file_id)


async def test_dialogue_api_uses_server_actor_and_accepts_only_a_new_message(api_client):
    now = datetime.now(UTC)
    service = AsyncMock(spec=TimWidgetService)
    conversation = AgentConversationSchema(id=7, title="Разговор", created_at=now, updated_at=now)
    service.create_conversation.return_value = conversation
    service.list_conversations.return_value = AgentConversationListSchema(
        items=[conversation], next_offset=None
    )
    request_id = uuid4()
    common = dict(conversation_id=7, request_id=request_id, sources=[], error=None, created_at=now)
    user = AgentMessageSchema(
        id=11, role="user", content="Есть задача?", status="completed", **common
    )
    assistant = AgentMessageSchema(id=12, role="assistant", content="", status="queued", **common)
    service.send_message.return_value = AgentMessageAcceptedSchema(
        user_message=user, assistant_message=assistant
    )
    service.get_messages.return_value = AgentMessagePageSchema(
        items=[user, assistant], next_before_id=None
    )
    service.retry_message.return_value = assistant
    app.dependency_overrides[get_agent_conversations_service] = lambda: service
    base = "/api/v1/tim/conversations"

    response = await api_client.post(base)
    assert response.status_code == 201 and response.json()["id"] == 7
    service.create_conversation.assert_awaited_once_with(user_id=1)
    assert (await api_client.get(base)).json()["items"][0]["id"] == 7
    page = {"route": "/projects/PRJ/board", "project_key": "PRJ", "task_id": 5}
    response = await api_client.post(
        f"{base}/7/messages",
        json={"request_id": str(request_id), "content": "Есть задача?", "page_context": page},
    )
    assert (
        response.status_code == 202 and response.json()["assistant_message"]["status"] == "queued"
    )
    sent = service.send_message.await_args.kwargs
    assert sent["user_id"] == 1 and sent["data"].page_context.project_key == "PRJ"
    assert (await api_client.get(f"{base}/7/messages")).json()["items"][0][
        "content"
    ] == "Есть задача?"
    assert (await api_client.post(f"{base}/7/messages/12/retry")).status_code == 202
    for injection in (
        {"history": []},
        {"user_id": 2},
        {"project_id": 2},
        {"page_context": {"route": "/", "user_id": 2}},
    ):
        rejected = await api_client.post(
            f"{base}/7/messages",
            json={"request_id": str(uuid4()), "content": "Вопрос", **injection},
        )
        assert rejected.status_code == 422
    assert service.send_message.await_count == 1


async def test_dialogue_errors_have_domain_statuses_without_internal_details(api_client):
    service = AsyncMock(spec=TimWidgetService)
    app.dependency_overrides[get_agent_conversations_service] = lambda: service
    path = "/api/v1/tim/conversations/7/messages"
    for error, expected in (
        (AgentConversationNotFoundError, 404),
        (AgentConversationBusyError, 409),
        (AgentMessageConflictError, 409),
        (AgentConversationsServiceError, 500),
    ):
        service.send_message.side_effect = error("секретная ошибка базы")
        response = await api_client.post(
            path, json={"request_id": str(uuid4()), "content": "Вопрос"}
        )
        assert response.status_code == expected
        assert "секретная" not in response.json()["detail"]
