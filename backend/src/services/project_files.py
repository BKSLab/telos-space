"""Сценарии файлов проекта с короткими областями БД."""

import asyncio
import hashlib
import logging
import mimetypes
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import PurePosixPath

from src.exceptions.access import AccessServiceError
from src.exceptions.auth import AuthServiceError
from src.exceptions.knowledge import KnowledgeIndexJobsRepositoryError
from src.exceptions.project_files import (
    ProjectFileNotFoundError,
    ProjectFilesRepositoryError,
    ProjectFilesServiceError,
    ProjectFileTooLargeError,
    ProjectFileUnsupportedError,
    ProjectFileValidationError,
)
from src.exceptions.storage import TaskAttachmentStorageError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.knowledge.catalog import SourceType
from src.knowledge.extract import INDEXABLE_EXTENSIONS
from src.repositories.knowledge_sources import KnowledgeSourcesRepository
from src.repositories.project_files import ProjectFilesRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.project_files import ProjectFileDetailSchema, ProjectFileSchema
from src.services.access import AccessService
from src.services.attachment_download import AttachmentDownload
from src.services.auth import AuthService
from src.storage.task_attachments import TaskAttachmentStorage
from src.utils.api_scopes import SCOPE_PROJECTS_READ

logger = logging.getLogger(__name__)
DATA_ERRORS = (
    ProjectFilesRepositoryError,
    KnowledgeIndexJobsRepositoryError,
    UnitOfWorkRepositoryError,
)


@dataclass(frozen=True, slots=True)
class ProjectFilesScope:
    """Явный набор зависимостей одной короткой операции."""

    files: ProjectFilesRepository
    sources: KnowledgeSourcesRepository
    access: AccessService
    auth: AuthService
    unit_of_work: UnitOfWork


ProjectFilesScopeFactory = Callable[[], AbstractAsyncContextManager[ProjectFilesScope]]


