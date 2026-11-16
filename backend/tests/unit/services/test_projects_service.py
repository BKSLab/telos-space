from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.db.models.knowledge_index_jobs import KnowledgeEntityType
from src.db.models.project_members import ProjectRole
from src.exceptions.projects import (
    ProjectDeadlineConflictError,
    ProjectKeyAlreadyExistsRepositoryError,
    ProjectKeyConflictError,
    ProjectMemberUserNotFoundError,
    ProjectNotFoundError,
    ProjectsRepositoryError,
    ProjectsServiceError,
    ProjectValidationError,
)
from src.repositories.project_deadline_changes import ProjectDeadlineChangesRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.schemas.projects import ProjectCreateSchema
from src.services.knowledge_events import KnowledgeEvents
from src.services.projects import DEFAULT_STAGES, ProjectsService, build_project_stats
from src.storage.task_attachments import TaskAttachmentStorage

TODAY = date(2026, 9, 1)
OWNER_ID = 1


def stage(stage_id: int, name: str, order_index: int, is_done: bool = False) -> SimpleNamespace:
    """Возвращает лёгкий дублёр стадии проекта."""
    return SimpleNamespace(
        id=stage_id,
        name=name,
        order_index=order_index,
        color="#58a6ff",
        is_done_stage=is_done,
    )


def task(
    task_id: int,
    stage_id: int,
    due_date: date | None = None,
    wbs_node_id: int | None = None,
) -> SimpleNamespace:
    """Возвращает лёгкий дублёр задачи."""
    return SimpleNamespace(
        id=task_id,
        stage_id=stage_id,
        due_date=due_date,
        wbs_node_id=wbs_node_id,
    )


def make_project(project_id: int = 7, key: str = "PROJ") -> SimpleNamespace:
    """Возвращает дублёр проекта со всеми полями схемы ответа."""
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=project_id,
        owner_id=OWNER_ID,
        key=key,
        name="Тестовый проект",
        description_md=None,
        description_sections=None,
        context_revision=0,
        status="ACTIVE",
        color="#58a6ff",
        icon=None,
        start_date=None,
        due_date=None,
        order_index=3,
        created_at=now,
        updated_at=now,
    )


def build_service(
    projects_repository: AsyncMock | None = None,
    stages_repository: AsyncMock | None = None,
    tasks_repository: AsyncMock | None = None,
    members_repository: AsyncMock | None = None,
    knowledge_events: AsyncMock | None = None,
    unit_of_work: AsyncMock | None = None,
) -> ProjectsService:
    """Собирает сервис проектов с подменёнными репозиториями."""
    members = members_repository or AsyncMock(spec=ProjectMembersRepository)
    if members_repository is None:
        members.get_project_ids_for_user.return_value = {7}
    users_repository = AsyncMock(spec=UsersRepository)
    users_repository.get_by_id.return_value = SimpleNamespace(
        id=OWNER_ID,
        username="owner",
        last_name="Тестов",
        first_name="Иван",
        middle_name=None,
    )
    return ProjectsService(
        projects_repository=projects_repository or AsyncMock(spec=ProjectsRepository),
        members_repository=members,
        stages_repository=stages_repository or AsyncMock(spec=ProjectStagesRepository),
        tasks_repository=tasks_repository or AsyncMock(spec=TasksRepository),
        unit_of_work=unit_of_work or AsyncMock(spec=UnitOfWork),
        knowledge_events=knowledge_events or AsyncMock(spec=KnowledgeEvents),
        attachment_storage=AsyncMock(spec=TaskAttachmentStorage),
        users_repository=users_repository,
        deadline_changes_repository=AsyncMock(spec=ProjectDeadlineChangesRepository),
    )


