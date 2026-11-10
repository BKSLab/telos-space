"""Один алгоритм синхронизации всех источников коллекции проекта."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from src.clients.embedding import EmbeddingClient
from src.clients.qdrant import ProjectQdrantClient
from src.clients.vision import VisionCapability
from src.db.models.knowledge_index_jobs import KnowledgeIndexJob
from src.exceptions.ai import AiProviderError
from src.exceptions.clients import ClientError
from src.exceptions.storage import TaskAttachmentStorageError
from src.knowledge.catalog import ProjectCatalog, SourceType, build_catalog, digest
from src.knowledge.documents import KnowledgeDocument
from src.knowledge.extract import IMAGE_EXTENSIONS, INDEXABLE_EXTENSIONS, extract_indexable_text
from src.repositories.knowledge_source_summaries import KnowledgeSourceSummariesRepository
from src.repositories.knowledge_sources import KnowledgeSourcesRepository
from src.repositories.project_files import ProjectFilesRepository
from src.repositories.unit_of_work import UnitOfWork
from src.services.source_summaries import SourceSummariesService
from src.storage.task_attachments import TaskAttachmentStorage


@dataclass(slots=True)
class PreparedIndexAction:
    """Снимок без сессии; внешняя фаза дополняет тексты файлов."""

    project_id: int
    catalog: ProjectCatalog
    extractions: list[dict] = field(default_factory=list)
    extraction_errors: list[str] = field(default_factory=list)
    summaries: list[dict] = field(default_factory=list)
    project_file_extractions: list[dict] = field(default_factory=list)
    cleaned_project_file_ids: list[int] = field(default_factory=list)
    index_completed: bool = False


class KnowledgeIndexService:
    def __init__(
        self,
        *,
        sources_repository: KnowledgeSourcesRepository,
        files_repository: ProjectFilesRepository,
        summaries_repository: KnowledgeSourceSummariesRepository,
        summarizer: SourceSummariesService,
        unit_of_work: UnitOfWork,
        attachment_storage: TaskAttachmentStorage,
        embedding_batch_size: int,
        chunk_target_chars: int,
        chunk_overlap_chars: int,
        embedding_client: EmbeddingClient,
        qdrant_client: ProjectQdrantClient,
        vision: VisionCapability,
        extract_originals: bool = True,
    ):
        self.sources_repository = sources_repository
        self.files_repository = files_repository
        self.summaries_repository = summaries_repository
        self.summarizer = summarizer
        self.unit_of_work = unit_of_work
        self.attachment_storage = attachment_storage
        self.embedding_batch_size = embedding_batch_size
        self.chunk_target_chars = chunk_target_chars
        self.chunk_overlap_chars = chunk_overlap_chars
        self.embedding_client = embedding_client
        self.qdrant_client = qdrant_client
        self.vision = vision
        self.extract_originals = extract_originals

    async def prepare(self, job: KnowledgeIndexJob) -> PreparedIndexAction:
        """Обычная мутация и полная пересборка читают один и тот же каталог."""
        rows = await self.sources_repository.get_project_rows(job.project_id)
        if any(not row.get("deleted_at") for row in rows.get("project_files", [])):
            await self.sources_repository.start_project_files(
                project_id=job.project_id, embedding_signature=self._embedding_signature()
            )
            await self.unit_of_work.commit()
        return PreparedIndexAction(job.project_id, build_catalog(job.project_id, rows))

    async def extract(self, action: PreparedIndexAction) -> None:
        """Использует общий кеш; извлечение production выполняет отдельная очередь.

        Явный режим extract_originals оставлен для автономных прежних клиентов
        индексатора. Composition приложения его отключает, поэтому vision
        оригинала вызывается только владельцем FileProcessingJob.
        """
        if not self.extract_originals:
            action.catalog = build_catalog(action.project_id, action.catalog.rows)
            return
        rows = action.catalog.rows
        cached = {row["attachment_id"]: row for row in rows.get("knowledge_attachment_texts", [])}
        for attachment in rows.get("task_attachments", []):
            previous = cached.get(attachment["id"])
            if previous and previous["status"] in {"ready", "unsupported"}:
                continue
            result = await self._extract_attachment(attachment)
            action.extractions.append(result)
            cached[attachment["id"]] = result
            if result["status"] == "failed":
                action.extraction_errors.append(f"Файл {attachment['id']}: {result['detail']}")
        rows["knowledge_attachment_texts"] = list(cached.values())
        file_texts = {
            row["project_file_id"]: row for row in rows.get("knowledge_project_file_texts", [])
        }
        for original in rows.get("project_files", []):
            if original.get("deleted_at"):
                try:
                    await self.attachment_storage.delete_if_exists(original["storage_key"])
                    action.cleaned_project_file_ids.append(original["id"])
                except TaskAttachmentStorageError:
                    action.extraction_errors.append(
                        f"Очистка файла {original['id']} временно недоступна."
                    )
                continue
            previous = file_texts.get(original["id"])
            if (
                previous
                and previous["content_hash"] == original["content_hash"]
                and previous["status"] in {"ready", "empty", "unsupported"}
            ):
                continue
            result = await self._extract_attachment(original)
            result["project_file_id"] = result.pop("attachment_id")
            if result["status"] == "ready" and result["content_hash"] != original["content_hash"]:
                result.update(
                    status="failed",
                    text="",
                    detail="Оригинал изменился; загрузите файл повторно.",
                    original_chars=0,
                )
            result["content_hash"] = original["content_hash"]
            result["index_status"] = "processing"
            action.project_file_extractions.append(result)
            file_texts[original["id"]] = result
            if result["status"] == "failed":
                action.extraction_errors.append(
                    f"Файл проекта {original['id']}: {result['detail']}"
                )
        rows["knowledge_project_file_texts"] = list(file_texts.values())
        action.catalog = build_catalog(action.project_id, rows)

    async def summarize(self, action: PreparedIndexAction) -> AsyncIterator[dict]:
        """Готовит описания по одному, чтобы worker сразу сохранял каждый результат.

        Args:
            action: Снимок с извлечёнными оригиналами, не связанный с DB-сессией.
        Yields:
            Готовое описание новой версии документа или файла.
        """
        rows = action.catalog.rows
        summaries = {item["source_id"]: item for item in rows.get("knowledge_source_summaries", [])}
        for source in action.catalog.sources.values():
            if (
                source.kind
                not in {SourceType.DOCUMENT, SourceType.ATTACHMENT, SourceType.PROJECT_FILE}
                or source.summary
            ):
                continue
            if (
                source.kind in {SourceType.ATTACHMENT, SourceType.PROJECT_FILE}
                and source.properties.get("extraction", {}).get("status") != "ready"
            ):
                continue
            try:
                result = await self.summarizer.summarize(source)
                action.summaries.append(result)
                summaries[source.source_id] = result
                source.summary = result["summary"]
                rows["knowledge_source_summaries"] = list(summaries.values())
                yield result
            except (ClientError, ValueError):
                # Поиск по оригиналам продолжает работать; очередь повторит только
                # отсутствующие версии, уже готовые описания сохранены worker-ом.
                action.extraction_errors.append(f"Описание {source.source_id} временно недоступно.")
        rows["knowledge_source_summaries"] = list(summaries.values())

    async def persist_extractions(self, action: PreparedIndexAction) -> None:
        """Сохраняет извлечение для FTS до обращения к embeddings/Qdrant."""
        if not (
            action.extractions or action.project_file_extractions or action.cleaned_project_file_ids
        ):
            return
        try:
            for result in action.extractions:
                await self.sources_repository.save_extraction(result)
            for result in action.project_file_extractions:
                await self.sources_repository.save_project_file_extraction(result)
            if action.cleaned_project_file_ids:
                await self.files_repository.purge_deleted(
                    project_id=action.project_id, file_ids=action.cleaned_project_file_ids
                )
            await self.unit_of_work.commit()
        except Exception:
            await self.unit_of_work.rollback()
            raise

    async def persist_summary(self, result: dict) -> None:
        """Фиксирует одно описание до перехода к следующему внешнему вызову.

        Args:
            result: Описание и идентификаторы версии источника.
        """
        try:
            await self.summaries_repository.save(result)
            await self.unit_of_work.commit()
        except Exception:
            await self.unit_of_work.rollback()
            raise

    async def execute_prepared(self, action: PreparedIndexAction) -> int:
        """Текст → embedding, свойства → payload, удалённое → delete."""
        if not action.catalog.sources:
            await self.qdrant_client.delete_collection(action.project_id)
            return 0
        documents = [
            chunk
            for source in action.catalog.sources.values()
            for chunk in source.chunks(self.chunk_target_chars, self.chunk_overlap_chars)
        ]
        existing = await self.qdrant_client.manifest(action.project_id)
        signature = self._embedding_signature()
        for document in documents:
            document.payload["embedding_signature"] = signature
        changed = [
            doc
            for doc in documents
            if existing.get(doc.point_id, {}).get("text_hash") != doc.payload["text_hash"]
            or existing.get(doc.point_id, {}).get("embedding_signature") != signature
        ]
        vectors = await self._embed(changed)
        await self.qdrant_client.ensure_collection(action.project_id)
        for start in range(0, len(changed), self.embedding_batch_size):
            await self.qdrant_client.upsert_documents(
                project_id=action.project_id,
                documents=changed[start : start + self.embedding_batch_size],
                vectors=vectors[start : start + self.embedding_batch_size],
            )
        unchanged_text = [
            doc
            for doc in documents
            if doc.point_id in existing
            and existing[doc.point_id].get("text_hash") == doc.payload["text_hash"]
            and existing[doc.point_id].get("embedding_signature") == signature
            and existing[doc.point_id] != doc.payload
        ]
        await self.qdrant_client.update_payloads(action.project_id, unchanged_text)
        expected = {doc.point_id for doc in documents}
        await self.qdrant_client.delete_points(action.project_id, sorted(set(existing) - expected))
        action.index_completed = True
        if action.extraction_errors:
            raise AiProviderError("; ".join(action.extraction_errors))
        return len(documents)

    async def persist_project_file_states(self, action: PreparedIndexAction) -> None:
        """Сохраняет прогресс файлов отдельно от результата задания проекта.

        Args:
            action: Снимок с версиями оригиналов и результатом синхронизации Qdrant.
        Raises:
            KnowledgeIndexJobsRepositoryError: Прогресс не удалось записать.
            UnitOfWorkRepositoryError: Транзакцию не удалось зафиксировать.
        """
        try:
            for original in action.catalog.rows.get("project_files", []):
                if original.get("deleted_at"):
                    continue
                await self.sources_repository.finish_project_file(
                    file_id=original["id"],
                    content_hash=original["content_hash"],
                    embedding_signature=self._embedding_signature(),
                    indexed=action.index_completed,
                    detail=None
                    if action.index_completed
                    else "Индексация временно недоступна. Повторите обработку файла.",
                )
            await self.unit_of_work.commit()
        except Exception:
            await self.unit_of_work.rollback()
            raise

    def _embedding_signature(self) -> str:
        return digest([self.embedding_client.model, self.qdrant_client.vector_dim])

    async def _extract_attachment(self, attachment: dict) -> dict:
        result = {
            "attachment_id": attachment["id"],
            "text": "",
            "status": "pending",
            "detail": None,
            "original_chars": 0,
            "content_hash": "",
        }
        suffix = Path(attachment["original_name"]).suffix.lower()
        if suffix not in INDEXABLE_EXTENSIONS:
            return {
                **result,
                "status": "unsupported",
                "detail": "Формат не поддерживает извлечение текста; метаданные файла доступны.",
            }
        try:
            content = await asyncio.to_thread(
                lambda: self.attachment_storage.resolve(attachment["storage_key"]).read_bytes()
            )
            result["content_hash"] = hashlib.sha256(content).hexdigest()
            extracted = await extract_indexable_text(
                attachment["original_name"], content, vision=self.vision
            )
            if not extracted:
                return {
                    **result,
                    "status": "empty",
                    "detail": "Распознавание изображения не дало текста."
                    if suffix in IMAGE_EXTENSIONS
                    else "Текст не найден; для PDF проверяется текстовый слой, OCR страниц не выполняется.",
                }
            result.update(text=extracted, original_chars=len(extracted), status="ready")
            return result
        except ClientError:
            return {
                **result,
                "status": "failed",
                "detail": "Сервис распознавания временно недоступен; запланирован повтор.",
            }
        except Exception:
            return {
                **result,
                "status": "failed",
                "detail": "Не удалось прочитать или разобрать файл; запланирован повтор.",
            }

    async def _embed(self, documents: list[KnowledgeDocument]) -> list[list[float]]:
        vectors = []
        for start in range(0, len(documents), self.embedding_batch_size):
            vectors.extend(
                await self.embedding_client.get_embeddings(
                    [doc.text for doc in documents[start : start + self.embedding_batch_size]]
                )
            )
        if len(vectors) != len(documents) or any(
            len(vector) != self.qdrant_client.vector_dim for vector in vectors
        ):
            raise ValueError(
                "Количество или размерность embeddings не совпадает с документами коллекции."
            )
        return vectors
