import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.clients.llm import LlmClient
from src.db.models.tasks import TaskPriority
from src.exceptions.wbs_nodes import (
    WbsSuggestionEmptyError,
    WbsSuggestionError,
    WbsSuggestionInvalidError,
)
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.wbs_nodes import WbsNodesRepository
from src.schemas.wbs_suggestion import (
    WbsSuggestedAssignmentSchema,
    WbsSuggestedNodeSchema,
    WbsSuggestionSchema,
)
from src.services.db_scope import WbsSuggestionScope
from src.services.knowledge_events import KnowledgeEvents
from src.services.wbs_suggestion import WbsSuggestionService

PROJECT = SimpleNamespace(id=1, key="PROJ", name="Портал", description_md=None)


def task(task_id: int, wbs_node_id: int | None = None) -> SimpleNamespace:
    """Возвращает дублёр задачи проекта."""
    return SimpleNamespace(
        id=task_id,
        project_id=1,
        number=task_id,
        title=f"Задача {task_id}",
        stage_id=1,
        wbs_node_id=wbs_node_id,
        wbs_position=None,
        canvas_x=None,
        canvas_y=None,
        priority=TaskPriority.MEDIUM,
        assignee=None,
        start_date=None,
        due_date=None,
    )


def node(node_id: int, parent_id: int | None = None, position: float = 1000.0) -> SimpleNamespace:
    """Возвращает дублёр существующего раздела ИСР."""
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=node_id,
        project_id=1,
        parent_id=parent_id,
        title=f"Раздел {node_id}",
        position=position,
        created_at=now,
        updated_at=now,
    )


def draft_node(temp_id: str, parent_temp_id: str | None = None) -> WbsSuggestedNodeSchema:
    """Возвращает предложенный раздел черновика."""
    return WbsSuggestedNodeSchema(
        temp_id=temp_id,
        parent_temp_id=parent_temp_id,
        title=f"Черновик {temp_id}",
    )


def build_service(
    tasks: list[SimpleNamespace] | None = None,
    nodes: list[SimpleNamespace] | None = None,
    llm_output: WbsSuggestionSchema | None = None,
    llm_error: Exception | None = None,
) -> tuple[WbsSuggestionService, dict[str, AsyncMock]]:
    """Собирает сервис предложения ИСР с подменёнными зависимостями."""
    projects_repository = AsyncMock(spec=ProjectsRepository)
    projects_repository.get_by_id.return_value = PROJECT
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = nodes or []
    wbs_repository.save.side_effect = [node(100 + index) for index in range(len(nodes or []) + 40)]
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_project.return_value = tasks or []
    stages_repository = AsyncMock(spec=ProjectStagesRepository)
    stages_repository.get_by_project.return_value = [SimpleNamespace(id=1, name="Бэклог")]
    activity_repository = AsyncMock(spec=TaskActivityRepository)
    unit_of_work = AsyncMock(spec=UnitOfWork)

    llm_client = AsyncMock(spec=LlmClient)
    if llm_error is not None:
        llm_client.get_structured_response.side_effect = llm_error
    else:
        llm_client.get_structured_response.return_value = llm_output or WbsSuggestionSchema()

    knowledge_events = AsyncMock(spec=KnowledgeEvents)
    db = WbsSuggestionScope(
        projects=projects_repository,
        wbs_nodes=wbs_repository,
        tasks=tasks_repository,
        stages=stages_repository,
        activity=activity_repository,
        knowledge_events=knowledge_events,
        unit_of_work=unit_of_work,
    )
    opened_scopes: list[WbsSuggestionScope] = []

    @asynccontextmanager
    async def scope():
        """Отдаёт ту же область: тест наблюдает за числом её открытий."""
        opened_scopes.append(db)
        yield db

    service = WbsSuggestionService(scope=scope, llm_client=llm_client)
    return service, {
        "wbs": wbs_repository,
        "tasks": tasks_repository,
        "activity": activity_repository,
        "llm": llm_client,
        "unit_of_work": unit_of_work,
        "scopes": opened_scopes,
    }


@pytest.mark.asyncio
async def test_suggest_passes_tasks_and_existing_structure_to_model() -> None:
    task_with_checklist = task(11)
    task_with_checklist.checklist = {
        "title": "Приёмка",
        "items": [
            {"text": "Согласовать поля API", "is_completed": True},
            {"text": "Проверить ответ", "is_completed": False},
        ],
    }
    service, mocks = build_service(
        tasks=[task_with_checklist, task(12, wbs_node_id=5)],
        nodes=[node(5)],
        llm_output=WbsSuggestionSchema(
            nodes=[draft_node("n1")],
            assignments=[
                WbsSuggestedAssignmentSchema(task_id=11, node_temp_id="n1"),
                WbsSuggestedAssignmentSchema(task_id=12, node_temp_id="n1"),
            ],
            summary="Разбито по этапам.",
        ),
    )

    result = await service.suggest(project_id=1)

    content = json.loads(mocks["llm"].get_structured_response.await_args.kwargs["content"])
    assert [item["task_id"] for item in content["tasks"]] == [11, 12]
    assert content["tasks"][0]["checklist"]["completed_items"] == 1
    assert content["tasks"][0]["checklist"]["items"][1] == {
        "text": "Проверить ответ",
        "is_completed": False,
    }
    assert content["tasks"][1]["current_section"] == "Раздел 5"
    assert content["existing_structure"] == [{"node_id": 5, "path": "Раздел 5"}]
    assert len(result.assignments) == 2
    assert result.skipped_task_ids == []
    # Предложение — это только черновик: проект не меняется.
    mocks["wbs"].save.assert_not_awaited()
    mocks["tasks"].update.assert_not_awaited()


