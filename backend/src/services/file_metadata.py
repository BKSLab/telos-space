"""Короткие сценарии загрузки/подписи задач и защищённых миниатюр."""

import logging

from src.exceptions.access import AccessServiceError
from src.exceptions.auth import AuthServiceError
from src.exceptions.file_processing import FileProcessingRepositoryError
from src.exceptions.knowledge import KnowledgeEventsServiceError
from src.exceptions.project_files import (
    ProjectFileNotFoundError,
    ProjectFilesRepositoryError,
    ProjectFilesServiceError,
)
from src.exceptions.storage import TaskAttachmentStorageError
from src.exceptions.task_attachments import (
    TaskAttachmentNotFoundError,
    TaskAttachmentsRepositoryError,
    TaskAttachmentsServiceError,
    TaskAttachmentValidationError,
)
from src.exceptions.tasks import TaskNotFoundError, TasksRepositoryError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.schemas.file_metadata import normalize_caption
from src.schemas.task_attachments import TaskAttachmentSchema
from src.services.attachment_download import AttachmentDownload
from src.services.file_comments import FileCommentsScopeFactory, authorize_task_write
from src.services.task_attachments import TaskAttachmentsService
from src.storage.task_attachments import TaskAttachmentStorage
from src.utils.api_scopes import SCOPE_PROJECTS_READ, SCOPE_TASKS_READ
from src.utils.file_previews import is_raster_previewable

TASK_FILE_DATA_ERRORS = (
    TaskAttachmentsRepositoryError,
    TasksRepositoryError,
    UnitOfWorkRepositoryError,
    KnowledgeEventsServiceError,
    FileProcessingRepositoryError,
)
logger = logging.getLogger(__name__)


