"""Сборка scoped-истории без зависимости от внешних клиентов и OCR."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.repositories.agent_chat_history import AgentChatHistoryRepository
from src.services.agent_chat_files import AgentChatFilesService
from src.services.agent_chat_history import (
    AgentChatHistoryService,
    ChatHistoryScope,
    ChatHistoryScopeFactory,
)
from src.services.context_evidence import ContextEvidenceService


def build_agent_chat_history_scope(
    *, session_factory: async_sessionmaker[AsyncSession]
) -> ChatHistoryScopeFactory:
    """Одна короткая сессия на набор чтений, без владения request/worker-транзакцией."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[ChatHistoryScope]:
        async with session_factory() as session:
            yield ChatHistoryScope(history=AgentChatHistoryRepository(session))

    return scope


def build_agent_chat_history_service(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    chat_files: AgentChatFilesService,
    evidence: ContextEvidenceService | None = None,
) -> AgentChatHistoryService:
    """Права исходят из того же chat_files, который обслуживает текущий runtime."""
    return AgentChatHistoryService(
        files=chat_files,
        scope=build_agent_chat_history_scope(session_factory=session_factory),
        evidence=evidence,
    )
