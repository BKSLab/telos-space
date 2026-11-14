"""Приватные загрузки и копирование выбранного участником файла в задачу."""

from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from src.exceptions.access import AccessServiceError
from src.exceptions.agent_files import (
    AgentFileConflictError,
    AgentFileNotFoundError,
    AgentFilesServiceError,
    AgentFileValidationError,
)
from src.exceptions.base import RepositoryError
from src.exceptions.storage import TaskAttachmentStorageError
from src.exceptions.task_attachments import TaskAttachmentsServiceError
from src.schemas.agent_files import AgentFileSchema
from src.services.agent_tool_scope import AgentProjectToolScopeFactory
from src.services.task_attachments import TaskAttachmentsService


class AgentFilesService:
    """Сохраняет загрузку в личном диалоге виджета с общей валидацией вложений."""

    max_file_size = TaskAttachmentsService.MAX_FILE_SIZE

    def __init__(self, *, scope: AgentProjectToolScopeFactory) -> None:
        self.scope = scope

    async def upload(
        self,
        *,
        user_id: int,
        conversation_id: int,
        file_name: str,
        content_type: str | None,
        content: bytes,
        project_id: int | None = None,
    ) -> AgentFileSchema:
        """Проверяет диалог, сохраняет файл и компенсирует запись при откате."""
        async with self._scope() as db:
            await self._require(db, user_id, conversation_id, project_id=project_id)
            if await db.uploads.count(conversation_id) >= 20:
                raise AgentFileConflictError("Лимит загрузок диалога достигнут.")
            name, extension, mime = db.attachments.validate_file(
                file_name=file_name, content_type=content_type, content=content
            )
            key = await db.upload_storage.save(conversation_id, extension, content)
            row = await db.uploads.save(
                dict(
                    id=uuid4(),
                    conversation_id=conversation_id,
                    original_name=name,
                    content_type=mime,
                    size=len(content),
                    storage_key=key,
                )
            )
            response = AgentFileSchema.model_validate(row)
            await db.unit_of_work.commit()
            return response

    async def delete_draft(self, *, user_id: int, conversation_id: int, file_id: UUID, project_id: int | None = None) -> None:
        """Удаляет неотправленный файл, сериализуясь с отправкой вопроса."""
        async with self._scope() as db:
            await self._require(db, user_id, conversation_id, project_id=project_id)
            row = await db.uploads.get(file_id)
            if row is None or row.conversation_id != conversation_id:
                raise AgentFileNotFoundError("Загрузка недоступна.")
            if row.message_id is not None:
                raise AgentFileConflictError("Файл уже отправлен в сообщении.")
            await db.upload_storage.delete(row.storage_key)
            await db.uploads.delete(row.id)
            await db.unit_of_work.commit()

    @staticmethod
    async def _require(db, user_id, conversation_id, *, project_id=None) -> None:
        user = await db.users.get_by_id(user_id)
        if user is None or not user.is_active:
            raise AgentFileNotFoundError("Пользователь недоступен.")
        conversation = await db.conversations.get_owned(conversation_id, user_id, lock=True)
        if conversation is None or (project_id is not None and conversation.project_id != project_id):
            raise AgentFileNotFoundError("Диалог недоступен.")
        if conversation.project_id is not None:
            await db.access.ensure_project_access(project_id=conversation.project_id, user_id=user_id)

    @asynccontextmanager
    async def _scope(self):
        try:
            async with self.scope() as db:
                yield db
        except AccessServiceError as error:
            raise AgentFileNotFoundError(str(error)) from error
        except TaskAttachmentsServiceError as error:
            raise AgentFileValidationError(str(error)) from error
        except (RepositoryError, TaskAttachmentStorageError) as error:
            raise AgentFilesServiceError(str(error)) from error


async def uploaded_content(db, *, file_id: UUID, user_id: int) -> tuple[AgentFileSchema, bytes]:
    """Возвращает байты только отправленного файла собственного диалога виджета."""
    row = await db.uploads.get(file_id)
    if row is None or row.message_id is None:
        raise AgentFileNotFoundError("Файл ещё не отправлен участником.")
    conversation = await db.conversations.get_owned(row.conversation_id, user_id)
    if conversation is None:
        raise AgentFileNotFoundError("Файл принадлежит другому пользователю.")
    if conversation.project_id is not None:
        await db.access.ensure_project_access(project_id=conversation.project_id, user_id=user_id)
    try:
        # Ограничиваем и повторное чтение, если файл был изменён извне.
        content = await db.upload_storage.read(
            row.storage_key, limit=TaskAttachmentsService.MAX_FILE_SIZE
        )
    except TaskAttachmentStorageError as error:
        raise AgentFilesServiceError("Не удалось прочитать загрузку.") from error
    return AgentFileSchema.model_validate(row), content
