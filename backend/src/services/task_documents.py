from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from src.clients.vision import VisionCapability
from src.exceptions.ai import AiProviderError
from src.exceptions.base import ServiceError
from src.exceptions.clients import ClientError
from src.exceptions.document_links import DocumentLinksServiceError
from src.exceptions.documents import DocumentsServiceError
from src.exceptions.knowledge import KnowledgeIndexJobsRepositoryError
from src.exceptions.storage import StorageError
from src.exceptions.task_attachments import TaskAttachmentsServiceError
from src.exceptions.task_documents import (
    TaskDocumentStepFailedError,
    TaskDocumentTaskNotFoundError,
    TaskDocumentTooLargeError,
    TaskDocumentUnsupportedFormatError,
    TaskDocumentValidationError,
)
from src.exceptions.tasks import TasksRepositoryError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.knowledge.extract import INDEXABLE_EXTENSIONS, extract_indexable_text
from src.schemas.task_documents import TaskDocumentImportSchema
from src.services.db_scope import (
    TaskDocumentImportScope,
    TaskDocumentImportScopeFactory,
)
from src.storage.task_attachments import TaskAttachmentStorage

logger = logging.getLogger(__name__)

UNSUPPORTED_FORMAT_MESSAGE = (
    "Этот формат нельзя преобразовать в документ проекта. "
    "Поддерживаются PDF, DOCX, TXT, Markdown, CSV, Excel и изображения."
)

# Ошибки вложенных сервисов, которые верхний сервис обязан преобразовать
# в свою иерархию. Перечислены явно: широкий `ApplicationError` скрыл бы
# появление нового семейства у зависимости.
NESTED_SERVICE_ERRORS = (
    TaskAttachmentsServiceError,
    DocumentsServiceError,
    DocumentLinksServiceError,
)