@pytest.mark.asyncio
async def test_create_project_adds_default_stages() -> None:
    projects_repository = AsyncMock(spec=ProjectsRepository)
    projects_repository.get_max_order_index.return_value = 2
    projects_repository.save.return_value = make_project()
    stages_repository = AsyncMock(spec=ProjectStagesRepository)
    members_repository = AsyncMock(spec=ProjectMembersRepository)
    service = build_service(
        projects_repository, stages_repository, members_repository=members_repository
    )

    await service.create_project(data={"key": "PROJ", "name": "Тестовый проект"}, owner_id=OWNER_ID)

    saved = projects_repository.save.await_args.kwargs["data"]
    assert saved["order_index"] == 3
    assert saved["owner_id"] == OWNER_ID
    membership = members_repository.save.await_args.kwargs["data"]
    assert membership["user_id"] == OWNER_ID
    assert membership["role"] is ProjectRole.OWNER
    created_stages = stages_repository.save_many.await_args.kwargs["items"]
    assert len(created_stages) == len(DEFAULT_STAGES)
    assert [item["order_index"] for item in created_stages] == list(range(len(DEFAULT_STAGES)))
    assert {item["project_id"] for item in created_stages} == {7}
    assert sum(1 for item in created_stages if item["is_done_stage"]) == 1


@pytest.mark.asyncio
async def test_delete_project_removes_attachment_directories() -> None:
    projects_repository = AsyncMock(spec=ProjectsRepository)
    projects_repository.get_by_id.return_value = SimpleNamespace(id=1)
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_project.return_value = [task(11, 1), task(12, 1)]
    storage = AsyncMock()
    service = ProjectsService(
        projects_repository=projects_repository,
        members_repository=AsyncMock(spec=ProjectMembersRepository),
        stages_repository=AsyncMock(spec=ProjectStagesRepository),
        tasks_repository=tasks_repository,
        attachment_storage=storage,
        users_repository=AsyncMock(spec=UsersRepository),
        deadline_changes_repository=AsyncMock(spec=ProjectDeadlineChangesRepository),
        unit_of_work=AsyncMock(spec=UnitOfWork),
        knowledge_events=AsyncMock(spec=KnowledgeEvents),
    )

    await service.delete_project(project_id=1)

    projects_repository.delete.assert_awaited_once()
    assert storage.delete_task_directory.await_count == 2


def test_build_project_stats_summarises_progress_and_deadlines() -> None:
    """Сводка проекта: прогресс, просроченные задачи и пустой проект."""

    stages = [
        stage(1, "Бэклог", 0),
        stage(2, "В работе", 1),
        stage(3, "Готово", 2, is_done=True),
    ]
    tasks = [
        task(1, 1),
        task(2, 1, due_date=TODAY - timedelta(days=3)),
        task(3, 2, due_date=TODAY + timedelta(days=2), wbs_node_id=5),
        task(4, 2, due_date=TODAY + timedelta(days=30)),
        task(5, 3, due_date=TODAY - timedelta(days=10)),
    ]

    stats = build_project_stats(project_id=1, stages=stages, tasks=tasks, today=TODAY)

    assert stats.total_tasks == 5
    assert stats.done_tasks == 1
    assert stats.in_progress_tasks == 2
    assert stats.overdue_tasks == 1
    assert stats.due_soon_tasks == 1
    assert stats.unassigned_tasks == 4
    assert stats.completion_rate == pytest.approx(0.2)
    assert stats.next_due_date == TODAY + timedelta(days=2)
    assert [item.tasks_count for item in stats.stage_breakdown] == [2, 2, 1]

    stages = [stage(1, "Бэклог", 0), stage(2, "Готово", 1, is_done=True)]
    tasks = [task(1, 1, due_date=TODAY - timedelta(days=1))]

    stats = build_project_stats(project_id=1, stages=stages, tasks=tasks, today=TODAY)

    assert stats.overdue_tasks == 1
    assert stats.in_progress_tasks == 0
    assert stats.next_due_date is None

    stats = build_project_stats(project_id=1, stages=[], tasks=[], today=TODAY)

    assert stats.total_tasks == 0
    assert stats.completion_rate == 0.0
    assert stats.stage_breakdown == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("name", "Новое имя"), ("description_md", "Описание")])
