from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from src.db.models.knowledge_index_jobs import KnowledgeEntityType
from src.db.models.task_activity import TaskActivityEventType
from src.db.models.tasks import TaskPriority
from src.exceptions.tasks import TaskForeignProjectError, TaskNotFoundError
from src.exceptions.wbs_nodes import (
    WbsNodeCycleError,
    WbsNodeForeignProjectError,
    WbsNodeNotFoundError,
    WbsNodesRepositoryError,
    WbsNodesServiceError,
)
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.wbs_nodes import WbsNodesRepository
from src.services.knowledge_events import KnowledgeEvents
from src.services.wbs_nodes import POSITION_STEP, WbsNodesService, _next_position

PROJECT = SimpleNamespace(id=1, key="PROJ")
TODAY = date.today()


def node(
    node_id: int,
    parent_id: int | None = None,
    position: float = 1000.0,
    title: str = "Backend",
    project_id: int = 1,
) -> SimpleNamespace:
    """Возвращает дублёр узла ИСР со всеми полями схемы ответа."""
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=node_id,
        project_id=project_id,
        parent_id=parent_id,
        title=title,
        position=position,
        created_at=now,
        updated_at=now,
    )


def task(
    task_id: int,
    stage_id: int = 1,
    wbs_node_id: int | None = None,
    due_date: date | None = None,
    wbs_position: float | None = None,
    canvas_x: float | None = None,
    canvas_y: float | None = None,
) -> SimpleNamespace:
    """Возвращает дублёр задачи для структуры ИСР."""
    return SimpleNamespace(
        id=task_id,
        project_id=1,
        number=task_id,
        title=f"Задача {task_id}",
        stage_id=stage_id,
        wbs_node_id=wbs_node_id,
        wbs_position=wbs_position,
        canvas_x=canvas_x,
        canvas_y=canvas_y,
        priority=TaskPriority.MEDIUM,
        assignee=None,
        start_date=None,
        due_date=due_date,
    )


def build_service(
    wbs_nodes_repository: AsyncMock | None = None,
    tasks_repository: AsyncMock | None = None,
    activity_repository: AsyncMock | None = None,
    stages_repository: AsyncMock | None = None,
    knowledge_events: AsyncMock | None = None,
    unit_of_work: AsyncMock | None = None,
) -> WbsNodesService:
    """Собирает сервис структуры ИСР с подменёнными репозиториями."""
    projects_repository = AsyncMock(spec=ProjectsRepository)
    projects_repository.get_by_id.return_value = PROJECT
    stages = stages_repository or AsyncMock(spec=ProjectStagesRepository)
    if stages_repository is None:
        stages.get_by_project.return_value = [
            SimpleNamespace(id=1, is_done_stage=False),
            SimpleNamespace(id=2, is_done_stage=True),
        ]
    participants = AsyncMock(spec=TaskParticipantsRepository)
    participants.get_by_task_ids.return_value = {}
    return WbsNodesService(
        wbs_nodes_repository=wbs_nodes_repository or AsyncMock(spec=WbsNodesRepository),
        projects_repository=projects_repository,
        stages_repository=stages,
        tasks_repository=tasks_repository or AsyncMock(spec=TasksRepository),
        activity_repository=activity_repository or AsyncMock(spec=TaskActivityRepository),
        unit_of_work=unit_of_work or AsyncMock(spec=UnitOfWork),
        knowledge_events=knowledge_events or AsyncMock(spec=KnowledgeEvents),
        participants_repository=participants,
    )


@pytest.mark.asyncio
async def test_get_structure_returns_flat_lists_and_stats() -> None:
    """Структура отдаётся плоскими списками со сводкой по задачам."""
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [node(1), node(2, parent_id=1)]
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_project.return_value = [
        task(11, stage_id=1, wbs_node_id=1),
        task(12, stage_id=2, wbs_node_id=2),
        task(13, stage_id=1, due_date=TODAY - timedelta(days=2)),
    ]

    result = await build_service(wbs_repository, tasks_repository).get_structure(project_id=1)

    assert result.stats.total_nodes == 2
    assert result.stats.total_tasks == 3
    assert result.stats.assigned_tasks == 2
    assert result.stats.unassigned_tasks == 1
    assert result.stats.done_tasks == 1
    assert result.stats.overdue_tasks == 1
    assert [item.key for item in result.tasks] == ["PROJ-11", "PROJ-12", "PROJ-13"]
    assert result.tasks[1].is_done is True


