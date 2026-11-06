"""Оригиналы, FTS, outbox и гонки удаления на настоящем PostgreSQL."""

import hashlib

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.knowledge_index_jobs import (
    KnowledgeIndexJob,
    KnowledgeIndexOperation,
    KnowledgeIndexStatus,
)
from src.db.models.knowledge_project_file_texts import KnowledgeProjectFileText
from src.db.models.knowledge_source_summaries import KnowledgeSourceSummary
from src.db.models.project_files import ProjectFile
from src.db.models.projects import Project
from src.db.models.users import User
from src.knowledge.catalog import SourceType, build_catalog
from src.knowledge.context import read_catalog
from src.repositories.knowledge_source_summaries import KnowledgeSourceSummariesRepository
from src.repositories.knowledge_sources import KnowledgeSourcesRepository
from src.repositories.project_files import ProjectFilesRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.knowledge import KnowledgeReadRequest


def original_data(project_id: int, user_id: int, name: str = "plan.txt") -> dict:
    """Создаёт метаданные неизменяемого локального оригинала."""
    return {
        "project_id": project_id,
        "uploaded_by_user_id": user_id,
        "original_name": name,
        "content_type": "text/plain",
        "storage_key": f"projects/{project_id}/files/{name}",
        "content_hash": hashlib.sha256(name.encode()).hexdigest(),
        "size": 100,
    }


def extraction_data(original: dict, text: str) -> dict:
    """Возвращает результат разбора, который может прийти с задержкой."""
    return {
        "project_file_id": original["id"],
        "content_hash": original["content_hash"],
        "text": text,
        "status": "ready",
        "detail": None,
        "original_chars": len(text),
    }


async def test_original_and_outbox_are_committed_or_rolled_back_together(
    db_session: AsyncSession,
    project: Project,
    user: User,
) -> None:
    files = ProjectFilesRepository(db_session)
    await db_session.execute(
        delete(KnowledgeIndexJob).where(KnowledgeIndexJob.project_id == project.id)
    )

    with pytest.raises(RuntimeError, match="abort upload"):
        async with db_session.begin_nested():
            original = await files.save(original_data(project.id, user.id))
            assert (
                await db_session.scalar(
                    select(func.count())
                    .select_from(ProjectFile)
                    .where(ProjectFile.id == original["id"])
                )
                == 1
            )
            assert (
                await db_session.scalar(
                    select(func.count())
                    .select_from(KnowledgeIndexJob)
                    .where(KnowledgeIndexJob.project_id == project.id)
                )
                == 1
            )
            raise RuntimeError("abort upload")

    assert await files.get_for_project(project_id=project.id, ids=None, limit=100, offset=0) == []
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(KnowledgeIndexJob)
            .where(KnowledgeIndexJob.project_id == project.id)
        )
        == 0
    )

    original = await files.save(original_data(project.id, user.id))
    await UnitOfWork(db_session).commit()
    stored = await files.get_by_id(project_id=project.id, file_id=original["id"])
    assert stored is not None
    assert stored["storage_key"] == original["storage_key"]
    assert stored["text"] == "" and stored["text_total_chars"] == 0
    job = (
        await db_session.execute(
            select(KnowledgeIndexJob).where(KnowledgeIndexJob.project_id == project.id)
        )
    ).scalar_one()
    assert job.status == KnowledgeIndexStatus.PENDING
    assert job.operation == KnowledgeIndexOperation.REINDEX_PROJECT
    assert job.transaction_id is not None


