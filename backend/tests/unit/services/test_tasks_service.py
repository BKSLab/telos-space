from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.db.models.knowledge_index_jobs import KnowledgeEntityType
from src.db.models.project_members import ProjectRole
from src.db.models.task_activity import TaskActivityEventType
from src.db.models.task_participants import TaskParticipantRole
from src.db.models.tasks import TaskPriority
from src.exceptions.project_stages import (
    ProjectStageForeignProjectError,
    ProjectStageNotFoundError,
)
from src.exceptions.projects import ProjectNotFoundError
from src.exceptions.tasks import (
    TaskDateRangeError,
    TaskNotFoundError,
    TaskNumberAllocationError,
    TaskNumberAlreadyExistsRepositoryError,
    TaskParticipantNotProjectMemberError,
    TaskReporterPermissionError,
    TasksRepositoryError,
    TasksServiceError,
)
from src.exceptions.wbs_nodes import WbsNodeForeignProjectError, WbsNodeNotFoundError
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.wbs_nodes import WbsNodesRepository
from src.services.knowledge_events import KnowledgeEvents
from src.services.tasks import TasksService
from src.storage.task_attachments import TaskAttachmentStorage

PROJECT = SimpleNamespace(id=1, key="PROJ")


def member_for_task(
    user_id: int,
    membership_id: int,
    username: str,
    role: ProjectRole = ProjectRole.MEMBER,
) -> SimpleNamespace:
    """Возвращает участника проекта с загруженной идентичностью пользователя."""
    return SimpleNamespace(
        id=membership_id,
        project_id=1,
        user_id=user_id,
        role=role,
        user=SimpleNamespace(
            id=user_id,
            username=username,
            last_name=f"Фамилия{user_id}",
            first_name=f"Имя{user_id}",
            middle_name=None,
            avatar_key=None,
        ),
    )


def make_task(
    task_id: int = 10,
    number: int = 42,
    stage_id: int = 1,
    priority: TaskPriority = TaskPriority.MEDIUM,
    assignee: str | None = None,
    start_date: date | None = None,
    due_date: date | None = None,
    baseline_start_date: date | None = None,
    baseline_due_date: date | None = None,
    completed_at: datetime | None = None,
    description_md: str | None = None,
) -> SimpleNamespace:
    """Возвращает дублёр задачи со всеми полями схемы ответа."""
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=task_id,
        revision=1,
        project_id=1,
        stage_id=stage_id,
        wbs_node_id=None,
        number=number,
        title="Реализовать фильтрацию",
        description_md=description_md,
        checklist=None,
        checklist_revision=0,
        priority=priority,
        role=None,
        assignee=assignee,
        start_date=start_date,
        due_date=due_date,
        baseline_start_date=baseline_start_date,
        baseline_due_date=baseline_due_date,
        completed_at=completed_at,
        position=1000.0,
        created_at=now,
        updated_at=now,
    )


def build_service(
    tasks_repository: AsyncMock | None = None,
    stages_repository: AsyncMock | None = None,
    activity_repository: AsyncMock | None = None,
    wbs_nodes_repository: AsyncMock | None = None,
    projects_repository: AsyncMock | None = None,
    knowledge_events: AsyncMock | None = None,
    members_repository: AsyncMock | None = None,
    participants_repository: AsyncMock | None = None,
) -> TasksService:
    """Собирает сервис задач с подменёнными репозиториями."""
    projects = projects_repository or AsyncMock(spec=ProjectsRepository)
    if projects_repository is None:
        projects.get_by_id.return_value = PROJECT
    comments_repository = AsyncMock(spec=TaskCommentsRepository)
    comments_repository.get_all.return_value = []
    participants = participants_repository or AsyncMock(spec=TaskParticipantsRepository)
    if participants_repository is None:
        participants.get_by_task_ids.return_value = {}
    if tasks_repository is not None:
        tasks_repository.get_for_update.side_effect = tasks_repository.get_by_id
    return TasksService(
        tasks_repository=tasks_repository or AsyncMock(spec=TasksRepository),
        members_repository=members_repository or AsyncMock(spec=ProjectMembersRepository),
        participants_repository=participants,
        projects_repository=projects,
        stages_repository=stages_repository or AsyncMock(spec=ProjectStagesRepository),
        comments_repository=comments_repository,
        activity_repository=activity_repository or AsyncMock(spec=TaskActivityRepository),
        wbs_nodes_repository=wbs_nodes_repository or AsyncMock(spec=WbsNodesRepository),
        unit_of_work=AsyncMock(spec=UnitOfWork),
        knowledge_events=knowledge_events or AsyncMock(spec=KnowledgeEvents),
        attachment_storage=AsyncMock(spec=TaskAttachmentStorage),
    )