@pytest.mark.asyncio
async def test_get_structure_wraps_repository_error() -> None:
    """Сбой репозитория выходит наружу ошибкой сервисного слоя."""
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.side_effect = WbsNodesRepositoryError("БД недоступна")

    with pytest.raises(WbsNodesServiceError) as exc_info:
        await build_service(wbs_repository).get_structure(project_id=1)

    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_create_node_computes_position_within_its_level() -> None:
    """Новый узел встаёт в конец своего уровня, первый — на базовую позицию."""
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [
        node(1, position=1000.0),
        node(2, position=2000.0),
        node(3, parent_id=1, position=5000.0),
    ]
    wbs_repository.save.return_value = node(4, position=3000.0)
    service = build_service(wbs_repository)

    await service.create_node(project_id=1, title="Deployment", parent_id=None)
    assert wbs_repository.save.await_args.kwargs["data"]["position"] == 3000.0

    wbs_repository.get_by_project.return_value = [node(1)]
    wbs_repository.save.return_value = node(2, parent_id=1)
    await service.create_node(project_id=1, title="API", parent_id=1)
    assert wbs_repository.save.await_args.kwargs["data"]["position"] == POSITION_STEP


@pytest.mark.asyncio
async def test_move_node_into_itself_or_its_descendant_raises_cycle() -> None:
    """Узел нельзя вложить ни в себя, ни в собственного потомка."""
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [
        node(1),
        node(2, parent_id=1),
        node(3, parent_id=2),
    ]
    service = build_service(wbs_repository)

    with pytest.raises(WbsNodeCycleError) as exc_info:
        await service.move_node(project_id=1, node_id=1, parent_id=3, before_id=None)
    assert exc_info.value.status_code == 409

    with pytest.raises(WbsNodeCycleError):
        await service.move_node(project_id=1, node_id=1, parent_id=1, before_id=None)

    wbs_repository.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_structure_only_changes_do_not_reindex_knowledge() -> None:
    """Создание узла и перестановка соседей не ставят заданий индексации.

    Текст задач при этом не меняется, а переиндексация проекта — дорогая
    операция: лишний повод её запустить дороже самого изменения.
    """
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = []
    wbs_repository.save.return_value = node(1)
    knowledge_events = AsyncMock(spec=KnowledgeEvents)
    await build_service(wbs_repository, knowledge_events=knowledge_events).create_node(
        project_id=1, title="Backend", parent_id=None
    )
    assert knowledge_events.method_calls == []

    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [
        node(1, position=1000.0),
        node(2, position=2000.0),
    ]
    wbs_repository.update.return_value = node(2, position=500.0)
    knowledge_events = AsyncMock(spec=KnowledgeEvents)
    await build_service(wbs_repository, knowledge_events=knowledge_events).move_node(
        project_id=1, node_id=2, parent_id=None, before_id=1
    )
    knowledge_events.upsert_many.assert_not_awaited()
    knowledge_events.reindex_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_node_enqueues_tasks_from_subtree_before_commit() -> None:
    """Переименование ставит задачи поддерева в очередь до коммита.

    Порядок принципиален: задание обязано попасть в базу той же
    транзакцией, что и само переименование.
    """
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    root = node(1)
    wbs_repository.get_by_id.return_value = root
    wbs_repository.get_by_project.return_value = [root, node(2, parent_id=1), node(3)]
    wbs_repository.update.return_value = node(1, title="Platform")
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_ids_by_wbs_nodes.return_value = [11, 12]
    knowledge_events = AsyncMock(spec=KnowledgeEvents)
    unit_of_work = AsyncMock(spec=UnitOfWork)
    manager = MagicMock()
    manager.attach_mock(knowledge_events.upsert_many, "enqueue_many")
    manager.attach_mock(unit_of_work.commit, "commit")

    await build_service(
        wbs_repository,
        tasks_repository,
        knowledge_events=knowledge_events,
        unit_of_work=unit_of_work,
    ).update_node(project_id=1, node_id=1, title="Platform")

    tasks_repository.get_ids_by_wbs_nodes.assert_awaited_once_with({1, 2})
    knowledge_events.upsert_many.assert_awaited_once_with(
        project_id=1,
        entity_type=KnowledgeEntityType.TASK,
        entity_ids=[11, 12],
    )
    assert manager.mock_calls[-2:] == [
        call.enqueue_many(**knowledge_events.upsert_many.await_args.kwargs),
        call.commit(),
    ]