async def test_extracted_file_is_searchable_readable_and_isolated_by_project(
    db_session: AsyncSession,
    project: Project,
    user: User,
) -> None:
    other_project = Project(
        owner_id=user.id,
        key="OTHER",
        name="Чужой проект",
        status="PLANNING",
        color="#123456",
        order_index=1,
    )
    db_session.add(other_project)
    await db_session.flush()
    files = ProjectFilesRepository(db_session)
    sources = KnowledgeSourcesRepository(db_session)
    original = await files.save(original_data(project.id, user.id, "кварцоплан.txt"))
    foreign = await files.save(original_data(other_project.id, user.id, "foreign.txt"))
    text = "Начало документа. Янтарноспецификация проекта. Конец документа."
    await sources.save_project_file_extraction(extraction_data(original, text))
    await sources.save_project_file_extraction(extraction_data(foreign, "секретопакет"))

    page = await files.get_by_id(
        project_id=project.id, file_id=original["id"], offset=9, max_chars=17
    )
    assert page is not None
    assert page["text"] == text[9:26]
    assert page["text_total_chars"] == len(text)
    assert page["extraction_status"] == "ready"
    assert await files.get_by_id(project_id=project.id, file_id=foreign["id"]) is None
    assert (
        await files.get_for_project(project_id=project.id, ids=[foreign["id"]], limit=100, offset=0)
        == []
    )

    source_id = f"project_file:{original['id']}"
    for query in ["кварцоплан", "Янтарноспецификация"]:
        hits = await sources.search(project.id, query, entity_type=SourceType.PROJECT_FILE)
        assert [hit["source_id"] for hit in hits] == [source_id]
    assert not await sources.search(project.id, "секретопакет")
    assert not await sources.search(other_project.id, "Янтарноспецификация")

    catalog = build_catalog(project.id, await sources.get_project_rows(project.id))
    source = catalog.sources[source_id]
    assert source.kind is SourceType.PROJECT_FILE
    assert source.parent_source_id == f"project:{project.id}"
    assert text in source.text
    assert source.payload()["task_ids"] == []
    assert source.payload()["task_id"] is None
    assert original["storage_key"] not in str(source.payload())
    assert any(
        link["source_id"] == source_id
        for link in catalog.sources[f"project:{project.id}"].relations
    )
    read = read_catalog(
        catalog,
        KnowledgeReadRequest(name="read_source", source_id=source_id, max_chars=8000),
    )
    assert text in read["text"]
    assert read_catalog(
        catalog,
        KnowledgeReadRequest(name="read_source", source_id=f"project_file:{foreign['id']}"),
    ) == {"error": "Источник не найден в этом проекте."}

    await KnowledgeSourceSummariesRepository(db_session).save(
        {
            "project_id": project.id,
            "source_id": source_id,
            "project_file_id": original["id"],
            "content_hash": source.summary_hash,
            "summary": "Технические требования проекта.",
        }
    )
    listing = await files.get_for_project(project_id=project.id, ids=None, limit=100, offset=0)
    assert [item["id"] for item in listing] == [original["id"]]
    assert listing[0]["summary"] == "Технические требования проекта."
    assert "text" not in listing[0]
    assert (
        build_catalog(project.id, await sources.get_project_rows(project.id))
        .sources[source_id]
        .summary
        == "Технические требования проекта."
    )


async def test_retry_preserves_text_and_only_source_mutations_enqueue_work(
    db_session: AsyncSession,
    project: Project,
    user: User,
) -> None:
    files = ProjectFilesRepository(db_session)
    sources = KnowledgeSourcesRepository(db_session)
    original = await files.save(original_data(project.id, user.id))
    await sources.start_project_files(project_id=project.id, embedding_signature="model-v1")
    text = "Сохранённый разбор документа"
    await sources.save_project_file_extraction(extraction_data(original, text))
    await sources.finish_project_file(
        file_id=original["id"],
        content_hash=original["content_hash"],
        embedding_signature="model-v1",
        indexed=True,
        detail=None,
    )
    ready = await files.get_by_id(project_id=project.id, file_id=original["id"])
    assert ready is not None
    assert ready["extraction_status"] == ready["index_status"] == "ready"
    assert ready["indexed_at"] is not None

    await db_session.execute(
        delete(KnowledgeIndexJob).where(KnowledgeIndexJob.project_id == project.id)
    )
    await sources.start_project_files(project_id=project.id, embedding_signature="model-v1")
    same_version = await files.get_by_id(project_id=project.id, file_id=original["id"])
    assert same_version is not None and same_version["index_status"] == "ready"
    await sources.reset_project_file(file_id=original["id"])
    await sources.save_project_file_extraction(
        {**extraction_data(original, text), "content_hash": "0" * 64, "text": "stale version"}
    )
    await sources.finish_project_file(
        file_id=original["id"],
        content_hash="0" * 64,
        embedding_signature="stale",
        indexed=True,
        detail=None,
    )
    pending = await files.get_by_id(project_id=project.id, file_id=original["id"])
    assert pending is not None
    assert pending["text"] == text
    assert pending["extraction_status"] == "ready"
    assert pending["index_status"] == "pending"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(KnowledgeIndexJob)
            .where(KnowledgeIndexJob.project_id == project.id)
        )
        == 0
    )

    await files.request_retry(project_id=project.id, file_id=original["id"])
    await UnitOfWork(db_session).commit()
    retried = await files.get_by_id(project_id=project.id, file_id=original["id"])
    assert retried is not None and retried["retry_requested_at"] is not None
    assert retried["text"] == text
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(KnowledgeIndexJob)
            .where(KnowledgeIndexJob.project_id == project.id)
        )
        == 1
    )


