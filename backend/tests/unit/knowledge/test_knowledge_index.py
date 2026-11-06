"""Синхронизация всего проекта с настоящим локальным Qdrant и подменой embeddings."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from qdrant_client import AsyncQdrantClient

from src.clients.qdrant import ProjectQdrantClient
from src.clients.vision import DisabledVisionCapability
from src.exceptions.knowledge import KnowledgeProviderError
from src.knowledge.context import coverage, file_issues
from src.repositories.knowledge_source_summaries import KnowledgeSourceSummariesRepository
from src.repositories.knowledge_sources import KnowledgeSourcesRepository
from src.repositories.project_files import ProjectFilesRepository
from src.services.knowledge_index import KnowledgeIndexService
from src.services.source_summaries import SourceSummariesService, SourceSummaryOutput


def base_rows():
    return {
        "projects": [
            {"id": 1, "key": "PROJ", "name": "TELOS SPACE", "description_md": "Паспорт проекта"}
        ],
        "tasks": [
            {
                "id": 7,
                "project_id": 1,
                "stage_id": 3,
                "number": 12,
                "title": "Приёмка",
                "description_md": "Контроль качества",
                "due_date": "2026-09-10",
            }
        ],
        "task_comments": [
            {
                "id": 8,
                "task_id": 7,
                "author_name": "Иван",
                "body_md": "Договорились провести проверку",
            }
        ],
        "documents": [
            {
                "id": 9,
                "project_id": 1,
                "slug": "spec",
                "title": "Спецификация",
                "content_md": "Требования к изделию",
            }
        ],
        "document_links": [{"id": 1, "document_id": 9, "task_id": 7}],
        "project_risks": [
            {
                "id": 12,
                "project_id": 1,
                "task_id": 7,
                "title": "Поставка",
                "mitigation_plan": "Превентивные меры",
                "response_plan": "Резервный адаптер",
                "status": "OPEN",
            }
        ],
    }


def build_service(tmp_path, *, rows=None, qdrant=None):
    repository = AsyncMock(spec=KnowledgeSourcesRepository)
    repository.get_project_rows.return_value = rows or base_rows()
    embedding = AsyncMock()
    embedding.model = "test-model"
    embedding.get_embeddings.side_effect = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
    qdrant = qdrant or ProjectQdrantClient(
        client=AsyncQdrantClient(location=":memory:"), collection_prefix="test", vector_dim=3
    )
    storage = Mock()
    storage.resolve.side_effect = lambda key: tmp_path / key
    service = KnowledgeIndexService(
        sources_repository=repository,
        files_repository=AsyncMock(spec=ProjectFilesRepository),
        summaries_repository=AsyncMock(spec=KnowledgeSourceSummariesRepository),
        summarizer=SourceSummariesService(
            llm_client=AsyncMock(
                get_structured_response=AsyncMock(
                    return_value=SourceSummaryOutput(summary="Описание тестового документа.")
                )
            ),
            chunk_chars=16000,
        ),
        unit_of_work=AsyncMock(),
        attachment_storage=storage,
        embedding_batch_size=32,
        chunk_target_chars=2200,
        chunk_overlap_chars=300,
        embedding_client=embedding,
        qdrant_client=qdrant,
        vision=DisabledVisionCapability(),
    )
    return service


async def sync(service, rows=None):
    if rows is not None:
        service.sources_repository.get_project_rows.return_value = rows
    action = await service.prepare(SimpleNamespace(project_id=1))
    await service.extract(action)
    await service.persist_extractions(action)
    async for summary in service.summarize(action):
        await service.persist_summary(summary)
    await service.execute_prepared(action)
    return action


async def test_repeated_sync_is_idempotent_and_metadata_needs_no_embeddings(tmp_path):
    service = build_service(tmp_path)
    first = await sync(service)
    manifest = await service.qdrant_client.manifest(1)
    rows, obsolete = coverage(first.catalog, manifest, target_chars=2200, overlap_chars=300)
    assert all(row["total"] == row["indexed"] for row in rows) and obsolete == 0
    service.embedding_client.reset_mock()
    await sync(service)
    service.embedding_client.get_embeddings.assert_not_awaited()
    changed = deepcopy(base_rows())
    changed["tasks"][0]["due_date"] = "2026-10-01"
    changed["document_links"] = []
    await sync(service, changed)
    service.embedding_client.get_embeddings.assert_not_awaited()
    payloads = {row["source_id"]: row for row in (await service.qdrant_client.manifest(1)).values()}
    assert payloads["task:7"]["properties"]["due_date"] == "2026-10-01"
    assert "task:7" not in payloads["document:9"]["related_source_ids"]
    await service.qdrant_client.close()


async def test_changes_shrink_chunks_and_keep_unrelated_sources(tmp_path):
    service = build_service(tmp_path)
    rows = base_rows()
    rows["tasks"][0]["description_md"] = "Подробное условие. " * 1000 + "Хвост задачи"
    first = await sync(service, rows)
    old_points = await service.qdrant_client.manifest(1)
    assert len([item for item in old_points.values() if item["source_id"] == "task:7"]) > 2
    assert any("Хвост задачи" in item["text"] for item in old_points.values())
    service.embedding_client.reset_mock()
    rows["tasks"][0]["description_md"] = "Краткое новое условие"
    rows["tasks"][0]["title"] = "Новая приёмка"
    await sync(service, rows)
    embedded = [
        text
        for call in service.embedding_client.get_embeddings.await_args_list
        for text in call.args[0]
    ]
    assert embedded and all("Новая приёмка" in text for text in embedded)
    assert all(
        "Новая приёмка" not in item.text
        for source in first.catalog.sources.values()
        if source.kind != "task"
        for item in source.chunks(2200, 300)
    )
    current = await service.qdrant_client.manifest(1)
    assert len(current) < len(old_points)
    assert {row["source_id"] for row in current.values()} == set(first.catalog.sources)
    await service.qdrant_client.close()


async def test_deleted_task_removes_children_but_preserves_linked_risk_and_document(tmp_path):
    service = build_service(tmp_path)
    await sync(service)
    rows = base_rows()
    rows["tasks"] = []
    rows["task_comments"] = []
    rows["document_links"] = []
    rows["project_risks"][0]["task_id"] = None
    await sync(service, rows)
    payloads = list((await service.qdrant_client.manifest(1)).values())
    assert {item["source_id"] for item in payloads} == {"project:1", "risk:12", "document:9"}
    assert all("task:7" not in item["related_source_ids"] for item in payloads)
    await sync(service, {})
    assert await service.qdrant_client.count(1) is None
    await service.qdrant_client.close()


async def test_embedding_failure_preserves_previous_index(tmp_path):
    service = build_service(tmp_path)
    await sync(service)
    before = await service.qdrant_client.manifest(1)
    rows = base_rows()
    rows["tasks"][0]["title"] = "Новый текст"
    service.embedding_client.get_embeddings.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError, match="offline"):
        await sync(service, rows)
    assert await service.qdrant_client.manifest(1) == before
    service.embedding_client.get_embeddings.side_effect = lambda texts: [[1.0] for _ in texts]
    with pytest.raises(ValueError, match="размерность"):
        await sync(service, rows)
    assert await service.qdrant_client.manifest(1) == before
    await service.qdrant_client.close()


async def test_attachment_text_is_complete_cached_and_fts_persisted_before_vectors(tmp_path):
    text = "Полное содержимое. " * 22000 + "Окончательное условие"
    (tmp_path / "long.txt").write_text(text, encoding="utf-8")
    rows = base_rows()
    rows["task_attachments"] = [
        {"id": 11, "task_id": 7, "original_name": "long.txt", "storage_key": "long.txt"}
    ]
    service = build_service(tmp_path, rows=rows)
    action = await sync(service)
    assert action.extractions[0]["text"] == text
    assert action.extractions[0]["status"] == "ready" and not file_issues(action.catalog)
    service.sources_repository.save_extraction.assert_awaited_once_with(action.extractions[0])
    assert any(
        "Окончательное условие" in row["text"]
        for row in (await service.qdrant_client.manifest(1)).values()
    )
    service.attachment_storage.resolve.reset_mock()
    rows["knowledge_attachment_texts"] = action.extractions
    await sync(service, rows)
    service.attachment_storage.resolve.assert_not_called()
    await service.qdrant_client.close()


async def test_failed_file_does_not_hide_other_sources_and_retry_recovers(tmp_path):
    rows = base_rows()
    rows["task_attachments"] = [
        {"id": 11, "task_id": 7, "original_name": "missing.txt", "storage_key": "missing.txt"}
    ]
    service = build_service(tmp_path, rows=rows)
    with pytest.raises(KnowledgeProviderError):
        await sync(service)
    payloads = list((await service.qdrant_client.manifest(1)).values())
    assert {row["source_id"] for row in payloads} >= {"task:7", "risk:12", "attachment:11"}
    assert (
        next(row for row in payloads if row["source_id"] == "attachment:11")["properties"][
            "extraction"
        ]["status"]
        == "failed"
    )
    (tmp_path / "missing.txt").write_text("Файл восстановлен", encoding="utf-8")
    action = await sync(service)
    assert not file_issues(action.catalog)
    await service.qdrant_client.close()


async def test_imported_original_is_linked_to_document_without_duplicate_extraction(tmp_path):
    rows = base_rows()
    rows["documents"][0]["origin_attachment_id"] = 11
    rows["task_attachments"] = [
        {"id": 11, "task_id": 7, "original_name": "original.pdf", "storage_key": "original.pdf"}
    ]
    rows["knowledge_attachment_texts"] = [
        {
            "attachment_id": 11,
            "text": "Исходные требования",
            "status": "ready",
            "detail": None,
            "original_chars": 18,
            "content_hash": "original",
        }
    ]
    service = build_service(tmp_path, rows=rows)
    action = await sync(service)
    service.attachment_storage.resolve.assert_not_called()
    assert not file_issues(action.catalog)
    assert "Исходные требования" in action.catalog.sources["attachment:11"].text
    rows["documents"][0]["content_md"] = "Отредактированные требования"
    edited = await sync(service, rows)
    assert "Исходные требования" in edited.catalog.sources["attachment:11"].text
    assert "Отредактированные требования" in edited.catalog.sources["document:9"].text
    assert {link["source_id"] for link in action.catalog.sources["document:9"].relations} >= {
        "attachment:11",
        "task:7",
    }
    await service.qdrant_client.close()


@pytest.mark.parametrize(
    "filename,content,status",
    [("file.bin", b"x", "unsupported"), ("empty.txt", b"", "empty"), ("scan.png", b"", "empty")],
)
async def test_unreadable_files_have_explicit_status(tmp_path, filename, content, status):
    (tmp_path / filename).write_bytes(content)
    service = build_service(tmp_path)
    result = await service._extract_attachment(
        {"id": 1, "original_name": filename, "storage_key": filename}
    )
    assert result["status"] == status and result["detail"]
    await service.qdrant_client.close()


async def test_embedding_model_and_dimension_change_refreshes_all_vectors(tmp_path):
    service = build_service(tmp_path)
    await sync(service)
    service.embedding_client.reset_mock()
    service.embedding_client.model = "another-model"
    await sync(service)
    assert service.embedding_client.get_embeddings.await_count > 0
    service.embedding_client.reset_mock()
    service.qdrant_client.vector_dim = 2
    service.embedding_client.get_embeddings.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
    await sync(service)
    points, _ = await service.qdrant_client.client.scroll(
        collection_name="test_1", with_vectors=True, limit=100
    )
    assert points and all(len(point.vector) == 2 for point in points)
    await service.qdrant_client.close()
