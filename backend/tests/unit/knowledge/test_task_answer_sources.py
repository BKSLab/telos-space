"""Карточки задач проходят настоящий цикл Ариса и финальную проверку доступа."""

import json
from contextlib import asynccontextmanager
from copy import copy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from src.agent.actions import ProjectAction
from src.agent.aris import ArisMode
from src.agent.tools import AgentTool, AgentToolContext, AgentToolExecutor, AgentToolRequest
from src.db.models.api_tokens import ApiTokenScope
from src.exceptions.access import ResourceNotAvailableError
from src.exceptions.ai import AgentAnswerError
from src.schemas.enums import TaskKind, TaskPriority, TaskStatus
from src.schemas.project_tool_inputs import (
    MyTasksInput,
    TaskInput,
    TaskListInput,
    TaskLookupInput,
    TaskRefInput,
)
from src.schemas.tasks import TaskSchema
from src.services.access import AccessGrant
from src.services.agent_actions import AgentActionsService
from src.services.agent_personal_actions import get_any_task
from src.services.agent_task_actions import get_task
from src.services.auth import Principal
from tests.unit.knowledge.agent_models import LegacyOutput, final, tool_results
from tests.unit.knowledge.test_project_agent import build_service

pytestmark = pytest.mark.asyncio


def task_schema(*, project_id=None, task_id=7, title="Первое чтение"):
    now = datetime.now(UTC)
    return TaskSchema(
        id=task_id,
        kind=TaskKind.PROJECT if project_id else TaskKind.PERSONAL,
        project_id=project_id,
        stage_id=3 if project_id else None,
        status=None if project_id else TaskStatus.IN_PROGRESS,
        number=12,
        key="PROJ-12" if project_id else "PERSONAL-12",
        title=title,
        description_md="Описание первого чтения",
        priority=TaskPriority.HIGH,
        assignee="Первый исполнитель",
        position=1000,
        created_at=now,
        updated_at=now,
    )


def personal_service(*, project_id=None):
    service, _, clients, source_db = build_service()
    initial = task_schema(project_id=project_id)
    current = initial.model_copy(
        update={
            "title": "Текущий заголовок",
            "description_md": "Актуальное описание",
            "priority": TaskPriority.LOW,
            "assignee": "Текущий исполнитель",
        }
    )
    db = SimpleNamespace(
        access=SimpleNamespace(ensure_task_access=AsyncMock()),
        tasks=SimpleNamespace(get_task=AsyncMock(side_effect=[initial, current])),
    )

    async def read(context, arguments):
        return await get_any_task(db, context, arguments)

    service.tools = AgentToolExecutor(
        [
            AgentTool("telos_get_any_task", "Прочитать доступную задачу", TaskRefInput, read),
        ]
    )
    return service, clients, source_db, db, initial, current


async def ask_personal(service):
    return await service.ask_personal(
        question="Что с задачей?",
        history=[],
        memory="",
        actor={"user_id": 1},
        execution=AgentToolContext(project_id=None, user_id=1, mode=ArisMode.WIDGET_PERSONAL),
    )


@pytest.mark.parametrize("tool_name", ["telos_get_task", "telos_get_any_task"])
async def test_project_direct_read_without_model_sources_returns_one_current_primary_task(
    tool_name,
):
    service, project, clients, db = build_service()
    task = db.tasks.get_by_ids.return_value[0]
    handler = AsyncMock(return_value={"source_id": "task:7", "task": {"id": 7}})
    service.tools = AgentToolExecutor(
        [
            AgentTool(tool_name, "Прочитать задачу", TaskInput, handler),
        ]
    )
    step = 0

    async def respond(messages, info):
        nonlocal step
        step += 1
        if step == 1:
            workflow = "tasks" if tool_name == "telos_get_task" else "personal"
            return ModelResponse(parts=[ToolCallPart("telos_load_capability", {"id": workflow})])
        if step == 2:
            assert tool_name in {tool.name for tool in info.function_tools}
            return ModelResponse(parts=[ToolCallPart(tool_name, {"task_id": 7})])
        assert tool_results(messages)[0]["result"]["source_handle"].startswith("SRC_")
        task.title = "Текущий заголовок"
        task.description_md = "Актуальное описание"
        task.priority = TaskPriority.LOW
        return final("Проверена задача.")

    service.agent_model = FunctionModel(respond)
    answer = await service.ask(project_id=project.id, question="Что с PROJ-12?", history=[])

    assert step == 3 and handler.await_count == 1
    assert len(answer.sources) == 1
    source = answer.sources[0]
    assert (source.source_id, source.entity_id, source.task_id, source.is_primary) == (
        "task:7",
        7,
        7,
        True,
    )
    assert "Текущий заголовок" in source.title
    assert source.card.key == "PROJ-12" and source.card.priority == "LOW"
    assert source.card.summary == "Актуальное описание"
    assert db.sources.get_project_rows.await_count >= 2
    clients.embedding_client.get_embedding.assert_not_awaited()
    clients.llm_client.get_structured_response.assert_not_awaited()


