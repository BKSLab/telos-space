"""ИСР, стадии, календарь, вехи и зависимости текущего проекта."""

from datetime import date

from src.agent.tools import AgentToolContext
from src.schemas import project_tool_inputs as inputs
from src.services.agent_action_helpers import checked_task, page
from src.services.agent_tool_scope import AgentProjectToolScope


async def list_stages(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.EmptyInput
) -> dict:
    """Возвращает стадии канбана с ID и признаком завершения."""
    return {
        "items": [
            row.model_dump(mode="json") for row in await db.stages.get_stage_list(ctx.project_id)
        ]
    }


async def create_stage(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.StageCreateInput
) -> dict:
    """Добавляет стадию в канбан проекта."""
    item = await db.stages.create_stage(ctx.project_id, args.model_dump())
    return {"stage": item.model_dump(mode="json"), "source_id": f"stage:{item.id}"}


async def update_stage(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.StageUpdateInput
) -> dict:
    """Меняет параметры и порядок стадии, проверяя принадлежность проекту."""
    await db.stages.get_stage_in_project(ctx.project_id, args.stage_id)
    item = await db.stages.update_stage(args.stage_id, args.changes.model_dump(exclude_unset=True))
    return {"stage": item.model_dump(mode="json"), "source_id": f"stage:{item.id}"}


async def delete_stage(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.StageInput
) -> dict:
    """Удаляет стадию по тем же правилам, что настройки канбана."""
    await db.stages.get_stage_in_project(ctx.project_id, args.stage_id)
    await db.stages.delete_stage(args.stage_id)
    return {"deleted": True, "stage_id": args.stage_id}


async def get_structure(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.EmptyInput
) -> dict:
    """Возвращает ИСР, размещение задач и нераспределённый пул."""
    return (await db.wbs.get_structure(ctx.project_id)).model_dump(mode="json")


async def create_wbs_node(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.WbsCreateInput
) -> dict:
    """Создаёт раздел ИСР с проверкой родительского проекта."""
    item = await db.wbs.create_node(ctx.project_id, args.title, args.parent_id)
    return {"node": item.model_dump(mode="json"), "source_id": f"wbs_node:{item.id}"}


async def update_wbs_node(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.WbsUpdateInput
) -> dict:
    """Переименовывает раздел ИСР."""
    item = await db.wbs.update_node(ctx.project_id, args.node_id, args.title)
    return {"node": item.model_dump(mode="json"), "source_id": f"wbs_node:{item.id}"}


async def move_wbs_node(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.WbsMoveInput
) -> dict:
    """Перемещает раздел ИСР с проверкой циклов и порядка."""
    item = await db.wbs.move_node(ctx.project_id, args.node_id, args.parent_id, args.before_id)
    return {"node": item.model_dump(mode="json"), "source_id": f"wbs_node:{item.id}"}


async def delete_wbs_node(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.WbsInput
) -> dict:
    """Удаляет раздел по существующим правилам сохранения его задач."""
    return (await db.wbs.delete_node(ctx.project_id, args.node_id)).model_dump(mode="json")


async def place_task(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskPlacementInput
) -> dict:
    """Распределяет задачу, возвращает в пул или изменяет её положение."""
    await checked_task(db, ctx, args.task_id)
    if args.before_task_id is not None:
        await checked_task(db, ctx, args.before_task_id)
    item = await db.wbs.place_task(ctx.project_id, **args.model_dump())
    return {"task": item.model_dump(mode="json"), "source_id": f"task:{item.id}"}


async def list_milestones(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.PageInput
) -> dict:
    """Возвращает страницу вех проекта."""
    return page(await db.milestones.list_milestones(ctx.project_id), args)


async def create_milestone(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.MilestoneCreateInput
) -> dict:
    """Создаёт веху с датой и необязательной привязкой к ИСР."""
    item = await db.milestones.create_milestone(ctx.project_id, args.model_dump())
    return {"milestone": item.model_dump(mode="json"), "source_id": f"milestone:{item.id}"}


async def update_milestone(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.MilestoneUpdateInput
) -> dict:
    """Меняет веху внутри проекта разговора."""
    item = await db.milestones.update_milestone(
        ctx.project_id, args.milestone_id, args.changes.model_dump(exclude_unset=True)
    )
    return {"milestone": item.model_dump(mode="json"), "source_id": f"milestone:{item.id}"}


async def delete_milestone(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.MilestoneInput
) -> dict:
    """Удаляет веху текущего проекта."""
    await db.milestones.delete_milestone(ctx.project_id, args.milestone_id)
    return {"deleted": True, "milestone_id": args.milestone_id}


async def list_dependencies(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.PageInput
) -> dict:
    """Возвращает зависимости задач проекта."""
    return page(await db.dependencies.list_dependencies(ctx.project_id), args)


async def create_dependency(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DependencyCreateInput
) -> dict:
    """Создаёт связь с проверкой проекта обеих задач и отсутствия цикла."""
    item = await db.dependencies.create_dependency(ctx.project_id, args.model_dump())
    return {"dependency": item.model_dump(mode="json")}


async def delete_dependency(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DependencyInput
) -> dict:
    """Удаляет зависимость выбранного проекта."""
    await db.dependencies.delete_dependency(ctx.project_id, args.dependency_id)
    return {"deleted": True, "dependency_id": args.dependency_id}


async def get_calendar(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.CalendarInput
) -> dict:
    """Читает календарь и рассчитанные backend сигналы риска."""
    return (
        await db.calendar.get_range(
            project_id=ctx.project_id,
            date_from=args.date_from,
            date_to=args.date_to,
            today=date.today(),
        )
    ).model_dump(mode="json")


async def preview_schedule(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.ScenarioPreviewInput
) -> dict:
    """Вычисляет последствия переноса, ничего не меняя."""
    return (await db.scenarios.preview(ctx.project_id, args.model_dump()["changes"])).model_dump(
        mode="json"
    )


async def apply_schedule(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.ScenarioApplyInput
) -> dict:
    """Атомарно применяет подтверждённые даты и версии задач из preview."""
    return (
        await db.scenarios.apply(
            ctx.project_id,
            args.model_dump()["changes"],
            date_change_reason=args.date_change_reason,
        )
    ).model_dump(mode="json")
