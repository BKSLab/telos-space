"""Инструменты задач, назначений, чек-листов и обсуждения."""

from datetime import date
from typing import Any

from src.agent.tools import AgentToolContext
from src.exceptions.agent_tools import AgentToolArgumentsError, AgentToolConflictError
from src.exceptions.projects import ProjectNotFoundError
from src.schemas import project_tool_inputs as inputs
from src.services.agent_action_helpers import checked_comment, checked_task, page
from src.services.agent_tool_scope import AgentProjectToolScope
from src.services.project_query import StageRefDto, resolve_stage_reference
from src.services.tasks import build_task_key
from src.utils.checklists import checklist_context

# Те же срезы текста, что видела модель в контексте легаси-планировщика Ариса.
TEXT_FRAGMENT_LIMIT = 1200


async def list_tasks(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskListInput
) -> dict:
    """Возвращает ограниченный список задач текущего проекта."""
    if args.stage_id is not None:
        await db.stages.get_stage_in_project(ctx.project_id, args.stage_id)
    rows = await db.tasks.get_task_list(ctx.project_id, stage_id=args.stage_id, search=args.search)
    result = page(rows, args)
    stages = {row.id: row for row in await db.stages.get_stage_list(ctx.project_id)}
    result["items"] = [_list_item(item, stages) for item in result["items"]]
    return result


#: Поля строки списка, которые пусты у проектной задачи чаще, чем заполнены:
#: без значения их не показываем (полная карточка — telos_get_task).
_OPTIONAL_LIST_FIELDS = (
    "status",
    "start_date",
    "baseline_start_date",
    "baseline_due_date",
    "completed_at",
    "search_match_source",
    "search_title",
    "search_excerpt",
)


def _list_item(item: dict[str, Any], stages: dict[int, Any]) -> dict[str, Any]:
    """Строка списка задач: то, по чему выбирают задачу, без служебного шума.

    Полный TaskSchema — ~800 символов на задачу; 22 задачи не влезали в бюджет
    результата, список обрезался, и модель путалась в страницах (замер 30.09.2026).
    Описание, чек-лист, позиция, ревизии и вложенные карточки пользователей —
    в telos_get_task; исполнитель и срок остаются и при пустом значении («не назначен»,
    «без срока» — тоже ответ).
    """
    stage = stages.get(item.get("stage_id"))
    row = {
        "id": item["id"],
        "key": item["key"],
        "title": item["title"],
        "stage_id": item.get("stage_id"),
        "stage": stage.name if stage is not None else None,
        "is_done": bool(stage and stage.is_done_stage),
        "priority": item.get("priority"),
        "assignee": item.get("assignee"),
        "due_date": item.get("due_date"),
    }
    for key in _OPTIONAL_LIST_FIELDS:
        if item.get(key) not in (None, ""):
            row[key] = item[key]
    if item.get("comments_count"):
        row["comments_count"] = item["comments_count"]
    participants = [
        {"role": person.get("role"), "name": " ".join(filter(None, [(person.get("user") or {}).get("last_name"), (person.get("user") or {}).get("first_name")])) or (person.get("user") or {}).get("username")}
        for person in item.get("participants") or []
    ]
    if participants:
        row["participants"] = participants
    row["updated_at"] = item.get("updated_at")
    return row


async def get_task(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskLookupInput
) -> dict:
    """Читает задачу по ID или ключу: назначения, ревизия чек-листа и название стадии.

    Ключ и название стадии экономят модели два шага (поиск ID через telos_list_tasks и
    стадии через telos_list_stages) на самом частом вопросе «что с задачей KEY-12».
    """
    task_id = args.task_id or await _task_id_by_key(db, ctx, args.task_key or "")
    task = await checked_task(db, ctx, task_id)
    result = {"task": task.model_dump(mode="json"), "source_id": f"task:{task.id}"}
    stage = next(
        (row for row in await db.stages.get_stage_list(ctx.project_id) if row.id == task.stage_id),
        None,
    )
    if stage is not None:
        result["stage"] = {"id": stage.id, "name": stage.name, "is_done_stage": stage.is_done_stage}
    return result