@pytest.mark.parametrize("kind", ["list", "multiple"])
async def test_project_list_or_multiple_direct_reads_do_not_choose_an_arbitrary_task(kind):
    service, project, clients, db = build_service()
    second = copy(db.tasks.get_by_ids.return_value[0])
    second.id, second.number, second.title = 8, 13, "Другая задача"
    db.tasks.get_by_ids.return_value = [db.tasks.get_by_ids.return_value[0], second]

    async def read(_context, args):
        return {"source_id": f"task:{args.task_id}", "task": {"id": args.task_id}}

    listed = AsyncMock(return_value={"items": [{"source_id": "task:7", "task": {"id": 7}}]})
    service.tools = AgentToolExecutor(
        [
            AgentTool("telos_get_task", "Прочитать задачу", TaskInput, read),
            AgentTool("telos_list_tasks", "Список задач", TaskListInput, listed),
        ]
    )

    async def respond(*, content, **_kwargs):
        if json.loads(content).get("read_results"):
            return LegacyOutput(answer="Проверены доступные задачи.")
        calls = (
            [AgentToolRequest(name="telos_list_tasks")]
            if kind == "list"
            else [
                AgentToolRequest(name="telos_get_task", arguments={"task_id": task_id})
                for task_id in (7, 8)
            ]
        )
        return LegacyOutput(tool_calls=calls)

    clients.llm_client.get_structured_response.side_effect = respond
    answer = await service.ask(project_id=project.id, question="Какие задачи есть?", history=[])

    assert answer.sources == []


@pytest.mark.parametrize("project_id", [None, 1], ids=["personal-task", "project-task"])
@pytest.mark.parametrize("selection", ["omitted", "explicit"])
async def test_personal_direct_read_returns_fresh_task_after_acl_with_fallback_or_valid_handle(
    project_id, selection
):
    service, clients, source_db, db, initial, current = personal_service(project_id=project_id)

    async def respond(*, content, **_kwargs):
        results = json.loads(content).get("read_results")
        if not results:
            return LegacyOutput(
                tool_calls=[
                    AgentToolRequest(name="telos_get_any_task", arguments={"task_id": initial.id})
                ]
            )
        handle = results[0]["result"]["source_handle"]
        assert (
            handle.startswith("SRC_") and results[0]["result"]["source_id"] == f"task:{initial.id}"
        )
        return LegacyOutput(
            answer="Проверена задача.",
            source_ids=[handle, f"task:{initial.id}", "SRC_invented_1"]
            if selection == "explicit"
            else [],
            primary_reference=handle if selection == "explicit" else None,
        )

    clients.llm_client.get_structured_response.side_effect = respond
    answer = await ask_personal(service)

    assert len(answer.sources) == 1
    source = answer.sources[0]
    assert (source.source_id, source.entity_id, source.task_id, source.is_primary) == (
        "task:7",
        7,
        7,
        True,
    )
    assert source.title == f"{current.key} · {current.title}"
    assert (
        source.excerpt == current.description_md and source.card.summary == current.description_md
    )
    assert source.card.priority == "LOW" and source.card.assignee == current.assignee
    assert db.access.ensure_task_access.await_count == db.tasks.get_task.await_count == 2
    source_db.sources.get_project_rows.assert_not_awaited()
    clients.embedding_client.get_embedding.assert_not_awaited()
    clients.qdrant_client.search.assert_not_awaited()


