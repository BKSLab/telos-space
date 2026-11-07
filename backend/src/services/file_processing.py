"""Извлечение оригиналов и локальный FTS без зависимости от embeddings."""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from src.clients.vision import DisabledVisionCapability, VisionCapability
from src.exceptions.clients import ClientError
from src.exceptions.storage import TaskAttachmentStorageError
from src.knowledge.extract import IMAGE_EXTENSIONS, INDEXABLE_EXTENSIONS, extract_indexable_text
from src.repositories.file_processing_jobs import FileProcessingJobsRepository
from src.repositories.file_processing_sources import FileProcessingSourcesRepository
from src.repositories.knowledge_index_jobs import KnowledgeIndexJobsRepository
from src.repositories.unit_of_work import UnitOfWork
from src.storage.task_attachments import TaskAttachmentStorage

logger = logging.getLogger(__name__)
TERMINAL_EXTRACTION_STATUSES = frozenset({"ready", "empty", "unsupported", "disabled"})


@dataclass(frozen=True, slots=True)
class FileProcessingConfig:
    """Ресурсные пределы независимого worker-а."""

    poll_seconds: float = 2
    max_attempts: int = 5
    lease_seconds: float = 1200
    retry_seconds: float = 30


@dataclass(frozen=True, slots=True)
class FileProcessingScope:
    """Зависимости одной короткой транзакции."""

    jobs: FileProcessingJobsRepository
    sources: FileProcessingSourcesRepository
    semantic_jobs: KnowledgeIndexJobsRepository
    unit_of_work: UnitOfWork


FileProcessingScopeFactory = Callable[[], AbstractAsyncContextManager[FileProcessingScope]]


@dataclass(frozen=True, slots=True)
class ClaimedFile:
    """Самодостаточный снимок для фазы без соединения с БД."""

    job_id: int
    attachment_id: int | None
    project_file_id: int | None
    lease_token: str
    snapshot: dict


@dataclass(frozen=True, slots=True)
class ExtractedFile:
    text: str = ""
    status: str = "ready"
    detail: str | None = None
    content_hash: str = ""
    original_chars: int = 0
    deleted: bool = False


def _read_original(storage: TaskAttachmentStorage, source: dict) -> bytes:
    """Не читает больше серверного размера оригинала плюс байт проверки роста."""
    with storage.resolve(source["storage_key"]).open("rb") as stream:
        content = stream.read(source["size"] + 1)
    if len(content) != source["size"]:
        raise ValueError("Размер оригинала изменился после загрузки.")
    return content


