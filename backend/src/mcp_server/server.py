"""MCP-сервер трекера: инструменты поверх тех же сервисов, что и REST.

Собственной бизнес-логики здесь нет. Каждый инструмент проходит ту же
аутентификацию и ту же проверку участия в проекте, что и HTTP-эндпоинты,
и вызывает те же сервисы. Право ключа на инструмент объявлено в реестре
(``registry.tt_tool``) и проверяется централизованно в ``tool_context``.

Сервер живёт отдельным процессом (``python -m src.mcp_server.worker``,
контейнер ``mcp``); сборка ASGI-приложения — в ``src/mcp_server/app.py``.
Здесь — сервер и инструменты чтения; запись — ``write_tools``, задачи и
календарь — ``task_tools``, риски — ``risk_tools``, каталог действий
проектного агента — ``project_tools``.
"""

import logging
from datetime import date
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.applications import Starlette

from src.core.settings import Settings
from src.exceptions.base import ApplicationError
from src.knowledge.catalog import SourceType
from src.mcp_server.catalog import CatalogDiagnostics, build_version
from src.mcp_server.context import ToolContext, resolve_project, resolve_task, tool_context
from src.mcp_server.links import LINKS_HINT, build_links, put_url
from src.mcp_server.presenters import (
    comment_item,
    project_detail,
    project_summary,
    shorten,
    task_detail,
    task_summary,
)
from src.mcp_server.registry import tt_tool
from src.mcp_server.services import build_knowledge_search
from src.schemas.knowledge import KnowledgeReadRequest
from src.services.project_query import UnknownStageError
from src.services.tasks import user_display_name
from src.utils.api_scopes import SCOPE_KNOWLEDGE_READ, SCOPE_PROJECTS_READ, SCOPE_TASKS_READ
from src.utils.task_filters import TaskFilters

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

INSTRUCTIONS = """TELOS SPACE — пространство проектов и задач с помощником Арисом (ARIS).
Ключ проекта PROJ, задачи PROJ-142.
Инструменты tt_ используют те же сервисы и права владельца ключа, что REST.
Списки постраничные: учитывай limit, offset и next_offset. Не выдумывай ID.
Описания в карточках и комментарии в списке — превью до 2000 символов.
Метаданные *_meta содержат truncated, полную length в Unicode code points
и вызов read_full; tt_get_task/tt_get_project с full_text=true и tt_get_comment
возвращают полный исходный текст без разбиения на части.
Поле url содержит проверенную ссылку. Каталог tt_list_project_tools раскрывает
общие операции, включая ИСР. Для изменений нужны соответствующие права;
опасные действия запрашивают отдельное подтверждение пользователя."""


mcp_server = MCPServer(
    name="telos-space",
    title="TELOS SPACE",
    instructions=INSTRUCTIONS,
    version=build_version(),
    middleware=[CatalogDiagnostics(build_version())],
)

ProjectKey = Annotated[str, Field(description="Ключ проекта, например PROJ.")]
TaskKey = Annotated[str, Field(description="Ключ задачи, например PROJ-142.")]
FullText = Annotated[
    bool,
    Field(description="Вернуть полное описание без обрезки; false — превью до 2000 символов."),
]


def links_of(tools: ToolContext):
    """Сборщик ссылок на интерфейс из настроек вызова."""
    return build_links(tools.settings.app.app_public_url)


def domain_message(error: ApplicationError, fallback: str) -> str:
    """Отдаёт доменное сообщение об ошибке, не раскрывая внутренних деталей."""
    detail = getattr(error, "detail", None)
    return str(detail) if detail else fallback


def with_task_url(tools: ToolContext, item: dict, task_id: int | None) -> dict:
    """Добавляет к карточке задачи ссылку на неё в интерфейсе."""
    return put_url(item, links_of(tools).task(task_id))


def parse_date(value: str, field_name: str = "Дата") -> date:
    """Разбирает date-only аргумент инструмента."""
    try:
        return date.fromisoformat(value.strip())
    except ValueError as error:
        raise ToolError(f"{field_name} должен быть в формате ГГГГ-ММ-ДД.") from error


# ── Проекты ─────────────────────────────────────────────────────────────────


