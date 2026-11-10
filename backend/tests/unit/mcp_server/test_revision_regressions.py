"""Регрессии ревизии общего контракта HTTP/MCP и серверного авторства."""

import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, create_autospec

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

from main import app
from src.api.v1.api_key_routes import API_KEY_ROUTE_SCOPES
from src.db.models.api_tokens import ApiTokenScope
from src.db.models.project_members import ProjectRole
from src.dependencies import services as http_services
from src.dependencies.auth import get_principal
from src.exceptions.access import ResourceWriteRequiredError
from src.mcp_server import services as mcp_services
from src.mcp_server.risk_tools import _links
from src.mcp_server.server import mcp_server
from src.services.auth import AuthService
from src.services.knowledge_events import KnowledgeEvents
from src.services.project_members import ProjectMembersService
from src.services.project_query import ResolvedTask
from src.utils.request_actor import reset_request_actor, set_request_actor
from tests.api.endpoints.test_personal_api_keys import key_principal
from tests.unit.mcp_server.conftest import PROJECT_ID, FakeContext
from tests.unit.services.test_access_service import PROJECT_ID as ACCESS_PROJECT_ID
from tests.unit.services.test_access_service import USER_ID, build_service


async def test_risk_owner_uses_the_real_member_service_signature() -> None:
    members = create_autospec(ProjectMembersService, instance=True)
    members.resolve_member_user_id.return_value = 42
    context = SimpleNamespace(services=SimpleNamespace(members=members))
    assert await _links(context, PROJECT_ID, " Boris ", None) == {"owner_user_id": 42}
    members.resolve_member_user_id.assert_awaited_once_with(project_id=PROJECT_ID, username="boris")


async def test_only_comment_author_or_project_owner_can_change_comment() -> None:
    for role, author_id, allowed in (
        (ProjectRole.MEMBER, USER_ID, True),
        (ProjectRole.OWNER, USER_ID + 1, True),
        (ProjectRole.MEMBER, USER_ID + 1, False),
        (ProjectRole.MEMBER, None, False),
        (ProjectRole.OWNER, None, True),
    ):
        service = build_service(
            member_role=role,
            comment=SimpleNamespace(id=10, task_id=7, author_id=author_id),
        )
        if allowed:
            grant = await service.ensure_comment_authorship(comment_id=10, user_id=USER_ID)
            assert grant.project_id == ACCESS_PROJECT_ID
        else:
            with pytest.raises(ResourceWriteRequiredError):
                await service.ensure_comment_authorship(comment_id=10, user_id=USER_ID)


async def test_wbs_draft_accepts_read_key_but_apply_still_requires_write(current_principal):
    principal = key_principal(current_principal, {"structure:read"})
    auth = AsyncMock(spec=AuthService)
    auth.resolve_principal.return_value = principal
    token = set_request_actor(None)
    try:
        for suffix, allowed in (("suggestion", True), ("suggestion/apply", False)):
            path = f"/api/v1/projects/{{project_id}}/wbs/{suffix}"
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": path,
                    "headers": [],
                    "route": SimpleNamespace(path=path),
                }
            )
            if allowed:
                assert (
                    await get_principal(auth, authorization="Bearer synthetic", request=request)
                    is principal
                )
            else:
                with pytest.raises(HTTPException) as raised:
                    await get_principal(auth, authorization="Bearer synthetic", request=request)
                assert raised.value.status_code == 403
    finally:
        reset_request_actor(token)


def test_api_key_scope_table_has_no_nonexistent_methods() -> None:
    actual = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    assert not API_KEY_ROUTE_SCOPES.keys() - actual


async def test_canonical_comment_cannot_replace_authenticated_author(tools):
    services = tools(ApiTokenScope.WRITE)
    services.query.resolve_task.return_value = ResolvedTask(
        task_id=7, project_id=PROJECT_ID, task_key="PROJ-142"
    )
    services.comments.add_comment.return_value = SimpleNamespace(
        id=1, author_name="Тестов Тест", created_at=datetime.now(UTC)
    )
    registered = mcp_server._tool_manager.get_tool("telos_add_comment")
    await registered.fn(
        context=FakeContext(), task_key="PROJ-142", body="Текст", author="Подставной автор"
    )
    assert services.comments.add_comment.await_args.kwargs == {
        "task_id": 7,
        "author_id": 1,
        "body_md": "Текст",
    }


async def test_http_and_mcp_factories_save_authenticated_author_snapshot(monkeypatch):
    from src.repositories.task_activity import TaskActivityRepository
    from src.repositories.task_comments import TaskCommentsRepository
    from src.repositories.tasks import TasksRepository
    from src.repositories.unit_of_work import UnitOfWork
    from src.repositories.users import UsersRepository

    users = AsyncMock(spec=UsersRepository)
    users.get_by_id.return_value = SimpleNamespace(
        id=1,
        username="owner",
        last_name="Проверенный",
        first_name="Автор",
        middle_name=None,
        has_avatar=False,
    )
    comments = AsyncMock(spec=TaskCommentsRepository)

    async def save(**data):
        saved = SimpleNamespace(id=1, created_at=datetime.now(UTC), author=None, **data)
        comments.get_by_id.return_value = saved
        return saved

    comments.save.side_effect = save
    tasks = AsyncMock(spec=TasksRepository)
    tasks.get_by_id.return_value = SimpleNamespace(id=7, project_id=1)
    activity = AsyncMock(spec=TaskActivityRepository)
    unit = AsyncMock(spec=UnitOfWork)
    knowledge = AsyncMock(spec=KnowledgeEvents)
    values = {
        "settings": SimpleNamespace(knowledge=SimpleNamespace(knowledge_enabled=True)),
        "users_repository": users,
        "comments_repository": comments,
        "tasks_repository": tasks,
        "activity_repository": activity,
        "unit_of_work": unit,
        "knowledge_events": knowledge,
    }
    for name, value in (
        ("UsersRepository", users),
        ("TaskCommentsRepository", comments),
        ("TasksRepository", tasks),
        ("TaskActivityRepository", activity),
        ("UnitOfWork", unit),
    ):
        monkeypatch.setattr(mcp_services, name, lambda session, value=value: value)
    monkeypatch.setattr(mcp_services, "build_knowledge_events", lambda *args: knowledge)
    factories = [
        lambda: mcp_services.build_comments_service(object(), values["settings"]),
        lambda: http_services.get_task_comments_service(
            **{
                name: values[name]
                for name in inspect.signature(http_services.get_task_comments_service).parameters
            }
        ),
    ]
    for factory in factories:
        comment = await factory().add_comment(task_id=7, author_id=1, body_md="Текст")
        assert comment.author_name == "Проверенный Автор"
