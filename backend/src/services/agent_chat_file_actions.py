"""Чтение временных файлов через точный серверный контекст текущего хода."""

import asyncio
from time import monotonic

from src.agent.tools import AgentToolContext
from src.exceptions.agent_tools import AgentToolAccessError
from src.schemas.project_tool_inputs import ChatFileReadInput, ChatFilesInput
from src.services.agent_chat_files import AgentChatFilesService, ChatFileContext


def file_context(ctx: AgentToolContext) -> ChatFileContext:
    """Переносит доверенный якорь исполнения, без аргументов модели о чате."""
    if ctx.user_id is None:
        raise AgentToolAccessError("Для файла нужен текущий участник чата.")
    return ChatFileContext(
        user_id=ctx.user_id, project_id=ctx.project_id, message_id=ctx.message_id,
        chat_turn_id=ctx.chat_turn_id, run_id=ctx.run_id, principal=ctx.principal,
    )


async def list_chat_files(
    files: AgentChatFilesService, ctx: AgentToolContext, args: ChatFilesInput,
) -> dict:
    """Находит опубликованные файлы всей доступной истории этого диалога."""
    result = await files.list_files(context=file_context(ctx), **args.model_dump())
    return result.model_dump(mode="json")


async def read_chat_file(
    files: AgentChatFilesService, ctx: AgentToolContext, args: ChatFileReadInput,
) -> dict:
    """Читает страницу сохранённого текста либо ищет нужную часть файла."""
    result = await files.read_text(context=file_context(ctx), **args.model_dump())
    deadline = monotonic() + files.wait_seconds
    while result.status in {"pending", "processing"} and monotonic() < deadline:
        await asyncio.sleep(min(files.poll_seconds, max(0, deadline - monotonic())))
        result = await files.read_text(context=file_context(ctx), **args.model_dump())
    return result.model_dump(mode="json")


async def initial_chat_files(files: AgentChatFilesService, ctx: AgentToolContext) -> list[dict]:
    """Передаёт метаданные и ограниченные первые фрагменты, а не весь файл."""
    context = file_context(ctx)
    page = await files.list_files(context=context, limit=20)
    result = []
    remaining = 6000
    for item in page.items:
        value = item.model_dump(mode="json")
        value.pop("excerpt", None)
        if item.status in {"ready", "empty"} and remaining:
            text = await files.read_text(context=context, file_id=item.file_id, max_chars=min(2000, remaining))
            value.update(text.model_dump(mode="json", exclude={"excerpt"}))
            remaining -= len(text.text)
        result.append(value)
    return result