@tt_tool(
    name="tt_list_projects",
    title="Список проектов",
    description=(
        "Возвращает проекты, доступные вызывающему, с ключом, названием, статусом и "
        "ссылкой. " + LINKS_HINT
    ),
    scope=SCOPE_PROJECTS_READ,
)
async def list_projects(context: Context) -> list[dict]:
    """Возвращает доступные пользователю проекты."""
    async with tool_context(context) as tools:
        try:
            projects = await tools.services.query.list_accessible_projects(
                user_id=tools.principal.user_id
            )
        except ApplicationError as error:
            raise ToolError("Не удалось получить список проектов.") from error
        links = links_of(tools)
        return [
            put_url(project_summary(item), links.project(item.project_key)) for item in projects
        ]


@tt_tool(
    name="tt_get_project",
    title="Карточка проекта",
    description=(
        "Возвращает описание проекта, его стадии и число задач в каждой стадии. "
        "По умолчанию описание — превью до 2000 символов; description_meta содержит "
        "факт обрезки, полную длину и вызов для дочитывания. full_text=true возвращает "
        "полный исходный Markdown. Проект задаётся ключом, например PROJ."
    ),
    scope=SCOPE_PROJECTS_READ,
)
async def get_project(
    context: Context, project_key: ProjectKey, full_text: FullText = False
) -> dict:
    """Возвращает подробную карточку проекта."""
    async with tool_context(context) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            overview = await tools.services.query.get_project_overview(project_id=project_id)
        except ApplicationError as error:
            raise ToolError("Не удалось получить состав проекта.") from error
        return put_url(
            project_detail(overview, full_text=full_text, include_text_metadata=True),
            links_of(tools).project(project_key),
        )


@tt_tool(
    name="tt_list_project_members",
    title="Команда проекта",
    description=(
        "Возвращает участников проекта: логин, ФИО и роль (OWNER, MEMBER). "
        "Логин отсюда годится для assignee, observers и owner в пишущих инструментах."
    ),
    scope=SCOPE_PROJECTS_READ,
)
async def list_project_members(context: Context, project_key: ProjectKey) -> list[dict]:
    """Возвращает команду проекта."""
    async with tool_context(context) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            members = await tools.services.members.get_member_list(project_id)
        except ApplicationError as error:
            raise ToolError("Не удалось получить команду проекта.") from error
        return [
            {
                "login": item.user.username,
                "full_name": user_display_name(item.user),
                "role": item.role.value,
            }
            for item in members
        ]


@tt_tool(
    name="tt_list_stages",
    title="Стадии проекта",
    description=(
        "Возвращает стадии (колонки доски) проекта по порядку: название и признак "
        "завершающей стадии. Название стадии используют tt_move_task и tt_create_task."
    ),
    scope=SCOPE_PROJECTS_READ,
)
async def list_stages(context: Context, project_key: ProjectKey) -> list[dict]:
    """Возвращает стадии проекта."""
    async with tool_context(context) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            stages = await tools.services.stages.get_stage_list(project_id=project_id)
        except ApplicationError as error:
            raise ToolError("Не удалось получить стадии проекта.") from error
        return [
            {"name": item.name, "order": item.order_index, "is_done_stage": item.is_done_stage}
            for item in stages
        ]


# ── Задачи: чтение ─────────────────────────────────────────────────────────


