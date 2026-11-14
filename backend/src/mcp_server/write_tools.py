"""Инструменты MCP, изменяющие задачи, комментарии и вехи.

Каждый вызывает те же сервисы, что и HTTP-эндпоинты: история задачи,
сквозная нумерация и постановка в очередь индексации знаний работают
одинаково независимо от канала. Право ключа на инструмент объявлено в
``telos_tool`` и проверяется в ``tool_context``; необязательный параметр
``actor`` может указывать только логин владельца ключа.
"""

import logging
from typing import Annotated
from uuid import UUID

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.agent.tools import AgentToolContext
from src.db.models.project_milestones import ProjectMilestoneStatus
from src.exceptions.access import AccessServiceError, ResourceWriteRequiredError
from src.exceptions.base import ApplicationError
from src.exceptions.project_stages import ProjectStageNameNotFoundError
from src.exceptions.projects import ProjectMemberUserNotFoundError
from src.mcp_server.context import (
    PROJECT_NOT_AVAILABLE,
    READ_ONLY_PROJECT,
    ToolContext,
    resolve_project,
    resolve_task,
    tool_context,
)
from src.mcp_server.registry import telos_tool
from src.mcp_server.server import TaskKey, domain_message, links_of, parse_date, with_task_url
from src.schemas.enums import TaskPriority
from src.schemas.task_transfers import TaskTransferTargetSchema
from src.services.project_query import StageRefDto
from src.utils.api_scopes import SCOPE_COMMENTS_WRITE, SCOPE_TASKS_DELETE, SCOPE_TASKS_WRITE

logger = logging.getLogger(__name__)

ProjectKey = Annotated[str, Field(description="Ключ проекта, например PROJ.")]
Actor = Annotated[str | None, Field(description="Логин текущего пользователя; действие всегда выполняется с его правами.")]


# ── Задачи ─────────────────────────────────────────────────────────────────


@telos_tool(
    name="telos_create_task",
    title="Создать задачу",
    description=(
        "Создаёт задачу в проекте. Без указания стадии задача попадает в начальную "
        "стадию проекта — крайнюю левую колонку доски, обычно «Бэклог». Возвращает "
        "ключ и ссылку на задачу. Для безопасного повтора после потери ответа "
        "передайте тот же request_id и те же аргументы."
    ),
    scope=SCOPE_TASKS_WRITE,
)
async def create_task(
    context: Context,
    project_key: ProjectKey,
    title: Annotated[str, Field(description="Заголовок задачи.", min_length=1, max_length=255)],
    description: Annotated[str | None, Field(description="Описание задачи в Markdown.")] = None,
    stage: Annotated[
        str | None,
        Field(
            description=(
                "Название стадии. Без значения задача попадает в начальную стадию "
                "проекта — крайнюю левую колонку доски, обычно «Бэклог»."
            )
        ),
    ] = None,
    priority: Annotated[
        str | None,
        Field(description="Приоритет: LOW, MEDIUM, HIGH или URGENT."),
    ] = None,
    assignee: Annotated[
        str | None,
        Field(description="Точный логин исполнителя из команды проекта."),
    ] = None,
    observers: Annotated[
        list[str] | None,
        Field(description="Логины наблюдателей из команды проекта."),
    ] = None,
    start_date: Annotated[str | None, Field(description="Начало в формате ГГГГ-ММ-ДД.")] = None,
    due_date: Annotated[str | None, Field(description="Срок в формате ГГГГ-ММ-ДД.")] = None,
    actor: Actor = None,
    request_id: Annotated[
        UUID | None,
        Field(description="Стабильный UUID операции: повтор с тем же ключом не создаёт новую задачу."),
    ] = None,
) -> dict:
    """Создаёт задачу в доступном проекте."""
    async with tool_context(context, require_write=True) as tools:
        project_id = await resolve_project(tools, project_key)
        payload: dict = {"title": title.strip()}
        if description is not None:
            payload["description_md"] = description
        if stage is not None:
            if request_id is not None:
                payload["stage_name"] = stage
            else:
                payload["stage_id"] = (await resolve_stage(tools, project_id, stage)).stage_id
        if priority is not None:
            payload["priority"] = parse_priority(priority)
        if assignee is not None:
            if request_id is not None:
                payload["executor_username"] = assignee
            else:
                payload["executor_id"] = (
                    await member_user_id(tools, project_id, assignee) if assignee.strip() else None
                )
        if observers:
            if request_id is not None:
                payload["observer_usernames"] = observers
            else:
                payload["observer_ids"] = [
                    await member_user_id(tools, project_id, login) for login in observers
                ]
        if start_date is not None:
            payload["start_date"] = parse_date(start_date)
        if due_date is not None:
            payload["due_date"] = parse_date(due_date)

        try:
            if request_id is not None:
                result = await repeatable_write(
                    tools, project_id=project_id, name="telos_create_task",
                    arguments=payload, request_id=request_id,
                )
                created = result["task"]
                return with_task_url(
                    tools,
                    {"task_key": created["key"], "title": created["title"], "created": True,
                     "revision": created.get("revision", 1)},
                    created["id"],
                )
            created = await tools.services.tasks.create_task(
                project_id=project_id,
                data=payload,
                created_by_user_id=tools.principal.user_id,
            )
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось создать задачу.")) from error
        return with_task_url(
            tools,
            {"task_key": created.key, "title": created.title, "created": True,
             "revision": getattr(created, "revision", 1)},
            created.id,
        )


