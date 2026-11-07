"""Короткие SQL-операции над оригиналом, извлечённым текстом и FTS."""

from sqlalchemy import delete, func, literal, select
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.knowledge_attachment_texts import KnowledgeAttachmentText
from src.db.models.knowledge_project_file_texts import KnowledgeProjectFileText
from src.db.models.project_files import ProjectFile
from src.db.models.task_attachments import TaskAttachment
from src.db.models.tasks import Task
from src.exceptions.file_processing import FileProcessingRepositoryError


class FileProcessingSourcesRepository:
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_snapshot(
        self, *, attachment_id: int | None, project_file_id: int | None, lock: bool = False
    ) -> dict | None:
        """Читает оригинал вместе с кешем; lock повторяет проверку перед записью."""
        if attachment_id is not None:
            source, cache = TaskAttachment, KnowledgeAttachmentText
            query = (
                select(
                    source.id,
                    source.task_id,
                    Task.project_id,
                    source.original_name,
                    source.caption,
                    source.storage_key,
                    source.size,
                    source.content_type,
                    literal(None).label("content_hash"),
                    literal(None).label("deleted_at"),
                    cache.text.label("cached_text"),
                    cache.status.label("cached_status"),
                    cache.detail.label("cached_detail"),
                    cache.content_hash.label("cached_hash"),
                    cache.original_chars.label("cached_chars"),
                )
                .join(Task, source.task_id == Task.id)
                .outerjoin(cache, cache.attachment_id == source.id)
                .where(source.id == attachment_id)
            )
        else:
            source, cache = ProjectFile, KnowledgeProjectFileText
            query = (
                select(
                    source.id,
                    literal(None).label("task_id"),
                    source.project_id,
                    source.original_name,
                    source.caption,
                    source.storage_key,
                    source.size,
                    source.content_type,
                    source.content_hash,
                    source.deleted_at,
                    cache.text.label("cached_text"),
                    cache.status.label("cached_status"),
                    cache.detail.label("cached_detail"),
                    cache.content_hash.label("cached_hash"),
                    cache.original_chars.label("cached_chars"),
                )
                .outerjoin(cache, cache.project_file_id == source.id)
                .where(source.id == project_file_id)
            )
        if lock:
            query = query.with_for_update(of=source)
        try:
            result = await self.db_session.execute(query)
            row = result.mappings().one_or_none()
            return dict(row) if row else None
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileProcessingRepositoryError("Не удалось прочитать исходный файл.") from error

    async def lock_attachment_task(self, *, attachment_id: int) -> bool:
        """Блокирует родителя до вложения; порядок row marks JOIN не гарантирован."""
        try:
            parent = await self.db_session.execute(
                select(Task.id)
                .join(TaskAttachment, TaskAttachment.task_id == Task.id)
                .where(TaskAttachment.id == attachment_id)
                .with_for_update(of=Task)
            )
            return parent.scalar_one_or_none() is not None
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileProcessingRepositoryError("Не удалось заблокировать задачу файла.") from error

    async def save_text(
        self,
        *,
        attachment_id: int | None,
        project_file_id: int | None,
        text: str,
        status: str,
        detail: str | None,
        content_hash: str,
        original_chars: int,
    ) -> None:
        """Сохраняет полный результат в прежнем общем кеше FTS и RAG."""
        cache = KnowledgeAttachmentText if attachment_id is not None else KnowledgeProjectFileText
        key = "attachment_id" if attachment_id is not None else "project_file_id"
        values = {
            key: attachment_id if attachment_id is not None else project_file_id,
            "text": text,
            "status": status,
            "detail": detail,
            "content_hash": content_hash,
            "original_chars": original_chars,
        }
        insert_values = (
            values if attachment_id is not None else {**values, "index_status": "pending"}
        )
        statement = insert(cache).values(**insert_values)
        try:
            await self.db_session.execute(
                statement.on_conflict_do_update(
                    index_elements=[getattr(cache, key)], set_={**values, "updated_at": func.now()}
                )
            )
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileProcessingRepositoryError(
                "Не удалось сохранить полный текст файла."
            ) from error

    async def purge_deleted(self, *, project_file_id: int, storage_key: str) -> None:
        """Удаляет только всё ещё помеченный оригинал после очистки хранения."""
        try:
            await self.db_session.execute(
                delete(ProjectFile).where(
                    ProjectFile.id == project_file_id,
                    ProjectFile.deleted_at.is_not(None),
                    ProjectFile.storage_key == storage_key,
                )
            )
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileProcessingRepositoryError("Не удалось завершить удаление файла.") from error

    async def lock_backfill_sources(
        self, *, source_type: str, after_id: int, limit: int
    ) -> list[int]:
        """Захватывает только ID; текст читается следующим READ COMMITTED statement."""
        if source_type not in {"task_attachment", "project_file"}:
            raise ValueError("Неизвестный тип оригинала для backfill.")
        source = TaskAttachment if source_type == "task_attachment" else ProjectFile
        conditions = [source.id > after_id]
        if source is ProjectFile:
            conditions.append(source.deleted_at.is_(None))
        statement = (
            select(source.id)
            .prefix_with("/* file_search_backfill_lock */")
            .where(*conditions)
            .order_by(source.id)
            .limit(limit)
            .with_for_update(of=source)
        )
        try:
            result = await self.db_session.execute(statement)
            return list(result.scalars())
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileProcessingRepositoryError(
                "Не удалось захватить пакет оригиналов для FTS."
            ) from error

    async def backfill_search_batch(self, *, source_type: str, source_ids: list[int]) -> int:
        """Индексирует кеш новым snapshot после захвата оригиналов в той же UoW."""
        if not source_ids:
            return 0
        if source_type == "task_attachment":
            source_table, cache_table, owner_column = (
                "task_attachments",
                "knowledge_attachment_texts",
                "attachment_id",
            )
            active = ""
        elif source_type == "project_file":
            source_table, cache_table, owner_column = (
                "project_files",
                "knowledge_project_file_texts",
                "project_file_id",
            )
            active = "AND source.deleted_at IS NULL"
        else:
            raise ValueError("Неизвестный тип оригинала для backfill.")
        # Имена выбраны только из локального allowlist; все входные значения — bind.
        statement = sql_text(f"""
            WITH batch AS MATERIALIZED (
                SELECT source.id, source.original_name, source.caption, coalesce(cache.text, '') AS body
                FROM {source_table} source
                LEFT JOIN {cache_table} cache ON cache.{owner_column} = source.id
                WHERE source.id = ANY(CAST(:source_ids AS integer[])) {active}
            ), saved AS (
                INSERT INTO file_search_chunks
                    ({owner_column}, chunk_index, start_offset, end_offset, title, caption, body)
                SELECT batch.id, offsets.start_offset / 11744, offsets.start_offset,
                    least(offsets.start_offset + 12000, length(batch.body)),
                    batch.original_name, left(coalesce(batch.caption, ''), 4000),
                    substr(batch.body, offsets.start_offset + 1, 12000)
                FROM batch CROSS JOIN LATERAL
                    generate_series(0, greatest(length(batch.body) - 1, 0), 11744) AS offsets(start_offset)
                ON CONFLICT ({owner_column}, chunk_index) WHERE {owner_column} IS NOT NULL
                DO UPDATE SET start_offset = excluded.start_offset, end_offset = excluded.end_offset,
                    title = excluded.title, caption = excluded.caption, body = excluded.body
                RETURNING {owner_column}
            )
            SELECT count(DISTINCT {owner_column}) FROM saved
        """)
        try:
            result = await self.db_session.execute(statement, {"source_ids": source_ids})
            return int(result.scalar_one())
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileProcessingRepositoryError(
                "Не удалось построить FTS по сохранённым текстам."
            ) from error

    async def enqueue_missing_batch(
        self, *, source_type: str, after_id: int, limit: int
    ) -> tuple[int, int, int]:
        """Явно ставит только прежние оригиналы без готового кеша; jobs не сбрасывает."""
        if source_type == "task_attachment":
            source_table, cache_table, owner_column = (
                "task_attachments",
                "knowledge_attachment_texts",
                "attachment_id",
            )
            active = ""
        elif source_type == "project_file":
            source_table, cache_table, owner_column = (
                "project_files",
                "knowledge_project_file_texts",
                "project_file_id",
            )
            active = "AND source.deleted_at IS NULL"
        else:
            raise ValueError("Неизвестный тип оригинала для очереди.")
        statement = sql_text(f"""
            WITH batch AS MATERIALIZED (
                SELECT source.id FROM {source_table} source
                LEFT JOIN {cache_table} cache ON cache.{owner_column} = source.id
                WHERE source.id > :after_id {active}
                    AND (cache.{owner_column} IS NULL OR cache.status IN ('pending', 'processing'))
                ORDER BY source.id LIMIT :limit
            ), saved AS (
                INSERT INTO file_processing_jobs ({owner_column}) SELECT id FROM batch
                ON CONFLICT ({owner_column}) WHERE {owner_column} IS NOT NULL DO NOTHING
                RETURNING id
            )
            SELECT coalesce((SELECT max(id) FROM batch), :after_id),
                (SELECT count(*) FROM batch), (SELECT count(*) FROM saved)
        """)
        try:
            row = (
                await self.db_session.execute(statement, {"after_id": after_id, "limit": limit})
            ).one()
            return int(row[0]), int(row[1]), int(row[2])
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileProcessingRepositoryError(
                "Не удалось поставить прежние оригиналы в очередь."
            ) from error
