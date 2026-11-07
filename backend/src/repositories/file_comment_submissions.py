"""Квитанции повторов с межпроцессной блокировкой PostgreSQL."""

from uuid import UUID

from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.file_comment_submissions import FileCommentSubmission
from src.exceptions.file_comments import (
    FileCommentsRepositoryError,
    FileCommentSubmissionRepositoryConflictError,
)


class FileCommentSubmissionsRepository:
    """Блокировка и unique вместе исключают повторную запись на конкурентных воркерах."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def lock_request(self, *, actor_user_id: int, request_id: UUID) -> None:
        """Сериализует один UUID автора до финального commit/rollback.

        Args:
            actor_user_id: Текущий автор запроса.
            request_id: Стабильный ключ отправки клиента.
        Raises:
            FileCommentsRepositoryError: Блокировку получить не удалось.
        """
        try:
            await self.db_session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        func.hashtextextended(f"file-comment:{actor_user_id}:{request_id}", 0)
                    )
                )
            )
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileCommentsRepositoryError(
                "Не удалось заблокировать отправку комментария."
            ) from error

    async def get_by_request(
        self, *, actor_user_id: int, request_id: UUID
    ) -> FileCommentSubmission | None:
        """Читает квитанцию одним запросом по уникальному индексу.

        Args:
            actor_user_id: Текущий автор.
            request_id: UUID запроса.
        Returns:
            Квитанция или None.
        Raises:
            FileCommentsRepositoryError: SELECT завершился ошибкой.
        """
        try:
            result = await self.db_session.execute(
                select(FileCommentSubmission).where(
                    FileCommentSubmission.actor_user_id == actor_user_id,
                    FileCommentSubmission.request_id == request_id,
                )
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileCommentsRepositoryError(
                "Не удалось прочитать квитанцию комментария."
            ) from error

    async def save(
        self,
        *,
        actor_user_id: int,
        task_id: int,
        request_id: UUID,
        arguments_hash: str,
        result: dict,
    ) -> None:
        """Фиксирует результат в общей транзакции комментария, файлов и истории.

        Args:
            actor_user_id: Серверный автор операции.
            task_id: Текущая задача комментария.
            request_id: Стабильный UUID клиента.
            arguments_hash: Хэш всех нормализованных аргументов.
            result: Публичный DTO первоначального результата.
        Raises:
            FileCommentSubmissionRepositoryConflictError: Ключ уже существует.
            FileCommentsRepositoryError: INSERT завершился ошибкой.
        """
        try:
            await self.db_session.execute(
                insert(FileCommentSubmission).values(
                    actor_user_id=actor_user_id,
                    task_id=task_id,
                    request_id=request_id,
                    arguments_hash=arguments_hash,
                    result=result,
                )
            )
        except IntegrityError as error:
            await self.db_session.rollback()
            constraint = getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)
            if constraint == "uq_file_comment_submission_actor_request":
                raise FileCommentSubmissionRepositoryConflictError(
                    "Квитанция уже существует."
                ) from error
            raise FileCommentsRepositoryError(
                "Не удалось сохранить квитанцию комментария."
            ) from error
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileCommentsRepositoryError(
                "Не удалось сохранить квитанцию комментария."
            ) from error
