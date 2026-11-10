"""Публичный контракт MCP: имена инструментов, входные схемы и форма ответа.

Каталог содержит ровно 46 канонических telos_<действие>_<сущность> имён.
Входные схемы и единые ответы с метаданными превью зафиксированы явно;
представления принимают DTO общих сервисов.
"""

from datetime import UTC, date, datetime

from src.mcp_server.presenters import (
    TEXT_LIMIT,
    TRUNCATION_NOTE,
    comment_item,
    project_detail,
    project_summary,
    shorten,
    task_detail,
    task_summary,
)
from src.mcp_server.server import mcp_server
from src.schemas.enums import TaskPriority
from src.services.project_query import (
    CommentDto,
    ProjectOverviewDto,
    ProjectStageDto,
    ProjectSummaryDto,
    TaskDetailsDto,
    TaskSummaryDto,
)

# Обязательные поля каждого инструмента: изменение этого множества ломает
# уже настроенных внешних клиентов.
EXPECTED_REQUIRED = {
    "telos_create_project": {"key", "name", "request_id"},
    "telos_get_latest_project_summary": {"project_key"},
    "telos_list_project_members": {"project_key"},
    "telos_list_stages": {"project_key"},
    "telos_get_comment": {"task_key", "comment_id"},
    "telos_list_task_activity": {"task_key"},
    "telos_list_attachments": {"task_key"},
    "telos_transfer_task": {"task_key", "target_project_key"},
    "telos_update_comment": {"comment_id", "body"},
    "telos_delete_comment": {"comment_id"},
    "telos_set_checklist": {"task_key", "checklist", "checklist_revision"},
    "telos_set_watchers": {"task_key", "observers"},
    "telos_list_dependencies": {"project_key"},
    "telos_create_dependency": {"predecessor", "successor"},
    "telos_delete_dependency": {"project_key", "dependency_id"},
    "telos_ask_aris": {"text"},
    "telos_get_aris_result": {"request_id"},
    "telos_list_personal_boards": {"args"},
    "telos_get_board_scene": {"args"},
    "telos_draw_board_diagram": {"args", "request_id"},
    "telos_update_board_shapes": {"args", "request_id"},
    "telos_arrange_board_objects": {"args", "request_id"},
    "telos_list_project_tools": {"project_key"},
    "telos_execute_project_tool": {"project_key", "name", "arguments"},
    "telos_read_project_knowledge": {"project_key", "request"},
    "telos_list_project_risks": {"project_key"},
    "telos_get_project_risk": {"project_key", "risk_key"},
    "telos_create_project_risk": {
        "project_key",
        "confirmed_by_user",
        "title",
        "description",
        "probability",
        "impact",
        "response_strategy",
    },
    "telos_update_project_risk": {"project_key", "risk_key", "confirmed_by_user"},
    "telos_list_projects": set(),
    "telos_get_project": {"project_key"},
    "telos_list_tasks": {"project_key"},
    "telos_get_task": {"task_key"},
    "telos_list_comments": {"task_key"},
    "telos_search_tasks": {"project_key", "query"},
    "telos_search_project_knowledge": {"project_key", "query"},
    "telos_get_calendar_range": {"project_key", "date_from", "date_to"},
    "telos_list_tasks_without_due_date": {"project_key"},
    "telos_list_milestones": {"project_key"},
    "telos_create_task": {"project_key", "title"},
    "telos_update_task": {"task_key"},
    "telos_move_task": {"task_key", "stage"},
    "telos_delete_task": {"task_key"},
    "telos_add_comment": {"task_key", "body"},
    "telos_set_task_dates": {"task_key"},
    "telos_create_milestone": {"project_key", "title", "due_date"},
}


EXPECTED_TOOLS = set(EXPECTED_REQUIRED)


async def test_tool_names_are_frozen() -> None:
    """Набор публичных инструментов не меняется рефакторингом."""
    tools = await mcp_server.list_tools()

    assert len(EXPECTED_TOOLS) == 46
    assert EXPECTED_TOOLS == {tool.name for tool in tools}


async def test_required_arguments_are_frozen() -> None:
    """Обязательные аргументы каждого инструмента остаются прежними."""
    tools = {tool.name: tool for tool in await mcp_server.list_tools()}

    actual = {name: set(tools[name].input_schema.get("required", [])) for name in EXPECTED_REQUIRED}

    assert actual == EXPECTED_REQUIRED


