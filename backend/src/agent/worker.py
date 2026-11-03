import asyncio
import logging
from collections.abc import Sequence
from typing import Protocol

from src.agent.wakeup import forget, listen

logger = logging.getLogger(__name__)


class TurnQueue(Protocol):
    """Постоянная очередь ответов Ариса: чат проекта или личный виджет."""

    async def process_next(self) -> bool:
        """Выполняет одно задание; False — очередь пуста."""
        ...


class AgentWorker:
    """Ограниченное число исполнителей постоянных очередей ответов Ариса."""

    def __init__(
        self, *, poll_seconds: float, concurrency: int, services: Sequence[TurnQueue] | None = None, service: TurnQueue | None = None
    ) -> None:
        self.services = list(services or ([service] if service is not None else []))
        if not self.services:
            raise ValueError("Нужна хотя бы одна очередь ответов агента.")
        self.poll_seconds = poll_seconds
        self.concurrency = concurrency

    async def run(self, stop_event: asyncio.Event) -> None:
        """Обрабатывает вопросы до остановки приложения.

        Args:
            stop_event: Сигнал завершения lifespan.
        """
        try:
            async with asyncio.TaskGroup() as group:
                for _ in range(self.concurrency):
                    group.create_task(self._consume(stop_event))
        finally:
            for service in self.services:
                drain = getattr(service, "drain", None)
                if drain is not None:
                    await drain()

    async def _consume(self, stop_event: asyncio.Event) -> None:
        wake = listen()
        try:
            await self._loop(stop_event, wake)
        finally:
            forget(wake)

    async def _loop(self, stop_event: asyncio.Event, wake: asyncio.Event) -> None:
        while not stop_event.is_set():
            # Сброс до опроса очереди: вопрос, поставленный во время опроса,
            # оставит сигнал взведённым, и ожидание ниже сразу закончится.
            wake.clear()
            worked = False
            # Каждая очередь получает шанс на каждом круге: занятый чат не душит виджет.
            for service in self.services:
                try:
                    worked = await service.process_next() or worked
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.error("❌ Очередь ответов Ариса временно недоступна.", exc_info=True)
            if worked:
                continue
            waiters = [
                asyncio.ensure_future(stop_event.wait()),
                asyncio.ensure_future(wake.wait()),
            ]
            try:
                await asyncio.wait(
                    waiters, timeout=self.poll_seconds, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for waiter in waiters:
                    waiter.cancel()
