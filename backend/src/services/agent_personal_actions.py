"""Личные действия Ариса вне одного проекта: мои задачи, разовые задачи.

Работают только в личном виджете. Каждое действие проверяет доступ самим
пользователем — ровно так же, как обычные HTTP-эндпоинты этих объектов.
"""

from src.agent.tools import AgentToolContext
from src.exceptions.agent_tools import AgentToolAccessError
from src.schemas import project_tool_inputs as inputs
from src.services.agent_action_helpers import page
from src.services.agent_tool_scope import AgentProjectToolScope

# Сколько проектов обходит поиск задач: дальше — уточнить запрос или проект.
SEARCH_PROJECTS_LIMIT = 20
SEARCH_PER_PROJECT = 10


def _compact(item: dict) -> dict:
    item.pop("description_md", None)
    item.pop("checklist", None)
    return item


def _task_payload(task) -> dict:
    data = task.model_dump(mode="json")
    result = {"task": data}
    if task.project_id is not None:
        # source_id понимает только каталог проекта: карточку покажем в проектном режиме.
        result["source_id"] = f"task:{task.id}"
    return result


async def list_my_tasks(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.MyTasksInput
) -> dict:
    """Все задачи на пользователе: проектные и разовые."""
    rows = await db.tasks.get_my_tasks(
        user_id=ctx.user_id, role=args.role, include_closed=args.include_closed
    )
    if args.search:
        needle = args.search.strip().lower()
        rows = [
            row for row in rows if needle in row.title.lower() or needle in (row.key or "").lower()
        ]
    result = page(rows, args)
    result["items"] = [_compact(item) for item in result["items"]]
    return result


async def list_my_projects(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.PageInput
) -> dict:
    """Проекты, доступные пользователю, с его ролью."""
    rows = await db.projects.get_project_list(ctx.user_id)
    result = page(rows, args)
    result["items"] = [
        {
            **{key: item.get(key) for key in ("id", "key", "name", "status", "due_date")},
            "my_role": "OWNER" if item.get("owner_id") == ctx.user_id else "MEMBER",
            "is_member": True,
        }
        for item in result["items"]
    ]
    return result


async def search_tasks(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskSearchInput
) -> dict:
    """Поиск задач по всем доступным проектам плюс по моим задачам вне проектов."""
    projects = await db.projects.get_project_list(ctx.user_id)
    found = []
    for project in projects[:SEARCH_PROJECTS_LIMIT]:
        rows = await db.tasks.get_task_list(project.id, search=args.search)
        found.extend(
            {**_compact(row.model_dump(mode="json")), "project_key": project.key}
            for row in rows[:SEARCH_PER_PROJECT]
        )
    needle = args.search.strip().lower()
    for row in await db.tasks.get_my_tasks(user_id=ctx.user_id, include_closed=True):
        if row.project_id is None and (
            needle in row.title.lower() or needle in (row.key or "").lower()
        ):
            found.append(_compact(row.model_dump(mode="json")))
    end = args.offset + args.limit
    return {
        "items": found[args.offset : end],
        "offset": args.offset,
        "total": len(found),
        "next_offset": end if end < len(found) else None,
        "projects_searched": min(len(projects), SEARCH_PROJECTS_LIMIT),
        "projects_total": len(projects),
    }


async def get_any_task(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskRefInput
) -> dict:
    """Задача любого вида по ID или ключу — если пользователю она доступна."""
    task_id = args.task_id
    if task_id is None:
        task_id = await _resolve_key(db, ctx, args.task_key)
    await db.access.ensure_task_access(task_id=task_id, user_id=ctx.user_id)
    return _task_payload(await db.tasks.get_task(task_id))


async def _resolve_key(db: AgentProjectToolScope, ctx: AgentToolContext, key: str) -> int:
    prefix, _, number = key.strip().upper().rpartition("-")
    if not prefix or not number.isdigit():
        raise AgentToolAccessError("Ключ задачи должен иметь вид PROJ-142.")
    project = await db.project_rows.get_by_key(prefix)
    if project is not None:
        await db.access.ensure_project_access(project_id=project.id, user_id=ctx.user_id)
        task = await db.task_rows.get_by_project_number(project.id, int(number))
        if task is not None:
            return task.id
    # Разовые задачи ищем среди своих: чужие по ключу не раскрываем.
    for row in await db.tasks.get_my_tasks(user_id=ctx.user_id, include_closed=True):
        if (row.key or "").upper() == key.strip().upper():
            return row.id
    raise AgentToolAccessError("Задача не найдена или недоступна.")


async def create_personal_task(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.PersonalTaskCreateInput
) -> dict:
    """Разовая задача без проекта; постановщик — пользователь."""
    task = await db.tasks.create_personal_task(
        data=args.model_dump(exclude_unset=True),
        created_by_user_id=ctx.user_id,
    )
    return _task_payload(task)








async def set_task_status(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskStatusInput
) -> dict:
    """Статус задачи разовой; проектная задача меняет стадию, а не статус."""
    grant = await db.access.ensure_task_access(task_id=args.task_id, user_id=ctx.user_id)
    if grant.project_id is not None:
        raise AgentToolAccessError(
            "Это проектная задача: её стадию меняет telos_move_task в проекте через telos_run_project_action."
        )
    task = await db.tasks.change_status(task_id=args.task_id, status=args.status)
    return _task_payload(task)