async def test_deletion_during_processing_hides_source_and_rejects_late_results(
    db_session: AsyncSession,
    project: Project,
    user: User,
) -> None:
    files = ProjectFilesRepository(db_session)
    sources = KnowledgeSourcesRepository(db_session)
    summaries = KnowledgeSourceSummariesRepository(db_session)
    original = await files.save(original_data(project.id, user.id))
    active = await files.save(original_data(project.id, user.id, "active.txt"))
    source_id = f"project_file:{original['id']}"
    await sources.save_project_file_extraction(extraction_data(original, "топазосекрет"))
    await sources.finish_project_file(
        file_id=original["id"],
        content_hash=original["content_hash"],
        embedding_signature="v1",
        indexed=True,
        detail=None,
    )
    summary_data = {
        "project_id": project.id,
        "source_id": source_id,
        "project_file_id": original["id"],
        "content_hash": "s" * 64,
        "summary": "Первое описание",
    }
    await summaries.save(summary_data)
    before = (
        (
            await db_session.execute(
                select(*KnowledgeProjectFileText.__table__.c).where(
                    KnowledgeProjectFileText.project_file_id == original["id"]
                )
            )
        )
        .mappings()
        .one()
    )
    await files.mark_deleted(project_id=project.id, file_id=original["id"])
    await UnitOfWork(db_session).commit()

    # Worker уже прочитал оригинал; результат приходит после удаления пользователем.
    await sources.start_project_files(project_id=project.id, embedding_signature="v2")
    await sources.reset_project_file(file_id=original["id"])
    await sources.save_project_file_extraction(extraction_data(original, "late resurrection"))
    await sources.finish_project_file(
        file_id=original["id"],
        content_hash=original["content_hash"],
        embedding_signature="v2",
        indexed=True,
        detail=None,
    )
    await summaries.save({**summary_data, "summary": "Запоздалое описание"})
    after = (
        (
            await db_session.execute(
                select(*KnowledgeProjectFileText.__table__.c).where(
                    KnowledgeProjectFileText.project_file_id == original["id"]
                )
            )
        )
        .mappings()
        .one()
    )
    assert dict(after) == dict(before)
    assert (
        await db_session.scalar(
            select(KnowledgeSourceSummary.summary).where(
                KnowledgeSourceSummary.source_id == source_id
            )
        )
        == "Первое описание"
    )
    assert await files.get_by_id(project_id=project.id, file_id=original["id"]) is None
    assert [
        item["id"]
        for item in await files.get_for_project(
            project_id=project.id, ids=None, limit=100, offset=0
        )
    ] == [active["id"]]
    assert not await sources.search(project.id, "топазосекрет")
    rows = await sources.get_project_rows(project.id)
    assert any(row["id"] == original["id"] and row["deleted_at"] for row in rows["project_files"])
    catalog = build_catalog(project.id, rows)
    assert source_id not in catalog.sources
    assert "error" in read_catalog(
        catalog, KnowledgeReadRequest(name="read_source", source_id=source_id)
    )

    await files.purge_deleted(project_id=project.id + 1000, file_ids=[original["id"]])
    assert (
        await db_session.scalar(select(ProjectFile.id).where(ProjectFile.id == original["id"]))
        == original["id"]
    )
    await files.purge_deleted(project_id=project.id, file_ids=[original["id"], active["id"]])
    assert (
        await db_session.scalar(select(ProjectFile.id).where(ProjectFile.id == original["id"]))
        is None
    )
    assert (
        await db_session.scalar(
            select(KnowledgeProjectFileText.project_file_id).where(
                KnowledgeProjectFileText.project_file_id == original["id"]
            )
        )
        is None
    )
    assert (
        await db_session.scalar(
            select(KnowledgeSourceSummary.source_id).where(
                KnowledgeSourceSummary.source_id == source_id
            )
        )
        is None
    )
    assert await files.get_by_id(project_id=project.id, file_id=active["id"]) is not None

    # Повторная доставка результата после физического удаления tombstone тоже безопасна.
    await sources.save_project_file_extraction(extraction_data(original, "late resurrection"))
    await summaries.save(summary_data)
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(KnowledgeProjectFileText)
            .where(KnowledgeProjectFileText.project_file_id == original["id"])
        )
        == 0
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(KnowledgeSourceSummary)
            .where(KnowledgeSourceSummary.source_id == source_id)
        )
        == 0
    )
