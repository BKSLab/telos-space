"""Worker извлечения оригиналов; embeddings/Qdrant ему не нужны."""

import asyncio
import logging

from src.services.file_processing import ClaimedFile, FileProcessingService

logger = logging.getLogger(__name__)


class FileProcessingWorker:
    """Обслуживает durable очередь с продлением lease во время vision."""

    def __init__(self, *, service: FileProcessingService) -> None:
        self.service = service

    async def run(self, stop_event: asyncio.Event) -> None:
        """Обрабатывает задания до остановки, оставляя прерванный lease для recovery."""
        while not stop_event.is_set():
            processed = False
            try:
                processed = await self.process_next()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("❌ Ошибка независимой обработки файлов.", exc_info=True)
            if not processed:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=self.service.config.poll_seconds
                    )
                except TimeoutError:
                    pass

    async def process_next(self) -> bool:
        """Обрабатывает один оригинал, не удерживая DB во время разбора/vision."""
        claimed = await self.service.claim_next()
        if claimed is None:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(claimed))
        try:
            result = await self.service.extract(claimed)
            await self.service.persist(claimed, result)
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        return True

    async def _heartbeat(self, claimed: ClaimedFile) -> None:
        while True:
            await asyncio.sleep(self.service.config.lease_seconds / 3)
            try:
                if not await self.service.renew(claimed):
                    return
            except Exception:
                logger.warning(
                    "⚠️ Lease файла job=%s не продлён; результат проверит квитанцию перед записью.",
                    claimed.job_id,
                    exc_info=True,
                )
                return
