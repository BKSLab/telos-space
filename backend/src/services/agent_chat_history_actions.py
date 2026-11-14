"""Инструменты истории используют доверенный контекст и короткие области reader-а."""

from src.agent.tools import AgentToolContext
from src.schemas.project_tool_inputs import ChatMessageInput, ChatMessagesInput
from src.services.agent_chat_file_actions import file_context
from src.services.agent_chat_history import AgentChatHistoryService


async def list_chat_messages(history: AgentChatHistoryService, ctx: AgentToolContext, args: ChatMessagesInput) -> dict:
    """Возвращает страницу сообщений с авторами, временем и собственными вложениями."""
    result = await history.list_messages(context=file_context(ctx), **args.model_dump())
    return result.model_dump(mode="json")


async def get_chat_message(history: AgentChatHistoryService, ctx: AgentToolContext, args: ChatMessageInput) -> dict:
    """Читает оригинал, окружение и серверно проверяемый фрагмент."""
    return await history.get_message(context=file_context(ctx), **args.model_dump())
