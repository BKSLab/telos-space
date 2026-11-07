"""Сборка независимого worker-а обработки файлов."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.clients.vision import VisionCapability
from src.files.worker import FileProcessingWorker
from src.repositories.file_processing_jobs import FileProcessingJobsRepository
from src.repositories.file_processing_sources import FileProcessingSourcesRepository
from src.repositories.knowledge_index_jobs import KnowledgeIndexJobsRepository
from src.repositories.unit_of_work import UnitOfWork
from src.services.file_processing import (
    FileProcessingConfig,
    FileProcessingScope,
    FileProcessingScopeFactory,
    FileProcessingService,
)
from src.services.file_search_backfill import FileSearchBackfillService
from src.storage.task_attachments import TaskAttachmentStorage


def build_file_processing_scope(session_factory: async_sessionmaker) -> FileProcessingScopeFactory:
    """Создаёт короткую DB-область с общей транзакцией кеша, FTS и outbox."""

    @asynccontextmanager
    async def scope() -> AsyncIterator[FileProcessingScope]:
        async with session_factory() as session:
            yield FileProcessingScope(
                jobs=FileProcessingJobsRepository(session),
                sources=FileProcessingSourcesRepository(session),
                semantic_jobs=KnowledgeIndexJobsRepository(session),
                unit_of_work=UnitOfWork(session),
            )

    return scope


def build_file_processing_worker(
    *,
    session_factory: async_sessionmaker,
    storage: TaskAttachmentStorage,
    vision: VisionCapability,
    config: FileProcessingConfig,
) -> FileProcessingWorker:
    """Собирает worker независимо от включённости семантического контура."""
    return FileProcessingWorker(
        service=FileProcessingService(
            scope=build_file_processing_scope(session_factory),
            storage=storage,
            vision=vision,
            config=config,
        )
    )


def build_file_search_backfill(*, session_factory: async_sessionmaker) -> FileSearchBackfillService:
    """Собирает cache-only backfill без зависимостей storage/vision/embeddings."""
    return FileSearchBackfillService(scope=build_file_processing_scope(session_factory))