async def test_project_changes_enqueue_the_right_knowledge_job(field: str, value: str) -> None:
    """Точечные поля ставят upsert проекта, смена ключа — полную переиндексацию."""

    projects_repository = AsyncMock(spec=ProjectsRepository)
    projects_repository.get_by_id.return_value = make_project()
    projects_repository.update.return_value = make_project()
    knowledge_events = AsyncMock(spec=KnowledgeEvents)

    await build_service(
        projects_repository=projects_repository,
        knowledge_events=knowledge_events or AsyncMock(spec=KnowledgeEvents),
    ).update_project(project_id=7, data={field: value}, updated_by_user_id=OWNER_ID)

    knowledge_events.upsert.assert_awaited_once_with(
        project_id=7,
        entity_type=KnowledgeEntityType.PROJECT,
        entity_id=7,
    )
    knowledge_events.reindex_project.assert_not_awaited()

    projects_repository = AsyncMock(spec=ProjectsRepository)
    projects_repository.get_by_id.return_value = make_project()
    projects_repository.update.return_value = make_project(key="NEW")
    knowledge_events = AsyncMock(spec=KnowledgeEvents)

    await build_service(
        projects_repository=projects_repository,
        knowledge_events=knowledge_events or AsyncMock(spec=KnowledgeEvents),
    ).update_project(
        project_id=7, data={"key": "NEW", "name": "Новое имя"}, updated_by_user_id=OWNER_ID
    )

    knowledge_events.reindex_project.assert_awaited_once_with(7)
    knowledge_events.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_creation_and_reads_report_their_failures() -> None:
    """Занятый ключ — 409, отсутствующий проект — 404, сбой репозитория — 500."""

    projects_repository = AsyncMock(spec=ProjectsRepository)
    projects_repository.get_max_order_index.return_value = 0
    projects_repository.save.side_effect = ProjectKeyAlreadyExistsRepositoryError(key="PROJ")
    stages_repository = AsyncMock(spec=ProjectStagesRepository)
    service = build_service(projects_repository, stages_repository)

    with pytest.raises(ProjectKeyConflictError) as exc_info:
        await service.create_project(
            data={"key": "PROJ", "name": "Тестовый проект"},
            owner_id=OWNER_ID,
        )

    assert exc_info.value.status_code == 409
    stages_repository.save_many.assert_not_awaited()

    projects_repository = AsyncMock(spec=ProjectsRepository)
    projects_repository.get_by_id.return_value = None

    with pytest.raises(ProjectNotFoundError) as exc_info:
        await build_service(projects_repository).get_project(project_id=999)

    assert exc_info.value.status_code == 404

    projects_repository = AsyncMock(spec=ProjectsRepository)
    projects_repository.get_all.side_effect = ProjectsRepositoryError("БД недоступна")

    with pytest.raises(ProjectsServiceError) as exc_info:
        await build_service(projects_repository).get_project_list(user_id=OWNER_ID)

    assert exc_info.value.status_code == 500