@pytest.mark.asyncio
async def test_create_task_allocates_number_and_uses_first_stage() -> None:
    """Номер и стадия по умолчанию выдаются сервисом, задача уходит в индекс."""
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_next_number.return_value = 43
    tasks_repository.get_max_position_by_stage.return_value = 2000.0
    tasks_repository.save.return_value = make_task(number=43)
    stages_repository = AsyncMock(spec=ProjectStagesRepository)
    stages_repository.get_by_project.return_value = [
        SimpleNamespace(id=1),
        SimpleNamespace(id=2),
    ]
    knowledge_events = AsyncMock(spec=KnowledgeEvents)

    result = await build_service(
        tasks_repository,
        stages_repository,
        knowledge_events=knowledge_events,
    ).create_task(
        project_id=1,
        data={"title": "Реализовать фильтрацию", "stage_id": None},
    )

    saved = tasks_repository.save.await_args.kwargs["data"]
    assert saved["number"] == 43
    assert saved["stage_id"] == 1
    assert saved["position"] == 3000.0
    assert result.key == "PROJ-43"
    knowledge_events.upsert.assert_awaited_once_with(
        project_id=1,
        entity_type=KnowledgeEntityType.TASK,
        entity_id=10,
    )


@pytest.mark.asyncio
async def test_create_task_assigns_team_roles_and_defaults_reporter() -> None:
    """Исполнитель, наблюдатели и постановщик по умолчанию попадают в состав."""
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_next_number.return_value = 43
    tasks_repository.get_max_position_by_stage.return_value = 0.0
    tasks_repository.save.return_value = make_task(number=43)
    stages_repository = AsyncMock(spec=ProjectStagesRepository)
    stages_repository.get_by_project.return_value = [SimpleNamespace(id=1)]
    members_repository = AsyncMock(spec=ProjectMembersRepository)
    owner = member_for_task(1, 11, "owner", ProjectRole.OWNER)
    executor = member_for_task(2, 12, "executor")
    observer = member_for_task(3, 13, "observer")
    members_repository.get_for_project.return_value = [owner, executor, observer]
    participants_repository = AsyncMock(spec=TaskParticipantsRepository)
    participants_repository.get_by_task_ids.return_value = {}

    await build_service(
        tasks_repository,
        stages_repository,
        members_repository=members_repository,
        participants_repository=participants_repository,
    ).create_task(
        project_id=1,
        created_by_user_id=1,
        data={"title": "С командой", "executor_id": 2, "observer_ids": [3]},
    )

    assert tasks_repository.save.await_args.kwargs["data"]["assignee"] == "Фамилия2 Имя2"
    participants_repository.delete_for_task.assert_awaited_once()
    assignments = participants_repository.save_many.await_args.args[1]
    assert {(item["project_member_id"], item["role"]) for item in assignments} == {
        (12, TaskParticipantRole.EXECUTOR),
        (11, TaskParticipantRole.REPORTER),
        (13, TaskParticipantRole.OBSERVER),
    }


