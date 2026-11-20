"""Паспорт, команда, доски, стикеры и реестр рисков проекта."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.agent.tools import AgentToolContext
from src.exceptions.agent_tools import AgentToolAccessError, AgentToolConflictError
from src.schemas import project_tool_inputs as inputs
from src.schemas.project_history import ProjectHistoryQuery
from src.schemas.project_risks import ProjectRiskFilters
from src.schemas.projects import ProjectSchema
from src.schemas.task_formulation import TaskFormulationInput
from src.services.agent_action_helpers import page
from src.services.agent_tool_scope import AgentProjectToolScope
from src.services.task_formulation import prepare_task_formulation


async def list_project_history(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.ProjectHistoryInput
) -> dict:
    """Читает общий журнал; следующие страницы фиксируют вычисленные местные даты."""
    timezone = args.timezone if "timezone" in args.model_fields_set else ctx.timezone
    payload = args.model_dump(exclude={"period"})
    payload["timezone"] = timezone
    if args.since is None:
        today = datetime.now(ZoneInfo(timezone)).date()
        period = args.period or "last_7_days"
        until = today - timedelta(days=1) if period == "yesterday" else today
        payload["since"] = until - timedelta(days=6) if period == "last_7_days" else until
        payload["until"] = until
    filters = ProjectHistoryQuery.model_validate(payload)
    page = await db.project_history.read(ctx.project_id, filters)
    arguments = filters.model_dump(mode="json", exclude_none=True)
    next_arguments = None
    if page.next_offset is not None:
        next_arguments = {**arguments, "offset": page.next_offset}
    return {
        **page.model_dump(mode="json"),
        "project_id": ctx.project_id,
        "offset": filters.offset,
        "arguments": arguments,
        "period": {
            "since": filters.since.isoformat(),
            "until": filters.until.isoformat(),
            "timezone": filters.timezone,
        },
        "has_more": page.next_offset is not None,
        "next_arguments": next_arguments,
    }


async def prepare_task(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: TaskFormulationInput
) -> dict:
    """Возвращает предложение; хранилище задач и write-сервисы не вызываются."""
    return prepare_task_formulation(args)


def _compact_project(project: ProjectSchema) -> dict:
    """Сохраняет метаданные и bounded context, полный паспорт читает source-reader."""
    result = project.model_dump(mode="json", exclude={"description_md", "description_sections"})
    result["context_read"] = {
        "tool": "telos_read_source",
        "arguments": {"source_id": f"project:{project.id}"},
    }
    return result


async def get_project(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.EmptyInput
) -> dict:
    """Читает паспорт, статистику и историю сроков проекта."""
    return {
        "project": _compact_project(await db.projects.get_project(ctx.project_id)),
        "statistics": (await db.projects.get_project_stats(ctx.project_id)).model_dump(mode="json"),
        "deadline_history": [
            item.model_dump(mode="json")
            for item in await db.projects.get_deadline_history(ctx.project_id)
        ],
        "source_id": f"project:{ctx.project_id}",
    }


async def get_latest_project_summary(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.EmptyInput
) -> dict:
    """Читает полный сохранённый анализ текущего проекта без новой генерации.

    Args:
        db: Явные зависимости инструмента проекта.
        ctx: Проект и пользователь, определённые сервером.
        args: Пустые аргументы без возможности выбрать чужую область анализа.

    Returns:
        Сохранённый анализ с датой и происхождением либо явное отсутствие свода.

    Raises:
        AgentToolConflictError: Если серверный контекст не содержит проект.
        AgentToolAccessError: Если пользователь не аутентифицирован.
        ProjectNotFoundError: Если актуальное членство больше не даёт доступ.
        AnalyticsServiceError: Если прочитать сохранённый анализ не удалось.
    """
    if ctx.project_id is None:
        raise AgentToolConflictError("Действию нужен проект.")
    if ctx.user_id is None:
        raise AgentToolAccessError("Инструменту необходим аутентифицированный участник.")
    report = await db.analytics_reader.get_latest(user_id=ctx.user_id, project_id=ctx.project_id)
    if report is None:
        return {
            "found": False,
            "report": None,
            "source_id": None,
            "provenance": "generated_analysis",
            "message": "Сохранённой сводки проекта нет: анализ ещё не формировали.",
        }
    return {
        "found": True,
        "report": report.model_dump(mode="json"),
        "source_id": f"analytics_report:{report.id}",
        "provenance": "generated_analysis",
        "message": (
            f"Сохранённый анализ на дату {report.created_at.isoformat()}. "
            "Он отражает состояние проекта на момент формирования; "
            "текущее состояние могло измениться."
        ),
    }


async def update_project(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.ProjectChangesInput
) -> dict:
    """Обновляет паспорт и настройки с проверкой причин переноса срока."""
    await db.access.ensure_project_ownership(project_id=ctx.project_id, user_id=ctx.user_id)
    item = await db.projects.update_project(
        ctx.project_id, args.model_dump(exclude_unset=True), updated_by_user_id=ctx.user_id
    )
    return {"project": _compact_project(item), "source_id": f"project:{ctx.project_id}"}


async def list_members(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.PageInput
) -> dict:
    """Читает публичные карточки участников без личных контактов."""
    result = page(await db.members.get_member_list(ctx.project_id), args)
    for item in result["items"]:
        item["source_id"] = f"member:{item['id']}"
    return result


async def add_member(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.MemberAddInput
) -> dict:
    """Владелец добавляет существующего пользователя по точному логину."""
    await db.access.ensure_project_ownership(project_id=ctx.project_id, user_id=ctx.user_id)
    item = await db.members.add_member(ctx.project_id, args.username)
    return {"member": item.model_dump(mode="json"), "source_id": f"member:{item.id}"}


async def remove_member(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.MemberInput
) -> dict:
    """Владелец удаляет участника с очисткой назначений, сохраняя себя."""
    await db.access.ensure_project_ownership(project_id=ctx.project_id, user_id=ctx.user_id)
    await db.members.remove_member(ctx.project_id, args.user_id)
    return {"removed": True, "user_id": args.user_id}


async def transfer_ownership(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.MemberInput
) -> dict:
    """Передаёт роль владельца другому участнику после отдельного решения."""
    await db.access.ensure_project_ownership(project_id=ctx.project_id, user_id=ctx.user_id)
    item = await db.projects.transfer_ownership(ctx.project_id, args.user_id, ctx.user_id)
    return {"project": item.model_dump(mode="json"), "source_id": f"project:{ctx.project_id}"}


async def list_boards(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.EmptyInput
) -> dict:
    """Возвращает доски проекта в порядке вкладок."""
    items = await db.boards.list_boards(ctx.project_id, user_id=ctx.user_id)
    return {"items": [item.model_dump(mode="json") for item in items], "total": len(items)}


async def list_stickers(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.StickerListInput
) -> dict:
    """Возвращает стикеры доски вместе с ревизиями, координатами и связями."""
    items = await db.stickers.list_stickers(ctx.project_id, args.board_id, user_id=ctx.user_id)
    return page(items, args)


async def create_sticker(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.StickerCreateInput
) -> dict:
    """Создаёт стикер с реальным авторством участника."""
    item = await db.stickers.create_sticker(
        project_id=ctx.project_id,
        board_id=args.board_id,
        data=args,
        author_id=ctx.user_id,
        author_username=ctx.username,
        author_display_name=ctx.display_name,
    )
    return {"sticker": item.model_dump(mode="json"), "source_id": f"sticker:{item.id}"}


async def update_sticker(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.StickerUpdateInput
) -> dict:
    """Меняет стикер с проверкой ревизии."""
    item = await db.stickers.update_sticker(
        project_id=ctx.project_id,
        board_id=args.board_id,
        sticker_id=args.sticker_id,
        data=args.changes,
    )
    return {"sticker": item.model_dump(mode="json"), "source_id": f"sticker:{item.id}"}


async def move_sticker(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.StickerMoveInput
) -> dict:
    """Меняет координаты, размер и слой стикера."""
    item = await db.stickers.move_sticker(
        project_id=ctx.project_id,
        board_id=args.board_id,
        sticker_id=args.sticker_id,
        data=args.position,
    )
    return {"sticker": item.model_dump(mode="json"), "source_id": f"sticker:{item.id}"}


async def delete_sticker(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.StickerDeleteInput
) -> dict:
    """Удаляет прочитанную ревизию стикера."""
    await db.stickers.delete_sticker(
        project_id=ctx.project_id,
        board_id=args.board_id,
        sticker_id=args.sticker_id,
        revision=args.revision,
    )
    return {"deleted": True, "sticker_id": args.sticker_id}


async def list_risks(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.RiskListInput
) -> dict:
    """Возвращает постраничный реестр зарегистрированных рисков."""
    return (
        await db.risks.list_risks(
            project_id=ctx.project_id,
            user_id=ctx.user_id,
            filters=ProjectRiskFilters.model_validate(
                args.model_dump(exclude={"page", "page_size"})
            ),
            page=args.page,
            page_size=args.page_size,
        )
    ).model_dump(mode="json")


async def get_risk(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.RiskInput
) -> dict:
    """Возвращает риск, его владельца, оценки, планы и связи."""
    item = await db.risks.get_risk(
        project_id=ctx.project_id, risk_id=args.risk_id, user_id=ctx.user_id
    )
    return {"risk": item.model_dump(mode="json"), "source_id": f"risk:{item.id}"}


async def create_risk(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.RiskCreateInput
) -> dict:
    """Создаёт риск с доменной проверкой участника и связанной задачи."""
    item = await db.risks.create_risk(project_id=ctx.project_id, user_id=ctx.user_id, data=args)
    return {"risk": item.model_dump(mode="json"), "source_id": f"risk:{item.id}"}


async def update_risk(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.RiskUpdateInput
) -> dict:
    """Редактирует риск или переводит его в CLOSED через обычную модель статусов."""
    item = await db.risks.update_risk(
        project_id=ctx.project_id, user_id=ctx.user_id, risk_id=args.risk_id, data=args.changes
    )
    return {"risk": item.model_dump(mode="json"), "source_id": f"risk:{item.id}"}


async def delete_risk(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.RiskInput
) -> dict:
    """Удаляет риск из проекта после подтверждения."""
    await db.risks.delete_risk(project_id=ctx.project_id, user_id=ctx.user_id, risk_id=args.risk_id)
    return {"deleted": True, "risk_id": args.risk_id}