@pytest.mark.asyncio
async def test_suggest_drops_invalid_parts_of_model_answer() -> None:
    service, _ = build_service(
        tasks=[task(11), task(12)],
        llm_output=WbsSuggestionSchema(
            nodes=[
                draft_node("n1"),
                draft_node("n1"),
                draft_node("n2", parent_temp_id="ghost"),
                draft_node("n3", parent_temp_id="n3"),
            ],
            assignments=[
                WbsSuggestedAssignmentSchema(task_id=11, node_temp_id="n1"),
                WbsSuggestedAssignmentSchema(task_id=11, node_temp_id="n2"),
                WbsSuggestedAssignmentSchema(task_id=99, node_temp_id="n1"),
                WbsSuggestedAssignmentSchema(task_id=12, node_temp_id="ghost"),
            ],
        ),
    )

    result = await service.suggest(project_id=1)

    assert [item.temp_id for item in result.nodes] == ["n1", "n2", "n3"]
    # Ссылки на несуществующего родителя и на самого себя становятся корнями.
    assert all(item.parent_temp_id is None for item in result.nodes)
    assert [(item.task_id, item.node_temp_id) for item in result.assignments] == [(11, "n1")]
    assert result.skipped_task_ids == [12]


@pytest.mark.asyncio
async def test_suggest_rejects_empty_scope_and_wraps_model_failure() -> None:
    """Без задач подсказка не запрашивается, сбой модели становится ошибкой сервиса."""

    service, mocks = build_service(tasks=[])

    with pytest.raises(WbsSuggestionEmptyError):
        await service.suggest(project_id=1)

    mocks["llm"].get_structured_response.assert_not_awaited()

    service, _ = build_service(tasks=[task(11)], llm_error=ValueError("сломался парсер"))

    with pytest.raises(WbsSuggestionError) as exc_info:
        await service.suggest(project_id=1)

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_apply_creates_parents_before_children_and_moves_tasks() -> None:
    service, mocks = build_service(tasks=[task(11), task(12)], nodes=[node(5, position=1000.0)])
    mocks["wbs"].save.side_effect = [node(101), node(102)]

    result = await service.apply(
        project_id=1,
        nodes=[draft_node("child", parent_temp_id="root"), draft_node("root")],
        assignments=[
            WbsSuggestedAssignmentSchema(task_id=11, node_temp_id="root"),
            WbsSuggestedAssignmentSchema(task_id=12, node_temp_id="child"),
        ],
    )

    created = [call.kwargs["data"] for call in mocks["wbs"].save.await_args_list]
    assert [item["parent_id"] for item in created] == [None, 101]
    # Новые корневые разделы встают после существующих.
    assert created[0]["position"] == 2000.0
    moved = [call.kwargs["data"]["wbs_node_id"] for call in mocks["tasks"].update.await_args_list]
    assert moved == [101, 102]
    assert result.created_nodes == 2
    assert result.assigned_tasks == 2
    mocks["unit_of_work"].commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_numbers_tasks_inside_each_node() -> None:
    service, mocks = build_service(tasks=[task(11), task(12)])
    mocks["wbs"].save.side_effect = [node(101)]

    await service.apply(
        project_id=1,
        nodes=[draft_node("root")],
        assignments=[
            WbsSuggestedAssignmentSchema(task_id=11, node_temp_id="root"),
            WbsSuggestedAssignmentSchema(task_id=12, node_temp_id="root"),
        ],
    )

    positions = [
        call.kwargs["data"]["wbs_position"] for call in mocks["tasks"].update.await_args_list
    ]
    assert positions == [1000.0, 2000.0]


@pytest.mark.asyncio
async def test_apply_rejects_unusable_draft() -> None:
    """Черновик отклоняется целиком: неизвестный родитель, чужая задача, задача дважды и слишком большая глубина."""

    service, mocks = build_service(tasks=[task(11)])

    with pytest.raises(WbsSuggestionInvalidError) as exc_info:
        await service.apply(
            project_id=1,
            nodes=[draft_node("child", parent_temp_id="ghost")],
            assignments=[],
        )

    assert exc_info.value.status_code == 422
    mocks["wbs"].save.assert_not_awaited()

    service, mocks = build_service(tasks=[task(11)])

    with pytest.raises(WbsSuggestionInvalidError):
        await service.apply(
            project_id=1,
            nodes=[draft_node("root")],
            assignments=[WbsSuggestedAssignmentSchema(task_id=77, node_temp_id="root")],
        )

    mocks["wbs"].save.assert_not_awaited()

    service, _ = build_service(tasks=[task(11)])

    with pytest.raises(WbsSuggestionInvalidError):
        await service.apply(
            project_id=1,
            nodes=[draft_node("a"), draft_node("b")],
            assignments=[
                WbsSuggestedAssignmentSchema(task_id=11, node_temp_id="a"),
                WbsSuggestedAssignmentSchema(task_id=11, node_temp_id="b"),
            ],
        )

    service, _ = build_service(tasks=[task(11)])

    with pytest.raises(WbsSuggestionInvalidError):
        await service.apply(
            project_id=1,
            nodes=[
                draft_node("n1"),
                draft_node("n2", parent_temp_id="n1"),
                draft_node("n3", parent_temp_id="n2"),
                draft_node("n4", parent_temp_id="n3"),
                draft_node("n5", parent_temp_id="n4"),
            ],
            assignments=[],
        )
