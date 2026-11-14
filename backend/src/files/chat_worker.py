"""Подготовка приватных черновиков независимо от очереди ответов Ариса."""

import asyncio
import logging

from src.agent.worker import AgentWorker
from src.exceptions.base import ServiceError
from src.services.agent_chat_files import AgentChatFilesService

logger = logging.getLogger(__name__)


class ChatFilePreparationWorker:
    """Ограничивает параллелизм подготовки и периодически удаляет брошенные черновики."""

    def __init__(
        self, *, files: AgentChatFilesService, poll_seconds: float, concurrency: int,
        cleanup_seconds: float, retention_hours: int,
    ) -> None:
        self.files = files
        self.queue = AgentWorker(service=files, poll_seconds=poll_seconds, concurrency=concurrency)
        self.cleanup_seconds = cleanup_seconds
        self.retention_hours = retention_hours

    async def run(self, stop: asyncio.Event) -> None:
        """Обрабатывает durable очередь и TTL до остановки backend."""
        async with asyncio.TaskGroup() as group:
            group.create_task(self.queue.run(stop))
            group.create_task(self._cleanup(stop))

    async def _cleanup(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.files.cleanup_agent_drafts(retention_hours=self.retention_hours)
            except ServiceError:
                logger.warning("⚠️ Очистка черновиков файлов отложена.", exc_info=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.cleanup_seconds)
            except TimeoutError:
                pass
