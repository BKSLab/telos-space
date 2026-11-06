"""Оригинал проходит worker, FTS и Qdrant; отказы не теряют извлечённый текст."""

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock

from qdrant_client import AsyncQdrantClient
from sqlalchemy import select, update

from src.clients.embedding import EmbeddingClient
from src.clients.llm import LlmClient
from src.clients.qdrant import ProjectQdrantClient
from src.clients.vision import DisabledVisionCapability
from src.db.models.knowledge_project_file_texts import KnowledgeProjectFileText
from src.db.models.project_files import ProjectFile
from src.exceptions.clients import EmbeddingClientError
from src.exceptions.knowledge import KnowledgeIndexJobsRepositoryError
from src.knowledge.catalog import build_catalog
from src.knowledge.extract import extract_indexable_text
from src.knowledge.worker import KnowledgeWorker
from src.repositories.knowledge_source_summaries import KnowledgeSourceSummariesRepository
from src.repositories.knowledge_sources import KnowledgeSourcesRepository
from src.repositories.project_files import ProjectFilesRepository
from src.repositories.unit_of_work import UnitOfWork
from src.services.knowledge_index import KnowledgeIndexService
from src.services.source_summaries import SourceSummariesService
from src.storage.task_attachments import TaskAttachmentStorage


def build_pipeline(db_session, storage):
    """Настоящие PG/файлы/Qdrant с управляемыми внешними embeddings."""
    embeddings = AsyncMock(spec=EmbeddingClient)
    embeddings.model = "test-embeddings"
    embeddings.get_embeddings.side_effect = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
    qdrant = ProjectQdrantClient(
        client=AsyncQdrantClient(location=":memory:"), collection_prefix="files", vector_dim=3
    )
    sources = KnowledgeSourcesRepository(db_session)
    service = KnowledgeIndexService(
        sources_repository=sources,
        files_repository=ProjectFilesRepository(db_session),
        summaries_repository=KnowledgeSourceSummariesRepository(db_session),
        summarizer=SourceSummariesService(llm_client=AsyncMock(spec=LlmClient), chunk_chars=16000),
        unit_of_work=UnitOfWork(db_session),
        attachment_storage=storage,
        embedding_batch_size=32,
        chunk_target_chars=2200,
        chunk_overlap_chars=300,
        embedding_client=embeddings,
        qdrant_client=qdrant,
        vision=DisabledVisionCapability(),
    )

    @asynccontextmanager
    async def scope():
        yield service

    worker = KnowledgeWorker(config=None, queue=None, index_service=scope, runtime=None)
    return worker, service, sources, qdrant, embeddings