@pytest.mark.asyncio
async def test_create_task_enforces_team_rules_before_writing() -> None:
    """Чужой постановщик доступен только владельцу, участник — только из команды."""
    tasks_repository = AsyncMock(spec=TasksRepository)
    members_repository = AsyncMock(spec=ProjectMembersRepository)
    members_repository.get.return_value = member_for_task(1, 11, "member", ProjectRole.MEMBER)

    with pytest.raises(TaskReporterPermissionError) as exc_info:
        await build_service(
            tasks_repository=tasks_repository,
            members_repository=members_repository,
        ).create_task(
            project_id=1,
            created_by_user_id=1,
            data={"title": "С чужим постановщиком", "reporter_id": 2},
        )
    assert exc_info.value.status_code == 403

    members_repository = AsyncMock(spec=ProjectMembersRepository)
    members_repository.get_for_project.return_value = [member_for_task(1, 11, "owner")]
    with pytest.raises(TaskParticipantNotProjectMemberError):
        await build_service(
            tasks_repository=tasks_repository,
            members_repository=members_repository,
        ).create_task(
            project_id=1,
            data={"title": "Чужое назначение", "executor_id": 999},
        )

    tasks_repository.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_task_retries_taken_number_and_gives_up_after_attempts() -> None:
    """Занятый номер вызывает повтор, а бесконечно занятый — отказ 409."""
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_next_number.side_effect = [43, 44]
    tasks_repository.get_max_position_by_stage.return_value = 0.0
    tasks_repository.save.side_effect = [
        TaskNumberAlreadyExistsRepositoryError(project_id=1, number=43),
        make_task(number=44),
    ]
    stages_repository = AsyncMock(spec=ProjectStagesRepository)
    stages_repository.get_by_project.return_value = [SimpleNamespace(id=1)]

    result = await build_service(tasks_repository, stages_repository).create_task(
        project_id=1,
        data={"title": "Реализовать фильтрацию", "stage_id": None},
    )
    assert tasks_repository.save.await_count == 2
    assert result.key == "PROJ-44"

    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_next_number.return_value = 43
    tasks_repository.get_max_position_by_stage.return_value = 0.0
    tasks_repository.save.side_effect = TaskNumberAlreadyExistsRepositoryError(
        project_id=1,
        number=43,
    )

    with pytest.raises(TaskNumberAllocationError) as exc_info:
        await build_service(tasks_repository, stages_repository).create_task(
            project_id=1,
            data={"title": "Реализовать фильтрацию", "stage_id": None},
        )
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_create_task_rejects_every_unusable_reference() -> None:
    """Проект без стадий, чужая стадия, чужой и отсутствующий узел, чужой проект.

    Все пять случаев — одно правило: ссылка проверяется до записи, и
    задача не создаётся. Прогон по списку показывает сразу все ссылки,
    которые перестали проверяться.
    """
    stages_repository = AsyncMock(spec=ProjectStagesRepository)
    stages_repository.get_by_project.return_value = []
    with pytest.raises(ProjectStageNotFoundError):
        await build_service(stages_repository=stages_repository).create_task(
            project_id=1,
            data={"title": "Задача", "stage_id": None},
        )

    stages_repository = AsyncMock(spec=ProjectStagesRepository)
    stages_repository.get_by_project.return_value = [SimpleNamespace(id=1)]
    stages_repository.get_by_id.return_value = SimpleNamespace(id=77, project_id=5)
    with pytest.raises(ProjectStageForeignProjectError) as exc_info:
        await build_service(stages_repository=stages_repository).create_task(
            project_id=1,
            data={"title": "Задача", "stage_id": 77},
        )
    assert exc_info.value.status_code == 409

    stages_repository = AsyncMock(spec=ProjectStagesRepository)
    stages_repository.get_by_project.return_value = [SimpleNamespace(id=1)]
    wbs_nodes_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_nodes_repository.get_by_id.return_value = SimpleNamespace(id=8, project_id=5)
    with pytest.raises(WbsNodeForeignProjectError):
        await build_service(
            stages_repository=stages_repository,
            wbs_nodes_repository=wbs_nodes_repository,
        ).create_task(
            project_id=1,
            data={"title": "Задача", "stage_id": None, "wbs_node_id": 8},
        )

    wbs_nodes_repository = AsyncMock(spec=WbsNodesRepository)
    wbs_nodes_repository.get_by_id.return_value = None
    with pytest.raises(WbsNodeNotFoundError):
        await build_service(
            stages_repository=stages_repository,
            wbs_nodes_repository=wbs_nodes_repository,
        ).create_task(
            project_id=1,
            data={"title": "Задача", "stage_id": None, "wbs_node_id": 8},
        )

    projects_repository = AsyncMock(spec=ProjectsRepository)
    projects_repository.get_by_id.return_value = None
    with pytest.raises(ProjectNotFoundError):
        await build_service(projects_repository=projects_repository).create_task(
            project_id=42,
            data={"title": "Задача", "stage_id": None},
        )


