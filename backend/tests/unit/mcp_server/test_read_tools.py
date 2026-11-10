"""Проверки инструментов чтения MCP.

Инструменты больше не ходят в репозитории: они вызывают use case и
превращают DTO в JSON. Поэтому здесь проверяется именно это — какой
сценарий вызван, с какими аргументами, и как его результат выглядит
снаружи.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.db.models.project_milestones import ProjectMilestoneStatus
from src.exceptions.project_stages import ProjectStageNameNotFoundError
from src.exceptions.projects import ProjectNotFoundError, ProjectsServiceError
from src.mcp_server import server as srv
from src.schemas.calendar import (
    CalendarMilestoneSchema,
    CalendarProjectSchema,
    CalendarRangeSchema,
    CalendarResponseSchema,
    CalendarRiskReasonSchema,
    CalendarSummarySchema,
    CalendarTaskSchema,
    UnscheduledTasksPageSchema,
)
from src.schemas.enums import TaskPriority
from src.services.project_query import (
    CommentDto,
    MilestoneDto,
    ProjectOverviewDto,
    ProjectStageDto,
    ProjectSummaryDto,
    ResolvedTask,
    TaskDetailsDto,
    TaskPageDto,
    TaskSummaryDto,
)
from src.utils.task_filters import TaskFilters
from tests.unit.mcp_server.conftest import PROJECT_ID, FakeContext

PROJECT = ProjectSummaryDto(
    project_key="PROJ",
    name="Тестовый проект",
    status="ACTIVE",
    start_date=None,
    due_date=date(2026, 9, 30),
)


def calendar_task(**overrides) -> CalendarTaskSchema:
    """Задача календаря в том виде, в каком её отдаёт сервис."""
    values = {
        "id": 7,
        "key": "PROJ-142",
        "title": "Задача",
        "start_date": date(2026, 9, 1),
        "due_date": date(2026, 9, 10),
        "baseline_start_date": None,
        "baseline_due_date": None,
        "drift_days": None,
        "stage_id": 11,
        "wbs_node_id": None,
        "wbs_path": None,
        "priority": TaskPriority.HIGH,
        "assignee": "Борис",
        "is_done": False,
        "is_overdue": True,
        "is_due_soon": False,
        "risk_level": "HIGH",
        "risk_reasons": [CalendarRiskReasonSchema(code="OVERDUE", message="Срок прошёл")],
        "updated_at": datetime(2026, 9, 2, 12, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return CalendarTaskSchema(**values)


def calendar_response(tasks: list[CalendarTaskSchema]) -> CalendarResponseSchema:
    """Ответ календаря с минимальным, но валидным окружением."""
    return CalendarResponseSchema(
        range=CalendarRangeSchema(
            date_from=date(2026, 9, 1),
            date_to=date(2026, 9, 30),
            today=date(2026, 9, 5),
        ),
        project=CalendarProjectSchema(start_date=None, due_date=date(2026, 9, 30)),
        tasks=tasks,
        stages=[],
        wbs_nodes=[],
        assignees=["Борис"],
        summary=CalendarSummarySchema(overdue=1, due_soon=0, unscheduled=0, drifted=0),
        recent_changes=[],
        milestones=[
            CalendarMilestoneSchema(
                wbs_node_id=None,
                wbs_path=None,
                id=3,
                title="MVP",
                due_date=date(2026, 9, 20),
                status=ProjectMilestoneStatus.PLANNED,
                description_md=None,
            )
        ],
        dependencies=[],
    )


def task_dto(number: int = 142, **overrides) -> TaskSummaryDto:
    """Краткая карточка задачи для ответа сервиса."""
    values = {
        "task_key": f"PROJ-{number}",
        "title": "Задача",
        "stage": "В работе",
        "is_done": False,
        "priority": TaskPriority.HIGH.value,
        "assignee": "Борис",
        "due_date": date(2026, 10, 1),
    }
    values.update(overrides)
    return TaskSummaryDto(**values)


async def test_list_projects_returns_service_result_and_reports_failure(tools) -> None:
    """Список проектов приходит из сервиса; его сбой становится ошибкой инструмента."""
    # Инструмент отдаёт то, что вернул сценарий, и не фильтрует сам. Раньше фильтрация по доступу жила в обработчике: правило «что видно владельцу токена» существовало только в MCP.
    services = tools()
    services.query.list_accessible_projects.return_value = [PROJECT]

    result = await srv.list_projects(FakeContext())

    assert [item["project_key"] for item in result] == ["PROJ"]
    services.query.list_accessible_projects.assert_awaited_once_with(user_id=1)
    # Сбой сценария превращается в ToolError без внутренних деталей.
    services = tools()
    services.query.list_accessible_projects.side_effect = ProjectsServiceError("сбой БД")

    with pytest.raises(ToolError) as error:
        await srv.list_projects(FakeContext())

    assert "сбой БД" not in str(error.value)


async def test_get_project_returns_stages_and_hides_foreign_projects(tools) -> None:
    """Карточка проекта со стадиями; чужой проект недоступен."""
    # Карточка проекта содержит стадии и число задач в каждой.
    services = tools()
    services.query.get_project_overview.return_value = ProjectOverviewDto(
        summary=PROJECT,
        description="Описание",
        total_tasks=2,
        stages=[
            ProjectStageDto(name="В работе", is_done_stage=False, task_count=2),
            ProjectStageDto(name="Готово", is_done_stage=True, task_count=0),
        ],
    )

    result = await srv.get_project(FakeContext(), project_key="PROJ")

    assert result["project_key"] == "PROJ"
    assert result["total_tasks"] == 2
    assert result["stages"] == [
        {"name": "В работе", "is_done_stage": False, "task_count": 2},
        {"name": "Готово", "is_done_stage": True, "task_count": 0},
    ]
    # Чужой проект неотличим от несуществующего.
    services = tools()
    services.query.resolve_project_id.side_effect = ProjectNotFoundError(project_id=0)

    with pytest.raises(ToolError) as error:
        await srv.get_project(FakeContext(), project_key="OTHER")

    assert str(error.value) == "Проект недоступен."


async def test_list_tasks_applies_filters_and_reports_unknown_stage(tools) -> None:
    """Фильтры доходят до use case, неизвестная стадия перечисляет доступные."""
    # Фильтры уходят в сценарий, а не применяются в обработчике.
    services = tools()
    services.query.list_tasks.return_value = TaskPageDto(
        items=[task_dto()], total=7, offset=0, limit=5
    )

    result = await srv.list_tasks(
        FakeContext(),
        project_key="PROJ",
        stage="В работе",
        assignee="Борис",
        only_open=True,
        priority="high",
        due_to="2026-10-01",
        text="отчёт",
        limit=5,
    )

    assert [item["task_key"] for item in result["items"]] == ["PROJ-142"]
    # Пагинация как в Service Desk: всего, есть ли ещё и откуда продолжать.
    assert (result["total"], result["has_more"], result["next_offset"]) == (7, True, 1)
    services.query.list_tasks.assert_awaited_once_with(
        project_id=PROJECT_ID,
        stage_name="В работе",
        assignee="Борис",
        only_open=True,
        limit=5,
        offset=0,
        query="отчёт",
        filters=TaskFilters(priority="high", due_to=date(2026, 10, 1)),
    )
    # Неизвестная стадия отвечает списком доступных. Без него вызывающему пришлось бы угадывать написание.
    services = tools()
    services.query.list_tasks.side_effect = ProjectStageNameNotFoundError(
        stage_name="Ревью",
        known=["В работе", "Готово"],
    )

    with pytest.raises(ToolError) as error:
        await srv.list_tasks(FakeContext(), project_key="PROJ", stage="Ревью")

    assert "В работе" in str(error.value)
    assert "Готово" in str(error.value)


async def test_get_task_returns_details_with_done_flag(tools) -> None:
    """Карточка задачи с числом комментариев и признаком завершения."""
    # Карточка задачи содержит описание и число комментариев.
    services = tools()
    services.query.resolve_task.return_value = ResolvedTask(
        task_id=7,
        project_id=PROJECT_ID,
        task_key="PROJ-142",
    )
    services.query.get_task_details.return_value = TaskDetailsDto(
        wbs_path=None,
        summary=task_dto(),
        role="BE",
        description="Подробности",
        comment_count=3,
    )

    services.tasks.get_task.return_value = SimpleNamespace(
        start_date=None,
        checklist_revision=0,
        report=None,
        participants=[
            SimpleNamespace(
                role=SimpleNamespace(value="OBSERVER"),
                user=SimpleNamespace(
                    username="b.boris", first_name="Борис", last_name="", middle_name=None
                ),
            )
        ],
    )

    from src.schemas.task_reports import TaskReportSchema

    services.tasks.get_task.return_value.report = TaskReportSchema(
        task_id=7, revision=2, body_md="Результат без комментария"
    )
    result = await srv.get_task(FakeContext(), task_key="PROJ-142")
    assert result["report"]["body_md"] == "Результат без комментария"

    assert result["task_key"] == "PROJ-142"
    assert result["comment_count"] == 3
    # Участники задачи — логинами: их понимают пишущие инструменты.
    assert result["participants"] == [
        {"role": "OBSERVER", "login": "b.boris", "full_name": "Борис"}
    ]
    services.query.get_task_details.assert_awaited_once_with(task_id=7)
    # Задача в завершающей стадии помечается как выполненная.
    services = tools()
    services.query.resolve_task.return_value = ResolvedTask(
        task_id=7,
        project_id=PROJECT_ID,
        task_key="PROJ-142",
    )
    services.query.get_task_details.return_value = TaskDetailsDto(
        wbs_path=None,
        summary=task_dto(stage="Готово", is_done=True),
        role=None,
        description=None,
        comment_count=0,
    )

    services.tasks.get_task.return_value = SimpleNamespace(
        start_date=None,
        checklist_revision=0,
        report=None,
        participants=[
            SimpleNamespace(
                role=SimpleNamespace(value="OBSERVER"),
                user=SimpleNamespace(
                    username="b.boris", first_name="Борис", last_name="", middle_name=None
                ),
            )
        ],
    )

    result = await srv.get_task(FakeContext(), task_key="PROJ-142")

    assert result["is_done"] is True


async def test_list_comments_returns_body_and_author(tools) -> None:
    """Комментарии отдаются с автором, временем и ключом задачи."""
    services = tools()
    services.query.resolve_task.return_value = ResolvedTask(
        task_id=7,
        project_id=PROJECT_ID,
        task_key="PROJ-142",
    )
    services.query.list_comments.return_value = [
        CommentDto(
            task_key="PROJ-142",
            author="Борис",
            created_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
            body="Текст",
            comment_id=31,
        )
    ]

    result = await srv.list_comments(FakeContext(), task_key="PROJ-142", limit=10)

    assert result == [
        {
            "comment_id": 31,
            "task_key": "PROJ-142",
            "author": "Борис",
            "created_at": "2026-09-01T10:00:00+00:00",
            "body": "Текст",
            "body_meta": {
                "truncated": False,
                "length": 5,
                "length_unit": "unicode_code_points",
                "read_full": None,
            },
        }
    ]


async def test_search_tasks_delegates_and_survives_no_matches(tools) -> None:
    """Поиск уходит в use case и на пустом результате отдаёт пустой список."""
    # Поиск выполняется сценарием, обработчик только форматирует ответ.
    services = tools()
    services.query.search_tasks.return_value = [task_dto()]

    result = await srv.search_tasks(FakeContext(), project_key="PROJ", query="отчёт", limit=7)

    assert [item["task_key"] for item in result] == ["PROJ-142"]
    services.query.search_tasks.assert_awaited_once_with(
        project_id=PROJECT_ID,
        query="отчёт",
        limit=7,
    )
    # Отсутствие совпадений — пустой список, а не ошибка.
    services = tools()
    services.query.search_tasks.return_value = []

    assert await srv.search_tasks(FakeContext(), project_key="PROJ", query="нет") == []


async def test_calendar_range_presents_keys_limits_and_rejects_bad_dates(tools) -> None:
    """Отображаемые ключи, пометка обрезанного результата и отказ на битой дате."""
    # Календарь отдаётся отображаемыми ключами, без внутренних id.
    services = tools()
    services.calendar.get_range.return_value = calendar_response([calendar_task()])

    result = await srv.get_calendar_range(
        FakeContext(),
        project_key="proj",
        date_from="2026-09-01",
        date_to="2026-09-30",
    )

    assert result["project_key"] == "PROJ"
    assert result["tasks"][0]["task_key"] == "PROJ-142"
    assert "id" not in result["tasks"][0]
    assert result["milestones"][0]["title"] == "MVP"
    assert result["truncated"] is False
    # Обрезанный по лимиту ответ помечается явно. Иначе вызывающий принял бы неполный список за полный.
    services = tools()
    services.calendar.get_range.return_value = calendar_response(
        [calendar_task(id=7, key="PROJ-1"), calendar_task(id=8, key="PROJ-2")]
    )

    result = await srv.get_calendar_range(
        FakeContext(),
        project_key="PROJ",
        date_from="2026-09-01",
        date_to="2026-09-30",
        limit=1,
    )

    assert [item["task_key"] for item in result["tasks"]] == ["PROJ-1"]
    assert result["truncated"] is True
    # Некорректная дата отвечает понятным сообщением о формате.
    tools()

    with pytest.raises(ToolError) as error:
        await srv.get_calendar_range(
            FakeContext(),
            project_key="PROJ",
            date_from="01.09.2026",
            date_to="2026-09-30",
        )

    assert "ГГГГ-ММ-ДД" in str(error.value)


async def test_list_tasks_without_due_date_is_bounded(tools) -> None:
    """Пул задач без срока ограничен лимитом и отдаётся ключами."""
    services = tools()
    services.calendar.get_unscheduled.return_value = UnscheduledTasksPageSchema(
        items=[
            calendar_task(
                title="Без срока",
                start_date=None,
                due_date=None,
                is_overdue=False,
                risk_level=None,
                risk_reasons=[],
            )
        ],
        next_cursor=None,
    )

    result = await srv.list_tasks_without_due_date(FakeContext(), project_key="PROJ", limit=3)

    assert [item["task_key"] for item in result] == ["PROJ-142"]
    assert services.calendar.get_unscheduled.await_args.kwargs["limit"] == 3


async def test_list_milestones_includes_system_project_deadline(tools) -> None:
    """Системная веха дедлайна приходит из сценария вместе с обычными."""
    services = tools()
    services.query.list_milestones.return_value = [
        MilestoneDto(
            title="MVP",
            due_date=date(2026, 9, 20),
            status="PLANNED",
            description="Первая версия",
            is_system=False,
        ),
        MilestoneDto(
            title="Дедлайн проекта",
            due_date=date(2026, 9, 30),
            status="PLANNED",
            description=None,
            is_system=True,
        ),
    ]

    result = await srv.list_milestones(FakeContext(), project_key="PROJ", limit=10)

    assert [item["title"] for item in result] == ["MVP", "Дедлайн проекта"]
    assert result[-1]["is_system"] is True