@tt_tool(
    name="tt_list_tasks",
    title="Список задач проекта",
    description=(
        "Возвращает страницу задач проекта с фильтрами: стадия, исполнитель, открытые "
        "или завершённые, приоритет, срок от и до, задачи без срока, текст. Ответ: "
        "items, total, has_more, next_offset. " + LINKS_HINT
    ),
    scope=SCOPE_TASKS_READ,
)
async def list_tasks(
    context: Context,
    project_key: ProjectKey,
    stage: Annotated[str | None, Field(description="Название стадии, например «В работе».")] = None,
    assignee: Annotated[str | None, Field(description="Имя исполнителя целиком.")] = None,
    only_open: Annotated[bool, Field(description="Оставить только незавершённые задачи.")] = False,
    is_done: Annotated[
        bool | None,
        Field(description="true — только завершённые, false — только открытые."),
    ] = None,
    priority: Annotated[
        str | None, Field(description="Приоритет: LOW, MEDIUM, HIGH или URGENT.")
    ] = None,
    due_from: Annotated[str | None, Field(description="Срок не раньше, ГГГГ-ММ-ДД.")] = None,
    due_to: Annotated[str | None, Field(description="Срок не позже, ГГГГ-ММ-ДД.")] = None,
    without_due_date: Annotated[bool, Field(description="Только задачи без срока.")] = False,
    text: Annotated[
        str | None, Field(description="Текст: поиск по названию, описанию и комментариям.")
    ] = None,
    limit: Annotated[int, Field(description="Максимум задач в ответе.", ge=1, le=MAX_LIMIT)] = (
        DEFAULT_LIMIT
    ),
    offset: Annotated[int, Field(description="Сколько задач пропустить.", ge=0)] = 0,
) -> dict:
    """Возвращает страницу задач проекта с фильтрами."""
    filters = TaskFilters(
        priority=priority,
        is_done=is_done,
        due_from=parse_date(due_from, "due_from") if due_from else None,
        due_to=parse_date(due_to, "due_to") if due_to else None,
        without_due_date=without_due_date,
    )
    async with tool_context(context) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            page = await tools.services.query.list_tasks(
                project_id=project_id,
                stage_name=stage,
                assignee=assignee,
                only_open=only_open,
                limit=limit,
                offset=offset,
                query=text,
                filters=filters,
            )
        except UnknownStageError as error:
            raise ToolError(error.detail) from error
        except ApplicationError as error:
            raise ToolError("Не удалось получить задачи проекта.") from error
        return {
            "items": [
                with_task_url(tools, task_summary(item), item.task_id) for item in page.items
            ],
            "total": page.total,
            "offset": page.offset,
            "limit": page.limit,
            "has_more": page.has_more,
            "next_offset": page.offset + len(page.items) if page.has_more else None,
        }


@tt_tool(
    name="tt_search_tasks",
    title="Поиск задач по тексту",
    description=(
        "Ищет задачи проекта по названию, описанию, тексту комментариев и номеру. "
        "Это лексический поиск: он точен по словам и номерам задач."
    ),
    scope=SCOPE_TASKS_READ,
)
async def search_tasks(
    context: Context,
    project_key: ProjectKey,
    query: Annotated[str, Field(description="Поисковый запрос.", min_length=1)],
    limit: Annotated[
        int,
        Field(description="Максимум задач в ответе.", ge=1, le=MAX_LIMIT),
    ] = DEFAULT_LIMIT,
) -> list[dict]:
    """Ищет задачи проекта лексическим поиском PostgreSQL."""
    async with tool_context(context) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            tasks = await tools.services.query.search_tasks(
                project_id=project_id,
                query=query,
                limit=limit,
            )
        except ApplicationError as error:
            raise ToolError("Не удалось выполнить поиск задач.") from error
        return [with_task_url(tools, task_summary(item), item.task_id) for item in tasks]


@tt_tool(
    name="tt_get_task",
    title="Карточка задачи",
    description=(
        "Возвращает карточку задачи: описание, стадию, приоритет, исполнителя, сроки, "
        "чек-лист, актуальный опубликованный отчёт, участников (исполнитель, постановщик, наблюдатели) и ссылку. "
        "По умолчанию описание — превью до 2000 символов; description_meta содержит "
        "факт обрезки, полную длину и вызов для дочитывания. full_text=true возвращает "
        "полный исходный Markdown. Задача задаётся ключом, например PROJ-142."
        " revision — версия общих полей для expected_revision при изменении."
    ),
    scope=SCOPE_TASKS_READ,
)
async def get_task(context: Context, task_key: TaskKey, full_text: FullText = False) -> dict:
    """Возвращает подробную карточку задачи."""
    async with tool_context(context) as tools:
        resolved = await resolve_task(tools, task_key)
        try:
            details = await tools.services.query.get_task_details(task_id=resolved.task_id)
            full = await tools.services.tasks.get_task(resolved.task_id)
        except ApplicationError as error:
            raise ToolError("Не удалось получить задачу.") from error
        item = task_detail(details, full_text=full_text, include_text_metadata=True)
        item.update(
            revision=details.revision,
            start_date=details.start_date.isoformat() if details.start_date else None,
            checklist_revision=full.checklist_revision,
            report=full.report.model_dump(mode="json") if full.report else None,
            participants=[
                {
                    "role": part.role.value,
                    "login": part.user.username,
                    "full_name": user_display_name(part.user),
                }
                for part in full.participants
            ],
        )
        return with_task_url(tools, item, resolved.task_id)