@pytest.mark.asyncio
async def test_reparent_and_delete_enqueue_only_tasks_of_the_subtree() -> None:
    """Перенос и удаление узла переиндексируют только задачи поддерева.

    Обе операции меняют путь ИСР у одних и тех же задач, поэтому и
    правило у них одно: переиндексация проекта целиком не нужна.
    """
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [node(1), node(2, parent_id=1), node(3)]
    wbs_repository.update.return_value = node(1, parent_id=3)
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_ids_by_wbs_nodes.return_value = [20, 21]
    knowledge_events = AsyncMock(spec=KnowledgeEvents)
    await build_service(
        wbs_repository, tasks_repository, knowledge_events=knowledge_events
    ).move_node(project_id=1, node_id=1, parent_id=3, before_id=None)
    tasks_repository.get_ids_by_wbs_nodes.assert_awaited_once_with({1, 2})
    assert knowledge_events.upsert_many.await_args.kwargs["entity_ids"] == [20, 21]
    knowledge_events.reindex_project.assert_not_awaited()

    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [node(1), node(2, parent_id=1), node(3)]
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_ids_by_wbs_nodes.return_value = [31, 32]
    tasks_repository.clear_wbs_node.return_value = 2
    knowledge_events = AsyncMock(spec=KnowledgeEvents)
    await build_service(
        wbs_repository, tasks_repository, knowledge_events=knowledge_events
    ).delete_node(project_id=1, node_id=1)
    knowledge_events.upsert_many.assert_awaited_once_with(
        project_id=1,
        entity_type=KnowledgeEntityType.TASK,
        entity_ids=[31, 32],
    )
    knowledge_events.reindex_project.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_node_releases_tasks_without_deleting_them() -> None:
    """Удаление раздела возвращает его задачи в пул, а не удаляет их."""
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [
        node(1),
        node(2, parent_id=1),
        node(3, parent_id=2),
        node(4),
    ]
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.clear_wbs_node.return_value = 8

    result = await build_service(wbs_repository, tasks_repository).delete_node(
        project_id=1,
        node_id=1,
    )

    assert tasks_repository.clear_wbs_node.await_args.kwargs["node_ids"] == {1, 2, 3}
    assert result.deleted_nodes == 3
    assert result.released_tasks == 8
    tasks_repository.delete.assert_not_awaited()
    wbs_repository.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_assign_and_unassign_task_record_the_change_in_history() -> None:
    """Перенос задачи в раздел и обратно записывается событием истории."""
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [node(5, title="API")]
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = task(11)
    tasks_repository.get_by_wbs_node.return_value = []
    tasks_repository.update.return_value = task(11, wbs_node_id=5, wbs_position=POSITION_STEP)
    activity_repository = AsyncMock(spec=TaskActivityRepository)

    result = await build_service(
        wbs_repository, tasks_repository, activity_repository
    ).assign_task(project_id=1, task_id=11, wbs_node_id=5)

    assert tasks_repository.update.await_args.kwargs["data"] == {
        "wbs_node_id": 5,
        "wbs_position": POSITION_STEP,
        "canvas_x": None,
        "canvas_y": None,
    }
    event = activity_repository.save.await_args.kwargs
    assert event["event_type"] == TaskActivityEventType.WBS_NODE_CHANGED
    assert event["from_value"] is None
    assert event["to_value"] == "API"
    assert result.wbs_node_id == 5

    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = task(11, wbs_node_id=5, wbs_position=1000.0)
    tasks_repository.update.return_value = task(11)
    activity_repository = AsyncMock(spec=TaskActivityRepository)

    result = await build_service(
        wbs_repository, tasks_repository, activity_repository
    ).unassign_task(project_id=1, task_id=11)

    assert tasks_repository.update.await_args.kwargs["data"] == {
        "wbs_node_id": None,
        "wbs_position": None,
        "canvas_x": None,
        "canvas_y": None,
    }
    event = activity_repository.save.await_args.kwargs
    assert event["from_value"] == "API"
    assert event["to_value"] is None
    assert result.wbs_node_id is None


