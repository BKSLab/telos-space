"""Одна отправка комментария, оригиналов и ссылок с устойчивым повтором."""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from uuid import UUID

from src.db.models.knowledge_index_jobs import KnowledgeEntityType
from src.db.models.task_activity import TaskActivityEventType
from src.exceptions.api_tokens import InsufficientTokenScopeError
from src.exceptions.file_comments import (
    FileCommentsRepositoryError,
    FileCommentsServiceError,
    FileCommentSubmissionConflictError,
    FileCommentSubmissionRepositoryConflictError,
    FileCommentValidationError,
)
from src.exceptions.knowledge import KnowledgeEventsServiceError
from src.exceptions.storage import TaskAttachmentStorageError
from src.exceptions.task_activity import TaskActivityRepositoryError
from src.exceptions.task_attachments import (
    TaskAttachmentLimitError,
    TaskAttachmentNotFoundError,
    TaskAttachmentsRepositoryError,
)
from src.exceptions.task_comments import TaskCommentsRepositoryError
from src.exceptions.tasks import TaskNotFoundError, TasksRepositoryError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.repositories.file_comment_submissions import FileCommentSubmissionsRepository
from src.repositories.file_processing_jobs import FileProcessingJobsRepository
from src.repositories.project_files import ProjectFilesRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_attachments import TaskAttachmentsRepository
from src.repositories.task_comment_attachments import TaskCommentAttachmentsRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.file_metadata import normalize_caption
from src.schemas.task_comments import CommentSchema
from src.services.access import AccessService
from src.services.auth import AuthService, Principal
from src.services.knowledge_events import KnowledgeEvents
from src.services.task_attachments import TaskAttachmentsService
from src.storage.task_attachments import TaskAttachmentStorage
from src.utils.api_scopes import SCOPE_COMMENTS_WRITE, SCOPE_TASKS_WRITE
from src.utils.file_previews import is_raster_previewable
from src.utils.request_actor import reset_request_actor, set_request_actor

logger = logging.getLogger(__name__)
MAX_COMMENT_FILES = 10
MAX_COMMENT_LENGTH = 50_000
FILE_COMMENT_DATA_ERRORS = (
    FileCommentsRepositoryError,
    TaskAttachmentsRepositoryError,
    TaskCommentsRepositoryError,
    TasksRepositoryError,
    TaskActivityRepositoryError,
    KnowledgeEventsServiceError,
    UnitOfWorkRepositoryError,
)


@dataclass(frozen=True, slots=True)
class CommentFileUpload:
    """Ограниченный транспортом оригинал с подписью."""

    name: str
    content: bytes
    content_type: str | None = None
    caption: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedCommentFile:
    """Оригинал, уже записанный вне транзакции метаданных."""

    upload: CommentFileUpload
    storage_key: str


@dataclass(frozen=True, slots=True)
class FileCommentsScope:
    """Зависимости одной короткой области с общей сессией и UnitOfWork."""

    auth: AuthService
    access: AccessService
    tasks: TasksRepository
    files: TaskAttachmentsRepository
    project_files: ProjectFilesRepository
    processing_jobs: FileProcessingJobsRepository
    attachments: TaskAttachmentsService
    comments: TaskCommentsRepository
    links: TaskCommentAttachmentsRepository
    submissions: FileCommentSubmissionsRepository
    activity: TaskActivityRepository
    knowledge_events: KnowledgeEvents
    unit_of_work: UnitOfWork


FileCommentsScopeFactory = Callable[[], AbstractAsyncContextManager[FileCommentsScope]]


async def authorize_task_write(
    db: FileCommentsScope,
    *,
    task_id: int,
    session_token: str | None,
    bearer_secret: str | None,
    comment: bool,
    with_files: bool,
) -> Principal:
    """Разрешает текущего автора и проверяет действующие доменные права.

    Args:
        db: Открытая короткая область БД.
        task_id: Текущая задача назначения.
        session_token: Cookie сессии.
        bearer_secret: Секрет bearer-токена.
        comment: Требуется ли scope комментариев.
        with_files: Требуется ли право менять файлы задачи.
    Returns:
        Текущий активный принципал.
    Raises:
        AuthServiceError: Текущие учётные данные или scopes не подходят.
        AccessServiceError: Задача недоступна для записи.
    """
    principal = await db.auth.resolve_principal(
        session_token=session_token, bearer_secret=bearer_secret
    )
    if principal.api_key is None and not principal.can_write:
        raise InsufficientTokenScopeError()
    if comment:
        principal.require(SCOPE_COMMENTS_WRITE)
    if with_files:
        principal.require(SCOPE_TASKS_WRITE)
    grant = await db.access.ensure_task_access(task_id=task_id, user_id=principal.user_id)
    db.access.require_write(grant)
    return principal