@pytest.mark.asyncio
async def test_reverse_schedule_is_rejected_on_create_and_update() -> None:
    """Начало позже срока не проходит ни при создании, ни при изменении."""
    tasks_repository = AsyncMock(spec=TasksRepository)

    with pytest.raises(TaskDateRangeError):
        await build_service(tasks_repository).create_task(
            project_id=1,
            data={
                "title": "Обратный интервал",
                "start_date": date(2026, 9, 9),
                "due_date": date(2026, 9, 8),
            },
        )
    tasks_repository.save.assert_not_awaited()

    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = make_task(due_date=date(2026, 9, 8))
    with pytest.raises(TaskDateRangeError) as exc_info:
        await build_service(tasks_repository).update_task(
            task_id=10,
            data={"start_date": date(2026, 9, 9)},
        )
    assert exc_info.value.status_code == 422
    tasks_repository.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_task_records_history_and_reindexes_only_semantic_fields() -> None:
    """История пишется на каждое изменение, переиндексация — только на смысловые.

    Приоритет, исполнитель и даты видны в карточке, но не меняют текст,
    по которому ищет агент: переиндексация на них — лишняя работа.
    """
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = make_task(
        priority=TaskPriority.LOW, assignee="Иван"
    )
    tasks_repository.update.return_value = make_task(
        priority=TaskPriority.URGENT, assignee="Мария"
    )
    activity_repository = AsyncMock(spec=TaskActivityRepository)
    knowledge_events = AsyncMock(spec=KnowledgeEvents)
    await build_service(
        tasks_repository,
        activity_repository=activity_repository,
        knowledge_events=knowledge_events,
    ).update_task(task_id=10, data={"priority": TaskPriority.URGENT, "assignee": "Мария"})
    events = [call.kwargs["event_type"] for call in activity_repository.save.await_args_list]
    assert TaskActivityEventType.PRIORITY_CHANGED in events
    assert TaskActivityEventType.ASSIGNEE_CHANGED in events
    knowledge_events.upsert.assert_not_awaited()

    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = make_task(
        start_date=date(2026, 9, 1), due_date=date(2026, 9, 8)
    )
    tasks_repository.update.return_value = make_task(
        start_date=date(2026, 9, 2), due_date=date(2026, 9, 8)
    )
    activity_repository = AsyncMock(spec=TaskActivityRepository)
    knowledge_events = AsyncMock(spec=KnowledgeEvents)
    await build_service(
        tasks_repository,
        activity_repository=activity_repository,
        knowledge_events=knowledge_events,
    ).update_task(task_id=10, data={"start_date": date(2026, 9, 2)})
    event = activity_repository.save.await_args.kwargs
    assert event["event_type"] == TaskActivityEventType.START_DATE_CHANGED
    assert event["from_value"] == "2026-09-01"
    assert event["to_value"] == "2026-09-02"
    knowledge_events.upsert.assert_not_awaited()

    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = make_task()
    tasks_repository.update.return_value = make_task()
    knowledge_events = AsyncMock(spec=KnowledgeEvents)
    await build_service(tasks_repository, knowledge_events=knowledge_events).update_task(
        task_id=10, data={"title": "Новый смысловой заголовок"}
    )
    knowledge_events.upsert.assert_awaited_once_with(
        project_id=1,
        entity_type=KnowledgeEntityType.TASK,
        entity_id=10,
    )


