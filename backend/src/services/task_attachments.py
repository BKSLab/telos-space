from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path

from src.db.models.knowledge_index_jobs import KnowledgeEntityType
from src.db.models.task_attachments import TaskAttachment
from src.db.models.tasks import Task
from src.exceptions.knowledge import KnowledgeEventsServiceError
from src.exceptions.storage import TaskAttachmentStorageError
from src.exceptions.task_attachments import (
    TaskAttachmentLimitError,
    TaskAttachmentNotFoundError,
    TaskAttachmentsRepositoryError,
    TaskAttachmentsServiceError,
    TaskAttachmentTooLargeError,
    TaskAttachmentUnsupportedTypeError,
    TaskAttachmentValidationError,
)
from src.exceptions.tasks import TaskNotFoundError, TasksRepositoryError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.repositories.task_attachments import TaskAttachmentsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.file_metadata import normalize_caption
from src.schemas.task_attachments import TaskAttachmentSchema
from src.services.knowledge_events import KnowledgeEvents
from src.storage.task_attachments import TaskAttachmentStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TaskAttachmentContent:
    """Проверенный файл и HTTP-метаданные для выдачи клиенту."""

    path: Path
    original_name: str
    content_type: str
    previewable: bool


@dataclass(frozen=True, slots=True)
class StoredAttachment:
    """Сохранённый файл вместе с ключом его физического хранения.

    Ключ нужен только владельцу составного сценария для компенсации и
    наружу, в HTTP-контракт, не попадает.
    """

    attachment: TaskAttachmentSchema
    storage_key: str