class TaskFileMutationsService:
    """Не удерживает SQL-транзакцию во время записи оригинала и thumbnail."""

    max_file_size = TaskAttachmentsService.MAX_FILE_SIZE

    def __init__(self, *, scope: FileCommentsScopeFactory, storage: TaskAttachmentStorage) -> None:
        self.scope = scope
        self.storage = storage

    async def upload(
        self,
        *,
        task_id: int,
        file_name: str,
        content_type: str | None,
        content: bytes,
        caption: str | None,
        session_token: str | None,
        bearer_secret: str | None,
    ) -> TaskAttachmentSchema:
        """Готовит оригинал на диске и атомарно записывает серверные метаданные.

        Args:
            task_id: Текущая задача назначения.
            file_name: Имя multipart-файла.
            content_type: Заявленный MIME, нормализуемый общей политикой файлов.
            content: Ограниченное содержимое оригинала.
            caption: Подпись пользователя либо None.
            session_token: Cookie сессии.
            bearer_secret: Bearer с приоритетом перед cookie.
        Returns:
            Метаданные файла с серверным автором загрузки.
        Raises:
            AuthServiceError: Автор или scopes недействительны.
            AccessServiceError: Задача недоступна.
            TaskAttachmentsServiceError: Валидация или сохранение не удались.
        """
        name, extension, mime = TaskAttachmentsService.validate_file(
            file_name=file_name, content_type=content_type, content=content
        )
        caption = self._caption(caption)
        storage_key = None
        committed = False
        try:
            async with self.scope() as db:
                await authorize_task_write(
                    db,
                    task_id=task_id,
                    session_token=session_token,
                    bearer_secret=bearer_secret,
                    comment=False,
                    with_files=True,
                )
            storage_key = await self.storage.save(
                task_id=task_id, extension=extension, content=content
            )
            if is_raster_previewable(name, mime):
                await self.storage.prepare_preview(storage_key)
            async with self.scope() as db:
                await authorize_task_write(
                    db,
                    task_id=task_id,
                    session_token=session_token,
                    bearer_secret=bearer_secret,
                    comment=False,
                    with_files=True,
                )
                task = await db.tasks.get_for_update(task_id=task_id)
                if task is None:
                    raise TaskNotFoundError(task_id)
                principal = await authorize_task_write(
                    db,
                    task_id=task_id,
                    session_token=session_token,
                    bearer_secret=bearer_secret,
                    comment=False,
                    with_files=True,
                )
                count = await db.files.get_count_for_task(task_id)
                result = await db.attachments.save_in_transaction(
                    task_id=task_id,
                    file_name=name,
                    content_type=mime,
                    content=content,
                    caption=caption,
                    uploaded_by_user_id=principal.user_id,
                    prepared_storage_key=storage_key,
                    task=task,
                    current_file_count=count,
                )
                await db.unit_of_work.commit()
                committed = True
                return result.attachment
        except (*TASK_FILE_DATA_ERRORS, TaskAttachmentStorageError) as error:
            raise TaskAttachmentsServiceError(str(error)) from error
        finally:
            if storage_key is not None and not committed:
                try:
                    await self.storage.delete_if_exists(storage_key)
                except TaskAttachmentStorageError:
                    logger.warning(
                        "⚠️ Не удалось убрать оригинал незавершённой загрузки задачи %s.",
                        task_id,
                        exc_info=True,
                    )

    async def update_caption(
        self,
        *,
        task_id: int,
        attachment_id: int,
        caption: str | None,
        session_token: str | None,
        bearer_secret: str | None,
    ) -> TaskAttachmentSchema:
        """Меняет только подпись текущего файла задачи.

        Args:
            task_id: Задача-владелец оригинала.
            attachment_id: Файл для изменения подписи.
            caption: Новая подпись; None очищает её.
            session_token: Cookie сессии.
            bearer_secret: Bearer-заголовок.
        Returns:
            Изменённые метаданные того же оригинала.
        Raises:
            TaskAttachmentNotFoundError: Файл отсутствует в этой задаче.
            AuthServiceError: Учётные данные или scope недействительны.
            AccessServiceError: Задача недоступна для записи.
            TaskAttachmentsServiceError: Подпись некорректна или запись не удалась.
        """
        caption = self._caption(caption)
        try:
            async with self.scope() as db:
                await authorize_task_write(
                    db,
                    task_id=task_id,
                    session_token=session_token,
                    bearer_secret=bearer_secret,
                    comment=False,
                    with_files=True,
                )
                task = await db.tasks.get_for_update(task_id=task_id)
                if task is None:
                    raise TaskNotFoundError(task_id)
                await authorize_task_write(
                    db,
                    task_id=task_id,
                    session_token=session_token,
                    bearer_secret=bearer_secret,
                    comment=False,
                    with_files=True,
                )
                row = await db.files.update_caption(
                    task_id=task_id, attachment_id=attachment_id, caption=caption
                )
                if row is None:
                    raise TaskAttachmentNotFoundError(attachment_id)
                await db.knowledge_events.reindex_project(task.project_id)
                result = TaskAttachmentSchema.model_validate(row)
                states = await db.files.get_processing_states([row.id])
                state = states.get(row.id, {})
                if not db.attachments.knowledge_enabled:
                    state.update(index_status="disabled", index_detail=None)
                for key, value in state.items():
                    setattr(result, key, value)
                await db.unit_of_work.commit()
                return result
        except TASK_FILE_DATA_ERRORS as error:
            raise TaskAttachmentsServiceError(str(error)) from error

    @staticmethod
    def _caption(value: str | None) -> str | None:
        try:
            return normalize_caption(value)
        except ValueError as error:
            raise TaskAttachmentValidationError(str(error)) from error

    async def retry(
        self,
        *,
        task_id: int,
        attachment_id: int,
        session_token: str | None,
        bearer_secret: str | None,
    ) -> TaskAttachmentSchema:
        """Устойчиво повторяет обработку, сохраняя готовый кеш и активный lease.

        Args:
            task_id: Текущая задача оригинала.
            attachment_id: Файл для повторной обработки.
            session_token: Cookie сессии.
            bearer_secret: Bearer с приоритетом перед cookie.
        Returns:
            Метаданные оригинала с актуальными состояниями обработки.
        Raises:
            AuthServiceError: Автор запроса или tasks:write недействительны.
            AccessServiceError: Задача недоступна для записи.
            TaskAttachmentNotFoundError: Файла в этой задаче нет.
            TaskAttachmentsServiceError: Повтор не удалось поставить в очередь.
        """
        try:
            async with self.scope() as db:
                await authorize_task_write(
                    db,
                    task_id=task_id,
                    session_token=session_token,
                    bearer_secret=bearer_secret,
                    comment=False,
                    with_files=True,
                )
                task = await db.tasks.get_for_update(task_id=task_id)
                if task is None:
                    raise TaskNotFoundError(task_id)
                await authorize_task_write(
                    db,
                    task_id=task_id,
                    session_token=session_token,
                    bearer_secret=bearer_secret,
                    comment=False,
                    with_files=True,
                )
                row = await db.files.get_by_id_for_task(
                    task_id=task_id, attachment_id=attachment_id
                )
                if row is None:
                    raise TaskAttachmentNotFoundError(attachment_id)
                if not await db.processing_jobs.request_retry(attachment_id=attachment_id):
                    raise TaskAttachmentNotFoundError(attachment_id)
                await db.knowledge_events.reindex_project(task.project_id)
                states = await db.files.get_processing_states([attachment_id])
                result = TaskAttachmentSchema.model_validate(row)
                state = states.get(attachment_id, {})
                if not db.attachments.knowledge_enabled:
                    state.update(index_status="disabled", index_detail=None)
                for key, value in state.items():
                    setattr(result, key, value)
                await db.unit_of_work.commit()
                return result
        except TASK_FILE_DATA_ERRORS as error:
            raise TaskAttachmentsServiceError(str(error)) from error


