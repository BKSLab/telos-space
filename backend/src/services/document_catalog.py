"""Общие сценарии документов без смены владельца и повторного разбора файлов."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID

from src.exceptions.api_tokens import InsufficientTokenScopeError
from src.exceptions.auth import NotAuthenticatedError
from src.exceptions.document_catalog import (
    CatalogDocumentConflictError,
    CatalogDocumentNotFoundError,
    CatalogDocumentValidationError,
    DocumentCatalogRepositoryError,
    DocumentCatalogServiceError,
)
from src.exceptions.file_processing import FileProcessingRepositoryError
from src.exceptions.knowledge import KnowledgeEventsServiceError
from src.exceptions.storage import TaskAttachmentStorageError
from src.exceptions.task_attachments import TaskAttachmentsServiceError
from src.exceptions.tasks import TasksRepositoryError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.repositories.document_catalog import DocumentCatalogRepository
from src.repositories.file_processing_jobs import FileProcessingJobsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.document_catalog import (
    DocumentCatalogItemSchema,
    DocumentCatalogPageSchema,
    DocumentCreateRequestSchema,
    DocumentTextPageSchema,
    DocumentUpdateRequestSchema,
)
from src.services.access import AccessService
from src.services.attachment_download import AttachmentDownload
from src.services.auth import AuthService, Principal
from src.services.knowledge_events import KnowledgeEvents
from src.services.task_attachments import TaskAttachmentsService
from src.storage.task_attachments import TaskAttachmentStorage
from src.utils.document_refs import decode_document_ref, encode_document_ref
from src.utils.file_previews import is_raster_previewable

logger = logging.getLogger(__name__)
DATA_ERRORS = (
    DocumentCatalogRepositoryError,
    FileProcessingRepositoryError,
    KnowledgeEventsServiceError,
    UnitOfWorkRepositoryError,
    TasksRepositoryError,
)


@dataclass(frozen=True, slots=True)
class DocumentCatalogScope:
    catalog: DocumentCatalogRepository
    access: AccessService
    auth: AuthService
    tasks: TasksRepository
    processing_jobs: FileProcessingJobsRepository
    knowledge_events: KnowledgeEvents
    unit_of_work: UnitOfWork


DocumentCatalogScopeFactory = Callable[[], AbstractAsyncContextManager[DocumentCatalogScope]]


class DocumentCatalogService:
    """Один контракт над Markdown, оригиналами проекта и оригиналами задачи."""

    def __init__(
        self,
        *,
        scope: DocumentCatalogScopeFactory,
        storage: TaskAttachmentStorage,
        knowledge_enabled: bool,
        max_file_size: int,
        embedding_signature: str,
        commit_writes: bool = True,
    ):
        self.scope = scope
        self.storage = storage
        self.knowledge_enabled = knowledge_enabled
        self.max_file_size = max_file_size
        self.commit_writes = commit_writes
        self.embedding_signature = embedding_signature
        # Только время жизни одного request-scoped сервиса; значения не входят
        # в DTO, repr и журналы. Bound WriteGate использует principal=None.
        self._credentials: tuple[str | None, str | None] | None = None
        self._current_principal: Principal | None = None

    async def resolve_principal(
        self, *, session_token: str | None, bearer_secret: str | None
    ) -> Principal:
        """Закрывает auth DB-область до чтения файла или длительного ответа."""
        async with self.scope() as db:
            principal = await db.auth.resolve_principal(
                session_token=session_token, bearer_secret=bearer_secret
            )
        self._credentials = (session_token, bearer_secret)
        self._current_principal = principal
        return principal

    async def list(
        self,
        *,
        user_id: int,
        project_id: int | None = None,
        task_id: int | None = None,
        q: str | None = None,
        scope: str = "all",
        source_kind: str | None = None,
        format: str | None = None,
        processing: str = "all",
        limit: int = 50,
        offset: int = 0,
        principal: Principal | None = None,
        personal_only: bool = False,
    ) -> DocumentCatalogPageSchema:
        """Читает общий каталог после ACL, фильтров и до пагинации."""
        try:
            async with self.scope() as db:
                principal = await self._refresh_principal(db, principal)
                if project_id is not None:
                    await db.access.ensure_project_access(project_id=project_id, user_id=user_id)
                if task_id is not None:
                    grant = await db.access.ensure_task_access(task_id=task_id, user_id=user_id)
                    if project_id is not None and grant.project_id != project_id:
                        raise CatalogDocumentNotFoundError()
                    self._scope(principal, "tasks:read")
                page = await db.catalog.list(
                    user_id=user_id,
                    knowledge_enabled=self.knowledge_enabled,
                    embedding_signature=self.embedding_signature,
                    project_id=project_id,
                    task_id=task_id,
                    q=q,
                    scope=scope,
                    source_kind=source_kind,
                    format=format,
                    processing=processing,
                    limit=limit,
                    offset=offset,
                    include_project_owned=principal is None or principal.allows("projects:read"),
                    include_task_owned=principal is None or principal.allows("tasks:read"),
                    personal_only=personal_only,
                )
                rows = page["items"]
                contexts = await db.catalog.tasks(
                    [(row["storage_kind"], row["source_id"]) for row in rows], user_id=user_id
                )
                items = [
                    self._item(
                        row, contexts.get((row["storage_kind"], row["source_id"]), []), principal
                    )
                    for row in rows
                ]
                total = page["total"]
                return DocumentCatalogPageSchema(
                    items=items,
                    total=total,
                    limit=limit,
                    offset=offset,
                    next_offset=offset + limit if offset + limit < total else None,
                )
        except DATA_ERRORS as error:
            raise DocumentCatalogServiceError(str(error)) from error

    async def get(
        self,
        *,
        document_ref: str,
        user_id: int,
        principal: Principal | None = None,
        for_write: bool = False,
    ) -> DocumentCatalogItemSchema:
        """Читает метаданные по стабильной ссылке с текущим доступом владельца."""
        try:
            async with self.scope() as db:
                row = await self._get(db, document_ref, user_id, principal, write=for_write)
                tasks = await db.catalog.tasks(
                    [(row["storage_kind"], row["source_id"])], user_id=user_id
                )
                return self._item(
                    row, tasks.get((row["storage_kind"], row["source_id"]), []), principal
                )
        except DATA_ERRORS as error:
            raise DocumentCatalogServiceError(str(error)) from error

    async def read_text(
        self,
        *,
        document_ref: str,
        user_id: int,
        offset: int = 0,
        max_chars: int = 8000,
        principal: Principal | None = None,
    ) -> DocumentTextPageSchema:
        """Читает сохранённый текст; parser, vision и embeddings не вызываются."""
        if offset < 0 or not 1 <= max_chars <= 8000:
            raise CatalogDocumentValidationError("Допустимы offset >= 0 и max_chars от 1 до 8000.")
        try:
            async with self.scope() as db:
                row = await self._get(db, document_ref, user_id, principal)
                page = await db.catalog.text(
                    kind=row["storage_kind"],
                    source_id=row["source_id"],
                    offset=offset,
                    max_chars=max_chars,
                    user_id=user_id,
                    knowledge_enabled=self.knowledge_enabled,
                    embedding_signature=self.embedding_signature,
                )
                if page is None:
                    raise CatalogDocumentNotFoundError()
                text = page["text"]
                total = page["text_total_chars"]
                end = min(total, offset + len(text))
                return DocumentTextPageSchema(
                    ref=document_ref,
                    text=text,
                    rendition="markdown" if row["storage_kind"] == "document" else "extracted_text",
                    content_hash=page["text_hash"],
                    offset=offset,
                    next_offset=end if end < total else None,
                    total_chars=total,
                )
        except DATA_ERRORS as error:
            raise DocumentCatalogServiceError(str(error)) from error

    async def read_context(
        self,
        *,
        document_refs: list[str],
        user_id: int,
        project_id: int | None,
        max_chars: int = 16000,
    ) -> str:
        """Собирает ограниченный AI-контекст только из доступных сохранённых текстов."""
        parts = []
        remaining = max_chars
        for ref in dict.fromkeys(document_refs):
            if remaining <= 0:
                break
            document = await self.get(document_ref=ref, user_id=user_id)
            if document.ownership.project_id != project_id:
                raise CatalogDocumentNotFoundError()
            page = await self.read_text(
                document_ref=ref, user_id=user_id, max_chars=min(8000, remaining)
            )
            state = document.states.extraction
            body = (
                page.text
                or f"Текстовое представление недоступно. Извлечение: {state.status}. {state.detail or ''}".strip()
            )
            block = f"Документ: {document.title}\n{body}"
            separator_size = 2 if parts else 0
            available = max(0, remaining - separator_size)
            parts.append(block[:available])
            remaining -= len(parts[-1]) + separator_size
        return "\n\n".join(parts)

    async def create(
        self, *, user_id: int, data: DocumentCreateRequestSchema, principal: Principal | None = None
    ) -> DocumentCatalogItemSchema:
        """Атомарно создаёт один Markdown и квитанцию безопасного повтора."""
        self._write_token(principal)
        self._validate_text(data.title, data.content_md, data.caption)
        arguments_hash = self._arguments_hash(
            {"operation": "text", **data.model_dump(mode="json", exclude={"request_id"})}
        )
        try:
            async with self.scope() as db:
                await self._owner(
                    db,
                    project_id=data.project_id,
                    owner_task_id=data.owner_task_id,
                    user_id=user_id,
                    principal=principal,
                )
                await db.catalog.lock_request(user_id=user_id, request_id=data.request_id)
                await self._owner(
                    db,
                    project_id=data.project_id,
                    owner_task_id=data.owner_task_id,
                    user_id=user_id,
                    principal=principal,
                    lock=True,
                )
                previous = await db.catalog.get_submission(
                    user_id=user_id, request_id=data.request_id
                )
                if previous:
                    return await self._replay(db, previous, arguments_hash, user_id, principal)
                identifier = await db.catalog.create(
                    kind="document",
                    data={
                        "project_id": data.project_id,
                        "owner_task_id": data.owner_task_id,
                        "authored_by_user_id": user_id,
                        "slug": f"doc-{data.request_id.hex}",
                        "title": data.title.strip(),
                        "content_md": data.content_md,
                        "caption": self._caption(data.caption),
                    },
                )
                ref = encode_document_ref("document", identifier)
                await db.catalog.save_submission(
                    user_id=user_id,
                    request_id=data.request_id,
                    arguments_hash=arguments_hash,
                    document_ref=ref,
                )
                row = await self._get(db, ref, user_id, principal, write=True)
                result = await self._item_in_scope(db, row, user_id, principal)
                await self._commit(db)
                return result
        except DATA_ERRORS as error:
            raise DocumentCatalogServiceError(str(error)) from error

    async def upload(
        self,
        *,
        user_id: int,
        request_id: UUID,
        file_name: str,
        content_type: str | None,
        content: bytes,
        project_id: int | None = None,
        owner_task_id: int | None = None,
        title: str | None = None,
        caption: str | None = None,
        principal: Principal | None = None,
    ) -> DocumentCatalogItemSchema:
        """Сохраняет только оригинал и outbox, без автоматического Markdown-дубля."""
        self._write_token(principal)
        if (project_id is None) == (owner_task_id is None):
            raise CatalogDocumentValidationError("Нужен ровно один владелец документа.")
        if len(content) > self.max_file_size:
            raise CatalogDocumentValidationError("Файл превышает предел размера загрузки.")
        try:
            name, extension, mime = TaskAttachmentsService.validate_file(
                file_name=file_name,
                content_type=content_type,
                content=content,
                max_file_size=self.max_file_size
                if owner_task_id is None
                else min(self.max_file_size, TaskAttachmentsService.MAX_FILE_SIZE),
            )
        except TaskAttachmentsServiceError as error:
            raise CatalogDocumentValidationError(error.detail) from error
        self._validate_text(title or name[:255], "", caption)
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        caption = self._caption(caption)
        digest = await asyncio.to_thread(lambda: hashlib.sha256(content).hexdigest())
        arguments_hash = self._arguments_hash(
            {
                "operation": "upload",
                "project_id": project_id,
                "owner_task_id": owner_task_id,
                "name": name,
                "mime": mime,
                "title": title,
                "caption": caption,
                "content_hash": digest,
            }
        )
        key = None
        stored = False
        try:
            async with self.scope() as db:
                await self._owner(
                    db,
                    project_id=project_id,
                    owner_task_id=owner_task_id,
                    user_id=user_id,
                    principal=principal,
                )
                previous = await db.catalog.get_submission(user_id=user_id, request_id=request_id)
                if previous:
                    return await self._replay(db, previous, arguments_hash, user_id, principal)
            key = await (
                self.storage.save(owner_task_id, extension, content)
                if owner_task_id is not None
                else self.storage.save_in(
                    PurePosixPath("projects", str(project_id), "files"), extension, content
                )
            )
            if is_raster_previewable(name, mime):
                await self.storage.prepare_preview(key)
            async with self.scope() as db:
                await db.catalog.lock_request(user_id=user_id, request_id=request_id)
                await self._owner(
                    db,
                    project_id=project_id,
                    owner_task_id=owner_task_id,
                    user_id=user_id,
                    principal=principal,
                    lock=True,
                )
                previous = await db.catalog.get_submission(user_id=user_id, request_id=request_id)
                if previous:
                    return await self._replay(db, previous, arguments_hash, user_id, principal)
                if owner_task_id is not None:
                    count = await db.catalog.attachment_count(task_id=owner_task_id)
                    if count >= TaskAttachmentsService.MAX_FILES_PER_TASK:
                        raise CatalogDocumentValidationError(
                            "В задаче достигнут предел числа оригиналов."
                        )
                kind = "attachment" if owner_task_id is not None else "project_file"
                values = {
                    "original_name": name,
                    "display_title": title.strip() if title else None,
                    "caption": caption,
                    "uploaded_by_user_id": user_id,
                    "storage_key": key,
                    "content_type": mime,
                    "size": len(content),
                }
                values.update(
                    {"task_id": owner_task_id}
                    if kind == "attachment"
                    else {"project_id": project_id, "content_hash": digest}
                )
                identifier = await db.catalog.create(kind=kind, data=values)
                ref = encode_document_ref(kind, identifier)
                await db.catalog.save_submission(
                    user_id=user_id,
                    request_id=request_id,
                    arguments_hash=arguments_hash,
                    document_ref=ref,
                )
                row = await self._get(db, ref, user_id, principal, write=True)
                result = await self._item_in_scope(db, row, user_id, principal)
                await self._commit(db)
                stored = True
                return result
        except (*DATA_ERRORS, TaskAttachmentStorageError) as error:
            raise DocumentCatalogServiceError(str(error)) from error
        finally:
            if key is not None and not stored:
                try:
                    await self.storage.delete_if_exists(key)
                except TaskAttachmentStorageError:
                    logger.warning(
                        "⚠️ Не удалось убрать оригинал незавершённой загрузки документа.",
                        exc_info=True,
                    )

    async def update(
        self,
        *,
        document_ref: str,
        user_id: int,
        data: DocumentUpdateRequestSchema,
        principal: Principal | None = None,
    ) -> DocumentCatalogItemSchema:
        """Проверяет версию и права перед изменением текста или метаданных."""
        self._write_token(principal)
        changes = data.model_dump(exclude_unset=True, exclude={"expected_revision"})
        if "title" in changes and (changes["title"] is None or not changes["title"].strip()):
            raise CatalogDocumentValidationError("Название не может быть пустым.")
        if "content_md" in changes and changes["content_md"] is None:
            raise CatalogDocumentValidationError("Текст не может быть null.")
        self._validate_text(
            changes.get("title") or "",
            changes.get("content_md") or "",
            changes.get("caption"),
            allow_empty_title=True,
        )
        try:
            async with self.scope() as db:
                row = await self._get(db, document_ref, user_id, principal, write=True, lock=True)
                if row["storage_kind"] != "document" and "content_md" in changes:
                    raise CatalogDocumentValidationError(
                        "Текстовое представление оригинала не редактируется."
                    )
                if "title" in changes:
                    changes["title" if row["storage_kind"] == "document" else "display_title"] = (
                        changes.pop("title").strip()
                    )
                if "caption" in changes:
                    changes["caption"] = self._caption(changes["caption"])
                if not await db.catalog.update(
                    kind=row["storage_kind"],
                    source_id=row["source_id"],
                    data=changes,
                    expected_revision=data.expected_revision,
                ):
                    raise CatalogDocumentConflictError()
                updated = await self._get(db, document_ref, user_id, principal, write=True)
                result = await self._item_in_scope(db, updated, user_id, principal)
                await self._commit(db)
                return result
        except DATA_ERRORS as error:
            raise DocumentCatalogServiceError(str(error)) from error

    async def delete(
        self, *, document_ref: str, user_id: int, principal: Principal | None = None
    ) -> None:
        """Удаляет сам документ; независимые связи снимаются каскадом."""
        self._write_token(principal)
        try:
            async with self.scope() as db:
                row = await self._get(db, document_ref, user_id, principal, write=True, lock=True)
                await db.catalog.delete(kind=row["storage_kind"], source_id=row["source_id"])
                await self._commit(db)
            if row["storage_kind"] == "attachment":
                try:
                    await self.storage.delete(row["storage_key"])
                except TaskAttachmentStorageError:
                    logger.warning(
                        "⚠️ Оригинал удалённого документа остался для очистки.", exc_info=True
                    )
        except DATA_ERRORS as error:
            raise DocumentCatalogServiceError(str(error)) from error

    async def retry(
        self, *, document_ref: str, user_id: int, principal: Principal | None = None
    ) -> DocumentCatalogItemSchema:
        """Повторяет незавершённые стадии, сохраняя успешный извлечённый текст."""
        self._write_token(principal)
        try:
            async with self.scope() as db:
                row = await self._get(db, document_ref, user_id, principal, write=True, lock=True)
                if row["storage_kind"] == "document" and (
                    not self.knowledge_enabled or row["project_id"] is None
                ):
                    raise CatalogDocumentValidationError(
                        "У этого текстового документа нет фоновых стадий обработки."
                    )
                if row["storage_kind"] != "document":
                    await db.processing_jobs.request_retry(
                        **{
                            (
                                "attachment_id"
                                if row["storage_kind"] == "attachment"
                                else "project_file_id"
                            ): row["source_id"]
                        }
                    )
                await db.catalog.update(
                    kind=row["storage_kind"],
                    source_id=row["source_id"],
                    data={},
                    expected_revision=row["version"],
                )
                await db.knowledge_events.reindex_project(row["project_id"])
                current = await self._get(db, document_ref, user_id, principal, write=True)
                result = await self._item_in_scope(db, current, user_id, principal)
                await self._commit(db)
                return result
        except DATA_ERRORS as error:
            raise DocumentCatalogServiceError(str(error)) from error

    async def link(
        self, *, document_ref: str, user_id: int, task_id: int, principal: Principal | None = None
    ) -> DocumentCatalogItemSchema:
        """Связывает проектный документ с задачей, не меняя владение и права."""
        return await self._link(
            document_ref=document_ref,
            user_id=user_id,
            task_id=task_id,
            principal=principal,
            remove=False,
        )

    async def unlink(
        self, *, document_ref: str, user_id: int, task_id: int, principal: Principal | None = None
    ) -> DocumentCatalogItemSchema:
        """Снимает независимую связь, сохраняя документ и оригинал."""
        return await self._link(
            document_ref=document_ref,
            user_id=user_id,
            task_id=task_id,
            principal=principal,
            remove=True,
        )

    async def _link(self, *, document_ref, user_id, task_id, principal, remove):
        self._write_token(principal)
        try:
            async with self.scope() as db:
                await db.tasks.get_for_update(task_id=task_id)
                principal = await self._refresh_principal(db, principal)
                grant = await db.access.ensure_task_access(task_id=task_id, user_id=user_id)
                self._scope(principal, "tasks:write")
                row = await self._get(db, document_ref, user_id, principal, write=True, lock=True)
                self._scope(principal, "tasks:write")
                if (
                    row["owner_task_id"] is not None
                    or row["project_id"] != grant.project_id
                    or grant.project_id is None
                ):
                    raise CatalogDocumentValidationError(
                        "Связать можно проектный документ с задачей того же проекта."
                    )
                await db.catalog.set_link(
                    kind=row["storage_kind"],
                    source_id=row["source_id"],
                    task_id=task_id,
                    remove=remove,
                )
                current = await self._get(db, document_ref, user_id, principal, write=True)
                result = await self._item_in_scope(db, current, user_id, principal)
                await self._commit(db)
                return result
        except DATA_ERRORS as error:
            raise DocumentCatalogServiceError(str(error)) from error

    async def prepare_download(
        self,
        *,
        document_ref: str,
        user_id: int,
        preview: bool = False,
        principal: Principal | None = None,
    ) -> AttachmentDownload:
        """Закрывает ACL DB-область до FileResponse и локальной миниатюры."""
        try:
            async with self.scope() as db:
                row = await self._get(db, document_ref, user_id, principal)
                if row["storage_kind"] == "document":
                    raise CatalogDocumentValidationError(
                        "У текстового документа нет загруженного оригинала."
                    )
            if preview:
                if not is_raster_previewable(row["original_name"], row["content_type"]):
                    raise CatalogDocumentNotFoundError()
                path = await self.storage.prepare_preview(row["storage_key"])
                if path is None:
                    raise CatalogDocumentNotFoundError()
                return AttachmentDownload(
                    path=path, media_type="image/jpeg", filename="preview.jpg", previewable=True
                )
            path = self.storage.resolve(row["storage_key"])
            if not await asyncio.to_thread(path.is_file):
                raise CatalogDocumentNotFoundError()
            return AttachmentDownload(
                path=path,
                media_type=row["content_type"],
                filename=row["original_name"],
                previewable=False,
            )
        except (*DATA_ERRORS, TaskAttachmentStorageError) as error:
            raise DocumentCatalogServiceError(str(error)) from error

    async def _get(self, db, ref, user_id, principal, *, write=False, lock=False):
        principal = await self._refresh_principal(db, principal)
        try:
            kind, identifier = decode_document_ref(ref)
        except ValueError as error:
            raise CatalogDocumentNotFoundError() from error
        row = await db.catalog.get(
            kind=kind,
            source_id=identifier,
            user_id=user_id,
            knowledge_enabled=self.knowledge_enabled,
            embedding_signature=self.embedding_signature,
        )
        if row is None:
            raise CatalogDocumentNotFoundError()
        if lock:
            # Task → original matches transfer and file-processing lock order.
            if row["owner_task_id"] is not None:
                await db.tasks.get_for_update(task_id=row["owner_task_id"])
            if not await db.catalog.lock_source(kind=kind, source_id=identifier):
                raise CatalogDocumentNotFoundError()
            principal = await self._refresh_principal(db, principal)
            row = await db.catalog.get(
                kind=kind,
                source_id=identifier,
                user_id=user_id,
                knowledge_enabled=self.knowledge_enabled,
                embedding_signature=self.embedding_signature,
            )
            if row is None:
                raise CatalogDocumentNotFoundError()
        self._scope(principal, self._write_scope(row) if write else self._read_scope(row))
        if write:
            await self._owner(
                db,
                project_id=None if row["owner_task_id"] else row["project_id"],
                owner_task_id=row["owner_task_id"],
                user_id=user_id,
                principal=principal,
            )
        return row

    async def _owner(self, db, *, project_id, owner_task_id, user_id, principal, lock=False):
        if lock and owner_task_id is not None:
            await db.tasks.get_for_update(task_id=owner_task_id)
        principal = await self._refresh_principal(db, principal)
        if owner_task_id is not None:
            grant = await db.access.ensure_task_access(task_id=owner_task_id, user_id=user_id)
            self._scope(principal, "tasks:write")
        else:
            grant = await db.access.ensure_project_access(project_id=project_id, user_id=user_id)
            self._scope(principal, "projects:write")
        db.access.require_write(grant)
        return grant

    async def _replay(self, db, previous, arguments_hash, user_id, principal):
        if previous.arguments_hash != arguments_hash:
            raise CatalogDocumentConflictError()
        row = await self._get(db, previous.document_ref, user_id, principal, write=True)
        return await self._item_in_scope(db, row, user_id, principal)

    async def _item_in_scope(self, db, row, user_id, principal):
        refs = await db.catalog.tasks([(row["storage_kind"], row["source_id"])], user_id=user_id)
        return self._item(row, refs.get((row["storage_kind"], row["source_id"]), []), principal)

    async def _commit(self, db):
        if self.commit_writes:
            await db.unit_of_work.commit()

    def _item(self, row, tasks, principal):
        principal = self._effective_principal(principal)
        kind = row["storage_kind"]
        ref = encode_document_ref(kind, row["source_id"])
        base = f"/api/v1/documents/{ref}"
        write = principal is None or principal.allows(self._write_scope(row))
        linked = any(item["relation"] == "linked" for item in tasks)
        can_write_task_links = principal is None or principal.allows("tasks:write")
        if principal is not None and not principal.allows("tasks:read"):
            tasks = []
        return DocumentCatalogItemSchema.model_validate(
            {
                "ref": ref,
                "source_kind": "text" if kind == "document" else "upload",
                "title": row["title"],
                "caption": row["caption"],
                "author": {"id": row["author_id"], "name": row["author_name"] or "Автор недоступен"}
                if row["author_id"]
                else None,
                "ownership": {
                    "kind": "task" if row["owner_task_id"] else "project",
                    "project_id": row["project_id"],
                    "owner_task_id": row["owner_task_id"],
                },
                "tasks": tasks,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "revision": row["version"],
                "format": row["format"],
                "states": {
                    "extraction": {
                        "status": row["extraction_status"],
                        "detail": row["extraction_detail"],
                    },
                    "fts": {"status": row["fts_status"], "detail": row["fts_detail"]},
                    "semantic": {
                        "status": row["semantic_status"],
                        "detail": row["semantic_detail"]
                        if row["semantic_status"] != "disabled"
                        else None,
                    },
                },
                "original": None
                if kind == "document"
                else {
                    "filename": row["original_name"],
                    "content_type": row["content_type"],
                    "size": row["size"],
                    "content_url": f"{base}/content",
                    "preview_url": f"{base}/preview"
                    if is_raster_previewable(row["original_name"], row["content_type"])
                    else None,
                },
                "text": {
                    "available": kind == "document"
                    or row["extraction_status"] in {"ready", "empty"},
                    "total_chars": row["text_total_chars"],
                    "rendition": "markdown" if kind == "document" else "extracted_text",
                    "content_hash": row["text_hash"],
                    "url": f"{base}/text",
                },
                "capabilities": {
                    "can_read": principal is None or principal.allows(self._read_scope(row)),
                    "can_edit": write,
                    "can_delete": write,
                    "can_retry": write
                    and (
                        kind != "document"
                        or self.knowledge_enabled
                        and row["project_id"] is not None
                    ),
                    "can_link": write and row["owner_task_id"] is None and can_write_task_links,
                    "can_detach": write and linked and can_write_task_links,
                },
            }
        )

    @staticmethod
    def _write_token(principal):
        if principal is not None and principal.api_key is None and not principal.can_write:
            raise InsufficientTokenScopeError()

    def _scope(self, principal, scope):
        principal = self._effective_principal(principal)
        if principal is not None:
            principal.require(scope)

    async def _refresh_principal(self, db, principal):
        if principal is None:
            return None
        if self._credentials is None:
            if self.commit_writes:
                raise NotAuthenticatedError()
            current = await db.auth.refresh_principal(principal)
        else:
            session_token, bearer_secret = self._credentials
            current = await db.auth.resolve_principal(
                session_token=session_token, bearer_secret=bearer_secret, record_usage=False
            )
        if current.user_id != principal.user_id:
            raise NotAuthenticatedError()
        self._current_principal = current
        return current

    def _effective_principal(self, principal):
        return (
            self._current_principal
            if principal is not None and self._current_principal is not None
            else principal
        )

    @staticmethod
    def _read_scope(row):
        return "tasks:read" if row["owner_task_id"] is not None else "projects:read"

    @staticmethod
    def _write_scope(row):
        return "tasks:write" if row["owner_task_id"] is not None else "projects:write"

    @staticmethod
    def _caption(value):
        value = value.strip() if value else None
        return value or None

    @staticmethod
    def _arguments_hash(value):
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    @staticmethod
    def _validate_text(title, content, caption, *, allow_empty_title=False):
        if not allow_empty_title and not title.strip():
            raise CatalogDocumentValidationError("Название не может быть пустым.")
        if any("\x00" in value for value in [title, content, caption or ""]):
            raise CatalogDocumentValidationError("Текст содержит недопустимый нулевой символ.")
        if len(title) > 255 or len(caption or "") > 2000:
            raise CatalogDocumentValidationError(
                "Название или примечание превышает допустимую длину."
            )
