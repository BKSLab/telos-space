import logging

from sqlalchemy import Result, case, func, literal, or_, select, union_all, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.db.models.project_files import ProjectFile
from src.db.models.task_attachments import TaskAttachment
from src.db.models.task_comment_attachments import TaskCommentAttachment
from src.db.models.task_comment_documents import TaskCommentDocument
from src.db.models.task_comments import TaskComment
from src.exceptions.task_comments import TaskCommentsRepositoryError
from src.repositories.file_processing_state import attachment_processing_query
from src.utils.document_refs import encode_document_ref
from src.utils.fts import (
    EXCERPT_HEADLINE_OPTIONS,
    HIGHLIGHT_START,
    TITLE_HEADLINE_OPTIONS,
    build_ts_query,
)

logger = logging.getLogger(__name__)


class TaskCommentsRepository:
    """Репозиторий комментариев задач канбана."""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def get_document_refs(self, comment_ids: list[int]) -> dict[int, list[str]]:
        """Читает все типы документов страницы без оригиналов и повторного OCR."""
        if not comment_ids:
            return {}
        try:
            sources = await self.db_session.execute(
                union_all(
                    select(
                        TaskCommentAttachment.comment_id,
                        literal("attachment").label("kind"),
                        TaskCommentAttachment.attachment_id.label("source_id"),
                    ).where(TaskCommentAttachment.comment_id.in_(comment_ids)),
                    select(
                        TaskCommentDocument.comment_id,
                        case(
                            (TaskCommentDocument.document_id.is_not(None), "document"),
                            else_="project_file",
                        ).label("kind"),
                        func.coalesce(
                            TaskCommentDocument.document_id, TaskCommentDocument.project_file_id
                        ).label("source_id"),
                    )
                    .outerjoin(ProjectFile, ProjectFile.id == TaskCommentDocument.project_file_id)
                    .where(
                        TaskCommentDocument.comment_id.in_(comment_ids),
                        or_(
                            TaskCommentDocument.project_file_id.is_(None),
                            ProjectFile.deleted_at.is_(None),
                        ),
                    ),
                ).order_by("comment_id", "kind", "source_id")
            )
            result: dict[int, list[str]] = {}
            for comment_id, kind, identifier in sources:
                result.setdefault(comment_id, []).append(encode_document_ref(kind, identifier))
            return result
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise TaskCommentsRepositoryError(
                "Не удалось прочитать документы комментариев."
            ) from error

    async def get_attachment_states(self, comment_ids: list[int]) -> dict[int, dict]:
        """Читает состояния всех файлов страницы комментариев одним JOIN.

        Args:
            comment_ids: Идентификаторы уже выбранных доступных комментариев.
        Returns:
            По одной ограниченной строке состояний на оригинал.
        Raises:
            TaskCommentsRepositoryError: SELECT завершился ошибкой.
        """
        if not comment_ids:
            return {}
        try:
            result = await self.db_session.execute(
                attachment_processing_query()
                .join(
                    TaskCommentAttachment, TaskCommentAttachment.attachment_id == TaskAttachment.id
                )
                .where(TaskCommentAttachment.comment_id.in_(comment_ids))
                .distinct()
            )
            return {
                row["id"]: {key: value for key, value in row.items() if key != "id"}
                for row in result.mappings()
            }
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise TaskCommentsRepositoryError(
                "Не удалось прочитать состояния файлов комментариев."
            ) from error

    async def update_text(
        self, comment_id: int, body_md: str, expected_body_md: str
    ) -> TaskComment | None:
        """Обновляет только прочитанную версию комментария одним UPDATE RETURNING."""
        try:
            result = await self.db_session.execute(
                update(TaskComment)
                .where(
                    TaskComment.id == comment_id,
                    TaskComment.body_md == expected_body_md,
                )
                .values(body_md=body_md)
                .returning(TaskComment)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise TaskCommentsRepositoryError("Не удалось обновить комментарий.") from error

    async def get_for_task(self, task_id: int) -> list[TaskComment]:
        """Возвращает комментарии задачи в хронологическом порядке.

        Args:
            task_id: Идентификатор задачи.

        Returns:
            Комментарии задачи.

        Raises:
            TaskCommentsRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(TaskComment)
                .options(joinedload(TaskComment.author), selectinload(TaskComment.attachments))
                .where(TaskComment.task_id == task_id)
                .order_by(TaskComment.created_at, TaskComment.id)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить комментарии задачи id=%s.", task_id, exc_info=True)
            raise TaskCommentsRepositoryError(
                f"Ошибка получения комментариев задачи id={task_id}."
            ) from error

    async def get_all(self) -> list[TaskComment]:
        """Возвращает все комментарии для построения агрегатов карточек.

        Args:
            Нет дополнительных аргументов.

        Returns:
            Комментарии всех задач.

        Raises:
            TaskCommentsRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(TaskComment).order_by(TaskComment.task_id, TaskComment.created_at)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить комментарии задач.", exc_info=True)
            raise TaskCommentsRepositoryError("Ошибка получения комментариев задач.") from error

    async def get_for_tasks(self, task_ids: set[int]) -> list[TaskComment]:
        """Возвращает комментарии заданного набора задач."""
        if not task_ids:
            return []
        try:
            result: Result = await self.db_session.execute(
                select(TaskComment)
                .where(TaskComment.task_id.in_(task_ids))
                .order_by(TaskComment.task_id, TaskComment.created_at, TaskComment.id)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить комментарии набора задач.", exc_info=True)
            raise TaskCommentsRepositoryError("Ошибка получения комментариев задач.") from error

    async def get_for_update(self, comment_id: int) -> TaskComment | None:
        """Блокирует источник публикации до окончания транзакции."""
        try:
            result = await self.db_session.execute(
                select(TaskComment)
                .where(TaskComment.id == comment_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise TaskCommentsRepositoryError("Не удалось заблокировать комментарий.") from error

    async def get_by_id(self, comment_id: int) -> TaskComment | None:
        """Возвращает комментарий по идентификатору.

        Args:
            comment_id: Идентификатор комментария.

        Returns:
            Найденный комментарий или ``None``.

        Raises:
            TaskCommentsRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(TaskComment)
                .options(joinedload(TaskComment.author), selectinload(TaskComment.attachments))
                .where(TaskComment.id == comment_id)
                .execution_options(populate_existing=True)
            )
            return result.scalar_one_or_none()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить комментарий id=%s.", comment_id, exc_info=True)
            raise TaskCommentsRepositoryError(
                f"Ошибка получения комментария id={comment_id}."
            ) from error

    async def search_task_ids(self, search: str) -> set[int]:
        """Возвращает id задач с совпадениями в комментариях.

        Args:
            search: Полнотекстовый запрос.

        Returns:
            Идентификаторы задач с совпадениями.

        Raises:
            TaskCommentsRepositoryError: Если поиск завершился ошибкой.
        """
        try:
            ts_query = build_ts_query(search)
            result: Result = await self.db_session.execute(
                select(TaskComment.task_id).where(TaskComment.search_vector.op("@@")(ts_query))
            )
            return set(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось выполнить FTS-поиск комментариев.", exc_info=True)
            raise TaskCommentsRepositoryError("Ошибка поиска по комментариям.") from error

    async def get_search_highlights(
        self,
        task_ids: list[int],
        search: str,
    ) -> dict[int, dict[str, str]]:
        """Возвращает последнее подсвеченное совпадение комментария для каждой задачи.

        Args:
            task_ids: Идентификаторы отображаемых задач.
            search: Полнотекстовый запрос.

        Returns:
            Поисковый контекст по идентификаторам задач.

        Raises:
            TaskCommentsRepositoryError: Если запрос к БД завершился ошибкой.
        """
        if not task_ids or not search.strip():
            return {}
        try:
            ts_query = build_ts_query(search)
            stmt = (
                select(
                    TaskComment.task_id,
                    func.ts_headline(
                        "russian", TaskComment.body_md, ts_query, EXCERPT_HEADLINE_OPTIONS
                    ).label("body_headline"),
                    func.ts_headline(
                        "russian",
                        func.coalesce(TaskComment.author_name, ""),
                        ts_query,
                        TITLE_HEADLINE_OPTIONS,
                    ).label("author_headline"),
                )
                .where(
                    TaskComment.task_id.in_(task_ids),
                    TaskComment.search_vector.op("@@")(ts_query),
                )
                .order_by(TaskComment.task_id, TaskComment.created_at.desc())
            )
            rows = (await self.db_session.execute(stmt)).mappings().all()
            highlights: dict[int, dict[str, str]] = {}
            for row in rows:
                task_id = row["task_id"]
                if task_id in highlights:
                    continue
                if HIGHLIGHT_START in row["body_headline"]:
                    highlights[task_id] = {
                        "search_match_source": "comment",
                        "search_excerpt": row["body_headline"],
                    }
                elif HIGHLIGHT_START in row["author_headline"]:
                    highlights[task_id] = {
                        "search_match_source": "comment_author",
                        "search_excerpt": row["author_headline"],
                    }
            return highlights
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось подготовить подсветку комментариев.", exc_info=True)
            raise TaskCommentsRepositoryError("Ошибка подсветки комментариев.") from error

    async def save(
        self, task_id: int, author_name: str | None, body_md: str, *, author_id: int | None = None
    ) -> TaskComment:
        """Создаёт комментарий задачи.

        Args:
            task_id: Идентификатор задачи.
            author_id: Автор — пользователь единого входа.
            author_name: Снимок полного имени автора.
            body_md: Текст комментария.

        Returns:
            Сохранённый комментарий.

        Raises:
            TaskCommentsRepositoryError: Если сохранить комментарий не удалось.
        """
        try:
            comment = TaskComment(
                task_id=task_id, author_id=author_id, author_name=author_name, body_md=body_md
            )
            self.db_session.add(comment)
            await self.db_session.flush()
            return comment
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось сохранить комментарий задачи id=%s.", task_id, exc_info=True
            )
            raise TaskCommentsRepositoryError(
                f"Ошибка сохранения комментария задачи id={task_id}."
            ) from error

    async def delete(self, comment: TaskComment) -> None:
        """Удаляет комментарий задачи.

        Args:
            comment: Удаляемая ORM-модель комментария.

        Returns:
            ``None`` после успешного удаления.

        Raises:
            TaskCommentsRepositoryError: Если удалить комментарий не удалось.
        """
        try:
            await self.db_session.delete(comment)
            await self.db_session.flush()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось удалить комментарий id=%s.", comment.id, exc_info=True)
            raise TaskCommentsRepositoryError(
                f"Ошибка удаления комментария id={comment.id}."
            ) from error