@pytest.mark.parametrize("kind", ["list", "multiple"])
async def test_personal_list_or_multiple_direct_reads_do_not_select_an_arbitrary_task(kind):
    service, clients, source_db, db, initial, _ = personal_service()
    second = initial.model_copy(update={"id": 8, "key": "PERSONAL-13", "title": "Другая задача"})
    db.tasks.get_task.side_effect = [initial, second]
    listed = AsyncMock(
        return_value={"items": [{"source_id": "task:7", "task": initial.model_dump(mode="json")}]}
    )
    service.tools.tools["telos_list_my_tasks"] = AgentTool(
        "telos_list_my_tasks", "Мои задачи", MyTasksInput, listed
    )

    async def respond(*, content, **_kwargs):
        if json.loads(content).get("read_results"):
            return LegacyOutput(answer="Проверены задачи.")
        calls = (
            [AgentToolRequest(name="telos_list_my_tasks")]
            if kind == "list"
            else [
                AgentToolRequest(name="telos_get_any_task", arguments={"task_id": task_id})
                for task_id in (7, 8)
            ]
        )
        return LegacyOutput(tool_calls=calls)

    clients.llm_client.get_structured_response.side_effect = respond
    answer = await ask_personal(service)

    assert answer.sources == []
    assert db.access.ensure_task_access.await_count == (0 if kind == "list" else 2)
    source_db.sources.get_project_rows.assert_not_awaited()


@pytest.mark.parametrize("personal", [False, True], ids=["project", "personal"])
async def test_forged_source_ids_and_primary_reference_never_become_task_cards(personal):
    if personal:
        service, clients, _, db, _, _ = personal_service()
        name = "telos_get_any_task"
    else:
        service, _, clients, db = build_service()
        name = "telos_get_task"
        handler = AsyncMock(return_value={"source_id": "task:7", "task": {"id": 7}})
        service.tools = AgentToolExecutor([AgentTool(name, "Прочитать задачу", TaskInput, handler)])

    async def respond(*, content, **_kwargs):
        if not json.loads(content).get("read_results"):
            return LegacyOutput(tool_calls=[AgentToolRequest(name=name, arguments={"task_id": 7})])
        return LegacyOutput(
            answer="Данные проверены.",
            source_ids=["task:7", "task:999999", "SRC_forged_1"],
            primary_reference="SRC_forged_1",
        )

    clients.llm_client.get_structured_response.side_effect = respond
    answer = (
        await ask_personal(service)
        if personal
        else await service.ask(project_id=1, question="Покажи задачу", history=[])
    )

    assert answer.sources == []
    if personal:
        assert db.access.ensure_task_access.await_count == db.tasks.get_task.await_count == 1


@pytest.mark.parametrize("change", ["delete", "move"])
async def test_project_task_removed_from_current_scope_is_not_returned_from_cached_read(change):
    service, _, clients, db = build_service()
    handler = AsyncMock(return_value={"source_id": "task:7", "task": {"id": 7}})
    service.tools = AgentToolExecutor(
        [AgentTool("telos_get_task", "Прочитать задачу", TaskInput, handler)]
    )

    async def respond(*, content, **_kwargs):
        results = json.loads(content).get("read_results")
        if not results:
            return LegacyOutput(
                tool_calls=[AgentToolRequest(name="telos_get_task", arguments={"task_id": 7})]
            )
        if change == "move":
            db.tasks.get_by_ids.return_value[0].project_id = 2
        else:
            db.tasks.get_by_ids.return_value = []
            db.tasks.search_ranked.return_value = []
        return LegacyOutput(
            answer="Данные проверены.", source_ids=[results[0]["result"]["source_handle"]]
        )

    clients.llm_client.get_structured_response.side_effect = respond
    answer = await service.ask(project_id=1, question="Покажи задачу", history=[])

    assert answer.sources == []
    assert db.sources.get_project_rows.await_count >= 2


