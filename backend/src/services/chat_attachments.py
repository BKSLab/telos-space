"""Безопасные загрузки и выдача файлов общего чата."""

import logging
import mimetypes
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from src.exceptions.project_chats import (
    ChatConflictError,
    ChatNotFoundError,
    ChatServiceError,
    ChatValidationError,
)
from src.exceptions.storage import TaskAttachmentStorageError
from src.schemas.project_chats import ChatAttachmentView
from src.services.project_chats import ProjectChatService
from src.services.task_attachments import TaskAttachmentsService
from src.storage.chat_attachments import ChatAttachmentStorage

logger = logging.getLogger(__name__)
MAX_FILE_SIZE = 10 * 1024 * 1024
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@dataclass(frozen=True)
class ChatFileContent:
    """Проверенные метаданные передаются FileResponse после закрытия БД."""

    path: Path
    original_name: str
    content_type: str
    previewable: bool


class ChatAttachmentsService:
    """Переиспользует файловое хранилище приложения с отдельным корнем чата."""

    def __init__(
        self, chat: ProjectChatService, storage: ChatAttachmentStorage, retention_hours: int
    ):
        self.chat = chat
        self.storage = storage
        self.retention_hours = retention_hours
        self.metrics = Counter()

    async def upload(
        self, project_id: int, user_id: int, name: str, content: bytes
    ) -> ChatAttachmentView:
        """Сохраняет файл вне DB-scope, затем проверяет права и фиксирует метаданные."""
        try:
            result = await self._upload(project_id, user_id, name, content)
            self.metrics["attachment_uploads_total"] += 1
            self.metrics["attachment_bytes_total"] += len(content)
            return result
        except Exception:
            self.metrics["attachment_errors_total"] += 1
            logger.warning("chat.attachment_failed project_id=%s actor_id=%s", project_id, user_id)
            raise

    async def _upload(
        self, project_id: int, user_id: int, name: str, content: bytes
    ) -> ChatAttachmentView:
        """Записывает бинарное содержимое и привязывает защищённые метаданные."""
        safe_name, extension, content_type = self._validate(name, content)
        async with self.chat.operation() as db:
            chat = await self.chat.require(db, project_id, user_id)
            chat_id = chat.id
        key = None
        try:
            key = await self.storage.save(chat_id, extension, content)
            async with self.chat.operation() as db:
                await self.chat.require(db, project_id, user_id, lock=True)
                if await db.attachments.draft_count(chat_id, user_id) >= 20:
                    raise ChatValidationError("Слишком много неотправленных файлов.")
                row = await db.attachments.save(
                    dict(
                        id=uuid4(),
                        chat_id=chat_id,
                        uploader_id=user_id,
                        original_name=safe_name,
                        content_type=content_type,
                        size_bytes=len(content),
                        storage_key=key,
                    )
                )
                result = ChatAttachmentView.model_validate(row)
                await db.unit_of_work.commit()
                return result
        except Exception as error:
            if key:
                try:
                    await self.storage.delete(key)
                except TaskAttachmentStorageError:
                    logger.exception(
                        "Не удалось компенсировать загрузку чата project=%s", project_id
                    )
            if isinstance(error, TaskAttachmentStorageError):
                raise ChatServiceError(str(error)) from error
            raise

    async def delete_draft(self, project_id: int, user_id: int, file_id) -> None:
        """Удаляет только свой неотправленный файл, сериализуясь с отправкой."""
        try:
            async with self.chat.operation() as db:
                chat = await self.chat.require(db, project_id, user_id, lock=True)
                row = await db.attachments.get(file_id)
                if row is None or row.chat_id != chat.id or row.uploader_id != user_id:
                    raise ChatNotFoundError("Черновик файла недоступен.")
                if row.message_id is not None:
                    raise ChatConflictError("Файл уже отправлен.")
                await self.storage.delete(row.storage_key)
                await db.attachments.delete(file_id)
                await db.unit_of_work.commit()
        except TaskAttachmentStorageError as error:
            raise ChatServiceError(str(error)) from error

    async def content(
        self, project_id: int, file_id, *, session_token, bearer_secret, thumbnail: bool = False
    ) -> ChatFileContent:
        """Авторизует скачивание без request-scoped сессии во время передачи файла."""
        principal = await self.chat.authenticate(
            project_id, session_token=session_token, bearer_secret=bearer_secret
        )
        async with self.chat.operation() as db:
            chat = await self.chat.require(db, project_id, principal.user_id)
            row = await db.attachments.get(file_id)
            if row is None or row.chat_id != chat.id:
                raise ChatNotFoundError("Файл недоступен.")
            if row.message_id is None:
                if row.uploader_id != principal.user_id:
                    raise ChatNotFoundError("Черновик принадлежит другому участнику.")
            else:
                message = await db.messages.get(chat.id, row.message_id)
                if message is None or message.deleted_at:
                    raise ChatNotFoundError("Сообщение удалено.")
            key, name, content_type = row.storage_key, row.original_name, row.content_type
        try:
            path = (
                await self.storage.prepare_preview(key)
                if thumbnail and content_type in IMAGE_TYPES.values()
                else self.storage.resolve(key)
            )
            if thumbnail and (content_type not in IMAGE_TYPES.values() or path is None):
                raise ChatNotFoundError("Предпросмотр изображения недоступен.")
        except TaskAttachmentStorageError as error:
            raise ChatNotFoundError(str(error)) from error
        return ChatFileContent(
            path=path,
            original_name=f"{Path(name).stem}.jpg" if thumbnail else name,
            content_type="image/jpeg" if thumbnail else content_type,
            previewable=content_type in IMAGE_TYPES.values(),
        )

    async def cleanup(self) -> int:
        """Удаляет просроченные черновики под той же блокировкой, что отправка."""
        try:
            return await self._cleanup()
        except TaskAttachmentStorageError as error:
            raise ChatServiceError(str(error)) from error

    async def _cleanup(self) -> int:
        """Сначала чистит метаданные, затем старые файлы без владельца в БД."""
        cutoff = datetime.now(UTC) - timedelta(hours=self.retention_hours)
        async with self.chat.operation() as db:
            expired = [(row.id, row.chat_id) for row in await db.attachments.get_expired(cutoff)]
        count = 0
        for file_id, chat_id in expired:
            async with self.chat.operation() as db:
                # Получение chat_id через проекцию не создаёт новые чаты.
                project_id = await db.chats.project_id(chat_id)
                if project_id is None:
                    continue
                await db.chats.get(project_id, lock=True)
                row = await db.attachments.get(file_id)
                if row and row.message_id is None and row.created_at < cutoff:
                    await self.storage.delete(row.storage_key)
                    await db.attachments.delete(file_id)
                    await db.unit_of_work.commit()
                    count += 1
        # Запас по возрасту защищает файлы незавершённых активных загрузок.
        # Сюда также попадают файлы проектов, удалённых каскадом в PostgreSQL.
        async for keys in self.storage.old_key_batches(cutoff.timestamp()):
            async with self.chat.operation() as db:
                existing = await db.attachments.get_existing_keys(keys)
            for key in set(keys) - existing:
                await self.storage.delete(key)
                count += 1
        return count

    @staticmethod
    def _validate(name: str, content: bytes):
        safe = Path((name or "").replace("\\", "/")).name.strip()
        extension = Path(safe).suffix.lower()
        if (
            not safe
            or len(safe) > 255
            or any(ord(char) < 32 for char in safe)
            or not content
            or len(content) > MAX_FILE_SIZE
        ):
            raise ChatValidationError("Нужен непустой файл до 10 МБ с корректным именем.")
        if extension not in TaskAttachmentsService.ALLOWED_EXTENSIONS:
            raise ChatValidationError("Этот формат файлов не поддерживается.")
        mime = mimetypes.guess_type(safe)[0] or "application/octet-stream"
        if extension in IMAGE_TYPES:
            valid = (
                (extension == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n"))
                or (extension in {".jpg", ".jpeg"} and content.startswith(b"\xff\xd8\xff"))
                or (extension == ".gif" and content[:6] in {b"GIF87a", b"GIF89a"})
                or (
                    extension == ".webp"
                    and content.startswith(b"RIFF")
                    and content[8:12] == b"WEBP"
                )
            )
            if not valid:
                raise ChatValidationError("Содержимое не соответствует формату изображения.")
            mime = IMAGE_TYPES[extension]
        elif mime.startswith("image/"):
            # Форматы без проверки сигнатуры доступны только скачиванием.
            mime = "application/octet-stream"
        return safe, extension, mime