@tt_tool(
    name="tt_list_comments",
    title="Комментарии задачи",
    description=(
        "Возвращает комментарии задачи в порядке добавления с превью текста до "
        "2000 символов. body_meta содержит факт обрезки, полную длину и вызов "
        "tt_get_comment для полного чтения. comment_id нужен также "
        "tt_update_comment и tt_delete_comment."
    ),
    scope=SCOPE_TASKS_READ,
)
async def list_comments(
    context: Context,
    task_key: TaskKey,
    limit: Annotated[
        int,
        Field(description="Максимум комментариев в ответе.", ge=1, le=MAX_LIMIT),
    ] = DEFAULT_LIMIT,
) -> list[dict]:
    """Возвращает комментарии задачи."""
    async with tool_context(context) as tools:
        resolved = await resolve_task(tools, task_key)
        try:
            comments = await tools.services.query.list_comments(
                task_id=resolved.task_id,
                task_key=resolved.task_key,
                limit=limit,
            )
        except ApplicationError as error:
            raise ToolError("Не удалось получить комментарии.") from error
        return [
            {**comment_item(item, include_text_metadata=True), "comment_id": item.comment_id}
            for item in comments
        ]


@tt_tool(
    name="tt_get_comment",
    title="Полный комментарий задачи",
    description=(
        "Возвращает полный исходный Markdown комментария без обрезки. "
        "Используйте task_key и comment_id из tt_list_comments; проверяются "
        "принадлежность комментария задаче и текущий проект задачи."
    ),
    scope=SCOPE_TASKS_READ,
)
async def get_comment(
    context: Context,
    task_key: TaskKey,
    comment_id: Annotated[int, Field(description="Идентификатор комментария из списка.", gt=0)],
) -> dict:
    """Возвращает полный комментарий доступной задачи."""
    async with tool_context(context) as tools:
        resolved = await resolve_task(tools, task_key)
        try:
            comment = await tools.services.query.get_comment(
                task_id=resolved.task_id,
                project_id=resolved.project_id,
                task_key=resolved.task_key,
                comment_id=comment_id,
            )
        except ApplicationError as error:
            raise ToolError("Комментарий недоступен.") from error
        return with_task_url(
            tools,
            {
                **comment_item(comment, full_text=True, include_text_metadata=True),
                "comment_id": comment.comment_id,
            },
            resolved.task_id,
        )


@tt_tool(
    name="tt_list_task_activity",
    title="История задачи",
    description=(
        "Возвращает историю изменений задачи: что поменялось (стадия, срок, "
        "исполнитель…), было и стало, кто сделал и через какой ключ."
    ),
    scope=SCOPE_TASKS_READ,
)
async def list_task_activity(
    context: Context,
    task_key: TaskKey,
    limit: Annotated[int, Field(description="Максимум событий.", ge=1, le=MAX_LIMIT)] = (
        DEFAULT_LIMIT
    ),
) -> list[dict]:
    """Возвращает историю изменений задачи."""
    async with tool_context(context) as tools:
        resolved = await resolve_task(tools, task_key)
        try:
            events = await tools.services.activity.get_activity(resolved.task_id)
        except ApplicationError as error:
            raise ToolError("Не удалось получить историю задачи.") from error
        return [
            {
                "event": item.event_type.value,
                "from": item.from_value,
                "to": item.to_value,
                "actor": item.actor_name,
                "via_api_key": item.via_api_key_label,
                "at": item.created_at.isoformat(),
            }
            for item in events[-limit:]
        ]


@tt_tool(
    name="tt_list_attachments",
    title="Файлы задачи",
    description="Возвращает файлы задачи: имя, тип, размер и дату загрузки.",
    scope=SCOPE_TASKS_READ,
)
async def list_attachments(context: Context, task_key: TaskKey) -> list[dict]:
    """Возвращает вложения задачи."""
    async with tool_context(context) as tools:
        resolved = await resolve_task(tools, task_key)
        try:
            files = await tools.services.attachments.get_attachments(resolved.task_id)
        except ApplicationError as error:
            raise ToolError("Не удалось получить файлы задачи.") from error
        return [
            {
                "attachment_id": item.id,
                "name": item.original_name,
                "content_type": item.content_type,
                "size": item.size,
                "created_at": item.created_at.isoformat(),
            }
            for item in files
        ]


# ── База знаний ─────────────────────────────────────────────────────────────