async def test_worker_recovers_embedding_failure_and_cleans_deleted_source(
    db_session, project, user, tmp_path, monkeypatch
):
    storage = TaskAttachmentStorage(tmp_path)
    content = "Согласованный бюджет проекта — 742000 рублей. Контрольный бирюзокомплект.".encode()
    key = await storage.save_in(
        PurePosixPath("projects", str(project.id), "files"), ".txt", content
    )
    original = ProjectFile(
        project_id=project.id,
        uploaded_by_user_id=user.id,
        original_name="Бюджет.txt",
        storage_key=key,
        content_type="text/plain",
        size=len(content),
        content_hash=hashlib.sha256(content).hexdigest(),
    )
    db_session.add(original)
    await db_session.flush()
    file_id = original.id
    extractor = AsyncMock(spec=extract_indexable_text, wraps=extract_indexable_text)
    monkeypatch.setattr("src.services.knowledge_index.extract_indexable_text", extractor)
    worker, service, sources, qdrant, embeddings = build_pipeline(db_session, storage)
    embeddings.get_embeddings.side_effect = EmbeddingClientError("Провайдер временно недоступен")
    job = SimpleNamespace(id=1, project_id=project.id, attempts=1)
    try:
        failed = await worker._prepare_and_execute_job(job)
        assert isinstance(failed.error, EmbeddingClientError)
        cached = (
            await db_session.execute(
                select(KnowledgeProjectFileText).execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert cached.status == "ready" and cached.index_status == "failed"
        assert "742000" in cached.text and storage.resolve(key).exists()
        assert any(
            hit["source_id"] == f"project_file:{file_id}"
            for hit in await sources.search(project.id, "бирюзокомплект")
        )
        embeddings.get_embeddings.side_effect = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
        recovered = await worker._prepare_and_execute_job(job)
        assert recovered.error is None
        assert extractor.await_count == 1
        cached = (
            await db_session.execute(
                select(KnowledgeProjectFileText).execution_options(populate_existing=True)
            )
        ).scalar_one()
        assert cached.index_status == "ready" and cached.indexed_hash == original.content_hash
        assert any(
            row["source_id"] == f"project_file:{file_id}"
            for row in (await qdrant.manifest(project.id)).values()
        )
        calls = embeddings.get_embeddings.await_count
        repeated = await worker._prepare_and_execute_job(job)
        assert repeated.error is None and embeddings.get_embeddings.await_count == calls

        await db_session.execute(
            update(ProjectFile)
            .where(ProjectFile.id == file_id)
            .values(deleted_at=datetime.now(UTC))
        )
        assert (
            f"project_file:{file_id}"
            not in build_catalog(project.id, await sources.get_project_rows(project.id)).sources
        )
        assert not await sources.search(project.id, "бирюзокомплект")
        cleaned = await worker._prepare_and_execute_job(job)
        assert cleaned.error is None
        assert not (tmp_path / key).exists()
        assert not await db_session.scalar(select(ProjectFile.id).where(ProjectFile.id == file_id))
        assert not any(
            row["source_id"] == f"project_file:{file_id}"
            for row in (await qdrant.manifest(project.id)).values()
        )
    finally:
        await qdrant.close()


async def test_nul_text_and_persistence_failure_do_not_leave_files_processing(
    db_session, project, user, tmp_path, monkeypatch
):
    """NUL нормализуется; сбой записи оставляет доступный повтор для обоих файлов."""
    storage = TaskAttachmentStorage(tmp_path)
    originals = {}
    for name, content in [
        ("nul.txt", "лево\x00право аметистокомплект".encode()),
        ("good.txt", "Согласован кварцобюджет 42000 рублей".encode()),
    ]:
        key = await storage.save_in(
            PurePosixPath("projects", str(project.id), "files"), ".txt", content
        )
        record = ProjectFile(
            project_id=project.id,
            uploaded_by_user_id=user.id,
            original_name=name,
            storage_key=key,
            content_type="text/plain",
            size=len(content),
            content_hash=hashlib.sha256(content).hexdigest(),
        )
        db_session.add(record)
        await db_session.flush()
        originals[record.id] = (key, content)
    worker, service, sources, qdrant, embeddings = build_pipeline(db_session, storage)
    job = SimpleNamespace(id=1, project_id=project.id, attempts=1)
    save_extraction = sources.save_project_file_extraction
    try:
        with monkeypatch.context() as patch:
            patch.setattr(
                sources,
                "save_project_file_extraction",
                AsyncMock(
                    spec=save_extraction,
                    side_effect=KnowledgeIndexJobsRepositoryError("Контролируемый отказ записи"),
                ),
            )
            result = await worker._prepare_and_execute_job(job)
        assert isinstance(result.error, KnowledgeIndexJobsRepositoryError)
        states = list(
            (
                await db_session.execute(
                    select(KnowledgeProjectFileText).execution_options(populate_existing=True)
                )
            ).scalars()
        )
        assert len(states) == 2
        assert all(
            row.status == "failed" and row.index_status == "failed" and row.detail for row in states
        )
        embeddings.get_embeddings.assert_not_awaited()
        result = await worker._prepare_and_execute_job(job)
        assert result.error is None
        states = list(
            (
                await db_session.execute(
                    select(KnowledgeProjectFileText).execution_options(populate_existing=True)
                )
            ).scalars()
        )
        assert all(row.status == "ready" and row.index_status == "ready" for row in states)
        assert any("лево право" in row.text for row in states)
        assert all("\x00" not in row.text for row in states)
        assert all(
            storage.resolve(key).read_bytes() == content for key, content in originals.values()
        )
        for query in ("аметистокомплект", "кварцобюджет"):
            assert await sources.search(project.id, query)
        indexed = {item["source_id"] for item in (await qdrant.manifest(project.id)).values()}
        assert {f"project_file:{id_}" for id_ in originals} <= indexed
    finally:
        await qdrant.close()