@pytest.mark.asyncio
async def test_assign_task_to_same_node_is_noop() -> None:
    """Повторное назначение в тот же раздел не пишет ни задачу, ни историю."""
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [node(5)]
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = task(11, wbs_node_id=5)
    activity_repository = AsyncMock(spec=TaskActivityRepository)

    await build_service(wbs_repository, tasks_repository, activity_repository).assign_task(
        project_id=1,
        task_id=11,
        wbs_node_id=5,
    )

    tasks_repository.update.assert_not_awaited()
    activity_repository.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_place_task_switches_between_canvas_and_structure() -> None:
    """Холст и раздел взаимоисключающи: одно место хранения за раз.

    Задача на холсте теряет позицию в разделе и не пишет событие истории,
    а задача, положенная в раздел, теряет координаты холста.
    """
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = []
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = task(11)
    tasks_repository.update.return_value = task(11, canvas_x=420.0, canvas_y=180.0)
    activity_repository = AsyncMock(spec=TaskActivityRepository)

    result = await build_service(
        wbs_repository, tasks_repository, activity_repository
    ).place_task(project_id=1, task_id=11, wbs_node_id=None, canvas_x=420.0, canvas_y=180.0)

    assert tasks_repository.update.await_args.kwargs["data"] == {
        "wbs_node_id": None,
        "wbs_position": None,
        "canvas_x": 420.0,
        "canvas_y": 180.0,
    }
    # Задача и так была вне структуры: раздел не менялся, событие не нужно.
    activity_repository.save.assert_not_awaited()
    assert result.canvas_x == 420.0

    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [node(5, title="API")]
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = task(11, canvas_x=420.0, canvas_y=180.0)
    tasks_repository.get_by_wbs_node.return_value = []
    tasks_repository.update.return_value = task(11, wbs_node_id=5, wbs_position=POSITION_STEP)

    await build_service(wbs_repository, tasks_repository).place_task(
        project_id=1,
        task_id=11,
        wbs_node_id=5,
    )

    assert tasks_repository.update.await_args.kwargs["data"]["canvas_x"] is None
    assert tasks_repository.update.await_args.kwargs["data"]["canvas_y"] is None


@pytest.mark.asyncio
async def test_wbs_write_operations_reject_foreign_and_unknown_objects() -> None:
    """Неизвестный родитель, узел чужого проекта и некорректное назначение задачи отклоняются до записи."""
    # Несуществующий родитель — отказ до записи.
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [node(1)]

    with pytest.raises(WbsNodeNotFoundError) as exc_info:
        await build_service(wbs_repository).create_node(
            project_id=1,
            title="API",
            parent_id=77,
        )

    assert exc_info.value.status_code == 404
    wbs_repository.save.assert_not_awaited()
    # Узел чужого проекта не читается и не пишется.
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_id.return_value = node(1, project_id=2)

    with pytest.raises(WbsNodeForeignProjectError):
        await build_service(wbs_repository).update_node(
            project_id=1,
            node_id=1,
            title="Platform",
        )

    wbs_repository.get_by_project.assert_not_awaited()
    wbs_repository.update.assert_not_awaited()
    # Отказ наступает до записи для всех трёх некорректных случаев.
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = []
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = task(11)
    with pytest.raises(WbsNodeNotFoundError):
        await build_service(wbs_repository, tasks_repository).assign_task(
            project_id=1, task_id=11, wbs_node_id=77
        )
    tasks_repository.update.assert_not_awaited()

    tasks_repository = AsyncMock(spec=TasksRepository)
    foreign_task = task(11)
    foreign_task.project_id = 5
    tasks_repository.get_by_id.return_value = foreign_task
    with pytest.raises(TaskForeignProjectError) as exc_info:
        await build_service(tasks_repository=tasks_repository).assign_task(
            project_id=1, task_id=11, wbs_node_id=5
        )
    assert exc_info.value.status_code == 409

    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = None
    with pytest.raises(TaskNotFoundError):
        await build_service(tasks_repository=tasks_repository).assign_task(
            project_id=1, task_id=999, wbs_node_id=5
        )