async def test_personal_final_acl_denial_drops_task_card_without_reusing_cached_payload():
    service, clients, source_db, db, _, _ = personal_service()
    db.access.ensure_task_access.side_effect = [
        None,
        ResourceNotAvailableError(resource="task", resource_id=7),
    ]

    async def respond(*, content, **_kwargs):
        results = json.loads(content).get("read_results")
        if not results:
            return LegacyOutput(
                tool_calls=[AgentToolRequest(name="telos_get_any_task", arguments={"task_id": 7})]
            )
        return LegacyOutput(
            answer="Данные проверены.", primary_reference=results[0]["result"]["source_handle"]
        )

    clients.llm_client.get_structured_response.side_effect = respond
    with pytest.raises(AgentAnswerError, match="Доступ к задаче изменился"):
        await ask_personal(service)
    assert db.access.ensure_task_access.await_count == 2
    assert db.tasks.get_task.await_count == 1
    source_db.sources.get_project_rows.assert_not_awaited()


async def test_project_task_card_rechecks_acl_after_membership_revoked_during_answer():
    """A global owned conversation must not retain fresh project data after access loss."""
    service, project, clients, source_db = build_service()
    principal = Principal(
        user_id=1,
        username="reader",
        last_name="Тест",
        first_name="Читатель",
        middle_name=None,
        scope=ApiTokenScope.READ,
        via_api_token=False,
    )
    grant = AccessGrant(project_id=project.id, resource_id=7, is_owner=False, can_write=False)
    tool_db = SimpleNamespace(
        access=SimpleNamespace(
            ensure_project_access=AsyncMock(return_value=grant),
            ensure_task_access=AsyncMock(return_value=grant),
        ),
        auth=SimpleNamespace(refresh_principal=AsyncMock(return_value=principal)),
        users=SimpleNamespace(
            get_by_id=AsyncMock(
                return_value=SimpleNamespace(
                    id=1,
                    is_active=True,
                    username="reader",
                    last_name="Тест",
                    first_name="Читатель",
                    middle_name=None,
                )
            )
        ),
        tasks=SimpleNamespace(get_task=AsyncMock(return_value=task_schema(project_id=project.id))),
        stages=SimpleNamespace(get_stage_list=AsyncMock(return_value=[])),
    )

    @asynccontextmanager
    async def tool_scope():
        yield tool_db

    actions = AgentActionsService(
        scope=tool_scope,
        actions=[
            ProjectAction(
                "telos_get_task",
                "Прочитать задачу",
                "Текущее чтение с ACL",
                TaskLookupInput,
                get_task,
                mutating=False,
            )
        ],
    )

    async def read(context, arguments):
        return await actions.execute(
            context=context, name="telos_get_task", arguments=arguments.model_dump()
        )

    service.tools = AgentToolExecutor(
        [
            AgentTool("telos_get_task", "Прочитать задачу", TaskLookupInput, read),
        ]
    )

    async def respond(*, content, **_kwargs):
        results = json.loads(content).get("read_results")
        if not results:
            return LegacyOutput(
                tool_calls=[
                    AgentToolRequest(
                        name="telos_get_task",
                        arguments={"task_id": 7},
                    )
                ]
            )
        tool_db.access.ensure_project_access.side_effect = ResourceNotAvailableError(
            resource="project", resource_id=project.id
        )
        source_db.tasks.get_by_ids.return_value[0].title = "Новые данные после отзыва доступа"
        return LegacyOutput(
            answer="Проверена задача.", source_ids=[results[0]["result"]["source_handle"]]
        )

    clients.llm_client.get_structured_response.side_effect = respond
    with pytest.raises(AgentAnswerError):
        await service.ask(
            project_id=project.id,
            question="Что с задачей?",
            history=[],
            actor={"user_id": 1},
            execution=AgentToolContext(project_id=project.id, user_id=1, principal=principal),
            conversation_id="conversation:owned-global",
        )

    assert tool_db.access.ensure_project_access.await_count == 2
    tool_db.access.ensure_task_access.assert_awaited_once_with(task_id=7, user_id=1)
    tool_db.tasks.get_task.assert_awaited_once_with(7)
    assert tool_db.auth.refresh_principal.await_count == 2
    assert all(call.args == (principal,) for call in tool_db.auth.refresh_principal.await_args_list)