@telos_tool(
    name="telos_update_task",
    title="Изменить задачу",
    description=(
        "Изменяет переданные поля задачи и не трогает остальные: заголовок, описание, "
        "приоритет, исполнитель, сроки. Стадия — telos_move_task, чек-лист — "
        "telos_set_checklist, наблюдатели — telos_set_watchers. Передавайте expected_revision "
        "из telos_get_task: устаревшая версия отклоняется без записи. Без версии сохраняется "
        "прежнее поведение без защиты от затирания чужой правки."
    ),
    scope=SCOPE_TASKS_WRITE,
)
async def update_task(
    context: Context,
    task_key: TaskKey,
    title: Annotated[str | None, Field(description="Новый заголовок.", max_length=255)] = None,
    description: Annotated[str | None, Field(description="Новое описание в Markdown.")] = None,
    priority: Annotated[
        str | None,
        Field(description="Приоритет: LOW, MEDIUM, HIGH или URGENT."),
    ] = None,
    assignee: Annotated[
        str | None,
        Field(
            description=(
                "Точный логин исполнителя из команды проекта; пустая строка снимает исполнителя."
            )
        ),
    ] = None,
    start_date: Annotated[
        str | None,
        Field(description="Начало ГГГГ-ММ-ДД; пустая строка снимает начало."),
    ] = None,
    due_date: Annotated[
        str | None,
        Field(description="Срок в формате ГГГГ-ММ-ДД; пустая строка снимает срок."),
    ] = None,
    actor: Actor = None,
    expected_revision: Annotated[
        int | None,
        Field(ge=1, strict=True, description="Ожидаемая revision из telos_get_task; устаревшая версия даёт конфликт."),
    ] = None,
) -> dict:
    """Изменяет переданные поля задачи."""
    async with tool_context(context, require_write=True) as tools:
        resolved = await resolve_task(tools, task_key)
        payload: dict = {}
        if title is not None:
            payload["title"] = title.strip()
        if description is not None:
            payload["description_md"] = description
        if priority is not None:
            payload["priority"] = parse_priority(priority)
        if assignee is not None:
            payload["executor_id"] = (
                await member_user_id(tools, resolved.project_id, assignee)
                if assignee.strip()
                else None
            )
        if start_date is not None:
            payload["start_date"] = parse_date(start_date) if start_date.strip() else None
        if due_date is not None:
            payload["due_date"] = parse_date(due_date) if due_date.strip() else None
        if not payload:
            raise ToolError("Не передано ни одного поля для изменения.")
        updated_fields = sorted(payload)
        if expected_revision is not None:
            payload["expected_revision"] = expected_revision

        try:
            updated = await tools.services.tasks.update_task(
                task_id=resolved.task_id,
                data=payload,
                updated_by_user_id=tools.principal.user_id,
            )
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось изменить задачу.")) from error
        return with_task_url(
            tools,
            {"task_key": updated.key, "updated_fields": updated_fields, "revision": getattr(updated, "revision", 1)},
            updated.id,
        )