async def _task_id_by_key(db: AgentProjectToolScope, ctx: AgentToolContext, key: str) -> int:
    """ID задачи текущего проекта по ключу KEY-12; чужой ключ — ошибка аргументов."""
    prefix, _, number = key.rpartition("-")
    project = await db.projects.get_project(ctx.project_id)
    if prefix.upper() != project.key.upper():
        raise AgentToolArgumentsError(
            f"Задача {key} не из текущего проекта {project.key}: ключ должен начинаться с "
            f"{project.key}-."
        )
    task = await db.task_rows.get_by_project_number(ctx.project_id, int(number))
    if task is None:
        raise AgentToolArgumentsError(f"Задачи {key} в проекте нет.")
    return task.id


async def create_task(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskCreateInput
) -> dict:
    """Создаёт задачу через обычный сценарий нумерации и ролевых назначений."""
    # Имена разрешаются после проверки квитанции в AgentActionsService:
    # переименование стадии или удаление участника не ломает повтор исходного запроса.
    payload = args.model_dump(
        exclude_unset=True, exclude={"stage_name", "executor_username", "observer_usernames"},
    )
    if args.stage_name is not None:
        stages = await db.stages.get_stage_list(ctx.project_id)
        payload["stage_id"] = resolve_stage_reference(
            [StageRefDto(stage.id, stage.name, stage.is_done_stage) for stage in stages],
            args.stage_name,
        ).stage_id
    if args.executor_username is not None:
        username = args.executor_username.strip()
        payload["executor_id"] = (
            await db.members.resolve_member_user_id(project_id=ctx.project_id, username=username)
            if username else None
        )
    if args.observer_usernames is not None:
        payload["observer_ids"] = [
            await db.members.resolve_member_user_id(project_id=ctx.project_id, username=username)
            for username in args.observer_usernames
        ]
    task = await db.tasks.create_task(
        ctx.project_id,
        payload,
        created_by_user_id=ctx.user_id,
    )
    return {"task": task.model_dump(mode="json"), "source_id": f"task:{task.id}"}


async def update_task(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskUpdateInput
) -> dict:
    """Меняет только переданные поля, сохраняя авторство действия."""
    await checked_task(db, ctx, args.task_id)
    task = await db.tasks.update_task(
        args.task_id, args.changes.model_dump(exclude_unset=True), updated_by_user_id=ctx.user_id
    )
    return {"task": task.model_dump(mode="json"), "source_id": f"task:{task.id}"}


async def move_task(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskMoveInput
) -> dict:
    """Меняет стадию и положение задачи с обычной историей переходов."""
    await checked_task(db, ctx, args.task_id)
    task = await db.tasks.move_task(args.task_id, args.stage_id, args.position)
    return {"task": task.model_dump(mode="json"), "source_id": f"task:{task.id}"}


async def delete_task(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskDeleteInput
) -> dict:
    """Удаляет только ту версию задачи, которую прочитал инициатор."""
    task = await checked_task(db, ctx, args.task_id)
    if task.updated_at != args.expected_updated_at:
        raise AgentToolConflictError("После подготовки удаления задача изменилась.")
    await db.tasks.delete_task(task.id, expected_updated_at=args.expected_updated_at)
    return {"deleted": True, "task_key": task.key, "title": task.title}


async def set_checklist(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.ChecklistInput
) -> dict:
    """Создаёт, изменяет или удаляет чек-лист по его текущей ревизии."""
    await checked_task(db, ctx, args.task_id)
    task = await db.tasks.update_task(
        args.task_id, args.model_dump(exclude={"task_id"}), updated_by_user_id=ctx.user_id
    )
    return {
        "task_key": task.key,
        "checklist": task.checklist.model_dump(mode="json") if task.checklist else None,
        "checklist_revision": task.checklist_revision,
        "source_id": f"task:{task.id}",
    }


async def fix_baseline(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskInput
) -> dict:
    """Фиксирует текущие даты как baseline отдельной явной операцией."""
    await checked_task(db, ctx, args.task_id)
    return {
        "task": (await db.tasks.fix_baseline(args.task_id)).model_dump(mode="json"),
        "source_id": f"task:{args.task_id}",
    }


async def list_comments(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskPageInput
) -> dict:
    """Возвращает комментарии доступной задачи."""
    await checked_task(db, ctx, args.task_id)
    return page(await db.comments.get_comments(args.task_id), args)


async def add_comment(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.CommentCreateInput
) -> dict:
    """Добавляет комментарий от имени пользователя, который ведёт разговор с агентом."""
    await checked_task(db, ctx, args.task_id)
    item = await db.comments.add_comment(task_id=args.task_id, author_id=ctx.user_id, body_md=args.body_md)
    return {"comment": item.model_dump(mode="json"), "source_id": f"comment:{item.id}"}


