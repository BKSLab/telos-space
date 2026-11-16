"""Инструменты истории используют доверенный контекст и короткие области reader-а."""

from src.agent.tools import AgentToolContext
from src.schemas.project_tool_inputs import ChatMessageInput, ChatMessagesInput
from src.services.agent_chat_file_actions import file_context
from src.services.agent_chat_history import AgentChatHistoryService
from src.services.context_results import HistoryReadResult


async def list_chat_messages(
    history: AgentChatHistoryService, ctx: AgentToolContext, args: ChatMessagesInput
) -> dict:
    """Передаёт фильтры общему reader-у; me разрешает сервис из доверенного контекста."""
    result = await history.list_messages(context=file_context(ctx), **args.model_dump())
    return HistoryReadResult(result.model_dump(mode="json"), guards=result._source_guards)


async def get_chat_message(
    history: AgentChatHistoryService, ctx: AgentToolContext, args: ChatMessageInput
) -> dict:
    """Читает оригинал, окружение и серверно проверяемый фрагмент."""
    return await history.get_message(context=file_context(ctx), **args.model_dump())