@telos_tool(
    name="telos_move_task",
    title="Перевести задачу в другую стадию",
    description=(
        "Переводит задачу в стадию с указанным названием и ставит её в конец стадии. "
        "Перевод в завершающую стадию закрывает задачу."
    ),
    scope=SCOPE_TASKS_WRITE,
)
async def move_task(
    context: Context,
    task_key: TaskKey,
    stage: Annotated[str, Field(description="Название целевой стадии.", min_length=1)],
    actor: Actor = None,
) -> dict:
    """Переводит задачу в другую стадию доски."""
    async with tool_context(context, require_write=True) as tools:
        resolved = await resolve_task(tools, task_key)
        target = await resolve_stage(tools, resolved.project_id, stage)

        try:
            moved = await tools.services.tasks.move_task(
                task_id=resolved.task_id,
                stage_id=target.stage_id,
            )
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось переместить задачу.")) from error
        return with_task_url(
            tools,
            {"task_key": moved.key, "stage": target.name, "is_done": target.is_done_stage},
            moved.id,
        )


@telos_tool(
    name="telos_transfer_task",
    title="Перенести задачу в другой проект",
    description=(
        "Переносит задачу в другой проект: у задачи будет новый ключ, связи с задачами "
        "и документами старого проекта снимаются. dry_run=true только показывает, что "
        "изменится. Нужна запись в оба проекта."
    ),
    scope=SCOPE_TASKS_WRITE,
)
async def transfer_task(
    context: Context,
    task_key: TaskKey,
    target_project_key: Annotated[str, Field(description="Ключ проекта, куда переносим.")],
    stage: Annotated[
        str | None, Field(description="Стадия в целевом проекте; без неё — начальная.")
    ] = None,
    remove_outsiders: Annotated[
        bool,
        Field(description="Снять участников, которых нет в команде целевого проекта."),
    ] = False,
    dry_run: Annotated[bool, Field(description="Только показать, что изменится.")] = False,
    actor: Actor = None,
) -> dict:
    """Переносит задачу между контекстами тем же сервисом, что REST transfer."""
    async with tool_context(context, require_write=True) as tools:
        resolved = await resolve_task(tools, task_key)
        target_id = await resolve_project(tools, target_project_key)
        stage_id = (await resolve_stage(tools, target_id, stage)).stage_id if stage else None
        target = TaskTransferTargetSchema(
            project_id=target_id, stage_id=stage_id, remove_outsiders=remove_outsiders
        )
        try:
            if dry_run:
                preview = await tools.services.moves.preview(
                    task_id=resolved.task_id, target=target, user_id=tools.principal.user_id
                )
                return {"dry_run": True, **preview.model_dump(mode="json")}
            result = await tools.services.moves.move(
                task_id=resolved.task_id, target=target, user_id=tools.principal.user_id
            )
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось перенести задачу.")) from error
        return with_task_url(
            tools,
            {
                "from_key": result.from_key,
                "task_key": result.to_key,
                "losses": result.losses.model_dump(mode="json"),
                "notes": result.notes,
            },
            result.task.id,
        )