@pytest.mark.asyncio
async def test_update_task_skips_history_when_values_unchanged() -> None:
    """Запись тем же значением не создаёт события истории."""
    tasks_repository = AsyncMock(spec=TasksRepository)
    task = make_task(priority=TaskPriority.LOW, assignee="Иван")
    tasks_repository.get_by_id.return_value = task
    tasks_repository.update.return_value = task
    activity_repository = AsyncMock(spec=TaskActivityRepository)

    await build_service(tasks_repository, activity_repository=activity_repository).update_task(
        task_id=10,
        data={"priority": TaskPriority.LOW, "assignee": "Иван"},
    )

    activity_repository.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_task_records_stage_change_and_appends_to_end() -> None:
    """Перенос в другую стадию ставит задачу в конец и пишет событие."""
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = make_task(stage_id=1)
    tasks_repository.get_max_position_by_stage.return_value = 5000.0
    tasks_repository.update.return_value = make_task(stage_id=2)
    stages_repository = AsyncMock(spec=ProjectStagesRepository)
    stages_repository.get_by_id.side_effect = [
        SimpleNamespace(id=2, project_id=1, name="В работе", is_done_stage=False),
        SimpleNamespace(id=1, project_id=1, name="Бэклог", is_done_stage=False),
    ]
    activity_repository = AsyncMock(spec=TaskActivityRepository)
    knowledge_events = AsyncMock(spec=KnowledgeEvents)

    await build_service(
        tasks_repository,
        stages_repository,
        activity_repository=activity_repository,
        knowledge_events=knowledge_events,
    ).move_task(task_id=10, stage_id=2)

    assert tasks_repository.update.await_args.kwargs["data"]["position"] == 6000.0
    event = activity_repository.save.await_args.kwargs
    assert event["event_type"] == TaskActivityEventType.STAGE_CHANGED
    assert event["from_value"] == "Бэклог"
    assert event["to_value"] == "В работе"
    knowledge_events.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_task_rejects_foreign_stage_and_ignores_empty_move() -> None:
    """Стадия чужого проекта отклоняется, перенос в ту же стадию ничего не пишет."""
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = make_task(stage_id=1)
    stages_repository = AsyncMock(spec=ProjectStagesRepository)
    stages_repository.get_by_id.return_value = SimpleNamespace(
        id=9, project_id=5, name="Чужая", is_done_stage=False
    )

    with pytest.raises(ProjectStageForeignProjectError) as exc_info:
        await build_service(tasks_repository, stages_repository).move_task(
            task_id=10, stage_id=9
        )
    assert exc_info.value.status_code == 409
    tasks_repository.update.assert_not_awaited()

    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = make_task(stage_id=2)
    stages_repository = AsyncMock(spec=ProjectStagesRepository)
    stages_repository.get_by_id.return_value = SimpleNamespace(
        id=2, project_id=1, name="Работа", is_done_stage=False
    )

    await build_service(tasks_repository, stages_repository).move_task(task_id=10, stage_id=2)

    tasks_repository.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_task_keeps_completed_at_across_repeated_transitions() -> None:
    """Отметка завершения ставится один раз, снимается и ставится заново.

    Переход между двумя завершающими стадиями не должен переписывать
    момент завершения: задача завершена с первого попадания в «Готово».
    """
    tasks_repository = AsyncMock(spec=TasksRepository)
    first_completed_at = datetime(2026, 9, 2, 10, tzinfo=UTC)
    tasks_repository.get_by_id.side_effect = [
        make_task(stage_id=1),
        make_task(stage_id=2, completed_at=first_completed_at),
        make_task(stage_id=3, completed_at=first_completed_at),
        make_task(stage_id=1),
    ]
    tasks_repository.update.side_effect = [
        make_task(stage_id=2, completed_at=first_completed_at),
        make_task(stage_id=3, completed_at=first_completed_at),
        make_task(stage_id=1),
        make_task(stage_id=2, completed_at=datetime(2026, 9, 2, 11, tzinfo=UTC)),
    ]
    open_stage = SimpleNamespace(id=1, project_id=1, name="Работа", is_done_stage=False)
    first_done_stage = SimpleNamespace(id=2, project_id=1, name="Готово", is_done_stage=True)
    second_done_stage = SimpleNamespace(id=3, project_id=1, name="Архив", is_done_stage=True)
    stages_repository = AsyncMock(spec=ProjectStagesRepository)
    stages_repository.get_by_id.side_effect = [
        first_done_stage,
        open_stage,
        second_done_stage,
        first_done_stage,
        open_stage,
        second_done_stage,
        first_done_stage,
        open_stage,
    ]
    service = build_service(tasks_repository, stages_repository)

    await service.move_task(10, 2, 1000)
    await service.move_task(10, 3, 1000)
    await service.move_task(10, 1, 1000)
    await service.move_task(10, 2, 1000)

    payloads = [item.kwargs["data"] for item in tasks_repository.update.await_args_list]
    assert payloads[0]["completed_at"].tzinfo is UTC
    assert "completed_at" not in payloads[1]
    assert payloads[2]["completed_at"] is None
    assert payloads[3]["completed_at"].tzinfo is UTC
    assert payloads[3]["completed_at"] >= payloads[0]["completed_at"]