class ProjectFilesService:
    """Хранит оригинал до постановки обработки в существующий outbox."""

    def __init__(
        self,
        *,
        scope: ProjectFilesScopeFactory,
        storage: TaskAttachmentStorage,
        max_file_size: int,
        knowledge_enabled: bool,
    ) -> None:
        self.scope = scope
        self.storage = storage
        self.max_file_size = max_file_size
        self.knowledge_enabled = knowledge_enabled

    async def list_files(
        self, *, project_id: int, user_id: int, search: str | None, limit: int, offset: int
    ) -> list[ProjectFileSchema]:
        """Возвращает доступные файлы через общий FTS знаний.

        Args:
            project_id: Проект, ограничивающий выборку.
            user_id: Локальный пользователь запроса.
            search: Запрос по имени и извлечённому тексту.
            limit: Размер страницы.
            offset: Смещение страницы.
        Returns:
            Метаданные файлов без внутренних ключей хранения.
        Raises:
            AccessServiceError: Проект недоступен пользователю.
            ProjectFilesServiceError: Данные не удалось прочитать.
        """
        try:
            async with self.scope() as db:
                await db.access.ensure_project_access(project_id=project_id, user_id=user_id)
                ids = None
                if search and search.strip():
                    hits = await db.sources.search(
                        project_id,
                        search.strip(),
                        entity_type=SourceType.PROJECT_FILE,
                        limit=limit,
                        offset=offset,
                    )
                    ids = [int(hit["source_id"].split(":", 1)[1]) for hit in hits]
                    offset = 0
                rows = await db.files.get_for_project(
                    project_id=project_id, ids=ids, limit=limit, offset=offset
                )
                return [self._schema(row) for row in rows]
        except DATA_ERRORS as error:
            raise ProjectFilesServiceError(str(error)) from error

    async def upload(
        self, *, project_id: int, user_id: int, file_name: str, content: bytes
    ) -> ProjectFileSchema:
        """Сохраняет оригинал и метаданные до фонового извлечения.

        Args:
            project_id: Проект-владелец файла.
            user_id: Автор загрузки, определённый сервером.
            file_name: Имя оригинала из multipart-запроса.
            content: Содержимое, прочитанное с ограничением размера.
        Returns:
            Сохранённый файл с состоянием ожидания обработки.
        Raises:
            AccessServiceError: Доступ отсутствует или отозван во время записи.
            ProjectFileValidationError: Имя некорректно или файл пуст.
            ProjectFileTooLargeError: Превышен размер оригинала.
            ProjectFileUnsupportedError: Формат не поддерживается.
            ProjectFilesServiceError: Не удалось сохранить оригинал и метаданные.
        """
        name, extension = self._validate(file_name, content)
        storage_key = None
        committed = False
        try:
            async with self.scope() as db:
                await db.access.ensure_project_access(project_id=project_id, user_id=user_id)
            storage_key = await self.storage.save_in(
                PurePosixPath("projects", str(project_id), "files"), extension, content
            )
            content_hash = await asyncio.to_thread(lambda: hashlib.sha256(content).hexdigest())
            async with self.scope() as db:
                # Права могли измениться, пока оригинал записывался на диск.
                await db.access.ensure_project_access(project_id=project_id, user_id=user_id)
                row = await db.files.save(
                    {
                        "project_id": project_id,
                        "uploaded_by_user_id": user_id,
                        "original_name": name,
                        "content_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
                        "size": len(content),
                        "storage_key": storage_key,
                        "content_hash": content_hash,
                    }
                )
                await db.unit_of_work.commit()
                committed = True
                return self._schema(row)
        except BaseException as error:
            if storage_key is not None and not committed:
                try:
                    await self.storage.delete_if_exists(storage_key)
                except TaskAttachmentStorageError:
                    logger.warning(
                        "⚠️ Не удалось убрать оригинал незавершённой загрузки проекта %s.",
                        project_id,
                        exc_info=True,
                    )
            if isinstance(error, (*DATA_ERRORS, TaskAttachmentStorageError)):
                raise ProjectFilesServiceError(str(error)) from error
            raise

    async def get_file(
        self, *, project_id: int, file_id: int, user_id: int, offset: int, max_chars: int
    ) -> ProjectFileDetailSchema:
        """Читает сохранённый текст независимо от доступности AI API.

        Args:
            project_id: Проект-владелец файла.
            file_id: Идентификатор файла.
            user_id: Пользователь запроса.
            offset: Смещение в извлечённом тексте.
            max_chars: Максимальный размер фрагмента.
        Returns:
            Метаданные, фрагмент текста и позицию продолжения.
        Raises:
            AccessServiceError: Проект недоступен.
            ProjectFileNotFoundError: Файл отсутствует в этом проекте или удалён.
            ProjectFilesServiceError: Данные не удалось прочитать.
        """
        try:
            async with self.scope() as db:
                await db.access.ensure_project_access(project_id=project_id, user_id=user_id)
                row = await db.files.get_by_id(
                    project_id=project_id, file_id=file_id, offset=offset, max_chars=max_chars
                )
                if row is None:
                    raise ProjectFileNotFoundError(str(file_id))
                end = offset + len(row["text"])
                return ProjectFileDetailSchema(
                    **self._schema(row).model_dump(),
                    text=row["text"],
                    text_total_chars=row["text_total_chars"],
                    text_offset=offset,
                    next_offset=end if end < row["text_total_chars"] else None,
                )
        except DATA_ERRORS as error:
            raise ProjectFilesServiceError(str(error)) from error

    async def delete_file(self, *, project_id: int, file_id: int, user_id: int) -> None:
        """Закрывает чтение до устойчивой фоновой очистки оригинала.

        Args:
            project_id: Проект-владелец файла.
            file_id: Удаляемый файл.
            user_id: Пользователь запроса.
        Returns:
            None после фиксации tombstone и outbox одной транзакцией.
        Raises:
            AccessServiceError: Проект недоступен.
            ProjectFileNotFoundError: Активного файла в проекте нет.
            ProjectFilesServiceError: Удаление не удалось зафиксировать.
        """
        try:
            async with self.scope() as db:
                await db.access.ensure_project_access(project_id=project_id, user_id=user_id)
                if await db.files.get_by_id(project_id=project_id, file_id=file_id) is None:
                    raise ProjectFileNotFoundError(str(file_id))
                await db.files.mark_deleted(project_id=project_id, file_id=file_id)
                await db.unit_of_work.commit()
        except DATA_ERRORS as error:
            raise ProjectFilesServiceError(str(error)) from error

    async def retry(self, *, project_id: int, file_id: int, user_id: int) -> ProjectFileSchema:
        """Повторяет незавершённые этапы, сохраняя извлечённый текст.

        Args:
            project_id: Проект-владелец файла.
            file_id: Файл для повторной обработки.
            user_id: Пользователь запроса.
        Returns:
            Метаданные файла с состоянием ожидания.
        Raises:
            AccessServiceError: Проект недоступен.
            ProjectFileNotFoundError: Активного файла в проекте нет.
            ProjectFilesServiceError: Повтор не удалось поставить в очередь.
        """
        try:
            async with self.scope() as db:
                await db.access.ensure_project_access(project_id=project_id, user_id=user_id)
                row = await db.files.get_by_id(project_id=project_id, file_id=file_id)
                if row is None:
                    raise ProjectFileNotFoundError(str(file_id))
                await db.sources.reset_project_file(file_id=file_id)
                await db.files.request_retry(project_id=project_id, file_id=file_id)
                await db.unit_of_work.commit()
                row.update(
                    extraction_status="ready"
                    if row.get("extraction_status") == "ready"
                    else "pending",
                    extraction_detail=None,
                    index_status="pending",
                    index_detail=None,
                )
                return self._schema(row)
        except DATA_ERRORS as error:
            raise ProjectFilesServiceError(str(error)) from error

    async def prepare_download(
        self, *, project_id: int, file_id: int, session_token: str | None, bearer_secret: str | None
    ) -> AttachmentDownload:
        """Проверяет auth/access до передачи FileResponse.

        Args:
            project_id: Проект-владелец оригинала.
            file_id: Идентификатор оригинала.
            session_token: Cookie сессии пользователя.
            bearer_secret: Bearer, имеющий приоритет перед cookie.
        Returns:
            Неизменяемое описание скачивания после закрытия DB-области.
        Raises:
            AuthServiceError: Учётные данные или scopes не допускают чтение.
            AccessServiceError: Проект недоступен.
            ProjectFileNotFoundError: Оригинал отсутствует или удалён.
            ProjectFilesServiceError: Метаданные не удалось прочитать.
        """
        try:
            async with self.scope() as db:
                principal = await db.auth.resolve_principal(
                    session_token=session_token, bearer_secret=bearer_secret
                )
                if principal.api_key is not None:
                    principal.require(SCOPE_PROJECTS_READ)
                await db.access.ensure_project_access(
                    project_id=project_id, user_id=principal.user_id
                )
                row = await db.files.get_by_id(project_id=project_id, file_id=file_id, max_chars=0)
                if row is None:
                    raise ProjectFileNotFoundError(str(file_id))
            path = await asyncio.to_thread(self.storage.resolve, row["storage_key"])
            return AttachmentDownload(
                path=path,
                media_type=row["content_type"],
                filename=row["original_name"],
                previewable=False,
            )
        except TaskAttachmentStorageError as error:
            raise ProjectFileNotFoundError(str(error)) from error
        except DATA_ERRORS as error:
            raise ProjectFilesServiceError(str(error)) from error

    def _schema(self, row: dict) -> ProjectFileSchema:
        data = dict(row)
        data["extraction_status"] = row.get("extraction_status") or "pending"
        data["index_status"] = (
            (row.get("index_status") or "pending") if self.knowledge_enabled else "disabled"
        )
        for field in ("extraction_detail", "index_detail", "indexed_at", "summary"):
            data.setdefault(field, None)
        data["content_url"] = f"/api/v1/projects/{row['project_id']}/files/{row['id']}/content"
        return ProjectFileSchema.model_validate(data)

    def _validate(self, name: str, content: bytes) -> tuple[str, str]:
        safe_name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if (
            not content
            or not safe_name
            or len(safe_name) > 512
            or any(ord(char) < 32 for char in safe_name)
        ):
            raise ProjectFileValidationError("Пустой файл или некорректное имя.")
        if len(content) > self.max_file_size:
            raise ProjectFileTooLargeError(str(len(content)))
        extension = PurePosixPath(safe_name).suffix.lower()
        if extension not in INDEXABLE_EXTENSIONS:
            raise ProjectFileUnsupportedError(extension)
        return safe_name, extension


PROJECT_FILE_DOWNLOAD_ERRORS = (AuthServiceError, AccessServiceError, ProjectFilesServiceError)