class FileProcessingService:
    """Читает короткий снимок, извлекает вне DB, атомарно сохраняет текст и FTS."""

    def __init__(
        self,
        *,
        scope: FileProcessingScopeFactory,
        storage: TaskAttachmentStorage,
        vision: VisionCapability,
        config: FileProcessingConfig,
    ) -> None:
        self.scope = scope
        self.storage = storage
        self.vision = vision
        self.config = config

    async def claim_next(self) -> ClaimedFile | None:
        """Захватывает оригинал; разные процессы не распознают его одновременно."""
        token = str(uuid4())
        async with self.scope() as db:
            job = await db.jobs.claim_next(
                lease_token=token, lease_seconds=self.config.lease_seconds
            )
            if job is None:
                return None
            snapshot = await db.sources.get_snapshot(
                attachment_id=job.attachment_id, project_file_id=job.project_file_id
            )
            result = ClaimedFile(
                job.id, job.attachment_id, job.project_file_id, token, snapshot or {}
            )
            await db.unit_of_work.commit()
            return result

    async def renew(self, claimed: ClaimedFile) -> bool:
        """Продлевает квитанцию без удержания соединения во внешней фазе."""
        async with self.scope() as db:
            owned = await db.jobs.renew(
                job_id=claimed.job_id,
                lease_token=claimed.lease_token,
                lease_seconds=self.config.lease_seconds,
            )
            await db.unit_of_work.commit()
            return owned

    async def extract(self, claimed: ClaimedFile) -> ExtractedFile:
        """Повторно использует прежний текст или читает локальный оригинал/vision."""
        source = claimed.snapshot
        if not source:
            return ExtractedFile(deleted=True)
        if source.get("deleted_at"):
            try:
                await self.storage.delete_if_exists(source["storage_key"])
                return ExtractedFile(deleted=True)
            except TaskAttachmentStorageError:
                return ExtractedFile(
                    status="failed", detail="Очистка оригинала временно недоступна."
                )
        cached_status = source.get("cached_status")
        reusable = cached_status in TERMINAL_EXTRACTION_STATUSES and not (
            cached_status == "disabled" and not isinstance(self.vision, DisabledVisionCapability)
        )
        if reusable and (
            not source.get("content_hash") or source["content_hash"] == source.get("cached_hash")
        ):
            return ExtractedFile(
                text=source.get("cached_text") or "",
                status=cached_status,
                detail=source.get("cached_detail"),
                content_hash=source.get("cached_hash") or "",
                original_chars=source.get("cached_chars") or 0,
            )
        suffix = Path(source["original_name"]).suffix.lower()
        if suffix not in INDEXABLE_EXTENSIONS:
            return ExtractedFile(
                status="unsupported",
                content_hash=source.get("content_hash") or "",
                detail="Формат не поддерживает извлечение текста; название и подпись доступны для поиска.",
            )
        if suffix in IMAGE_EXTENSIONS and isinstance(self.vision, DisabledVisionCapability):
            return ExtractedFile(
                status="disabled",
                content_hash=source.get("content_hash") or "",
                detail="Распознавание изображений выключено; название и подпись доступны для поиска.",
            )
        try:
            content = await asyncio.to_thread(_read_original, self.storage, source)
            content_hash = hashlib.sha256(content).hexdigest()
            if source.get("content_hash") and source["content_hash"] != content_hash:
                return ExtractedFile(
                    status="failed",
                    content_hash=source["content_hash"],
                    detail="Оригинал изменился; загрузите файл повторно.",
                )
            extracted = await extract_indexable_text(
                source["original_name"], content, vision=self.vision
            )
            if not extracted:
                return ExtractedFile(
                    status="empty",
                    content_hash=content_hash,
                    detail="Текст не найден. Для PDF проверяется текстовый слой; OCR страниц не выполняется.",
                )
            return ExtractedFile(
                text=extracted, content_hash=content_hash, original_chars=len(extracted)
            )
        except ClientError:
            logger.warning(
                "⚠️ Распознавание оригинала job=%s временно недоступно.",
                claimed.job_id,
                exc_info=True,
            )
            return ExtractedFile(
                status="failed",
                content_hash=source.get("content_hash") or "",
                detail="Сервис распознавания временно недоступен; запланирован повтор.",
            )
        except Exception:
            logger.warning("⚠️ Разбор оригинала job=%s не выполнен.", claimed.job_id, exc_info=True)
            return ExtractedFile(
                status="failed",
                content_hash=source.get("content_hash") or "",
                detail="Не удалось прочитать или разобрать файл; запланирован повтор.",
            )

    async def persist(self, claimed: ClaimedFile, result: ExtractedFile) -> bool:
        """Проверяет актуальный оригинал/lease и фиксирует cache, FTS и outbox вместе."""
        async with self.scope() as db:
            # Порядок original → job совпадает с триггером обновления источника.
            if claimed.attachment_id is not None:
                if not await db.sources.lock_attachment_task(attachment_id=claimed.attachment_id):
                    return False
            current = await db.sources.get_snapshot(
                attachment_id=claimed.attachment_id,
                project_file_id=claimed.project_file_id,
                lock=True,
            )
            job = await db.jobs.lock_owned(job_id=claimed.job_id, lease_token=claimed.lease_token)
            if job is None or current is None:
                return False
            original_matches = all(
                current.get(key) == claimed.snapshot.get(key)
                for key in ("storage_key", "size", "content_type", "content_hash")
            )
            if not original_matches or bool(current.get("deleted_at")) != bool(
                claimed.snapshot.get("deleted_at")
            ):
                await db.jobs.finish(
                    job_id=job.id,
                    lease_token=claimed.lease_token,
                    succeeded=False,
                    detail=None,
                    max_attempts=self.config.max_attempts,
                    retry_seconds=0,
                )
                await db.unit_of_work.commit()
                return False
            if result.deleted:
                if claimed.project_file_id is not None and current.get("deleted_at"):
                    await db.sources.purge_deleted(
                        project_file_id=claimed.project_file_id, storage_key=current["storage_key"]
                    )
                await db.unit_of_work.commit()
                return True
            if not current.get("deleted_at"):
                await db.sources.save_text(
                    attachment_id=claimed.attachment_id,
                    project_file_id=claimed.project_file_id,
                    text=result.text,
                    status=result.status,
                    detail=result.detail,
                    content_hash=result.content_hash,
                    original_chars=result.original_chars,
                )
                if current["project_id"] is not None:
                    await db.semantic_jobs.add_project_change(current["project_id"])
            await db.jobs.finish(
                job_id=job.id,
                lease_token=claimed.lease_token,
                succeeded=result.status != "failed",
                detail=result.detail,
                max_attempts=self.config.max_attempts,
                retry_seconds=self.config.retry_seconds * min(2 ** max(job.attempts - 1, 0), 16),
            )
            await db.unit_of_work.commit()
            return True
