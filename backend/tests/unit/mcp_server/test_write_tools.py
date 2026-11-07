"""Проверки инструментов записи MCP.

Инструмент отвечает за разбор аргументов, права токена и перевод ошибок;
сами изменения делает доменный сервис. Поэтому здесь проверяется, что
именно попадает в сервис и что видит вызывающий, а не состояние БД.
"""

from dataclasses import fields
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.db.models.api_tokens import ApiTokenScope
from src.db.models.project_milestones import ProjectMilestoneStatus
from src.exceptions.projects import ProjectMemberUserNotFoundError, ProjectNotFoundError
from src.exceptions.tasks import TaskNotFoundError
from src.mcp_server import write_tools as wt
from src.schemas.enums import TaskPriority
from src.services.project_query import ResolvedTask, StageRefDto, UnknownStageError
from tests.unit.mcp_server.conftest import PROJECT_ID, FakeContext

TASK_ID = 7
TASK_KEY = "PROJ-142"

RESOLVED = ResolvedTask(task_id=TASK_ID, project_id=PROJECT_ID, task_key=TASK_KEY)
IN_PROGRESS = StageRefDto(stage_id=11, name="В работе", is_done_stage=False)
DONE = StageRefDto(stage_id=12, name="Готово", is_done_stage=True)


