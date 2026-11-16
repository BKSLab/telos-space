"""Общие проверки проектной области для явно зарегистрированных действий."""

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from src.agent.tools import AgentToolContext
from src.exceptions.agent_tools import AgentToolAccessError
from src.schemas.project_tool_inputs import PageInput
from src.schemas.tasks import TaskSchema
from src.services.agent_tool_scope import AgentProjectToolScope


def task_model_payload(task, *, project_bound: bool) -> dict:
    """Детали других источников читаются отдельно с их собственными identity/proofs."""
    data = task.model_dump(mode="json", exclude={"report", "last_comment", "comments_count"})
    reads = {"report": {"loaded": False}, "comments": {"loaded": False}}
    if task.project_id is not None:
        for key, name in (("report", "telos_get_task_report"), ("comments", "telos_list_comments")):
            arguments = {"task_id": task.id}
            reads[key]["read"] = (
                {"tool": name, "arguments": arguments}
                if project_bound
                else {
                    "tool": "telos_run_project_action",
                    "arguments": {
                        "project_key": task.key.rpartition("-")[0],
                        "name": name,
                        "arguments": arguments,
                    },
                }
            )
    return {"task": data, "source_reads": reads, "source_id": f"task:{task.id}"}


def page(items: Sequence[BaseModel], args: PageInput) -> dict[str, Any]:
    """Ограничивает публичные результаты списка с курсором продолжения."""
    end = args.offset + args.limit
    return {
        "items": [item.model_dump(mode="json") for item in items[args.offset : end]],
        "offset": args.offset,
        "total": len(items),
        "next_offset": end if end < len(items) else None,
    }


def task_document_listing(task_id: int) -> dict[str, Any]:
    """Отдельное чтение каталога: отсутствие вложений в TaskSchema не означает пустой список."""
    return {
        "loaded": False,
        "tool": "telos_list_documents",
        "arguments": {"task_id": task_id, "scope": "all"},
    }


async def checked_task(
    db: AgentProjectToolScope, ctx: AgentToolContext, task_id: int
) -> TaskSchema:
    """Проверяет текущий проект, даже если пользователь имеет доступ к чужому."""
    grant = await db.access.ensure_task_access(task_id=task_id, user_id=ctx.user_id)
    if grant.project_id != ctx.project_id:
        raise AgentToolAccessError("Задача находится вне проекта разговора.")
    return await db.tasks.get_task(task_id)


async def checked_comment(
    db: AgentProjectToolScope, ctx: AgentToolContext, comment_id: int
) -> None:
    """Ограничивает комментарий проектом разговора."""
    grant = await db.access.ensure_comment_access(comment_id=comment_id, user_id=ctx.user_id)
    if grant.project_id != ctx.project_id:
        raise AgentToolAccessError("Комментарий находится вне проекта разговора.")
