"""Пакетная запись связей существующих файлов с комментарием."""

from sqlalchemy import and_, insert, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.document_links import DocumentLink
from src.db.models.task_comment_attachments import TaskCommentAttachment
from src.db.models.task_comment_documents import TaskCommentDocument
from src.exceptions.file_comments import FileCommentsRepositoryError


class TaskCommentAttachmentsRepository:
    """Записывает связи без собственного commit и без повторных оригиналов."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_document_links(
        self, *, task_id: int, sources: list[tuple[str, int]]
    ) -> dict[tuple[str, int], int]:
        """Читает и блокирует связи одного набора документов одним SELECT."""
        if not sources:
            return {}
        try:
            rows = await self.db_session.execute(
                select(
                    DocumentLink.id,
                    DocumentLink.document_id,
                    DocumentLink.project_file_id,
                )
                .where(
                    DocumentLink.task_id == task_id,
                    or_(
                        *[
                            and_(DocumentLink.document_id == identifier)
                            if kind == "document"
                            else and_(DocumentLink.project_file_id == identifier)
                            for kind, identifier in sources
                        ]
                    ),
                )
                .with_for_update()
            )
            return {
                ("document", doc) if doc is not None else ("project_file", file): link
                for link, doc, file in rows
            }
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileCommentsRepositoryError("Не удалось прочитать связи документов.") from error

    async def save_documents(
        self, *, task_id: int, comment_id: int, sources: list[tuple[str, int, int | None]]
    ) -> None:
        """Сохраняет проверенные ссылки; shared-ссылка зависит от связи с задачей."""
        if not sources:
            return
        try:
            values = []
            for kind, identifier, link_id in sources:
                values.append(
                    {
                        "comment_id": comment_id,
                        "task_id": task_id,
                        "document_id": identifier if kind == "document" else None,
                        "project_file_id": identifier if kind == "project_file" else None,
                        "document_link_id": link_id,
                    }
                )
            await self.db_session.execute(insert(TaskCommentDocument).values(values))
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileCommentsRepositoryError(
                "Не удалось связать документы с комментарием."
            ) from error

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
