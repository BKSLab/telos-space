"""Инструменты MCP для чек-листа, наблюдателей, зависимостей и учёта времени.

Всё — поверх тех же сервисов, что и REST: чек-лист и наблюдатели меняются
тем же ``TasksService.update_task`` (REST ``PATCH /tasks/{id}``), зависимости —
``TaskDependenciesService``, трудозатраты — ``TimeEntriesService``. Своих
выборок и правил здесь нет.
"""

from typing import Annotated

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from src.exceptions.base import ApplicationError
from src.mcp_server.context import resolve_project, resolve_task, tool_context
from src.mcp_server.registry import telos_tool
from src.mcp_server.server import TaskKey, domain_message, with_task_url
from src.mcp_server.write_tools import Actor, ProjectKey, member_user_id
from src.schemas.task_checklists import TaskChecklistSchema
from src.utils.api_scopes import SCOPE_TASKS_READ, SCOPE_TASKS_WRITE

# ── Чек-лист и наблюдатели ─────────────────────────────────────────────────


@telos_tool(
    name="telos_set_checklist",
    title="Задать чек-лист задачи",
    description=(
        "Заменяет чек-лист задачи целиком (пункты в нужном порядке) или удаляет его "
        "(checklist=null). checklist_revision — из telos_get_task: если чек-лист успели "
        "поменять, вернётся конфликт, и его надо перечитать."
    ),
    scope=SCOPE_TASKS_WRITE,
)
async def set_checklist(
    context: Context,
    task_key: TaskKey,
    checklist: Annotated[
        TaskChecklistSchema | None,
        Field(description="Полный чек-лист: title и items[{text, done}]; null удаляет."),
    ],
    checklist_revision: Annotated[
        int, Field(description="Ожидаемая версия чек-листа из telos_get_task.", ge=0)
    ],
    actor: Actor = None,
) -> dict:
    """Заменяет чек-лист тем же сценарием, что PATCH /tasks/{id}."""
    async with tool_context(context) as tools:
        resolved = await resolve_task(tools, task_key)
        try:
            updated = await tools.services.tasks.update_task(
                task_id=resolved.task_id,
                data={
                    "checklist": checklist.model_dump() if checklist is not None else None,
                    "checklist_revision": checklist_revision,
                },
                updated_by_user_id=tools.principal.user_id,
            )
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось изменить чек-лист.")) from error
        return with_task_url(
            tools,
            {"task_key": updated.key, "checklist_revision": updated.checklist_revision},
            updated.id,
        )


@telos_tool(
    name="telos_set_watchers",
    title="Задать наблюдателей задачи",
    description=(
        "Заменяет список наблюдателей задачи. Логины — участники проекта "
        "(telos_list_project_members); пустой список снимает всех наблюдателей."
    ),
    scope=SCOPE_TASKS_WRITE,
)
async def set_watchers(
    context: Context,
    task_key: TaskKey,
    observers: Annotated[list[str], Field(description="Логины наблюдателей.")],
    actor: Actor = None,
    expected_revision: Annotated[
        int | None,
        Field(ge=1, strict=True, description="Ожидаемая revision задачи из telos_get_task; без неё прежнее поведение."),
    ] = None,
) -> dict:
    """Заменяет наблюдателей тем же сценарием, что PATCH /tasks/{id}."""
    async with tool_context(context) as tools:
        resolved = await resolve_task(tools, task_key)
        observer_ids = [
            await member_user_id(tools, resolved.project_id, login) for login in observers
        ]
        payload = {"observer_ids": observer_ids}
        if expected_revision is not None:
            payload["expected_revision"] = expected_revision
        try:
            updated = await tools.services.tasks.update_task(
                task_id=resolved.task_id,
                data=payload,
                updated_by_user_id=tools.principal.user_id,
            )
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось изменить наблюдателей.")) from error
        watchers = [
            part.user.username for part in updated.participants if part.role.value == "OBSERVER"
        ]
        return with_task_url(
            tools,
            {"task_key": updated.key, "observers": watchers, "revision": getattr(updated, "revision", 1)},
            updated.id,
        )