async def update_comment(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.CommentUpdateInput
) -> dict:
    """Редактирует прочитанную версию комментария без смены автора."""
    await checked_comment(db, ctx, args.comment_id)
    item = await db.comments.update_comment(args.comment_id, args.body_md, args.expected_body_md)
    return {"comment": item.model_dump(mode="json"), "source_id": f"comment:{item.id}"}


async def delete_comment(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.CommentInput
) -> dict:
    """Удаляет комментарий из проекта разговора."""
    await checked_comment(db, ctx, args.comment_id)
    await db.comments.delete_comment(args.comment_id)
    return {"deleted": True, "comment_id": args.comment_id}






async def _project_key(db: AgentProjectToolScope, project_id: int) -> str:
    project = await db.project_rows.get_by_id(project_id)
    if project is None:
        raise ProjectNotFoundError(project_id=project_id)
    return project.key


def _task_brief(task: Any, project_key: str, stage_by_id: dict[int, Any]) -> dict:
    """Краткая карточка задачи для сводок: ключ, стадия по имени, сроки и source_id."""
    stage = stage_by_id.get(task.stage_id)
    return {
        "task_id": task.id,
        "task_key": build_task_key(project_key, task.number),
        "title": task.title[:512],
        "description": (task.description_md or "")[:TEXT_FRAGMENT_LIMIT],
        "checklist": checklist_context(getattr(task, "checklist", None), limit=10, chars=200),
        "stage": stage.name[:255] if stage is not None else None,
        "is_done": bool(stage and stage.is_done_stage),
        "priority": task.priority.value,
        "role": task.role.value if task.role else None,
        "assignee": task.assignee,
        "due_date": str(task.due_date) if task.due_date else None,
        "source_id": f"task:{task.id}",
    }


async def get_task_statistics(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.EmptyInput
) -> dict:
    """Разбивка задач проекта по стадиям, приоритетам и исполнителям одним агрегатом."""
    today = date.today()
    statistics = await db.task_rows.get_project_statistics(project_id=ctx.project_id, today=today)
    by_stage: dict[str, int] = {}
    for stage in await db.stages.get_stage_list(ctx.project_id):
        count = statistics.by_stage.get(stage.id)
        if count:
            # Одноимённые стадии не затирают друг друга: счётчики складываются.
            by_stage[stage.name] = by_stage.get(stage.name, 0) + count
    return {
        "today": today.isoformat(),
        "total": statistics.total,
        "overdue": statistics.overdue,
        "by_stage": by_stage,
        "by_priority": statistics.by_priority,
        "by_assignee": statistics.by_assignee,
        "source_id": f"project:{ctx.project_id}",
    }


async def list_overdue_tasks(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.OverdueTasksInput
) -> dict:
    """Незавершённые задачи со сроком раньше сегодняшнего, самые давние первыми."""
    today = date.today()
    rows = await db.task_rows.get_overdue_limited(
        project_id=ctx.project_id, today=today, limit=args.limit + 1
    )
    stage_by_id = {stage.id: stage for stage in await db.stages.get_stage_list(ctx.project_id)}
    project_key = await _project_key(db, ctx.project_id)
    return {
        "today": today.isoformat(),
        "items": [_task_brief(task, project_key, stage_by_id) for task in rows[: args.limit]],
        "truncated": len(rows) > args.limit,
    }


async def list_recent_activity(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.RecentActivityInput
) -> dict:
    """Последние события истории задач проекта с ключами и названиями задач."""
    events = await db.activity_rows.get_recent_by_project(ctx.project_id, limit=args.limit)
    tasks_by_id = {
        task.id: task for task in await db.task_rows.get_by_ids({item.task_id for item in events})
    }
    project_key = await _project_key(db, ctx.project_id)
    items = []
    for item in events:
        task = tasks_by_id.get(item.task_id)
        if task is None:
            continue
        items.append(
            {
                "task_id": task.id,
                "task_key": build_task_key(project_key, task.number),
                "title": task.title[:512],
                "created_at": item.created_at.isoformat(),
                "event_type": item.event_type.value,
                "from_value": (item.from_value or "")[:TEXT_FRAGMENT_LIMIT],
                "to_value": (item.to_value or "")[:TEXT_FRAGMENT_LIMIT],
                "actor": item.actor_name,
                "source_id": f"task:{task.id}",
            }
        )
    return {"items": items}
