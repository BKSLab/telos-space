"""Совместимый проектный интерфейс над общей очередью диалогов Ариса."""

from src.services.tim_widget import AgentConversationConfig, ConversationMemory, TimWidgetService


class AgentConversationsService(TimWidgetService):
    """Проектные диалоги сохраняют свою привязку и прежние публичные методы."""


__all__ = ["AgentConversationConfig", "AgentConversationsService", "ConversationMemory"]
