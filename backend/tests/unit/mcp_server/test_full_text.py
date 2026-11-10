"""Полное чтение MCP, явные превью и единый канонический контракт."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.db.models.api_tokens import ApiTokenScope
from src.exceptions.access import ResourceNotAvailableError
from src.exceptions.auth import NotAuthenticatedError
from src.exceptions.projects import ProjectsServiceError
from src.exceptions.task_comments import TaskCommentNotFoundError, TaskCommentsRepositoryError
from src.exceptions.tasks import TaskNotFoundError
from src.mcp_server import context as ctx
from src.mcp_server import registry
from src.mcp_server import server as srv
from src.mcp_server.presenters import (
    TEXT_LIMIT,
    TRUNCATION_NOTE,
    comment_item,
    project_detail,
    task_detail,
)
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.tasks import TasksRepository
from src.services.auth import ApiKeyGrant
from src.services.project_query import (
    CommentDto,
    ProjectOverviewDto,
    ProjectQueryService,
    ProjectSummaryDto,
    ResolvedTask,
    TaskDetailsDto,
    TaskPageDto,
    TaskSummaryDto,
)
from tests.unit.mcp_server.conftest import FakeContext, make_principal
from tests.unit.mcp_server.test_tool_context import TransportContext, app_state

LAST_CONDITION = "- [ ] Последнее условие: сохранить **весь Markdown** и 🧭 без изменений.\n"
LONG_TEXT = "# Условия\r\n\n" + "- Кириллица, 漢字, 🙂, е\u0301 и `code` — [ссылка](https://example.test).\n" * 90 + LAST_CONDITION


def configure_reads(services):
    """Задаёт одинаковые исходные тексты в DTO общих сервисов."""
    project = ProjectOverviewDto(
        summary=ProjectSummaryDto("PROJ", "Проект", "ACTIVE", None, None),
        description=LONG_TEXT,
        total_tasks=1,
        stages=[],
    )
    task = TaskDetailsDto(
        summary=TaskSummaryDto("PROJ-142", "Задача", "Новая", False, "MEDIUM", None, None,
                               task_id=7),
        role=None,
        wbs_path=None,
        description=LONG_TEXT,
        comment_count=1,
    )
    comment = CommentDto(
        task_key="PROJ-142",
        author="Борис",
        created_at=datetime(2026, 11, 7, 10, 0, tzinfo=UTC),
        body=LONG_TEXT,
        comment_id=31,
    )
    services.query.resolve_task.return_value = ResolvedTask(7, 1, "PROJ-142")
    services.query.get_project_overview.return_value = project
    services.query.get_task_details.return_value = task
    services.query.list_comments.return_value = [comment]
    services.query.get_comment.return_value = comment
    services.query.list_accessible_projects.return_value = [project.summary]
    services.query.list_tasks.return_value = TaskPageDto([task.summary], 1, 0, 50)
    services.tasks.get_task.return_value = SimpleNamespace(
        start_date=None, checklist_revision=0, report=None, participants=[]
    )
    return project, task, comment


async def test_full_descriptions_preserve_unicode_markdown_and_last_condition(tools):
    """Превью указывает точный способ чтения всего текста; DTO остаётся исходным."""
    services = tools()
    project, task, _ = configure_reads(services)
    for name, arguments, source in (
        ("telos_get_project", {"project_key": "PROJ"}, project),
        ("telos_get_task", {"task_key": "PROJ-142"}, task),
    ):
        tool = srv.mcp_server._tool_manager.get_tool(name)
        preview = await tool.fn(FakeContext(), **arguments)
        assert preview["description"] == LONG_TEXT[:TEXT_LIMIT] + TRUNCATION_NOTE
        assert preview["description_meta"] == {
            "truncated": True,
            "length": len(LONG_TEXT),
            "length_unit": "unicode_code_points",
            "read_full": {"tool": name, "arguments": {**arguments, "full_text": True}},
        }

        read = preview["description_meta"]["read_full"]
        complete = await srv.mcp_server._tool_manager.get_tool(read["tool"]).fn(
            FakeContext(), **read["arguments"]
        )
        assert complete["description"] == LONG_TEXT
        assert complete["description"].endswith(LAST_CONDITION)
        assert complete["description_meta"]["length"] == len(LONG_TEXT)
        assert complete["description_meta"]["truncated"] is False
        assert complete["description_meta"]["read_full"] is None
        assert source.description == LONG_TEXT


async def test_comment_preview_can_be_read_by_id_without_text_loss(tools):
    """Комментарий дочитывается по ID в контексте исходной задачи и проекта."""
    services = tools()
    _, _, comment = configure_reads(services)
    preview, = await srv.list_comments(FakeContext(), task_key="PROJ-142")
    assert preview["body"] == LONG_TEXT[:TEXT_LIMIT] + TRUNCATION_NOTE
    assert preview["comment_id"] == 31
    assert preview["body_meta"] == {
        "truncated": True,
        "length": len(LONG_TEXT),
        "length_unit": "unicode_code_points",
        "read_full": {
            "tool": "telos_get_comment", "arguments": {"task_key": "PROJ-142", "comment_id": 31}
        },
    }
    read = preview["body_meta"]["read_full"]
    complete = await srv.mcp_server._tool_manager.get_tool(read["tool"]).fn(
        FakeContext(), **read["arguments"]
    )
    assert complete["body"] == LONG_TEXT
    assert complete["body"].endswith(LAST_CONDITION)
    assert complete["author"] == "Борис"
    assert complete["comment_id"] == 31
    assert complete["body_meta"]["truncated"] is False
    services.query.get_comment.assert_awaited_once_with(
        task_id=7, project_id=1, task_key="PROJ-142", comment_id=31
    )
    assert comment.body == LONG_TEXT


async def test_empty_boundary_and_literal_truncation_note_are_unambiguous(tools):
    """Пустое превью совместимо; full_text сохраняет пустую строку и исходный суффикс."""
    project, task, comment = configure_reads(tools())
    for text in (None, "", TRUNCATION_NOTE, "я" * TEXT_LIMIT, "я" * (TEXT_LIMIT + 1)):
        for presenter, source, text_field in (
            (project_detail, replace(project, description=text), "description"),
            (task_detail, replace(task, description=text), "description"),
            (comment_item, replace(comment, body=text), "body"),
        ):
            preview = presenter(source)
            meta = preview[f"{text_field}_meta"]
            assert meta["length"] == len(text or "")
            assert meta["truncated"] is (text is not None and len(text) > TEXT_LIMIT)
            if not text:
                assert preview[text_field] is None
            elif len(text) <= TEXT_LIMIT:
                assert preview[text_field] == text
                assert meta["read_full"] is None
            complete = presenter(source, full_text=True)
            assert complete[text_field] == text
            assert complete[f"{text_field}_meta"]["truncated"] is False


async def test_canonical_read_defaults_and_compact_lists_keep_their_shapes(tools):
    """Карточки содержат метаданные превью; компактные списки не несут описаний."""
    project, task, comment = configure_reads(tools())
    for name, arguments, expected in (
        ("telos_get_project", {"project_key": "PROJ"}, project_detail(project)),
        ("telos_get_task", {"task_key": "PROJ-142"}, {
            **task_detail(task), "start_date": None, "checklist_revision": 0,
            "report": None, "participants": [], "revision": task.revision,
        }),
    ):
        result = await srv.mcp_server._tool_manager.get_tool(name).fn(FakeContext(), **arguments)
        assert {key: value for key, value in result.items() if key != "url"} == expected
    comments = await srv.mcp_server._tool_manager.get_tool("telos_list_comments").fn(
        FakeContext(), task_key="PROJ-142"
    )
    assert comments == [{**comment_item(comment), "comment_id": comment.comment_id}]

    projects = await srv.list_projects(FakeContext())
    tasks = await srv.list_tasks(FakeContext(), project_key="PROJ")
    assert "description" not in projects[0]
    assert "description_meta" not in projects[0]
    assert "description" not in tasks["items"][0]
    assert "description_meta" not in tasks["items"][0]


async def test_full_reads_recheck_scopes_access_and_key_revocation(services, monkeypatch):
    """Дочитывание снова проходит настоящий контекст прав, включая прежний READ-токен."""
    configure_reads(services)
    monkeypatch.setattr(ctx, "build_tool_services", lambda **_: services)
    monkeypatch.setattr(registry, "_record_usage", AsyncMock(spec=registry._record_usage))
    context = TransportContext(state=app_state())
    for tool, arguments, required_scope, read in (
        (srv.get_project, {"project_key": "PROJ", "full_text": True}, "projects:read",
         services.query.get_project_overview),
        (srv.get_task, {"task_key": "PROJ-142", "full_text": True}, "tasks:read",
         services.query.get_task_details),
        (srv.get_comment, {"task_key": "PROJ-142", "comment_id": 31}, "tasks:read",
         services.query.get_comment),
    ):
        key = ApiKeyGrant(uuid4(), "Клиент", "test", frozenset({required_scope}), 1)
        principal = replace(make_principal(), api_key=key)
        services.auth.resolve_principal.return_value = principal
        result = await tool(context, **arguments)
        assert result.get("description", result.get("body")) == LONG_TEXT
        read_count = read.await_count

        services.access.ensure_project_access.side_effect = ResourceNotAvailableError(
            resource="Проект", resource_id=1
        )
        with pytest.raises(ToolError, match="Проект недоступен"):
            await tool(context, **arguments)
        assert read.await_count == read_count
        services.access.ensure_project_access.side_effect = None

        services.auth.resolve_principal.return_value = replace(
            principal, api_key=replace(key, scopes=frozenset({"boards:read"}))
        )
        with pytest.raises(ToolError):
            await tool(context, **arguments)
        assert read.await_count == read_count

        services.auth.resolve_principal.side_effect = NotAuthenticatedError()
        with pytest.raises(ToolError, match="Требуется действующий токен"):
            await tool(context, **arguments)
        assert read.await_count == read_count
        services.auth.resolve_principal.side_effect = None

        services.auth.resolve_principal.return_value = make_principal(ApiTokenScope.READ)
        result = await tool(context, **arguments)
        assert result.get("description", result.get("body")) == LONG_TEXT


async def test_comment_query_rejects_missing_foreign_and_moved_sources():
    """Общий сервис сверяет текущий проект задачи и принадлежность комментария."""
    tasks = AsyncMock(spec=TasksRepository)
    comments = AsyncMock(spec=TaskCommentsRepository)
    task = SimpleNamespace(id=7, project_id=1)
    comment = SimpleNamespace(
        id=31, task_id=7, author_name="Борис", body_md=LONG_TEXT,
        created_at=datetime(2026, 11, 7, 10, 0, tzinfo=UTC),
    )

    @asynccontextmanager
    async def scope():
        yield SimpleNamespace(tasks=tasks, comments=comments)

    service = ProjectQueryService(scope=scope)
    arguments = {"task_id": 7, "project_id": 1, "task_key": "PROJ-142", "comment_id": 31}
    tasks.get_by_id.return_value = task
    comments.get_by_id.return_value = comment
    result = await service.get_comment(**arguments)
    assert result.body == LONG_TEXT
    assert result.comment_id == 31

    for invalid_comment in (None, SimpleNamespace(task_id=8)):
        comments.get_by_id.return_value = invalid_comment
        with pytest.raises(TaskCommentNotFoundError):
            await service.get_comment(**arguments)
    comments.get_by_id.return_value = comment
    for invalid_task in (None, SimpleNamespace(project_id=2), SimpleNamespace(project_id=None)):
        tasks.get_by_id.return_value = invalid_task
        with pytest.raises(TaskNotFoundError):
            await service.get_comment(**arguments)
    tasks.get_by_id.return_value = task
    comments.get_by_id.side_effect = TaskCommentsRepositoryError("internal database detail")
    with pytest.raises(ProjectsServiceError):
        await service.get_comment(**arguments)


async def test_full_text_is_optional_and_comment_id_has_a_positive_schema():
    """Новые параметры необязательны; ID берётся из доступного списка комментариев."""
    tools = {tool.name: tool for tool in await srv.mcp_server.list_tools()}
    for name, required in (("telos_get_project", {"project_key"}), ("telos_get_task", {"task_key"})):
        schema = tools[name].input_schema
        assert set(schema["required"]) == required
        assert schema["properties"]["full_text"]["default"] is False
    schema = tools["telos_get_comment"].input_schema
    assert set(schema["required"]) == {"task_key", "comment_id"}
    assert schema["properties"]["comment_id"]["exclusiveMinimum"] == 0
    assert registry.TOOL_REGISTRY["telos_get_comment"].scope == "tasks:read"
