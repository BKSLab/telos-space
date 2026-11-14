"""Единая проекция документов с текущими SQL-правами и устойчивыми ссылками."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    Integer,
    Text,
    and_,
    case,
    cast,
    delete,
    exists,
    func,
    insert,
    literal,
    or_,
    select,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.db.models.document_index_states import DocumentIndexState
from src.db.models.document_links import DocumentLink
from src.db.models.document_submissions import DocumentSubmission
from src.db.models.documents import Document
from src.db.models.file_processing_jobs import FileProcessingJob
from src.db.models.file_search_chunks import FileSearchChunk
from src.db.models.knowledge_attachment_texts import KnowledgeAttachmentText
from src.db.models.knowledge_project_file_texts import KnowledgeProjectFileText
from src.db.models.project_files import ProjectFile
from src.db.models.project_members import ProjectMember
from src.db.models.projects import Project
from src.db.models.task_attachments import TaskAttachment
from src.db.models.task_participants import TaskParticipant
from src.db.models.tasks import Task
from src.db.models.users import User
from src.exceptions.document_catalog import DocumentCatalogRepositoryError
from src.repositories.file_processing_state import fts_state_columns
from src.utils.fts import build_ts_query

SOURCE_MODELS = {"document": Document, "project_file": ProjectFile, "attachment": TaskAttachment}


def _project_visible(project_id, user_id: int):
    return exists(
        select(ProjectMember.id).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        )
    )


def _task_visible(task, user_id: int):
    return or_(
        _project_visible(task.project_id, user_id),
        and_(
            task.project_id.is_(None),
            exists(
                select(TaskParticipant.id).where(
                    TaskParticipant.task_id == task.id, TaskParticipant.user_id == user_id
                )
            ),
        ),
    )


def _text_hash(value):
    return func.encode(func.sha256(func.convert_to(func.coalesce(value, ""), "UTF8")), "hex")


def _sources(user_id: int, knowledge_enabled: bool, embedding_signature: str):
    branches = []
    for kind, source in SOURCE_MODELS.items():
        owner = aliased(Task, name=f"{kind}_owner")
        cache = KnowledgeAttachmentText if kind == "attachment" else KnowledgeProjectFileText
        text = source.content_md if kind == "document" else func.coalesce(cache.text, "")
        project_id = (
            func.coalesce(source.project_id, owner.project_id)
            if kind == "document"
            else (owner.project_id if kind == "attachment" else source.project_id)
        )
        owner_task_id = (
            source.owner_task_id
            if kind == "document"
            else (source.task_id if kind == "attachment" else cast(literal(None), Integer))
        )
        title = (
            source.title
            if kind == "document"
            else func.coalesce(source.display_title, source.original_name)
        )
        author_id = source.authored_by_user_id if kind == "document" else source.uploaded_by_user_id
        source_key = func.concat(kind, ":", source.id)
        idx = DocumentIndexState
        semantic = case(
            (literal(not knowledge_enabled) | project_id.is_(None), "disabled"),
            (idx.source_id.is_(None), "pending"),
            (idx.embedding_signature != embedding_signature, "pending"),
            (
                (idx.source_version != source.version) | (idx.text_hash != _text_hash(text)),
                "pending",
            ),
            else_=idx.status,
        )
        extraction = (
            literal("not_required")
            if kind == "document"
            else func.coalesce(cache.status, "pending")
        )
        fields = [
            literal(kind).label("storage_kind"),
            source.id.label("source_id"),
            project_id.label("project_id"),
            owner_task_id.label("owner_task_id"),
            title.label("title"),
            source.caption,
            source.created_at,
            source.updated_at,
            source.version,
            author_id.label("author_id"),
            func.coalesce(
                func.nullif(
                    func.trim(
                        func.concat_ws(" ", User.last_name, User.first_name, User.middle_name)
                    ),
                    "",
                ),
                User.username,
            ).label("author_name"),
            extraction.label("extraction_status"),
            (cast(literal(None), Text) if kind == "document" else cache.detail).label(
                "extraction_detail"
            ),
            (
                literal("ready")
                if kind == "document"
                else fts_state_columns(
                    FileProcessingJob.status, cache.status, FileProcessingJob.detail
                )[0]
            ).label("fts_status"),
            (cast(literal(None), Text) if kind == "document" else FileProcessingJob.detail).label(
                "fts_detail"
            ),
            semantic.label("semantic_status"),
            idx.detail.label("semantic_detail"),
            func.length(text).label("text_total_chars"),
            _text_hash(text).label("text_hash"),
            (cast(literal(None), Text) if kind == "document" else source.storage_key).label(
                "storage_key"
            ),
            (cast(literal(None), Text) if kind == "document" else source.original_name).label(
                "original_name"
            ),
            (cast(literal(None), Text) if kind == "document" else source.content_type).label(
                "content_type"
            ),
            (cast(literal(None), Integer) if kind == "document" else source.size).label("size"),
            (
                literal("md")
                if kind == "document"
                else func.lower(func.regexp_replace(source.original_name, r"^.*\.", ""))
            ).label("format"),
        ]
        query = select(*fields).select_from(source)
        if kind == "document":
            query = query.outerjoin(owner, owner.id == source.owner_task_id)
            visible = or_(
                and_(source.project_id.is_not(None), _project_visible(source.project_id, user_id)),
                and_(source.owner_task_id.is_not(None), _task_visible(owner, user_id)),
            )
        elif kind == "attachment":
            query = (
                query.join(owner, owner.id == source.task_id)
                .outerjoin(cache, cache.attachment_id == source.id)
                .outerjoin(FileProcessingJob, FileProcessingJob.attachment_id == source.id)
            )
            visible = _task_visible(owner, user_id)
        else:
            query = query.outerjoin(cache, cache.project_file_id == source.id).outerjoin(
                FileProcessingJob, FileProcessingJob.project_file_id == source.id
            )
            visible = and_(
                source.deleted_at.is_(None), _project_visible(source.project_id, user_id)
            )
        query = (
            query.outerjoin(User, User.id == author_id)
            .outerjoin(idx, and_(idx.source_id == source_key, idx.project_id == project_id))
            .where(visible)
        )
        branches.append(query)
    return union_all(*branches).subquery("accessible_documents")


def _linked(source, user_id):
    return exists(
        select(DocumentLink.id).join(Task, Task.id == DocumentLink.task_id).where(
            Task.project_id == source.c.project_id,
            _task_visible(Task, user_id),
            or_(
                and_(
                    source.c.storage_kind == "document",
                    DocumentLink.document_id == source.c.source_id,
                ),
                and_(
                    source.c.storage_kind == "project_file",
                    DocumentLink.project_file_id == source.c.source_id,
                ),
            )
        )
    )


class DocumentCatalogRepository:
    """Не меняет владение исходных таблиц при чтении общей библиотеки."""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def list(
        self,
        *,
        user_id: int,
        knowledge_enabled: bool,
        embedding_signature: str,
        project_id: int | None,
        task_id: int | None,
        q: str | None,
        scope: str,
        source_kind: str | None,
        format: str | None,
        processing: str,
        limit: int,
        offset: int,
        include_project_owned: bool = True,
        include_task_owned: bool = True,
        personal_only: bool = False,
    ) -> dict:
        """Фильтрует текущий ACL и контекст до стабильной пагинации."""
        source = _sources(user_id, knowledge_enabled, embedding_signature)
        conditions = []
        if personal_only:
            conditions.extend([source.c.project_id.is_(None), source.c.owner_task_id.is_not(None)])
        if not include_project_owned:
            conditions.append(source.c.owner_task_id.is_not(None))
        if not include_task_owned:
            conditions.append(source.c.owner_task_id.is_(None))
        linked = _linked(source, user_id)
        if project_id is not None:
            conditions.append(source.c.project_id == project_id)
        if task_id is not None:
            conditions.append(
                or_(
                    source.c.owner_task_id == task_id,
                    exists(
                        select(DocumentLink.id).join(Task, Task.id == DocumentLink.task_id).where(
                            DocumentLink.task_id == task_id,
                            Task.project_id == source.c.project_id,
                            _task_visible(Task, user_id),
                            or_(
                                and_(
                                    source.c.storage_kind == "document",
                                    DocumentLink.document_id == source.c.source_id,
                                ),
                                and_(
                                    source.c.storage_kind == "project_file",
                                    DocumentLink.project_file_id == source.c.source_id,
                                ),
                            ),
                        )
                    ),
                )
            )
        if scope == "project":
            conditions.extend([source.c.owner_task_id.is_(None), ~linked])
        elif scope == "tasks":
            conditions.append(or_(source.c.owner_task_id.is_not(None), linked))
        if source_kind:
            conditions.append(
                source.c.storage_kind == "document"
                if source_kind == "text"
                else source.c.storage_kind != "document"
            )
        if format:
            conditions.append(source.c.format == format.lower().lstrip("."))
        failed = or_(
            source.c.extraction_status == "failed",
            source.c.fts_status == "failed",
            source.c.semantic_status == "failed",
        )
        pending = or_(
            source.c.extraction_status.in_(["pending", "processing"]),
            source.c.fts_status.in_(["pending", "processing"]),
            source.c.semantic_status.in_(["pending", "processing", "unknown"]),
        )
        if processing == "failed":
            conditions.append(failed)
        elif processing == "pending":
            conditions.extend([~failed, pending])
        elif processing == "ready":
            conditions.extend([~failed, ~pending])
        if q and q.strip():
            terms = build_ts_query(q.strip())
            conditions.append(
                or_(
                    and_(
                        source.c.storage_kind == "document",
                        source.c.source_id.in_(
                            select(Document.id).where(Document.search_vector.op("@@")(terms))
                        ),
                    ),
                    and_(
                        source.c.storage_kind == "attachment",
                        source.c.source_id.in_(
                            select(FileSearchChunk.attachment_id).where(
                                FileSearchChunk.search_vector.op("@@")(terms)
                            )
                        ),
                    ),
                    and_(
                        source.c.storage_kind == "project_file",
                        source.c.source_id.in_(
                            select(FileSearchChunk.project_file_id).where(
                                FileSearchChunk.search_vector.op("@@")(terms)
                            )
                        ),
                    ),
                )
            )
        filtered = select(source).where(*conditions).cte("filtered_documents")
        page = (
            select(filtered)
            .order_by(filtered.c.updated_at.desc(), filtered.c.storage_kind, filtered.c.source_id)
            .limit(limit)
            .offset(offset)
            .subquery()
        )
        result = await self._execute(
            select(
                select(func.count()).select_from(filtered).scalar_subquery().label("total"),
                func.coalesce(
                    (select(func.jsonb_agg(func.to_jsonb(page.table_valued()))).scalar_subquery()),
                    literal("[]").cast(JSONB),
                ).label("items"),
            )
        )
        row = result.mappings().one()
        return {"total": row["total"], "items": list(row["items"])}

    async def get(
        self,
        *,
        kind: str,
        source_id: int,
        user_id: int,
        knowledge_enabled: bool,
        embedding_signature: str,
    ) -> dict | None:
        """Возвращает документ только при текущем доступе владельца."""
        source = _sources(user_id, knowledge_enabled, embedding_signature)
        result = await self._execute(
            select(source).where(source.c.storage_kind == kind, source.c.source_id == source_id)
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def tasks(
        self, refs: list[tuple[str, int]], *, user_id: int
    ) -> dict[tuple[str, int], list[dict]]:
        """Пакетно читает только допустимые отношения к текущим задачам."""
        if not refs:
            return {}
        branches = []
        for kind, model in SOURCE_MODELS.items():
            ids = [identifier for ref_kind, identifier in refs if ref_kind == kind]
            if not ids:
                continue
            owner_id = (
                model.owner_task_id
                if kind == "document"
                else (model.task_id if kind == "attachment" else None)
            )
            columns = [
                literal(kind).label("storage_kind"),
                model.id.label("source_id"),
                Task.id,
                func.concat(func.coalesce(Project.key, "T"), "-", Task.number).label("key"),
                Task.title,
            ]
            if owner_id is not None:
                branches.append(
                    select(*columns, literal("owner").label("relation"))
                    .select_from(model)
                    .join(Task, Task.id == owner_id)
                    .outerjoin(Project, Project.id == Task.project_id)
                    .where(model.id.in_(ids), _task_visible(Task, user_id))
                )
            if kind != "attachment":
                link_column = (
                    DocumentLink.document_id if kind == "document" else DocumentLink.project_file_id
                )
                branches.append(
                    select(*columns, literal("linked").label("relation"))
                    .select_from(model)
                    .join(DocumentLink, link_column == model.id)
                    .join(Task, Task.id == DocumentLink.task_id)
                    .outerjoin(Project, Project.id == Task.project_id)
                    .where(
                        model.id.in_(ids),
                        Task.project_id == model.project_id,
                        _task_visible(Task, user_id),
                    )
                )
        result = await self._execute(union_all(*branches))
        grouped = {}
        for row in result.mappings():
            value = dict(row)
            key = value.pop("storage_kind"), value.pop("source_id")
            grouped.setdefault(key, []).append(value)
        return grouped

    async def text(
        self,
        *,
        kind: str,
        source_id: int,
        offset: int,
        max_chars: int,
        user_id: int,
        knowledge_enabled: bool,
        embedding_signature: str,
    ) -> dict | None:
        """Читает ограниченную страницу без извлечения оригинала."""
        model, key = (
            (Document, Document.id)
            if kind == "document"
            else (
                (KnowledgeAttachmentText, KnowledgeAttachmentText.attachment_id)
                if kind == "attachment"
                else (KnowledgeProjectFileText, KnowledgeProjectFileText.project_file_id)
            )
        )
        text = model.content_md if kind == "document" else model.text
        source = _sources(user_id, knowledge_enabled, embedding_signature)
        fragment = (
            select(func.substring(text, offset + 1, max_chars))
            .where(key == source_id)
            .scalar_subquery()
        )
        result = await self._execute(
            select(
                source.c.text_hash,
                source.c.text_total_chars,
                func.coalesce(fragment, "").label("text"),
            ).where(source.c.storage_kind == kind, source.c.source_id == source_id)
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def attachment_count(self, *, task_id: int) -> int:
        """Считает оригиналы под уже удерживаемой блокировкой задачи."""
        result = await self._execute(
            select(func.count())
            .select_from(TaskAttachment)
            .where(TaskAttachment.task_id == task_id)
        )
        return result.scalar_one()

    async def lock_source(self, *, kind: str, source_id: int) -> bool:
        """Блокирует первичную запись для атомарного изменения её версии."""
        model = SOURCE_MODELS[kind]
        result = await self._execute(
            select(model.id).where(model.id == source_id).with_for_update()
        )
        return result.scalar_one_or_none() is not None

    async def create(self, *, kind: str, data: dict) -> int:
        """Вставляет первичную запись; финальный commit выполняет сценарий."""
        model = SOURCE_MODELS[kind]
        result = await self._execute(insert(model).values(**data).returning(model.id))
        return result.scalar_one()

    async def update(
        self, *, kind: str, source_id: int, data: dict, expected_revision: int
    ) -> bool:
        """Меняет только прочитанную версию; не перезаписывает чужой редактор."""
        model = SOURCE_MODELS[kind]
        result = await self._execute(
            update(model)
            .where(model.id == source_id, model.version == expected_revision)
            .values(**data, version=model.version + 1, updated_at=func.now())
            .returning(model.id)
        )
        return result.scalar_one_or_none() is not None

    async def delete(self, *, kind: str, source_id: int) -> None:
        """Удаляет текст/вложение либо ставит tombstone проектного оригинала."""
        model = SOURCE_MODELS[kind]
        statement = (
            update(model).where(model.id == source_id).values(deleted_at=func.now())
            if kind == "project_file"
            else delete(model).where(model.id == source_id)
        )
        await self._execute(statement)

    async def set_link(self, *, kind: str, source_id: int, task_id: int, remove: bool) -> None:
        """Меняет независимую связь; ни одна операция не удаляет сам источник."""
        field = "document_id" if kind == "document" else "project_file_id"
        if remove:
            statement = delete(DocumentLink).where(
                getattr(DocumentLink, field) == source_id, DocumentLink.task_id == task_id
            )
        else:
            statement = (
                pg_insert(DocumentLink)
                .values(**{field: source_id, "task_id": task_id})
                .on_conflict_do_nothing()
            )
        await self._execute(statement)

    async def lock_request(self, *, user_id: int, request_id: UUID) -> None:
        """Сериализует одинаковый request_id автора между процессами."""
        await self._execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtextextended(f"document:{user_id}:{request_id}", 0)
                )
            )
        )

    async def get_submission(self, *, user_id: int, request_id: UUID) -> DocumentSubmission | None:
        """Читает сохранённую квитанцию без раскрытия чужих запросов."""
        result = await self._execute(
            select(DocumentSubmission).where(
                DocumentSubmission.actor_user_id == user_id,
                DocumentSubmission.request_id == request_id,
            )
        )
        return result.scalar_one_or_none()

    async def save_submission(
        self, *, user_id: int, request_id: UUID, arguments_hash: str, document_ref: str
    ) -> None:
        """Сохраняет результат в транзакции первичного документа."""
        await self._execute(
            insert(DocumentSubmission).values(
                actor_user_id=user_id,
                request_id=request_id,
                arguments_hash=arguments_hash,
                document_ref=document_ref,
            )
        )

    async def _execute(self, statement):
        try:
            return await self.db_session.execute(statement)
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise DocumentCatalogRepositoryError("Ошибка хранилища документов.") from error
