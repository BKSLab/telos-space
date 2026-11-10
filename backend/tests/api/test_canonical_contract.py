"""Единственный HTTP-контракт без прежних URL и ослабления прав."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from main import app
from src.db.models.api_tokens import ApiTokenScope
from src.dependencies.auth import get_principal
from src.dependencies.services import (
    get_agent_actions_service,
    get_agent_requests_service,
    get_auth_service,
    get_task_transfers_service,
    get_tasks_service,
)
from src.dependencies.task_attention import get_task_attention_service
from src.exceptions.board_stickers import BoardStickerTaskMismatchError
from src.schemas.agent_requests import AgentRequestResultSchema
from src.schemas.http_errors import HttpValidationErrorResponseSchema
from src.schemas.task_attention import TaskAttentionSnapshot
from src.schemas.task_transfers import TaskTransferPreviewSchema, TaskTransferResultSchema
from src.schemas.tasks import TaskSchema
from src.services.agent_actions import AgentActionsService
from src.services.agent_requests import AgentRequestsService
from src.services.auth import ApiKeyGrant, AuthService
from src.services.task_attention import TaskAttentionService
from src.services.task_transfers import TaskTransfersService
from src.services.tasks import TasksService

ACTION_ID = "00000000-0000-4000-8000-000000000001"
RETIRED_REQUESTS = (
    ("GET", "/api/v1/tim/conversations"),
    ("POST", "/api/v1/tim/conversations"),
    ("GET", "/api/v1/tim/conversations/1/messages"),
    ("POST", "/api/v1/tim/conversations/1/messages"),
    ("POST", "/api/v1/tim/conversations/1/messages/1/retry"),
    ("POST", "/api/v1/tim/conversations/1/files"),
    ("DELETE", f"/api/v1/tim/conversations/1/files/{ACTION_ID}"),
    ("GET", f"/api/v1/tim/conversations/1/files/{ACTION_ID}/content"),
    ("GET", f"/api/v1/tim/conversations/1/files/{ACTION_ID}/preview"),
    ("POST", f"/api/v1/tim/conversations/1/actions/{ACTION_ID}/decision"),
    ("POST", f"/api/v1/projects/1/tim/actions/{ACTION_ID}/decision"),
    ("GET", "/api/v1/projects/1/agent/conversations"),
    ("POST", "/api/v1/projects/1/agent/conversations"),
    ("GET", "/api/v1/projects/1/agent/conversations/1/messages"),
    ("POST", "/api/v1/projects/1/agent/conversations/1/messages"),
    ("POST", "/api/v1/projects/1/agent/conversations/1/messages/1/retry"),
    ("POST", "/api/v1/projects/1/agent/conversations/1/files"),
    ("DELETE", f"/api/v1/projects/1/agent/conversations/1/files/{ACTION_ID}"),
    ("GET", f"/api/v1/projects/1/agent/conversations/1/files/{ACTION_ID}/content"),
    ("GET", f"/api/v1/projects/1/agent/conversations/1/files/{ACTION_ID}/preview"),
    ("POST", f"/api/v1/projects/1/agent/conversations/1/actions/{ACTION_ID}/decision"),
    ("POST", "/api/v1/agent/ask"),
    ("GET", f"/api/v1/agent/requests/{ACTION_ID}"),
    ("GET", f"/api/v1/agent/pending/{ACTION_ID}"),
    ("POST", f"/api/v1/agent/pending/{ACTION_ID}/confirm"),
    ("POST", f"/api/v1/agent/pending/{ACTION_ID}/reject"),
    ("GET", "/api/v1/my/tasks"),
    ("GET", "/api/v1/my/task-attention"),
    ("GET", "/api/v1/me/calendar"),
    ("GET", "/api/v1/me/calendar/unscheduled"),
    ("GET", "/api/v1/me/calendar/ics-token"),
    ("POST", "/api/v1/me/calendar/ics-token"),
    ("DELETE", "/api/v1/me/calendar/ics-token"),
    ("PATCH", "/api/v1/tasks/1/move"),
    ("GET", "/api/v1/tasks/1/move-to/preview"),
    ("POST", "/api/v1/tasks/1/move-to"),
    ("GET", "/api/v1/projects/1/board/stickers"),
    ("POST", "/api/v1/projects/1/board/stickers"),
    ("PATCH", "/api/v1/projects/1/board/stickers/1"),
    ("PATCH", "/api/v1/projects/1/board/stickers/1/position"),
    ("DELETE", "/api/v1/projects/1/board/stickers/1"),
)


async def test_retired_http_routes_are_absent(api_client):
    """Прежние URL отсутствуют даже до этапа аутентификации."""
    app.dependency_overrides.clear()
    failures = []
    for method, path in RETIRED_REQUESTS:
        response = await api_client.request(method, path)
        if response.status_code != 404:
            failures.append(f"{method} {path}: {response.status_code}")
    assert not failures, "Прежние URL остались доступны: " + ", ".join(failures)


async def test_current_user_and_task_placement_routes_keep_distinct_behaviors(api_client):
    """Личный список, размещение в стадии и перенос используют разные сценарии."""
    now = datetime.now(UTC)
    task = TaskSchema(
        id=1,
        project_id=1,
        stage_id=3,
        number=1,
        key="TEST-1",
        title="Задача",
        priority="MEDIUM",
        position=1000,
        created_at=now,
        updated_at=now,
    )
    tasks = AsyncMock(spec=TasksService)
    tasks.get_my_tasks.return_value = []
    tasks.move_task.return_value = task
    transfers = AsyncMock(spec=TaskTransfersService)
    transfers.preview.return_value = TaskTransferPreviewSchema(
        from_key="TEST-1",
        target_kind="PERSONAL",
        target_name="Мои задачи",
        target_key="T",
        losses={},
    )
    transfers.move.return_value = TaskTransferResultSchema(
        task=task,
        from_key="TEST-1",
        to_key="T-1",
        losses={},
    )
    attention = AsyncMock(spec=TaskAttentionService)
    attention.snapshot.return_value = TaskAttentionSnapshot(
        new_tasks=0,
        overdue_tasks=0,
        updated_tasks=0,
        total=0,
        items=[],
    )
    app.dependency_overrides.update(
        {
            get_tasks_service: lambda: tasks,
            get_task_transfers_service: lambda: transfers,
            get_task_attention_service: lambda: attention,
        }
    )
    assert (await api_client.get("/api/v1/users/me/tasks")).json() == []
    assert (await api_client.get("/api/v1/users/me/task-attention")).json()["total"] == 0
    response = await api_client.patch("/api/v1/tasks/1/stage-placement", json={"stage_id": 3})
    assert response.status_code == 200 and response.json()["stage_id"] == 3
    transfers.move.assert_not_awaited()
    response = await api_client.get("/api/v1/tasks/1/transfer/preview", params={"personal": True})
    assert response.status_code == 200 and response.json()["target_kind"] == "PERSONAL"
    response = await api_client.post("/api/v1/tasks/1/transfer", json={"personal": True})
    assert response.status_code == 200 and response.json()["to_key"] == "T-1"
    assert transfers.preview.await_args.kwargs["user_id"] == 1
    assert transfers.move.await_args.kwargs["user_id"] == 1
    assert tasks.move_task.await_count == 1


async def test_my_tasks_project_filter_combines_query_parameters_and_validates_id(api_client):
    """Проект сужает личный список вместе с прочими фильтрами и имеет положительный ID."""
    tasks = AsyncMock(spec=TasksService)
    tasks.get_my_tasks.return_value = []
    attention = AsyncMock(spec=TaskAttentionService)
    attention.filter_ids.return_value = {7, 8}
    app.dependency_overrides.update(
        {
            get_tasks_service: lambda: tasks,
            get_task_attention_service: lambda: attention,
        }
    )
    response = await api_client.get(
        "/api/v1/users/me/tasks",
        params={
            "project_id": 4,
            "role": "watching",
            "include_closed": True,
            "attention": "updated",
        },
    )
    assert response.status_code == 200 and response.json() == []
    tasks.get_my_tasks.assert_awaited_once_with(
        user_id=1,
        project_id=4,
        role="watching",
        include_closed=True,
        attention_task_ids={7, 8},
    )
    attention.filter_ids.assert_awaited_once_with(1, "updated")
    for invalid_id in (0, -1, "unknown"):
        response = await api_client.get(
            "/api/v1/users/me/tasks", params={"project_id": invalid_id}
        )
        assert response.status_code == 422
    assert tasks.get_my_tasks.await_count == 1


async def test_aris_request_scope_does_not_authorize_pending_action_decisions(
    api_client,
    current_principal,
):
    """Право задать вопрос не даёт внешнему клиенту подтвердить опасное действие."""
    key = ApiKeyGrant(
        key_id=uuid4(),
        label="Клиент",
        key_prefix="12345678",
        owner_user_id=1,
        scopes=frozenset({"agent:ask"}),
    )
    principal = replace(current_principal, via_api_token=True, api_key=key)
    auth = AsyncMock(spec=AuthService)
    auth.resolve_principal.return_value = principal
    requests = AsyncMock(spec=AgentRequestsService)
    request_id = uuid4()
    requests.ask_external.return_value = request_id
    requests.wait_external_result.return_value = AgentRequestResultSchema(
        request_id=request_id,
        status="queued",
        write_allowed=False,
    )
    requests.get_external_result.return_value = requests.wait_external_result.return_value
    actions = AsyncMock(spec=AgentActionsService)
    app.dependency_overrides.pop(get_principal)
    app.dependency_overrides.update(
        {
            get_auth_service: lambda: auth,
            get_agent_requests_service: lambda: requests,
            get_agent_actions_service: lambda: actions,
        }
    )
    headers = {"Authorization": "Bearer telos_key_12345678_11111111111111111111111111111111"}
    response = await api_client.post(
        "/api/v1/aris/requests", json={"text": "Мои задачи"}, headers=headers
    )
    assert response.status_code == 200 and response.json()["write_allowed"] is False
    assert requests.ask_external.await_args.kwargs["user_id"] == 1
    assert requests.ask_external.await_args.kwargs["api_key_id"] == key.key_id
    assert (
        await api_client.get(f"/api/v1/aris/requests/{request_id}", headers=headers)
    ).status_code == 200
    assert (await api_client.get("/api/v1/users/me/tasks", headers=headers)).status_code == 403
    for api_key in (key, None):
        auth.resolve_principal.return_value = replace(
            principal, api_key=api_key, scope=ApiTokenScope.WRITE
        )
        for suffix in ("", "/confirm", "/reject"):
            method = "GET" if not suffix else "POST"
            response = await api_client.request(
                method, f"/api/v1/aris/pending/{ACTION_ID}{suffix}", headers=headers
            )
            assert response.status_code == 403
    actions.get_pending.assert_not_awaited()
    actions.decide_pending.assert_not_awaited()
    auth.resolve_principal.return_value = replace(
        principal, api_key=replace(key, scopes=frozenset({"tasks:read"}))
    )
    response = await api_client.post(
        "/api/v1/aris/requests", json={"text": "Мои задачи"}, headers=headers
    )
    assert response.status_code == 403 and requests.ask_external.await_count == 1


async def test_validation_openapi_matches_both_runtime_detail_forms(api_client):
    """422 документирует строку доменной ошибки и список ошибок параметров."""
    from src.dependencies.services import get_board_stickers_service
    from src.services.board_stickers import BoardStickersService

    service = AsyncMock(spec=BoardStickersService)
    app.dependency_overrides[get_board_stickers_service] = lambda: service
    path = "/api/v1/projects/1/boards/3/stickers"
    malformed = await api_client.post(path, json={"body": "   "})
    assert malformed.status_code == 422 and isinstance(malformed.json()["detail"], list)
    HttpValidationErrorResponseSchema.model_validate(malformed.json())
    service.create_sticker.assert_not_awaited()
    service.create_sticker.side_effect = BoardStickerTaskMismatchError({99})
    rejected = await api_client.post(path, json={"body": "Стикер", "task_ids": [99]})
    assert rejected.status_code == 422 and isinstance(rejected.json()["detail"], str)
    HttpValidationErrorResponseSchema.model_validate(rejected.json())
    old_color = await api_client.post(path, json={"body": "Стикер", "color": "yellow"})
    assert old_color.status_code == 422 and service.create_sticker.await_count == 1
    schema = app.openapi()
    documented = schema["paths"]["/api/v1/projects/{project_id}/boards/{board_id}/stickers"][
        "post"
    ]["responses"]["422"]["content"]["application/json"]["schema"]
    assert documented == {"$ref": "#/components/schemas/HttpValidationErrorResponseSchema"}
    detail = schema["components"]["schemas"]["HttpValidationErrorResponseSchema"]["properties"][
        "detail"
    ]
    assert {variant["type"] for variant in detail["anyOf"]} == {"string", "array"}
