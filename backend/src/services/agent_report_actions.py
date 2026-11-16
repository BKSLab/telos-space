"""Отчёты — самостоятельные результаты задач, а не комментарии."""

from src.agent.tools import AgentToolContext
from src.db.models.api_tokens import ApiTokenScope
from src.exceptions.agent_tools import AgentToolConflictError
from src.schemas import project_tool_inputs as inputs
from src.schemas.task_reports import TaskReportSchema
from src.services.agent_action_helpers import checked_task
from src.services.agent_task_actions import _task_id_by_key
from src.services.agent_tool_scope import AgentProjectToolScope
from src.services.auth import Principal


def _principal(ctx: AgentToolContext) -> Principal:
    """Личность задаёт сервер; модель не выбирает автора или права."""
    return Principal(
        user_id=ctx.user_id,
        username=ctx.username,
        last_name=ctx.display_name,
        first_name="",
        middle_name=None,
        scope=ApiTokenScope.WRITE if ctx.can_write else ApiTokenScope.READ,
        via_api_token=False,
    )


def _reports(db: AgentProjectToolScope):
    if db.reports is None:
        raise AgentToolConflictError("Чтение отчётов не настроено.")
    return db.reports


def _source_id(report: TaskReportSchema) -> str | None:
    """Только опубликованная версия с текстом, без подмены ID задачей."""
    return f"task_report:{report.id}" if report.id is not None and report.body_md.strip() else None


async def get_task_report(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskReportReadInput
) -> dict:
    """Читает текущий текст; next_offset гарантирует возможность дочитать хвост."""
    task_id = args.task_id or await _task_id_by_key(db, ctx, args.task_key or "")
    task = await checked_task(db, ctx, task_id)
    report = await _reports(db).get(task_id, _principal(ctx))
    end = min(len(report.body_md), args.offset + args.max_chars)
    return {
        "task_key": task.key,
        "task_title": task.title,
        "source_id": _source_id(report),
        "report": report.model_dump(mode="json", exclude={"body_md"}),
        "text": report.body_md[args.offset : end],
        "offset": args.offset,
        "total_chars": len(report.body_md),
        "next_offset": end if end < len(report.body_md) else None,
    }


async def list_task_reports(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.PageInput
) -> dict:
    """Полный реестр с точным числом задач без отчётов; поиск не заменяет перечень."""
    page = await _reports(db).list_project(
        ctx.project_id, _principal(ctx), offset=args.offset, limit=args.limit
    )
    result = page.model_dump(mode="json")
    for item in result["items"]:
        body = item["report"].pop("body_md")
        item["source_id"] = (
            f"task_report:{item['report']['id']}"
            if item["report"]["id"] is not None and body.strip()
            else None
        )
        item["preview"] = body[:400]
        item["total_chars"] = len(body)
        item["read_with"] = "telos_get_task_report"
    return result


async def publish_task_report(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskReportPublishInput
) -> dict:
    """Публикует через общий сервис, сохраняя авторство, версии и outbox."""
    await checked_task(db, ctx, args.task_id)
    saved = await _reports(db).publish(
        args.task_id, _principal(ctx), args.expected_revision, args.body_md
    )
    return {
        "report": saved.model_dump(mode="json"),
        "source_id": _source_id(saved),
    }