# ── Зависимости ────────────────────────────────────────────────────────────


@telos_tool(
    name="telos_list_dependencies",
    title="Зависимости задач проекта",
    description=(
        "Возвращает связи «задача A должна закончиться до начала задачи B» в проекте; "
        "можно оставить только связи одной задачи."
    ),
    scope=SCOPE_TASKS_READ,
)
async def list_dependencies(
    context: Context,
    project_key: ProjectKey,
    task_key: Annotated[str | None, Field(description="Оставить только связи этой задачи.")] = None,
) -> list[dict]:
    """Возвращает граф зависимостей проекта с ключами задач."""
    async with tool_context(context) as tools:
        project_id = await resolve_project(tools, project_key)
        only = (await resolve_task(tools, task_key)).task_id if task_key else None
        try:
            items = await tools.services.dependencies.list_dependencies(project_id)
            if only is not None:
                items = [
                    item
                    for item in items
                    if only in (item.predecessor_task_id, item.successor_task_id)
                ]
            keys = await tools.services.query.get_task_keys(
                project_id=project_id,
                task_ids={item.predecessor_task_id for item in items}
                | {item.successor_task_id for item in items},
            )
        except ApplicationError as error:
            raise ToolError("Не удалось получить зависимости.") from error
        return [
            {
                "dependency_id": item.id,
                "predecessor": keys.get(item.predecessor_task_id),
                "successor": keys.get(item.successor_task_id),
                "type": item.dependency_type.value,
                "lag_days": item.lag_days,
            }
            for item in items
        ]


@telos_tool(
    name="telos_create_dependency",
    title="Связать задачи зависимостью",
    description=(
        "Создаёт связь «predecessor должна закончиться до начала successor» "
        "(FINISH_TO_START) с необязательным лагом в днях. Обе задачи — одного проекта; "
        "цикл сервис не допустит."
    ),
    scope=SCOPE_TASKS_WRITE,
)
async def create_dependency(
    context: Context,
    predecessor: Annotated[str, Field(description="Ключ задачи-предшественника.")],
    successor: Annotated[str, Field(description="Ключ задачи-последователя.")],
    lag_days: Annotated[int, Field(description="Лаг в днях.", ge=0, le=3650)] = 0,
    actor: Actor = None,
) -> dict:
    """Создаёт зависимость тем же сервисом, что REST."""
    async with tool_context(context) as tools:
        first = await resolve_task(tools, predecessor)
        second = await resolve_task(tools, successor)
        if first.project_id != second.project_id:
            raise ToolError("Связывать можно только задачи одного проекта.")
        try:
            created = await tools.services.dependencies.create_dependency(
                first.project_id,
                {
                    "predecessor_task_id": first.task_id,
                    "successor_task_id": second.task_id,
                    "lag_days": lag_days,
                },
            )
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось создать зависимость.")) from error
        return {
            "dependency_id": created.id,
            "predecessor": first.task_key,
            "successor": second.task_key,
            "lag_days": created.lag_days,
        }


@telos_tool(
    name="telos_delete_dependency",
    title="Удалить зависимость",
    description="Удаляет связь задач по dependency_id из telos_list_dependencies. Требует confirm=true.",
    scope=SCOPE_TASKS_WRITE,
)
async def delete_dependency(
    context: Context,
    project_key: ProjectKey,
    dependency_id: Annotated[int, Field(description="Идентификатор связи.", gt=0)],
    confirm: Annotated[bool, Field(description="Подтверждение удаления.")] = False,
    actor: Actor = None,
) -> dict:
    """Удаляет зависимость проекта."""
    if not confirm:
        raise ToolError("Удаление требует confirm=true.")
    async with tool_context(context) as tools:
        project_id = await resolve_project(tools, project_key)
        try:
            await tools.services.dependencies.delete_dependency(project_id, dependency_id)
        except ApplicationError as error:
            raise ToolError(domain_message(error, "Не удалось удалить зависимость.")) from error
        return {"dependency_id": dependency_id, "deleted": True}