def deadline_service(previous: date | None, has_been_set: bool = False) -> ProjectsService:
    """Сервис с сохранённым сроком и реальным снимком имени автора."""
    service = build_service()
    project = make_project()
    project.due_date = previous
    project.due_date_has_been_set = has_been_set
    service.projects_repository.get_by_id.return_value = project

    async def update(*, project, data):
        for key, value in data.items():
            setattr(project, key, value)
        return project

    service.projects_repository.update.side_effect = update
    service.users_repository.get_by_id.return_value = SimpleNamespace(
        id=OWNER_ID,
        username="owner",
        last_name="Тестов",
        first_name="Иван",
        middle_name=None,
    )
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "previous,new_date,ever_set",
    [
        (TODAY, TODAY + timedelta(days=1), True),
        (TODAY, None, True),
        (None, TODAY, True),
        (TODAY, TODAY + timedelta(days=1), False),
    ],
)
@pytest.mark.parametrize("comment", [None, " \n "])
async def test_deadline_revision_requires_reason(previous, new_date, ever_set, comment):
    service = deadline_service(previous, ever_set)
    with pytest.raises(ProjectValidationError):
        await service.update_project(
            7, {"due_date": new_date, "due_date_comment": comment}, OWNER_ID
        )
    service.deadline_changes_repository.save.assert_not_awaited()
    service.projects_repository.update.assert_not_awaited()
    service.unit_of_work.commit.assert_not_awaited()
    service.unit_of_work.rollback.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "previous,new_date,comment",
    [
        (None, TODAY, None),
        (TODAY, TODAY + timedelta(days=5), "Сдвиг поставки"),
        (TODAY, None, "Перепланирование"),
    ],
)
async def test_deadline_change_records_dates_actor_and_reason(previous, new_date, comment):
    service = deadline_service(previous)
    result = await service.update_project(
        7, {"due_date": new_date, "due_date_comment": comment}, OWNER_ID
    )
    assert result.due_date == new_date
    assert result.due_date_has_been_set is True
    service.deadline_changes_repository.save.assert_awaited_once_with(
        data={
            "project_id": 7,
            "previous_due_date": previous,
            "new_due_date": new_date,
            "changed_by_user_id": OWNER_ID,
            "changed_by_name": "Тестов Иван",
            "comment": comment,
        }
    )
    service.projects_repository.get_by_id.assert_awaited_once_with(project_id=7, for_update=True)
    service.unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_unchanged_date_creates_no_history_and_stale_date_is_rejected():
    service = deadline_service(TODAY)
    await service.update_project(
        7, {"due_date": TODAY, "due_date_comment": "Лишний комментарий"}, OWNER_ID
    )
    service.deadline_changes_repository.save.assert_not_awaited()
    with pytest.raises(ProjectDeadlineConflictError):
        await service.update_project(
            7,
            {
                "due_date": TODAY + timedelta(days=2),
                "expected_due_date": None,
                "due_date_comment": "Работа с устаревшей формой",
            },
            OWNER_ID,
        )
    service.deadline_changes_repository.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_updated_start_date_cannot_cross_existing_due_date():
    service = deadline_service(TODAY)
    with pytest.raises(ProjectValidationError):
        await service.update_project(7, {"start_date": TODAY + timedelta(days=1)}, OWNER_ID)
    service.projects_repository.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_structured_description_is_in_search_text_and_enqueues_indexing():
    service = deadline_service(None)
    result = await service.update_project(
        7,
        {
            "description_sections": {
                "problem": "  Потери заявок  ",
                "goal": "Учёт заявок",
                "additional": "Старые **сведения**",
            }
        },
        OWNER_ID,
    )
    assert result.description_sections.problem == "Потери заявок"
    assert (
        result.description_md
        == "## Проблема\n\nПотери заявок\n\n## Цель\n\nУчёт заявок\n\n## Дополнительно\n\nСтарые **сведения**"
    )
    service.knowledge_events.upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_creation_accepts_custom_stages_and_validated_team_atomically():
    service = deadline_service(None)
    service.projects_repository.get_max_order_index.return_value = 0
    service.projects_repository.save.return_value = make_project()
    service.users_repository.get_by_username.return_value = SimpleNamespace(id=2, is_active=True)
    data = ProjectCreateSchema.model_validate(
        {
            "key": "PROJ",
            "name": "Проект",
            "member_usernames": [" Anna ", "anna"],
            "stages": [{"name": "Заявки"}, {"name": "Выполнено", "is_done_stage": True}],
        }
    ).model_dump()
    await service.create_project(data, OWNER_ID)
    assert [
        item["name"] for item in service.stages_repository.save_many.await_args.kwargs["items"]
    ] == ["Заявки", "Выполнено"]
    assert [
        call.kwargs["data"]["user_id"] for call in service.members_repository.save.await_args_list
    ] == [1, 2]
    service.users_repository.get_by_username.assert_awaited_once_with(username="anna")
    service.unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_team_member_prevents_project_creation():
    service = deadline_service(None)
    service.users_repository.get_by_username.return_value = None
    with pytest.raises(ProjectMemberUserNotFoundError):
        await service.create_project(
            {"key": "PROJ", "name": "Проект", "member_usernames": ["missing"]}, OWNER_ID
        )
    service.projects_repository.save.assert_not_awaited()
    service.unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_history_write_rolls_back_project_change():
    service = deadline_service(TODAY)
    service.deadline_changes_repository.save.side_effect = ProjectsRepositoryError(
        "Сбой записи истории"
    )
    with pytest.raises(ProjectsServiceError):
        await service.update_project(7, {"due_date": None, "due_date_comment": "Причина"}, OWNER_ID)
    service.projects_repository.update.assert_not_awaited()
    service.unit_of_work.rollback.assert_awaited_once()