@pytest.mark.asyncio
async def test_fix_baseline_copies_current_plan_and_records_history() -> None:
    """Фиксация плана копирует текущие даты в базовые одним коммитом."""
    tasks_repository = AsyncMock(spec=TasksRepository)
    task = make_task(start_date=date(2026, 9, 2), due_date=date(2026, 9, 8))
    fixed = make_task(
        start_date=task.start_date,
        due_date=task.due_date,
        baseline_start_date=task.start_date,
        baseline_due_date=task.due_date,
    )
    tasks_repository.get_by_id.return_value = task
    tasks_repository.update.return_value = fixed
    activity_repository = AsyncMock(spec=TaskActivityRepository)
    knowledge_events = AsyncMock(spec=KnowledgeEvents)
    service = build_service(
        tasks_repository,
        activity_repository=activity_repository,
        knowledge_events=knowledge_events,
    )

    result = await service.fix_baseline(task_id=10)

    assert result.baseline_start_date == date(2026, 9, 2)
    assert result.baseline_due_date == date(2026, 9, 8)
    assert tasks_repository.update.await_args.kwargs["data"] == {
        "baseline_start_date": date(2026, 9, 2),
        "baseline_due_date": date(2026, 9, 8),
    }
    assert activity_repository.save.await_args.kwargs["event_type"] is (
        TaskActivityEventType.BASELINE_CHANGED
    )
    knowledge_events.upsert.assert_not_awaited()
    service.unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_task_removes_attachment_directory() -> None:
    """Удаление задачи убирает и каталог её вложений."""
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = make_task()
    storage = AsyncMock()
    service = build_service(tasks_repository)
    service.attachment_storage = storage

    await service.delete_task(task_id=10)

    tasks_repository.delete.assert_awaited_once()
    storage.delete_task_directory.assert_awaited_once_with(10)


@pytest.mark.asyncio
async def test_missing_task_is_404_and_repository_failure_is_500() -> None:
    """Отсутствие объекта и сбой базы — разные ответы, а не один на двоих."""
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_id.return_value = None
    with pytest.raises(TaskNotFoundError) as not_found:
        await build_service(tasks_repository).get_task(task_id=999)
    assert not_found.value.status_code == 404

    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_project.side_effect = TasksRepositoryError("БД недоступна")
    with pytest.raises(TasksServiceError) as failure:
        await build_service(tasks_repository).get_task_list(project_id=1)
    assert failure.value.status_code == 500