@tt_tool(
    name="tt_search_project_knowledge",
    title="Смысловой поиск по базе знаний проекта",
    description=(
        "Ищет по смыслу в задачах, документах, комментариях и вложениях проекта "
        "и возвращает фрагменты с указанием источника. Подходит для вопросов "
        "«что решили по…», когда точные слова неизвестны."
    ),
    scope=SCOPE_KNOWLEDGE_READ,
)
async def search_project_knowledge(
    context: Context,
    project_key: ProjectKey,
    query: Annotated[str, Field(description="Смысловой запрос.", min_length=2)],
    entity_type: Annotated[
        SourceType | None,
        Field(
            description=(
                "Ограничить тип источника. Все поля проверяются по текущему состоянию проекта."
            )
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(description="Максимум фрагментов в ответе.", ge=1, le=50),
    ] = 10,
) -> list[dict]:
    """Гибридный поиск по всем типам с актуальным содержимым и связями."""
    async with tool_context(context) as tools:
        project_id = await resolve_project(tools, project_key)
        search = build_knowledge_search(tools.services.query, tools.runtime, tools.settings)
    # Внешние вызовы (вектор, Qdrant) — после закрытия сессии вызова.
    try:
        return await search.search(
            project_id=project_id, query=query, entity_type=entity_type, limit=limit
        )
    except ApplicationError as error:
        raise ToolError("Не удалось прочитать источники проекта.") from error


@tt_tool(
    name="tt_read_project_knowledge",
    title="Источники и связи проекта",
    description="Читает все типы знаний проекта: list_sources — список, read_source — текст по страницам, related_sources — связи в обе стороны, search_sources — FTS. Используйте source_id из результатов поиска/списка; next_offset задаёт следующую страницу. Для read_source offset измеряется в символах. Работает без векторного поиска.",
    scope=SCOPE_KNOWLEDGE_READ,
)
async def read_project_knowledge(
    context: Context,
    project_key: ProjectKey,
    request: KnowledgeReadRequest,
) -> dict:
    """Единый read-only доступ к полному каталогу после проверки участия."""
    async with tool_context(context) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            return await tools.services.query.read_knowledge(project_id=project_id, request=request)
        except ApplicationError as error:
            raise ToolError("Не удалось прочитать источники проекта.") from error


# ── Календарь и вехи ───────────────────────────────────────────────────────


@tt_tool(
    name="tt_get_calendar_range",
    title="Временной диапазон проекта",
    description=(
        "Возвращает ограниченный диапазон временной карты: даты задач, вехи, "
        "baseline, drift и рассчитанные backend-причины риска."
    ),
    scope=SCOPE_PROJECTS_READ,
)
async def get_calendar_range(
    context: Context,
    project_key: ProjectKey,
    date_from: Annotated[str, Field(description="Первый день диапазона, ГГГГ-ММ-ДД.")],
    date_to: Annotated[str, Field(description="Последний день диапазона, ГГГГ-ММ-ДД.")],
    today: Annotated[
        str | None,
        Field(description="Текущая локальная дата пользователя, ГГГГ-ММ-ДД."),
    ] = None,
    limit: Annotated[
        int,
        Field(description="Максимум задач в ответе.", ge=1, le=MAX_LIMIT),
    ] = DEFAULT_LIMIT,
) -> dict:
    """Возвращает календарные факты по отображаемому ключу проекта."""
    first = parse_date(date_from, "date_from")
    last = parse_date(date_to, "date_to")
    current = parse_date(today, "today") if today else date.today()
    async with tool_context(context) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            calendar = await tools.services.calendar.get_range(
                project_id=project_id,
                date_from=first,
                date_to=last,
                today=current,
            )
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось получить календарь.")) from error
        visible_tasks = calendar.tasks[:limit]
        return {
            "project_key": project_key.strip().upper(),
            "range": {
                "date_from": calendar.range.date_from.isoformat(),
                "date_to": calendar.range.date_to.isoformat(),
            },
            "tasks": [
                {
                    "task_key": item.key,
                    "title": item.title,
                    "start_date": item.start_date.isoformat() if item.start_date else None,
                    "due_date": item.due_date.isoformat() if item.due_date else None,
                    "baseline_start_date": (
                        item.baseline_start_date.isoformat() if item.baseline_start_date else None
                    ),
                    "baseline_due_date": (
                        item.baseline_due_date.isoformat() if item.baseline_due_date else None
                    ),
                    "drift_days": item.drift_days,
                    "assignee": item.assignee,
                    "is_done": item.is_done,
                    "risk_level": item.risk_level,
                    "risk_reasons": [
                        reason.model_dump(mode="json", exclude_none=True)
                        for reason in item.risk_reasons
                    ],
                }
                for item in visible_tasks
            ],
            "milestones": [
                {
                    "title": item.title,
                    "due_date": item.due_date.isoformat(),
                    "status": item.status.value,
                    "is_system": item.is_system,
                }
                for item in calendar.milestones
            ],
            "summary": calendar.summary.model_dump(mode="json"),
            "truncated": len(calendar.tasks) > limit,
        }


@tt_tool(
    name="tt_list_tasks_without_due_date",
    title="Задачи проекта без срока",
    description=(
        "Возвращает ограниченный пул задач без due_date, чтобы их можно было "
        "явно запланировать, не загружая весь backlog проекта."
    ),
    scope=SCOPE_TASKS_READ,
)
async def list_tasks_without_due_date(
    context: Context,
    project_key: ProjectKey,
    limit: Annotated[
        int,
        Field(description="Максимум задач в ответе.", ge=1, le=MAX_LIMIT),
    ] = DEFAULT_LIMIT,
) -> list[dict]:
    """Возвращает задачи без срока с отображаемыми ключами."""
    async with tool_context(context) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            page = await tools.services.calendar.get_unscheduled(
                project_id=project_id,
                today=date.today(),
                cursor=None,
                limit=limit,
            )
        except ApplicationError as error:
            raise ToolError(
                domain_message(error, "Не удалось получить задачи без срока.")
            ) from error
        return [
            {
                "task_key": item.key,
                "title": item.title,
                "assignee": item.assignee,
                "priority": item.priority.value,
                "risk_reasons": [
                    reason.model_dump(mode="json", exclude_none=True)
                    for reason in item.risk_reasons
                ],
            }
            for item in page.items
        ]


@tt_tool(
    name="tt_list_milestones",
    title="Вехи проекта",
    description=(
        "Возвращает пользовательские вехи проекта и системную веху дедлайна "
        "проекта без внутренних числовых идентификаторов."
    ),
    scope=SCOPE_PROJECTS_READ,
)
async def list_milestones(
    context: Context,
    project_key: ProjectKey,
    limit: Annotated[
        int,
        Field(description="Максимум пользовательских вех.", ge=1, le=MAX_LIMIT),
    ] = DEFAULT_LIMIT,
) -> list[dict]:
    """Возвращает простые вехи доступного проекта."""
    async with tool_context(context) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            milestones = await tools.services.query.list_milestones(
                project_id=project_id,
                limit=limit,
            )
        except ApplicationError as error:
            raise ToolError("Не удалось получить вехи проекта.") from error
        return [
            {
                "title": item.title,
                "due_date": item.due_date.isoformat(),
                "status": item.status,
                "description": shorten(item.description),
                "is_system": item.is_system,
            }
            for item in milestones
        ]



def build_mcp_app(*, settings: Settings) -> Starlette:
    """Собирает ASGI-приложение MCP для монтирования в основное приложение.

    Настройки приходят из composition root: транспортный модуль не читает
    конфигурацию приложения сам.

    Args:
        settings: Настройки приложения.

    Returns:
        Starlette-приложение транспорта Streamable HTTP.
    """
    app_settings = settings.app
    # Настройки передаются явно: иначе SDK сам включает защиту с allowlist
    # только под localhost, и доступ по адресу сервера отвечает 421.
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(app_settings.mcp_allowed_hosts),
        allowed_origins=list(app_settings.mcp_allowed_origins),
    )
    return mcp_server.streamable_http_app(
        streamable_http_path="/",
        transport_security=security,
    )

# Остальные инструменты регистрируются импортом: декоратор привязывает их к
# тому же серверу. Импорт в конце файла, потому что модули опираются на уже
# созданный ``mcp_server``.
from src.mcp_server import write_tools  # noqa: E402,F401  isort:skip
from src.mcp_server import task_tools  # noqa: E402,F401  isort:skip
from src.mcp_server import risk_tools  # noqa: E402,F401  isort:skip
from src.mcp_server import project_tools  # noqa: E402,F401  isort:skip
from src.mcp_server import agent_tools  # noqa: E402,F401  isort:skip
from src.mcp_server import board_tools  # noqa: E402,F401  isort:skip