@telos_tool(
    name="telos_set_task_dates",
    title="Изменить плановые даты задачи",
    description=(
        "Изменяет start_date и/или due_date задачи через доменный сервис и историю. "
        "Пустая строка снимает дату. expected_revision из telos_get_task защищает "
        "от изменения поверх устаревшей версии; без него действует прежнее поведение."
    ),
    scope=SCOPE_TASKS_WRITE,
)
async def set_task_dates(
    context: Context,
    task_key: TaskKey,
    start_date: Annotated[
        str | None,
        Field(description="Начало ГГГГ-ММ-ДД; пустая строка снимает начало."),
    ] = None,
    due_date: Annotated[
        str | None,
        Field(description="Завершение ГГГГ-ММ-ДД; пустая строка снимает срок."),
    ] = None,
    actor: Actor = None,
    expected_revision: Annotated[
        int | None,
        Field(ge=1, strict=True, description="Ожидаемая revision общих полей из telos_get_task."),
    ] = None,
) -> dict:
    """Меняет только явно переданные календарные поля задачи."""
    async with tool_context(context, require_write=True) as tools:
        resolved = await resolve_task(tools, task_key)
        payload: dict = {}
        if start_date is not None:
            payload["start_date"] = parse_date(start_date) if start_date.strip() else None
        if due_date is not None:
            payload["due_date"] = parse_date(due_date) if due_date.strip() else None
        if not payload:
            raise ToolError("Не передано ни одной даты для изменения.")
        updated_fields = sorted(payload)
        if expected_revision is not None:
            payload["expected_revision"] = expected_revision
        try:
            updated = await tools.services.tasks.update_task(
                task_id=resolved.task_id,
                data=payload,
                updated_by_user_id=tools.principal.user_id,
            )
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось изменить даты задачи.")) from error
        return {
            "task_key": updated.key,
            "start_date": updated.start_date.isoformat() if updated.start_date else None,
            "due_date": updated.due_date.isoformat() if updated.due_date else None,
            "updated_fields": updated_fields,
            "revision": getattr(updated, "revision", 1),
        }


@telos_tool(
    name="telos_delete_task",
    title="Удалить задачу",
    description=(
        "Безвозвратно удаляет задачу вместе с её комментариями и файлами. "
        "Требует явного подтверждения confirm=true и права tasks:delete."
    ),
    scope=SCOPE_TASKS_DELETE,
)
async def delete_task(
    context: Context,
    task_key: TaskKey,
    confirm: Annotated[
        bool,
        Field(description="Подтверждение удаления; без него задача не удаляется."),
    ] = False,
    actor: Actor = None,
) -> dict:
    """Удаляет задачу после явного подтверждения."""
    if not confirm:
        # Удаление необратимо. Отдельное поле подтверждения заставляет модель
        # выразить намерение явно, а не «дотянуться» до инструмента случайно.
        raise ToolError("Удаление требует confirm=true: действие необратимо.")

    async with tool_context(context, require_write=True) as tools:
        resolved = await resolve_task(tools, task_key)
        key = resolved.task_key
        await ensure_granted(
            tools.services.access.ensure_task_deletion(
                task_id=resolved.task_id, user_id=tools.principal.user_id
            )
        )
        try:
            await tools.services.tasks.delete_task(task_id=resolved.task_id)
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось удалить задачу.")) from error
        return {"task_key": key, "deleted": True}


# ── Комментарии ────────────────────────────────────────────────────────────


@telos_tool(
    name="telos_add_comment",
    title="Добавить комментарий к задаче",
    description=(
        "Добавляет комментарий к задаче от имени проверенного пользователя запроса "
        "и фиксирует событие в её истории. Автор определяется сервером. "
        "Повторяйте тот же request_id и текст после потери ответа, чтобы не создать дубль."
    ),
    scope=SCOPE_COMMENTS_WRITE,
)
async def add_comment(
    context: Context,
    task_key: TaskKey,
    body: Annotated[str, Field(description="Текст комментария в Markdown.", min_length=1)],
    actor: Actor = None,
    author: Annotated[
        str | None,
        Field(description="Прежнее поле подписи: принимается для совместимости, автор определяется сервером."),
    ] = None,
    request_id: Annotated[
        UUID | None,
        Field(description="Стабильный UUID операции: повтор с тем же ключом не добавляет ещё один комментарий."),
    ] = None,
) -> dict:
    """Добавляет комментарий к задаче."""
    async with tool_context(context, require_write=True) as tools:
        resolved = await resolve_task(tools, task_key)

        try:
            if request_id is not None:
                result = await repeatable_write(
                    tools, project_id=resolved.project_id, name="telos_add_comment",
                    arguments={"task_id": resolved.task_id, "body_md": body},
                    request_id=request_id,
                )
                comment = result["comment"]
                return with_task_url(
                    tools,
                    {
                        "comment_id": comment["id"], "task_key": resolved.task_key,
                        "author": comment["author_name"], "created_at": comment["created_at"],
                    },
                    resolved.task_id,
                )
            comment = await tools.services.comments.add_comment(
                task_id=resolved.task_id, author_id=tools.principal.user_id, body_md=body,
            )
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось добавить комментарий.")) from error
        return with_task_url(
            tools,
            {
                "comment_id": comment.id,
                "task_key": resolved.task_key,
                "author": comment.author_name,
                "created_at": comment.created_at.isoformat(),
            },
            resolved.task_id,
        )