def saved_task(**overrides) -> SimpleNamespace:
    """Задача в том виде, в каком её возвращает доменный сервис."""
    values = {
        "id": TASK_ID,
        "key": TASK_KEY,
        "title": "Задача",
        "start_date": None,
        "due_date": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def write_tools(tools):
    """Инструменты записи с токеном, у которого есть право записи."""

    def install():
        services = tools(ApiTokenScope.WRITE)
        # Повторный вызов внутри одного теста начинает новый сценарий:
        # история вызовов предыдущего не должна в него просачиваться.
        for field in fields(services):
            double = getattr(services, field.name)
            if isinstance(double, AsyncMock):
                double.reset_mock()
        services.query.resolve_task.return_value = RESOLVED
        services.query.resolve_stage.return_value = IN_PROGRESS
        services.tasks.create_task.return_value = saved_task()
        services.tasks.update_task.return_value = saved_task()
        services.tasks.move_task.return_value = saved_task()
        return services

    return install


async def test_create_task_passes_only_given_fields(write_tools) -> None:
    """Непереданные поля не попадают в сервис.

    Иначе инструмент затирал бы значения по умолчанию вместо того, чтобы
    оставить их доменному слою.
    """
    services = write_tools()

    result = await wt.create_task(FakeContext(), project_key="PROJ", title="  Новая  ")

    payload = services.tasks.create_task.await_args.kwargs["data"]
    assert payload == {"title": "Новая"}
    assert result == {"task_key": TASK_KEY, "title": "Задача", "created": True, "revision": 1}


async def test_create_task_uses_the_owner_of_the_token_as_author(write_tools) -> None:
    """Автором создания записывается владелец токена."""
    services = write_tools()

    await wt.create_task(FakeContext(), project_key="PROJ", title="Новая")

    assert services.tasks.create_task.await_args.kwargs["created_by_user_id"] == 1


async def test_create_task_resolves_optional_fields(write_tools) -> None:
    """Стадия по названию, приоритет и дата из строки, исполнитель по точному логину команды."""
    # Название стадии превращается в идентификатор сервисом проекта.
    services = write_tools()

    await wt.create_task(FakeContext(), project_key="PROJ", title="Новая", stage="в работе")

    services.query.resolve_stage.assert_awaited_once_with(
        project_id=PROJECT_ID,
        stage_name="в работе",
    )
    assert services.tasks.create_task.await_args.kwargs["data"]["stage_id"] == 11
    # Приоритет и срок приходят строками, а уходят типизированными.
    services = write_tools()

    await wt.create_task(
        FakeContext(),
        project_key="PROJ",
        title="Новая",
        priority="high",
        due_date="2026-10-01",
    )

    payload = services.tasks.create_task.await_args.kwargs["data"]
    assert payload["priority"] is TaskPriority.HIGH
    assert payload["due_date"] == date(2026, 10, 1)
    # Исполнитель разрешается сервисом команды проекта.
    services = write_tools()
    services.members.resolve_member_user_id.return_value = 42

    await wt.create_task(FakeContext(), project_key="PROJ", title="Новая", assignee="boris")

    services.members.resolve_member_user_id.assert_awaited_once_with(
        project_id=PROJECT_ID,
        username="boris",
    )
    assert services.tasks.create_task.await_args.kwargs["data"]["executor_id"] == 42


async def test_create_task_rejects_unusable_arguments(write_tools) -> None:
    """Неизвестный или внешний пользователь, неизвестный приоритет, битая дата и чужой проект."""
    # Несуществующий и посторонний логин дают один и тот же ответ. Различие в тексте позволило бы перебором проверять, кто есть в системе.
    services = write_tools()
    services.members.resolve_member_user_id.side_effect = ProjectMemberUserNotFoundError(
        username="stranger"
    )

    with pytest.raises(ToolError) as error:
        await wt.create_task(FakeContext(), project_key="PROJ", title="Новая", assignee="stranger")

    assert str(error.value) == "Активный пользователь с таким логином не входит в команду проекта."
    services.tasks.create_task.assert_not_awaited()
    # Неверный приоритет отвечает списком допустимых значений.
    services = write_tools()

    with pytest.raises(ToolError) as error:
        await wt.create_task(FakeContext(), project_key="PROJ", title="Новая", priority="ASAP")

    assert "HIGH" in str(error.value)
    services.tasks.create_task.assert_not_awaited()
    # Дата вне ISO-формата отклоняется до вызова сервиса.
    services = write_tools()

    with pytest.raises(ToolError) as error:
        await wt.create_task(
            FakeContext(),
            project_key="PROJ",
            title="Новая",
            due_date="01.10.2026",
        )

    assert "ГГГГ-ММ-ДД" in str(error.value)
    services.tasks.create_task.assert_not_awaited()
    # Недоступный проект не даёт создать в нём задачу.
    services = write_tools()
    services.query.resolve_project_id.side_effect = ProjectNotFoundError(project_id=0)

    with pytest.raises(ToolError) as error:
        await wt.create_task(FakeContext(), project_key="OTHER", title="Новая")

    assert str(error.value) == "Проект недоступен."
    services.tasks.create_task.assert_not_awaited()


async def test_update_task_touches_only_given_fields_and_clears_by_empty_value(write_tools) -> None:
    """Изменяются только переданные поля; пустая строка очищает исполнителя и срок, пустой запрос отклоняется."""
    # Пустое изменение — ошибка, а не молчаливый успех.
    services = write_tools()

    with pytest.raises(ToolError) as error:
        await wt.update_task(FakeContext(), task_key=TASK_KEY)

    assert "ни одного поля" in str(error.value)
    services.tasks.update_task.assert_not_awaited()
    # Переданный заголовок меняется, остальные поля не упоминаются.
    services = write_tools()

    result = await wt.update_task(FakeContext(), task_key=TASK_KEY, title="  Другой  ")

    assert services.tasks.update_task.await_args.kwargs == {
        "task_id": TASK_ID,
        "data": {"title": "Другой"},
        "updated_by_user_id": 1,
    }
    assert result == {"task_key": TASK_KEY, "updated_fields": ["title"], "revision": 1}
    # Пустая строка снимает исполнителя и не ищет пользователя.
    services = write_tools()

    await wt.update_task(FakeContext(), task_key=TASK_KEY, assignee="   ")

    assert services.tasks.update_task.await_args.kwargs["data"] == {"executor_id": None}
    services.members.resolve_member_user_id.assert_not_awaited()
    # Пустая строка снимает срок, а не считается неверной датой.
    services = write_tools()

    await wt.update_task(FakeContext(), task_key=TASK_KEY, due_date="")

    assert services.tasks.update_task.await_args.kwargs["data"] == {"due_date": None}


async def test_move_task_resolves_stage_by_name(write_tools) -> None:
    """Стадия ищется по названию, неизвестная — с перечислением доступных."""
    # Перевод в завершающую стадию виден в ответе инструмента.
    services = write_tools()
    services.query.resolve_stage.return_value = DONE

    result = await wt.move_task(FakeContext(), task_key=TASK_KEY, stage="готово")

    services.tasks.move_task.assert_awaited_once_with(task_id=TASK_ID, stage_id=12)
    assert result == {"task_key": TASK_KEY, "stage": "Готово", "is_done": True}
    # Неизвестная стадия отвечает списком доступных.
    services = write_tools()
    services.query.resolve_stage.side_effect = UnknownStageError(
        stage_name="Ревью",
        known=["В работе", "Готово"],
    )

    with pytest.raises(ToolError) as error:
        await wt.move_task(FakeContext(), task_key=TASK_KEY, stage="Ревью")

    assert "В работе" in str(error.value)
    services.tasks.move_task.assert_not_awaited()


async def test_delete_task_requires_explicit_confirmation(write_tools) -> None:
    """Без подтверждения задача не удаляется, с подтверждением — удаляется."""
    # Без confirm=true удаление не выполняется и задача даже не ищется. Удаление необратимо, поэтому подтверждение проверяется раньше всего.
    services = write_tools()

    with pytest.raises(ToolError) as error:
        await wt.delete_task(FakeContext(), task_key=TASK_KEY)

    assert "confirm=true" in str(error.value)
    services.tasks.delete_task.assert_not_awaited()
    services.query.resolve_task.assert_not_awaited()
    # С подтверждением задача удаляется по своему идентификатору.
    services = write_tools()

    result = await wt.delete_task(FakeContext(), task_key=TASK_KEY, confirm=True)

    services.tasks.delete_task.assert_awaited_once_with(task_id=TASK_ID)
    assert result == {"task_key": TASK_KEY, "deleted": True}


async def test_add_comment_is_written_by_token_owner(write_tools) -> None:
    """Автор комментария — владелец токена; подписать чужим именем нельзя."""
    services = write_tools()
    services.comments.add_comment.return_value = SimpleNamespace(
        id=5,
        author_name="Тестов Тест",
        created_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
    )

    result = await wt.add_comment(FakeContext(), task_key=TASK_KEY, body="Текст")

    assert services.comments.add_comment.await_args.kwargs["author_id"] == 1
    assert "author_name" not in services.comments.add_comment.await_args.kwargs
    assert result["author"] == "Тестов Тест"
    assert result["task_key"] == TASK_KEY
    assert result["created_at"] == "2026-09-01T10:00:00+00:00"


async def test_set_task_dates_and_milestones_use_domain_services(write_tools) -> None:
    """Даты задачи и вехи проходят через доменные сервисы; пустой запрос и неизвестный статус отклоняются."""
    # Даты меняются тем же сервисом, что и через HTTP. Так изменение попадает в историю задачи и очередь индексации знаний независимо от канала.
    services = write_tools()
    services.tasks.update_task.return_value = saved_task(
        start_date=date(2026, 9, 1),
        due_date=date(2026, 10, 1),
    )

    result = await wt.set_task_dates(
        FakeContext(),
        task_key=TASK_KEY,
        start_date="2026-09-01",
        due_date="2026-10-01",
    )

    assert services.tasks.update_task.await_args.kwargs == {
        "task_id": TASK_ID,
        "data": {"start_date": date(2026, 9, 1), "due_date": date(2026, 10, 1)},
        "updated_by_user_id": 1,
    }
    assert result["updated_fields"] == ["due_date", "start_date"]
    assert result["due_date"] == "2026-10-01"
    # Вызов без дат не доходит до сервиса.
    services = write_tools()

    with pytest.raises(ToolError) as error:
        await wt.set_task_dates(FakeContext(), task_key=TASK_KEY)

    assert "ни одной даты" in str(error.value)
    services.tasks.update_task.assert_not_awaited()
    # Веха создаётся сервисом, а в ответе возвращается отображаемый ключ.
    services = write_tools()
    services.milestones.create_milestone.return_value = SimpleNamespace(
        title="MVP",
        due_date=date(2026, 9, 20),
        status=ProjectMilestoneStatus.PLANNED,
    )

    result = await wt.create_milestone(
        FakeContext(),
        project_key=" proj ",
        title="  MVP ",
        due_date="2026-09-20",
    )

    project_id, payload = services.milestones.create_milestone.await_args.args
    assert project_id == PROJECT_ID
    assert payload["title"] == "MVP"
    assert payload["status"] is ProjectMilestoneStatus.PLANNED
    assert result == {
        "project_key": "PROJ",
        "title": "MVP",
        "due_date": "2026-09-20",
        "status": "PLANNED",
        "created": True,
    }
    # Неизвестный статус вехи отклоняется до вызова сервиса.
    services = write_tools()

    with pytest.raises(ToolError) as error:
        await wt.create_milestone(
            FakeContext(),
            project_key="PROJ",
            title="MVP",
            due_date="2026-09-20",
            status="DONE",
        )

    assert "PLANNED" in str(error.value)
    services.milestones.create_milestone.assert_not_awaited()


async def test_domain_error_is_translated(write_tools) -> None:
    """Доменная ошибка отдаётся своим сообщением, а не трассировкой."""
    services = write_tools()
    services.tasks.create_task.side_effect = TaskNotFoundError(task_id=TASK_ID)

    with pytest.raises(ToolError) as error:
        await wt.create_task(FakeContext(), project_key="PROJ", title="Новая")

    assert str(error.value) == TaskNotFoundError(task_id=TASK_ID).detail


async def test_read_scope_cannot_use_write_tools(tools) -> None:
    """Токен только для чтения не выполняет изменения.

    Это единственная защита записи в MCP: у инструментов нет отдельного
    слоя прав, кроме области действия токена.
    """
    services = tools(ApiTokenScope.READ)

    with pytest.raises(ToolError):
        await wt.create_task(FakeContext(), project_key="PROJ", title="Новая")

    services.tasks.create_task.assert_not_awaited()
