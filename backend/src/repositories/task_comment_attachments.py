"""Пакетная запись связей существующих файлов с комментарием."""

from sqlalchemy import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.task_comment_attachments import TaskCommentAttachment
from src.exceptions.file_comments import FileCommentsRepositoryError


class TaskCommentAttachmentsRepository:
    """Записывает связи без собственного commit и без повторных оригиналов."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def save_many(self, *, task_id: int, comment_id: int, attachment_ids: list[int]) -> None:
        """Связывает до десяти оригиналов одним INSERT.

        Args:
            task_id: Общая задача, закреплённая составными FK.
            comment_id: Созданный комментарий той же транзакции.
            attachment_ids: Проверенные уникальные ID оригиналов.
        Raises:
            FileCommentsRepositoryError: INSERT или проверка FK завершились ошибкой.
        """
        if not attachment_ids:
            return
        try:
            await self.db_session.execute(
                insert(TaskCommentAttachment).values(
                    [
                        {
                            "task_id": task_id,
                            "comment_id": comment_id,
                            "attachment_id": attachment_id,
                        }
                        for attachment_id in attachment_ids
                    ]
                )
            )
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileCommentsRepositoryError("Не удалось связать файлы с комментарием.") from error