@telos_tool(
    name="telos_update_comment",
    title="Исправить комментарий",
    description=(
        "Заменяет текст комментария. Править можно свой комментарий; чужой — только "
        "владельцу проекта. comment_id — из telos_list_comments."
    ),
    scope=SCOPE_COMMENTS_WRITE,
)
async def update_comment(
    context: Context,
    comment_id: Annotated[int, Field(description="Идентификатор комментария.", gt=0)],
    body: Annotated[str, Field(description="Новый текст в Markdown.", min_length=1)],
    actor: Actor = None,
) -> dict:
    """Исправляет комментарий с той же проверкой авторства, что и REST."""
    async with tool_context(context, require_write=True) as tools:
        await ensure_comment_author(tools, comment_id)
        try:
            current = await tools.services.comments.get_comment(comment_id)
            updated = await tools.services.comments.update_comment(
                comment_id, body, current.body_md
            )
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось исправить комментарий.")) from error
        return {"comment_id": updated.id, "updated": True}


@telos_tool(
    name="telos_delete_comment",
    title="Удалить комментарий",
    description=(
        "Удаляет комментарий. Удалить можно свой; чужой — только владельцу проекта. "
        "Требует confirm=true."
    ),
    scope=SCOPE_COMMENTS_WRITE,
)
async def delete_comment(
    context: Context,
    comment_id: Annotated[int, Field(description="Идентификатор комментария.", gt=0)],
    confirm: Annotated[bool, Field(description="Подтверждение удаления.")] = False,
    actor: Actor = None,
) -> dict:
    """Удаляет комментарий с проверкой авторства."""
    if not confirm:
        raise ToolError("Удаление требует confirm=true: действие необратимо.")
    async with tool_context(context, require_write=True) as tools:
        await ensure_comment_author(tools, comment_id)
        try:
            await tools.services.comments.delete_comment(comment_id=comment_id)
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось удалить комментарий.")) from error
        return {"comment_id": comment_id, "deleted": True}


# ── Вехи ───────────────────────────────────────────────────────────────────


@telos_tool(
    name="telos_create_milestone",
    title="Создать проектную веху",
    description="Создаёт простую календарную веху проекта без отдельного workflow.",
    scope=SCOPE_TASKS_WRITE,
)
async def create_milestone(
    context: Context,
    project_key: ProjectKey,
    title: Annotated[str, Field(description="Название вехи.", min_length=1, max_length=255)],
    due_date: Annotated[str, Field(description="Дата вехи в формате ГГГГ-ММ-ДД.")],
    description: Annotated[str | None, Field(description="Описание вехи в Markdown.")] = None,
    status: Annotated[
        str,
        Field(description="Статус PLANNED или ACHIEVED."),
    ] = "PLANNED",
    actor: Actor = None,
) -> dict:
    """Создаёт пользовательскую веху в доступном проекте."""
    async with tool_context(context, require_write=True) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            milestone_status = ProjectMilestoneStatus(status.strip().upper())
        except ValueError as error:
            raise ToolError("Статус вехи должен быть PLANNED или ACHIEVED.") from error
        try:
            created = await tools.services.milestones.create_milestone(
                project_id,
                {
                    "title": title.strip(),
                    "due_date": parse_date(due_date),
                    "status": milestone_status,
                    "description_md": description,
                },
            )
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось создать веху.")) from error
        result = {
            "project_key": project_key.strip().upper(),
            "title": created.title,
            "due_date": created.due_date.isoformat(),
            "status": created.status.value,
            "created": True,
        }
        url = links_of(tools).project(project_key)
        if url:
            result["url"] = url
        return result