async def test_tools_never_expose_numeric_ids() -> None:
    """Наружу отдаются отображаемые ключи, а не первичные ключи БД."""
    tools = await mcp_server.list_tools()

    for tool in tools:
        if tool.name not in EXPECTED_TOOLS:
            continue
        properties = set(tool.input_schema.get("properties", {}))
        forbidden = properties & {"project_id", "task_id", "milestone_id"}
        assert not forbidden, f"{tool.name} принимает числовой идентификатор: {forbidden}."


async def test_every_tool_keeps_its_description() -> None:
    """Описание — часть контракта: по нему модель выбирает инструмент."""
    tools = await mcp_server.list_tools()

    for tool in tools:
        assert tool.description, f"У инструмента {tool.name} нет описания."


def _project() -> ProjectSummaryDto:
    """Краткая карточка проекта из сервиса."""
    return ProjectSummaryDto(
        project_key="CHAR",
        name="Характеризация",
        status="ACTIVE",
        start_date=date(2026, 9, 1),
        due_date=date(2026, 12, 31),
    )


def _task() -> TaskSummaryDto:
    """Краткая карточка задачи из сервиса."""
    return TaskSummaryDto(
        task_key="CHAR-142",
        title="Собрать отчёт",
        stage="В работе",
        is_done=False,
        priority=TaskPriority.HIGH.value,
        assignee="Борис",
        due_date=date(2026, 10, 1),
    )


def test_presenter_keys_are_frozen() -> None:
    """Набор полей каждого представления MCP зафиксирован: проект, его карточка, задача, её карточка и комментарий."""
    # Карточка проекта в списке сохраняет свои ключи.
    result = project_summary(_project())

    assert set(result) == {"project_key", "name", "status", "start_date", "due_date"}
    assert result["project_key"] == "CHAR"
    assert result["start_date"] == "2026-09-01"
    # Подробная карточка проекта сохраняет свои ключи и форму стадий.
    result = project_detail(
        ProjectOverviewDto(
            summary=_project(),
            description="Описание проекта.",
            total_tasks=3,
            stages=[ProjectStageDto(name="В работе", is_done_stage=False, task_count=3)],
        )
    )

    assert set(result) == {
        "project_key",
        "name",
        "status",
        "start_date",
        "due_date",
        "description",
        "description_meta",
        "total_tasks",
        "stages",
    }
    assert result["stages"] == [{"name": "В работе", "is_done_stage": False, "task_count": 3}]
    # Карточка задачи в списке сохраняет свои ключи и формат ключа задачи.
    result = task_summary(_task())

    assert set(result) == {
        "task_key",
        "checklist",
        "title",
        "stage",
        "is_done",
        "priority",
        "assignee",
        "due_date",
    }
    assert result["task_key"] == "CHAR-142"
    assert result["is_done"] is False
    # Подробная карточка задачи сохраняет свои ключи.
    result = task_detail(
        TaskDetailsDto(
            summary=_task(),
            role="BE",
            wbs_path="1.2 Раздел",
            description="Подробности задачи.",
            comment_count=2,
        )
    )

    assert set(result) == {
        "task_key",
        "checklist",
        "title",
        "stage",
        "is_done",
        "priority",
        "assignee",
        "due_date",
        "role",
        "wbs_path",
        "description",
        "description_meta",
        "comment_count",
    }
    # Комментарий сохраняет свои ключи и ISO-формат времени.
    comment = CommentDto(
        task_key="CHAR-142",
        author="Борис",
        created_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        body="Текст комментария.",
    )

    result = comment_item(comment)

    assert set(result) == {"task_key", "author", "created_at", "body", "body_meta"}
    assert result["created_at"] == "2026-09-01T10:00:00+00:00"


def test_long_text_is_truncated_with_a_visible_note() -> None:
    """Длинный текст обрезается с пометкой, короткий остаётся как есть."""
    # Обрезка длинного текста остаётся явной: модель не примет её за конец.
    result = shorten("а" * (TEXT_LIMIT + 100))

    assert result is not None
    assert result.endswith(TRUNCATION_NOTE)
    assert len(result) == TEXT_LIMIT + len(TRUNCATION_NOTE)
    # Короткий текст не трогается, пустой отдаётся как ``None``.
    assert shorten("коротко") == "коротко"
    assert shorten("") is None
    assert shorten(None) is None