class FilePreviewsService:
    """Проверяет текущие права до локального декодирования и выдачи JPEG."""

    def __init__(self, *, scope: FileCommentsScopeFactory, storage: TaskAttachmentStorage) -> None:
        self.scope = scope
        self.storage = storage

    async def prepare_task(
        self,
        *,
        task_id: int,
        attachment_id: int,
        session_token: str | None,
        bearer_secret: str | None,
    ) -> AttachmentDownload:
        """Готовит миниатюру оригинала доступной задачи после закрытия БД.

        Args:
            task_id: Текущая задача оригинала.
            attachment_id: Идентификатор оригинала.
            session_token: Cookie сессии.
            bearer_secret: Bearer с приоритетом перед cookie.
        Returns:
            Путь и безопасные метаданные JPEG без ссылок на сессию.
        Raises:
            AuthServiceError: Пользователь или scope недействительны.
            AccessServiceError: Текущая задача недоступна.
            TaskAttachmentsServiceError: Оригинал отсутствует или не является безопасным растром.
        """
        try:
            async with self.scope() as db:
                principal = await db.auth.resolve_principal(
                    session_token=session_token, bearer_secret=bearer_secret
                )
                principal.require(SCOPE_TASKS_READ)
                await db.access.ensure_task_access(task_id=task_id, user_id=principal.user_id)
                row = await db.files.get_by_id_for_task(
                    task_id=task_id, attachment_id=attachment_id
                )
                if row is None:
                    raise TaskAttachmentNotFoundError(attachment_id)
                key, name, mime = row.storage_key, row.original_name, row.content_type
            if not is_raster_previewable(name, mime):
                raise TaskAttachmentNotFoundError(attachment_id)
            path = await self.storage.prepare_preview(key)
            if path is None:
                raise TaskAttachmentNotFoundError(attachment_id)
            return AttachmentDownload(
                path=path, media_type="image/jpeg", filename="preview.jpg", previewable=True
            )
        except TaskAttachmentStorageError as error:
            raise TaskAttachmentNotFoundError(attachment_id) from error
        except TASK_FILE_DATA_ERRORS as error:
            raise TaskAttachmentsServiceError(str(error)) from error

    async def prepare_project(
        self,
        *,
        project_id: int,
        file_id: int,
        session_token: str | None,
        bearer_secret: str | None,
    ) -> AttachmentDownload:
        """Готовит миниатюру активного файла доступного проекта.

        Args:
            project_id: Проект-владелец оригинала.
            file_id: Активный оригинал проекта.
            session_token: Cookie пользователя.
            bearer_secret: Bearer-заголовок.
        Returns:
            Безопасные метаданные локальной JPEG-миниатюры.
        Raises:
            AuthServiceError: Учётные данные или scope недействительны.
            AccessServiceError: Проект недоступен.
            ProjectFilesServiceError: Оригинал отсутствует или не является безопасным растром.
        """
        try:
            async with self.scope() as db:
                principal = await db.auth.resolve_principal(
                    session_token=session_token, bearer_secret=bearer_secret
                )
                principal.require(SCOPE_PROJECTS_READ)
                await db.access.ensure_project_access(
                    project_id=project_id, user_id=principal.user_id
                )
                row = await db.project_files.get_by_id(
                    project_id=project_id, file_id=file_id, max_chars=0
                )
                if row is None:
                    raise ProjectFileNotFoundError(str(file_id))
            if not is_raster_previewable(row["original_name"], row["content_type"]):
                raise ProjectFileNotFoundError(str(file_id))
            path = await self.storage.prepare_preview(row["storage_key"])
            if path is None:
                raise ProjectFileNotFoundError(str(file_id))
            return AttachmentDownload(
                path=path, media_type="image/jpeg", filename="preview.jpg", previewable=True
            )
        except TaskAttachmentStorageError as error:
            raise ProjectFileNotFoundError(str(file_id)) from error
        except ProjectFilesRepositoryError as error:
            raise ProjectFilesServiceError(str(error)) from error


FILE_PREVIEW_ERRORS = (
    AuthServiceError,
    AccessServiceError,
    TaskAttachmentsServiceError,
    ProjectFilesServiceError,
)