# ── Общие помощники инструментов записи ────────────────────────────────────


async def repeatable_write(
    tools: ToolContext, *, project_id: int, name: str, arguments: dict, request_id: UUID,
) -> dict:
    """Исполняет прямую запись с общей квитанцией и проверяет доступ к результату.

    Args:
        tools: Аутентифицированный контекст MCP с проверенным scope записи.
        project_id: Текущий проект операции.
        name: Создание задачи или добавление комментария из общего каталога.
        arguments: Нормализованные доменные аргументы.
        request_id: Стабильный ключ вызова канонического действия.
    Returns:
        Сохранённый результат доменного действия.
    Raises:
        ApplicationError: При конфликте ключа или отказе доступа к результату.
        ToolError: Если результат больше не принадлежит доступному контексту.
    """
    response = await tools.services.actions.execute(
        context=AgentToolContext(
            project_id=project_id, user_id=tools.principal.user_id,
            can_write=tools.principal.can_write, request_id=request_id,
            allowed_tools=frozenset({name}),
            principal=tools.principal,
        ),
        name=name, arguments=arguments,
    )
    result = response["action"]["result"]
    task_id = result["task"]["id"] if name == "telos_create_task" else arguments["task_id"]
    # Квитанция не даёт доступ к задаче, перенесённой в другой проект или удалённой.
    grant = await tools.services.access.ensure_task_access(
        task_id=task_id, user_id=tools.principal.user_id,
    )
    if grant.project_id != project_id or not grant.can_write:
        raise ToolError(PROJECT_NOT_AVAILABLE)
    return result


async def resolve_stage(tools: ToolContext, project_id: int, stage: str) -> StageRefDto:
    """Находит стадию сервисом и переводит его ошибку в ToolError."""
    try:
        return await tools.services.query.resolve_stage(project_id=project_id, stage_name=stage)
    except ProjectStageNameNotFoundError as error:
        raise ToolError(error.detail) from error
    except ApplicationError as error:
        raise ToolError("Не удалось получить стадии проекта.") from error


async def member_user_id(tools: ToolContext, project_id: int, login: str) -> int:
    """Разрешает участника проекта по логину сервисом команды проекта.

    Правило «только активный участник этого проекта» принадлежит сервису:
    оно одинаково для всех каналов и не должно повторяться в транспорте.
    """
    try:
        return await tools.services.members.resolve_member_user_id(
            project_id=project_id,
            username=login.strip().lower(),
        )
    except ProjectMemberUserNotFoundError as error:
        raise ToolError(
            "Активный пользователь с таким логином не входит в команду проекта."
        ) from error
    except ApplicationError as error:
        raise ToolError("Не удалось проверить участника проекта.") from error


async def ensure_granted(check):
    """Выполняет проверку доступа сервиса и переводит отказ в ToolError."""
    try:
        grant = await check
    except ResourceWriteRequiredError as error:
        raise ToolError(READ_ONLY_PROJECT) from error
    except AccessServiceError as error:
        raise ToolError(domain_message(error, PROJECT_NOT_AVAILABLE)) from error
    return grant


async def ensure_comment_author(tools: ToolContext, comment_id: int) -> None:
    """Автор комментария или владелец проекта и проект открыт на запись — как в REST."""
    grant = await ensure_granted(
        tools.services.access.ensure_comment_authorship(
            comment_id=comment_id, user_id=tools.principal.user_id
        )
    )
    if not grant.can_write:
        raise ToolError(READ_ONLY_PROJECT)


def parse_priority(value: str) -> TaskPriority:
    """Разбирает приоритет и подсказывает допустимые значения."""
    try:
        return TaskPriority(value.strip().upper())
    except ValueError as error:
        allowed = ", ".join(item.value for item in TaskPriority)
        raise ToolError(f"Приоритет должен быть одним из: {allowed}.") from error