class TaskAttachmentsService:
    """Сервис сценариев работы с файлами задач."""

    MAX_FILE_SIZE = 10 * 1024 * 1024
    MAX_FILES_PER_TASK = 20
    ALLOWED_EXTENSIONS = frozenset(
        {
            ".7z",
            ".avif",
            ".bmp",
            ".csv",
            ".doc",
            ".docx",
            ".gif",
            ".gz",
            ".jpeg",
            ".jpg",
            ".log",
            ".md",
            ".odp",
            ".ods",
            ".odt",
            ".pdf",
            ".png",
            ".ppt",
            ".pptx",
            ".rar",
            ".rtf",
            ".tar",
            ".txt",
            ".webp",
            ".xls",
            ".xlsm",
            ".xlsx",
            ".zip",
        }
    )
    PREVIEWABLE_EXTENSIONS = frozenset({".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"})

    def __init__(
        self,
        attachments_repository: TaskAttachmentsRepository,
        tasks_repository: TasksRepository,
        storage: TaskAttachmentStorage,
        unit_of_work: UnitOfWork,
        knowledge_events: KnowledgeEvents,
        knowledge_enabled: bool = True,
    ) -> None:
        self.attachments_repository = attachments_repository
        self.tasks_repository = tasks_repository
        self.storage = storage
        self.unit_of_work = unit_of_work
        self.knowledge_events = knowledge_events
        self.knowledge_enabled = knowledge_enabled

    @property
    def max_file_size(self) -> int:
        """Возвращает максимальный размер чтения multipart-файла."""
        return self.MAX_FILE_SIZE

    async def get_attachments(self, task_id: int) -> list[TaskAttachmentSchema]:
        """Возвращает файлы существующей задачи.

        Args:
            task_id: Идентификатор задачи.

        Returns:
            Метаданные файлов в хронологическом порядке.

        Raises:
            TaskNotFoundError: Если задача не найдена.
            TaskAttachmentsServiceError: Если получить файлы не удалось.
        """
        try:
            await self._ensure_task_exists(task_id)
            attachments = await self.attachments_repository.get_for_task(task_id=task_id)
            states = (
                await self.attachments_repository.get_processing_states(
                    [item.id for item in attachments]
                )
                if attachments
                else {}
            )
            result = [self._to_schema(attachment) for attachment in attachments]
            for item in result:
                state = states.get(item.id, {})
                if not self.knowledge_enabled:
                    state.update(index_status="disabled", index_detail=None)
                for key, value in state.items():
                    setattr(item, key, value)
            return result
        except (TaskAttachmentsRepositoryError, TasksRepositoryError) as error:
            logger.error("❌ Ошибка получения файлов задачи id=%s.", task_id, exc_info=True)
            raise TaskAttachmentsServiceError(str(error)) from error

    async def save_in_transaction(
        self,
        *,
        task_id: int,
        file_name: str,
        content_type: str | None,
        content: bytes,
        index_for_knowledge: bool = True,
        caption: str | None = None,
        uploaded_by_user_id: int | None = None,
        prepared_storage_key: str | None = None,
        task: Task | None = None,
        current_file_count: int | None = None,
    ) -> StoredAttachment:
        """Сохраняет файл, оставляя финальный commit владельцу сценария.

        Наружу отдаётся ещё и ключ хранения: откат транзакции уберёт
        метаданные, но не физический файл, и владелец сценария должен
        знать, что именно ему компенсировать.

        Args:
            task_id: Идентификатор задачи.
            file_name: Исходное имя из multipart-запроса.
            content_type: MIME-тип, заявленный клиентом.
            content: Прочитанное с ограничением бинарное содержимое.
            index_for_knowledge: Ставить ли файл в очередь индексации.

        Returns:
            Метаданные созданного файла и ключ его физического хранения.

        Raises:
            TaskNotFoundError: Если задача не найдена.
            TaskAttachmentValidationError: Если файл пуст или имя некорректно.
            TaskAttachmentTooLargeError: Если файл превышает 10 МБ.
            TaskAttachmentUnsupportedTypeError: Если расширение запрещено.
            TaskAttachmentLimitError: Если к задаче уже прикреплено 20 файлов.
            TaskAttachmentsServiceError: Если сохранить файл не удалось.
        """
        storage_key: str | None = None
        try:
            task = task if task is not None else await self._ensure_task_exists(task_id)
            if task.id != task_id:
                raise TaskAttachmentValidationError("Оригинал принадлежит другой задаче.")
            if (
                current_file_count
                if current_file_count is not None
                else await self.attachments_repository.get_count_for_task(task_id)
            ) >= self.MAX_FILES_PER_TASK:
                raise TaskAttachmentLimitError(max_files=self.MAX_FILES_PER_TASK)
            original_name, extension, effective_content_type = self._validate_file(
                file_name=file_name,
                content_type=content_type,
                content=content,
                max_file_size=self.MAX_FILE_SIZE,
            )
            try:
                caption = normalize_caption(caption)
            except ValueError as error:
                raise TaskAttachmentValidationError(str(error)) from error
            storage_key = prepared_storage_key
            if storage_key is None:
                storage_key = await self.storage.save(
                    task_id=task_id, extension=extension, content=content
                )
            attachment = await self.attachments_repository.save(
                task_id=task_id,
                original_name=original_name,
                storage_key=storage_key,
                content_type=effective_content_type,
                size=len(content),
                caption=caption,
                uploaded_by_user_id=uploaded_by_user_id,
            )
            logger.info(
                "✅ Файл %r добавлен к задаче id=%s. Размер: %s байт.",
                original_name,
                task_id,
                len(content),
            )
            if index_for_knowledge:
                await self.knowledge_events.upsert(
                    project_id=task.project_id,
                    entity_type=KnowledgeEntityType.ATTACHMENT,
                    entity_id=attachment.id,
                )
            schema = self._to_schema(attachment)
            if task.project_id is None or not self.knowledge_enabled:
                schema.index_status = "disabled"
            return StoredAttachment(attachment=schema, storage_key=storage_key)
        except (
            TaskAttachmentStorageError,
            TaskAttachmentsRepositoryError,
            TasksRepositoryError,
            KnowledgeEventsServiceError,
            UnitOfWorkRepositoryError,
        ) as error:
            if storage_key is not None:
                try:
                    await self.storage.delete(storage_key)
                except TaskAttachmentStorageError:
                    logger.warning(
                        "⚠️ Не удалось компенсирующе удалить файл %s.",
                        storage_key,
                        exc_info=True,
                    )
            logger.error("❌ Ошибка загрузки файла задачи id=%s.", task_id, exc_info=True)
            raise TaskAttachmentsServiceError(str(error)) from error

    async def upload_attachment(
        self,
        *,
        task_id: int,
        file_name: str,
        content_type: str | None,
        content: bytes,
        index_for_knowledge: bool = True,
        caption: str | None = None,
        uploaded_by_user_id: int | None = None,
    ) -> TaskAttachmentSchema:
        """Проверяет и сохраняет новый файл задачи как самостоятельную запись.

        Args:
            task_id: Идентификатор задачи.
            file_name: Исходное имя из multipart-запроса.
            content_type: MIME-тип, заявленный клиентом.
            content: Прочитанное с ограничением бинарное содержимое.
            index_for_knowledge: Ставить ли файл в очередь индексации.

        Returns:
            Метаданные созданного файла.

        Raises:
            TaskNotFoundError: Если задача не найдена.
            TaskAttachmentValidationError: Если файл пуст или имя некорректно.
            TaskAttachmentTooLargeError: Если файл превышает 10 МБ.
            TaskAttachmentUnsupportedTypeError: Если расширение запрещено.
            TaskAttachmentLimitError: Если к задаче уже прикреплено 20 файлов.
            TaskAttachmentsServiceError: Если сохранить файл не удалось.
        """
        stored = await self.save_in_transaction(
            task_id=task_id,
            file_name=file_name,
            content_type=content_type,
            content=content,
            index_for_knowledge=index_for_knowledge,
            caption=caption,
            uploaded_by_user_id=uploaded_by_user_id,
        )
        try:
            await self.unit_of_work.commit()
        except UnitOfWorkRepositoryError as error:
            await self._remove_file(stored.storage_key)
            logger.error("❌ Ошибка загрузки файла задачи id=%s.", task_id, exc_info=True)
            raise TaskAttachmentsServiceError(str(error)) from error
        return stored.attachment

    async def _remove_file(self, storage_key: str) -> None:
        """Best-effort удаляет физический файл после отката записи."""
        try:
            await self.storage.delete(storage_key)
        except TaskAttachmentStorageError:
            logger.warning(
                "⚠️ Не удалось компенсирующе удалить файл %s.",
                storage_key,
                exc_info=True,
            )

    async def get_attachment_content(
        self,
        *,
        task_id: int,
        attachment_id: int,
    ) -> TaskAttachmentContent:
        """Возвращает проверенный путь и метаданные файла для HTTP-ответа.

        Args:
            task_id: Идентификатор задачи.
            attachment_id: Идентификатор файла.

        Returns:
            Данные для безопасной выдачи через ``FileResponse``.

        Raises:
            TaskAttachmentNotFoundError: Если запись не найдена в этой задаче.
            TaskAttachmentsServiceError: Если файл недоступен в хранилище.
        """
        try:
            attachment = await self._get_attachment(task_id, attachment_id)
            return TaskAttachmentContent(
                path=self.storage.resolve(attachment.storage_key),
                original_name=attachment.original_name,
                content_type=attachment.content_type,
                previewable=self._is_previewable(
                    attachment.original_name,
                    attachment.content_type,
                ),
            )
        except (TaskAttachmentStorageError, TaskAttachmentsRepositoryError) as error:
            logger.error("❌ Файл задачи id=%s недоступен.", attachment_id, exc_info=True)
            raise TaskAttachmentsServiceError(str(error)) from error

    async def delete_attachment(self, *, task_id: int, attachment_id: int) -> None:
        """Удаляет метаданные и физический файл задачи.

        Args:
            task_id: Идентификатор задачи.
            attachment_id: Идентификатор файла.

        Raises:
            TaskAttachmentNotFoundError: Если файл не найден в этой задаче.
            TaskAttachmentsServiceError: Если удалить метаданные не удалось.
        """
        try:
            task = await self.tasks_repository.get_for_update(task_id=task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            attachment = await self._get_attachment(task_id, attachment_id)
            storage_key = attachment.storage_key
            await self.attachments_repository.delete(attachment=attachment)
            await self.knowledge_events.delete(
                project_id=task.project_id,
                entity_type=KnowledgeEntityType.ATTACHMENT,
                entity_id=attachment_id,
            )
            await self.unit_of_work.commit()
            try:
                await self.storage.delete(storage_key)
            except TaskAttachmentStorageError:
                logger.warning(
                    "⚠️ Метаданные файла id=%s удалены, но storage key %s остался.",
                    attachment_id,
                    storage_key,
                    exc_info=True,
                )
        except (
            TaskAttachmentsRepositoryError,
            TasksRepositoryError,
            KnowledgeEventsServiceError,
            UnitOfWorkRepositoryError,
        ) as error:
            logger.error("❌ Ошибка удаления файла задачи id=%s.", attachment_id, exc_info=True)
            raise TaskAttachmentsServiceError(str(error)) from error

    async def _ensure_task_exists(self, task_id: int) -> Task:
        """Возвращает существующую задачу либо поднимает 404."""
        task = await self.tasks_repository.get_by_id(task_id=task_id)
        if task is None:
            raise TaskNotFoundError(task_id=task_id)
        return task

    async def _get_attachment(
        self,
        task_id: int,
        attachment_id: int,
    ) -> TaskAttachment:
        """Возвращает файл в пределах задачи либо поднимает 404."""
        attachment = await self.attachments_repository.get_by_id_for_task(
            attachment_id=attachment_id,
            task_id=task_id,
        )
        if attachment is None:
            raise TaskAttachmentNotFoundError(attachment_id=attachment_id)
        return attachment

    @classmethod
    def validate_file(
        cls,
        *,
        file_name: str,
        content_type: str | None,
        content: bytes,
        max_file_size: int | None = None,
    ) -> tuple[str, str, str]:
        """Проверяет загрузку до выбора задачи, используя общие ограничения файлов."""
        return cls._validate_file(
            file_name=file_name,
            content_type=content_type,
            content=content,
            max_file_size=max_file_size,
        )

    @classmethod
    def _validate_file(
        cls,
        *,
        file_name: str,
        content_type: str | None,
        content: bytes,
        max_file_size: int | None = None,
    ) -> tuple[str, str, str]:
        """Нормализует имя и проверяет размер и расширение файла."""
        if not content:
            raise TaskAttachmentValidationError("Нельзя загрузить пустой файл.")
        limit = cls.MAX_FILE_SIZE if max_file_size is None else max_file_size
        if len(content) > limit:
            raise TaskAttachmentTooLargeError(max_size_mb=limit // 1024 // 1024)
        original_name = Path((file_name or "").replace("\\", "/")).name.strip()
        if not original_name or "\x00" in original_name:
            raise TaskAttachmentValidationError("Имя файла некорректно.")
        if len(original_name) > 512:
            raise TaskAttachmentValidationError("Имя файла длиннее 512 символов.")
        extension = Path(original_name).suffix.lower()
        if extension not in cls.ALLOWED_EXTENSIONS:
            raise TaskAttachmentUnsupportedTypeError(extension=extension)
        guessed_type = mimetypes.guess_type(original_name)[0]
        effective_content_type = (
            (content_type or guessed_type or "application/octet-stream")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        return original_name, extension, effective_content_type

    def _to_schema(self, attachment: TaskAttachment) -> TaskAttachmentSchema:
        """Дополняет ORM-модель вычисляемыми полями API."""
        return TaskAttachmentSchema(
            id=attachment.id,
            task_id=attachment.task_id,
            original_name=attachment.original_name,
            caption=getattr(attachment, "caption", None),
            uploaded_by_user_id=getattr(attachment, "uploaded_by_user_id", None),
            content_type=attachment.content_type,
            size=attachment.size,
            created_at=attachment.created_at,
            content_url=(f"/api/v1/tasks/{attachment.task_id}/attachments/{attachment.id}/content"),
            previewable=self._is_previewable(
                attachment.original_name,
                attachment.content_type,
            ),
        )

    def _is_previewable(self, file_name: str, content_type: str) -> bool:
        """Разрешает inline-preview только безопасных растровых изображений."""
        return Path(
            file_name
        ).suffix.lower() in self.PREVIEWABLE_EXTENSIONS and content_type.startswith("image/")