class TaskDocumentImportService:
    """Импортирует файл как оригинал задачи и текстовый документ проекта.

    Наружу сервис отдаёт только собственные ошибки: эндпоинту не нужно
    знать, из скольких вложенных сервисов собран сценарий.
    """

    def __init__(
        self,
        *,
        scope: TaskDocumentImportScopeFactory,
        attachment_storage: TaskAttachmentStorage,
        vision: VisionCapability,
        max_file_size: int,
    ) -> None:
        """Создаёт сервис импорта документа в задачу.

        Args:
            scope: Фабрика короткой области работы с базой. Между чтением
                задачи и записью результата стоит распознавание файла,
                поэтому соединение на это время удерживаться не должно.
            attachment_storage: Хранилище файлов задач для компенсации.
            vision: Способность распознавать изображения.
            max_file_size: Предел размера исходного файла.
        """
        self.scope = scope
        self.attachment_storage = attachment_storage
        self.vision = vision
        self.max_file_size = max_file_size

    async def import_file(
        self,
        *,
        task_id: int,
        user_id: int,
        file_name: str,
        content_type: str | None,
        content: bytes,
        caption: str | None = None,
    ) -> TaskDocumentImportSchema:
        """Сохраняет исходник, извлекает текст и создаёт связь документа с задачей.

        Args:
            task_id: Задача, в которую импортируется документ.
            user_id: Пользователь, выполняющий импорт.
            file_name: Исходное имя файла.
            content_type: MIME-тип, заявленный клиентом.
            content: Бинарное содержимое файла.

        Returns:
            Созданные оригинал, документ проекта и их связь.

        Raises:
            TaskDocumentTaskNotFoundError: Если задача не найдена.
            TaskDocumentValidationError: Если файл пуст или имя некорректно.
            TaskDocumentTooLargeError: Если файл превышает лимит.
            TaskDocumentUnsupportedFormatError: Если формат не индексируется
                или в файле не нашлось текста.
            TaskDocumentStepFailedError: Если отказал вложенный шаг импорта.
            AiProviderError: Если vision-модель недоступна.
        """
        # Первая короткая DB-фаза: читаем задачу и закрываем область до
        # распознавания файла, которое может длиться сотни секунд.
        async with self.scope() as db:
            project_id = await self._get_project_id(db, task_id=task_id)
        safe_name = self._validate(file_name=file_name, content=content)
        extracted = await self._extract_text(safe_name=safe_name, content=content)

        storage_key: str | None = None
        # Вторая короткая DB-фаза: три записи одной транзакцией.
        async with self.scope() as db:
            try:
                # Три записи — один бизнес-факт, поэтому и транзакция одна:
                # вложенные сервисы не фиксируют свою часть сами.
                stored = await db.attachments.save_in_transaction(
                    task_id=task_id,
                    file_name=safe_name,
                    content_type=content_type,
                    content=content,
                    caption=caption,
                    uploaded_by_user_id=user_id,
                    index_for_knowledge=False,
                )
                storage_key = stored.storage_key
                await db.knowledge_sources.save_extraction(
                    {
                        "attachment_id": stored.attachment.id,
                        "text": extracted,
                        "status": "ready",
                        "detail": None,
                        "original_chars": len(extracted),
                        "content_hash": hashlib.sha256(content).hexdigest(),
                    }
                )
                title = safe_name[:255]
                document = await db.documents.create_document(
                    project_id=project_id,
                    title=title,
                    slug=None,
                    content_md=_document_markdown(title=title, content=extracted),
                    origin_attachment_id=stored.attachment.id,
                    commit=False,
                )
                link = await db.links.create_link(
                    document_id=document.id,
                    task_id=task_id,
                    user_id=user_id,
                    commit=False,
                )
                await db.unit_of_work.commit()
                return TaskDocumentImportSchema(
                    attachment=stored.attachment,
                    document=document,
                    link=link,
                )
            except BaseException as error:
                # Откат снимает все записи разом; компенсировать нужно
                # только то, что база откатить не может, — файл на диске.
                await self._rollback(db)
                await self._remove_stored_file(storage_key)
                if isinstance(error, KnowledgeIndexJobsRepositoryError):
                    raise TaskDocumentStepFailedError(_as_service_error(error)) from error
                if isinstance(error, NESTED_SERVICE_ERRORS):
                    raise TaskDocumentStepFailedError(error) from error
                raise

    @staticmethod
    async def _get_project_id(db: TaskDocumentImportScope, *, task_id: int) -> int:
        """Возвращает проект задачи импорта, не поднимая ORM-модель выше."""
        try:
            task = await db.tasks.get_by_id(task_id=task_id)
        except TasksRepositoryError as error:
            logger.error("❌ Не удалось прочитать задачу id=%s.", task_id, exc_info=True)
            raise TaskDocumentStepFailedError(_as_service_error(error)) from error
        if task is None:
            raise TaskDocumentTaskNotFoundError(task_id=task_id)
        return task.project_id

    def _validate(self, *, file_name: str, content: bytes) -> str:
        """Проверяет файл до любых записей и возвращает безопасное имя."""
        safe_name = Path((file_name or "").replace("\\", "/")).name.strip()
        if not content:
            raise TaskDocumentValidationError("Нельзя загрузить пустой файл.")
        if len(content) > self.max_file_size:
            raise TaskDocumentTooLargeError(max_size_mb=self.max_file_size // 1024 // 1024)
        if not safe_name or "\x00" in safe_name:
            raise TaskDocumentValidationError("Имя файла некорректно.")
        if Path(safe_name).suffix.lower() not in INDEXABLE_EXTENSIONS:
            raise TaskDocumentUnsupportedFormatError(UNSUPPORTED_FORMAT_MESSAGE)
        return safe_name

    async def _extract_text(self, *, safe_name: str, content: bytes) -> str:
        """Извлекает текст файла до начала записи."""
        try:
            extracted = await extract_indexable_text(
                safe_name,
                content,
                vision=self.vision,
            )
        except ValueError as error:
            raise TaskDocumentUnsupportedFormatError(
                f"Не удалось прочитать «{safe_name}»: файл повреждён или имеет неверный формат."
            ) from error
        except ClientError as error:
            logger.error("❌ Vision недоступен при импорте документа.", exc_info=True)
            raise AiProviderError(str(error)) from error
        if not extracted:
            raise TaskDocumentUnsupportedFormatError(
                f"В «{safe_name}» не удалось найти текст для документа проекта."
            )
        return extracted

    @staticmethod
    async def _rollback(db: TaskDocumentImportScope) -> None:
        """Откатывает транзакцию импорта, не маскируя исходную ошибку."""
        try:
            await db.unit_of_work.rollback()
        except UnitOfWorkRepositoryError:
            logger.warning("⚠️ Не удалось откатить транзакцию импорта документа.", exc_info=True)

    async def _remove_stored_file(self, storage_key: str | None) -> None:
        """Удаляет физический файл: его откат транзакции не затрагивает.

        Метаданные исчезнут вместе с откатом, а файл на диске останется
        сиротой, если его не убрать здесь.
        """
        if storage_key is None:
            return
        try:
            await self.attachment_storage.delete(storage_key)
        except StorageError:
            logger.warning(
                "⚠️ Не удалось удалить файл %s после отката импорта.",
                storage_key,
                exc_info=True,
            )


def _as_service_error(error: Exception) -> ServiceError:
    """Оборачивает ошибку нижнего слоя в носитель статуса и формулировки."""
    carrier = ServiceError(str(error))
    return carrier


def _document_markdown(*, title: str, content: str) -> str:
    """Собирает Markdown документа, не дублируя заголовок для .md файлов."""
    normalized = content.strip()
    if Path(title).suffix.lower() == ".md":
        return normalized
    return f"# {title}\n\n{normalized}"
