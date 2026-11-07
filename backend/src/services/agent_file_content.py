"""Приватная выдача оригиналов и миниатюр без открытой DB-сессии."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from src.exceptions.agent_files import AgentFileNotFoundError, AgentFilesServiceError
from src.exceptions.api_keys import ApiKeyRouteNotAllowedError
from src.exceptions.base import RepositoryError
from src.exceptions.storage import TaskAttachmentStorageError
from src.repositories.agent_conversations import AgentConversationsRepository
from src.repositories.agent_files import AgentFilesRepository
from src.services.access import AccessService
from src.services.attachment_download import AttachmentDownload
from src.services.auth import AuthService
from src.storage.task_attachments import TaskAttachmentStorage


@dataclass(frozen=True, slots=True)
class AgentFileReadScope:
    """Минимальные зависимости короткой проверки видимости загрузки."""

    auth: AuthService
    access: AccessService
    conversations: AgentConversationsRepository
    files: AgentFilesRepository


class AgentFileContentService:
    """Только владелец диалога с актуальным доступом читает его файлы."""

    def __init__(
        self,
        *,
        scope: Callable[[], AbstractAsyncContextManager[AgentFileReadScope]],
        storage: TaskAttachmentStorage,
    ) -> None:
        self.scope = scope
        self.storage = storage

    async def prepare(
        self,
        *,
        conversation_id: int,
        file_id: UUID,
        session_token: str | None,
        bearer_secret: str | None,
        project_id: int | None = None,
        thumbnail: bool = False,
    ) -> AttachmentDownload:
        """Проверяет владельца до чтения диска; миниатюра не использует vision."""
        try:
            async with self.scope() as db:
                principal = await db.auth.resolve_principal(
                    session_token=session_token, bearer_secret=bearer_secret
                )
                if principal.api_key is not None:
                    raise ApiKeyRouteNotAllowedError("Приватные файлы диалогов")
                conversation = await db.conversations.get_owned(conversation_id, principal.user_id)
                if conversation is None or (
                    project_id is not None and conversation.project_id != project_id
                ):
                    raise AgentFileNotFoundError("Диалог недоступен.")
                if conversation.project_id is not None:
                    await db.access.ensure_project_access(
                        project_id=conversation.project_id, user_id=principal.user_id
                    )
                row = await db.files.get(file_id)
                if row is None or row.conversation_id != conversation_id:
                    raise AgentFileNotFoundError("Файл недоступен.")
                key, name, mime = row.storage_key, row.original_name, row.content_type
            path = self.storage.resolve(key)
            if thumbnail:
                if not mime.startswith("image/"):
                    raise AgentFileNotFoundError("Предпросмотр изображения недоступен.")
                path = await self.storage.prepare_preview(key)
                if path is None:
                    raise AgentFileNotFoundError("Предпросмотр изображения недоступен.")
            return AttachmentDownload(
                path=path,
                filename=f"{Path(name).stem}.jpg" if thumbnail else name,
                media_type="image/jpeg" if thumbnail else mime,
                previewable=thumbnail,
            )
        except TaskAttachmentStorageError as error:
            raise AgentFileNotFoundError("Файл недоступен в хранилище.") from error
        except RepositoryError as error:
            raise AgentFilesServiceError("Не удалось прочитать файл диалога.") from error