class FileCommentsService:
    """Хранит файлы до SQL, затем атомарно фиксирует весь бизнес-факт."""

    max_file_size = TaskAttachmentsService.MAX_FILE_SIZE
    max_files = MAX_COMMENT_FILES

    def __init__(
        self,
        *,
        scope: FileCommentsScopeFactory,
        storage: TaskAttachmentStorage,
        knowledge_enabled: bool = True,
    ) -> None:
        self.scope = scope
        self.storage = storage
        self.knowledge_enabled = knowledge_enabled

    async def submit(
        self,
        *,
        task_id: int,
        request_id: UUID,
        body_md: str,
        uploads: list[CommentFileUpload],
        attachment_ids: list[int],
        session_token: str | None,
        bearer_secret: str | None,
    ) -> CommentSchema:
        """Создаёт или безопасно повторяет одну отправку комментария.

        Args:
            task_id: Текущая задача комментария и всех файлов.
            request_id: Стабильный UUID клиента, сохраняемый при сетевом повторе.
            body_md: Текст, допускающий пустую строку при наличии файлов.
            uploads: До десяти новых ограниченных оригиналов с подписями.
            attachment_ids: ID существующих оригиналов этой же задачи.
            session_token: Cookie пользователя.
            bearer_secret: Bearer с приоритетом перед cookie.
        Returns:
            Первоначальный результат отправки с вложенными метаданными файлов.
        Raises:
            FileCommentValidationError: Пустая отправка или неверные аргументы.
            FileCommentSubmissionConflictError: UUID использован с другими аргументами.
            TaskAttachmentNotFoundError: Выбранный оригинал отсутствует в этой задаче.
            AuthServiceError: Пользователь или право отозваны, в том числе при повторе.
            AccessServiceError: Текущая задача недоступна пользователю.
            FileCommentsServiceError: Составную запись выполнить не удалось.
        """
        uploads = self._validate(body_md, uploads, attachment_ids)
        arguments_hash = await asyncio.to_thread(
            self._arguments_hash, task_id, body_md, uploads, attachment_ids
        )
        prepared: list[PreparedCommentFile] = []
        committed = False
        try:
            # Ранний повтор не пишет даже временные копии оригиналов.
            async with self.scope() as db:
                principal = await authorize_task_write(
                    db,
                    task_id=task_id,
                    session_token=session_token,
                    bearer_secret=bearer_secret,
                    comment=True,
                    with_files=bool(uploads or attachment_ids),
                )
                previous = await db.submissions.get_by_request(
                    actor_user_id=principal.user_id, request_id=request_id
                )
                if previous is not None:
                    return self._replay(previous, task_id, arguments_hash)
            for upload in uploads:
                _, extension, _ = TaskAttachmentsService.validate_file(
                    file_name=upload.name, content_type=upload.content_type, content=upload.content
                )
                key = await self.storage.save(
                    task_id=task_id, extension=extension, content=upload.content
                )
                prepared.append(PreparedCommentFile(upload, key))
                if is_raster_previewable(upload.name, upload.content_type or ""):
                    await self.storage.prepare_preview(key)
            async with self.scope() as db:
                principal = await authorize_task_write(
                    db,
                    task_id=task_id,
                    session_token=session_token,
                    bearer_secret=bearer_secret,
                    comment=True,
                    with_files=bool(uploads or attachment_ids),
                )
                await db.submissions.lock_request(
                    actor_user_id=principal.user_id, request_id=request_id
                )
                task = await db.tasks.get_for_update(task_id=task_id)
                if task is None:
                    raise TaskNotFoundError(task_id)
                # После ожидания PG-lock перенос/отзыв доступа проверяется заново.
                principal = await authorize_task_write(
                    db,
                    task_id=task_id,
                    session_token=session_token,
                    bearer_secret=bearer_secret,
                    comment=True,
                    with_files=bool(uploads or attachment_ids),
                )
                previous = await db.submissions.get_by_request(
                    actor_user_id=principal.user_id, request_id=request_id
                )
                if previous is not None:
                    return self._replay(previous, task_id, arguments_hash)
                selected = (
                    await db.files.get_many_for_task(task_id=task_id, attachment_ids=attachment_ids)
                    if attachment_ids
                    else []
                )
                found = {item.id for item in selected}
                missing = next(
                    (item_id for item_id in attachment_ids if item_id not in found), None
                )
                if missing is not None:
                    raise TaskAttachmentNotFoundError(missing)
                count = await db.files.get_count_for_task(task_id) if prepared else 0
                if count + len(prepared) > TaskAttachmentsService.MAX_FILES_PER_TASK:
                    raise TaskAttachmentLimitError(TaskAttachmentsService.MAX_FILES_PER_TASK)
                new_ids: list[int] = []
                for index, item in enumerate(prepared):
                    stored = await db.attachments.save_in_transaction(
                        task_id=task_id,
                        file_name=item.upload.name,
                        content_type=item.upload.content_type,
                        content=item.upload.content,
                        caption=item.upload.caption,
                        uploaded_by_user_id=principal.user_id,
                        prepared_storage_key=item.storage_key,
                        task=task,
                        current_file_count=count + index,
                        index_for_knowledge=False,
                    )
                    new_ids.append(stored.attachment.id)
                comment = await db.comments.save(
                    task_id=task_id,
                    author_id=principal.user_id,
                    author_name=principal.full_name,
                    body_md=body_md,
                )
                await db.links.save_many(
                    task_id=task_id,
                    comment_id=comment.id,
                    attachment_ids=[*attachment_ids, *new_ids],
                )
                actor_token = set_request_actor(principal.request_actor())
                try:
                    await db.activity.save(
                        task_id=task_id,
                        event_type=TaskActivityEventType.COMMENT_ADDED,
                        from_value=None,
                        to_value=body_md[:255],
                    )
                finally:
                    reset_request_actor(actor_token)
                await db.knowledge_events.upsert(
                    project_id=task.project_id,
                    entity_type=KnowledgeEntityType.COMMENT,
                    entity_id=comment.id,
                )
                result = CommentSchema.model_validate(await db.comments.get_by_id(comment.id))
                states = (
                    await db.comments.get_attachment_states([comment.id])
                    if result.attachments
                    else {}
                )
                for item in result.attachments:
                    state = states.get(item.id, {})
                    if not self.knowledge_enabled:
                        state.update(index_status="disabled", index_detail=None)
                    for key, value in state.items():
                        setattr(item, key, value)
                await db.submissions.save(
                    actor_user_id=principal.user_id,
                    task_id=task_id,
                    request_id=request_id,
                    arguments_hash=arguments_hash,
                    result=result.model_dump(mode="json"),
                )
                await db.unit_of_work.commit()
                committed = True
                return result
        except FileCommentSubmissionRepositoryConflictError as error:
            raise FileCommentSubmissionConflictError(str(error)) from error
        except (*FILE_COMMENT_DATA_ERRORS, TaskAttachmentStorageError) as error:
            raise FileCommentsServiceError(str(error)) from error
        finally:
            if not committed:
                for item in prepared:
                    try:
                        await self.storage.delete_if_exists(item.storage_key)
                    except TaskAttachmentStorageError:
                        logger.warning(
                            "⚠️ Не удалось убрать оригинал незавершённой отправки задачи %s.",
                            task_id,
                            exc_info=True,
                        )

    def _validate(
        self, body_md: str, uploads: list[CommentFileUpload], attachment_ids: list[int]
    ) -> list[CommentFileUpload]:
        if len(body_md) > MAX_COMMENT_LENGTH or "\x00" in body_md:
            raise FileCommentValidationError(
                "Текст комментария некорректен или длиннее 50000 символов."
            )
        if len(uploads) + len(attachment_ids) > MAX_COMMENT_FILES:
            raise FileCommentValidationError("К комментарию можно добавить не более десяти файлов.")
        if any(type(item) is not int or item <= 0 for item in attachment_ids) or len(
            set(attachment_ids)
        ) != len(attachment_ids):
            raise FileCommentValidationError(
                "Идентификаторы файлов должны быть уникальными положительными целыми числами."
            )
        if not body_md.strip() and not uploads and not attachment_ids:
            raise FileCommentValidationError("Добавьте текст или файлы комментария.")
        result = []
        for upload in uploads:
            name, _, content_type = TaskAttachmentsService.validate_file(
                file_name=upload.name, content_type=upload.content_type, content=upload.content
            )
            try:
                caption = normalize_caption(upload.caption)
            except ValueError as error:
                raise FileCommentValidationError(str(error)) from error
            result.append(CommentFileUpload(name, upload.content, content_type, caption))
        return result

    @staticmethod
    def _arguments_hash(
        task_id: int, body_md: str, uploads: list[CommentFileUpload], attachment_ids: list[int]
    ) -> str:
        payload = {
            "task_id": task_id,
            "body_md": body_md,
            "attachment_ids": sorted(attachment_ids),
            "files": [
                {
                    "name": item.name,
                    "content_type": item.content_type,
                    "caption": item.caption,
                    "sha256": hashlib.sha256(item.content).hexdigest(),
                }
                for item in uploads
            ],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    @staticmethod
    def _replay(previous, task_id: int, arguments_hash: str) -> CommentSchema:
        if previous.task_id != task_id or previous.arguments_hash != arguments_hash:
            raise FileCommentSubmissionConflictError()
        return CommentSchema.model_validate(previous.result)