@pytest.mark.asyncio
async def test_wbs_positions_are_computed_and_compacted() -> None:
    """Позиция узла считается по соседям и уплотняется, позиция задачи внутри раздела — тоже."""
    # Позиция вычисляется по соседям: между ними либо в конец уровня.
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [
        node(1, position=1000.0),
        node(2, position=2000.0),
        node(3, position=3000.0),
    ]
    wbs_repository.update.return_value = node(3, position=1500.0)
    await build_service(wbs_repository).move_node(
        project_id=1, node_id=3, parent_id=None, before_id=2
    )
    assert wbs_repository.update.await_args.kwargs["data"]["position"] == 1500.0

    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [
        node(1, position=1000.0),
        node(2, position=2000.0),
        node(3, parent_id=1, position=1000.0),
    ]
    wbs_repository.update.return_value = node(3, position=3000.0)
    await build_service(wbs_repository).move_node(
        project_id=1, node_id=3, parent_id=None, before_id=None
    )
    updated = wbs_repository.update.await_args.kwargs["data"]
    assert updated["position"] == 3000.0
    assert updated["parent_id"] is None
    # Когда между соседями не осталось зазора, уровень перенумеровывается.
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [
        node(1, position=1000.0),
        node(2, position=1000.0000001),
        node(3, parent_id=1, position=500.0),
    ]
    wbs_repository.update.return_value = node(3)

    await build_service(wbs_repository).move_node(
        project_id=1,
        node_id=3,
        parent_id=None,
        before_id=2,
    )

    wbs_repository.update_positions.assert_awaited_once()
    positions = wbs_repository.update_positions.await_args.kwargs["positions"]
    assert positions == {1: 1000.0, 2: 2000.0}
    assert wbs_repository.update.await_args.kwargs["data"]["position"] == 1500.0
    # Позиция внутри раздела считается по соседям и уплотняется при нужде.
    wbs_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_repository.get_by_project.return_value = [node(5)]
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = task(11)
    tasks_repository.get_by_wbs_node.return_value = [
        task(21, wbs_node_id=5, wbs_position=1000.0),
        task(22, wbs_node_id=5, wbs_position=2000.0),
    ]
    tasks_repository.update.return_value = task(11, wbs_node_id=5, wbs_position=1500.0)

    await build_service(wbs_repository, tasks_repository).place_task(
        project_id=1,
        task_id=11,
        wbs_node_id=5,
        before_task_id=22,
    )

    assert tasks_repository.update.await_args.kwargs["data"]["wbs_position"] == 1500.0

    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = task(11)
    tasks_repository.get_by_wbs_node.return_value = [
        task(21, wbs_node_id=5),
        task(22, wbs_node_id=5),
    ]
    tasks_repository.update.return_value = task(11, wbs_node_id=5, wbs_position=3000.0)

    await build_service(wbs_repository, tasks_repository).place_task(
        project_id=1,
        task_id=11,
        wbs_node_id=5,
    )

    tasks_repository.update_wbs_positions.assert_awaited_once_with(
        positions={21: 1000.0, 22: 2000.0},
    )
    assert tasks_repository.update.await_args.kwargs["data"]["wbs_position"] == 3000.0
    # Расчёт позиции: пустой уровень, вставка перед первым, кончившийся зазор.
    assert _next_position(positions=[], before_index=0) == POSITION_STEP
    assert _next_position(positions=[1000.0], before_index=0) == 500.0
    assert _next_position(positions=[1000.0, 1000.0000001], before_index=1) is None
