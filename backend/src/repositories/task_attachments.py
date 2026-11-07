import logging

from sqlalchemy import Result, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.task_attachments import TaskAttachment
from src.exceptions.task_attachments import TaskAttachmentsRepositoryError
from src.repositories.file_processing_state import attachment_processing_query

logger = logging.getLogger(__name__)


class TaskAttachmentsRepository:
    """Репозиторий метаданных файлов задач."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_processing_states(self, attachment_ids: list[int]) -> dict[int, dict]:
        """Читает состояния пачки файлов без N+1 и без полного извлечённого текста.

        Args:
            attachment_ids: Идентификаторы уже доступных пользователю оригиналов.
        Returns:
            Состояния этапов по ID оригинала.
        Raises:
            TaskAttachmentsRepositoryError: SELECT завершился ошибкой.
        """
        if not attachment_ids:
            return {}
        try:
            result = await self.db_session.execute(
                attachment_processing_query().where(TaskAttachment.id.in_(attachment_ids))
            )
            return {
                row["id"]: {key: value for key, value in row.items() if key != "id"}
                for row in result.mappings()
            }
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise TaskAttachmentsRepositoryError(
                "Не удалось прочитать состояния файлов задачи."
            ) from error

    async def get_for_task(self, task_id: int) -> list[TaskAttachment]:
        """Возвращает файлы задачи в хронологическом порядке.

        Args:
            task_id: Идентификатор задачи.

        Returns:
            Метаданные файлов задачи.

        Raises:
            TaskAttachmentsRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(TaskAttachment)
                .where(TaskAttachment.task_id == task_id)
                .order_by(TaskAttachment.created_at, TaskAttachment.id)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить файлы задачи id=%s.", task_id, exc_info=True)
            raise TaskAttachmentsRepositoryError(
                f"Ошибка получения файлов задачи id={task_id}."
            ) from error

    async def get_for_tasks(self, task_ids: set[int]) -> list[TaskAttachment]:
        """Возвращает файлы заданного набора задач."""
        if not task_ids:
            return []
        try:
            result: Result = await self.db_session.execute(
                select(TaskAttachment)
                .where(TaskAttachment.task_id.in_(task_ids))
                .order_by(TaskAttachment.task_id, TaskAttachment.created_at, TaskAttachment.id)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить файлы набора задач.", exc_info=True)
            raise TaskAttachmentsRepositoryError("Ошибка получения файлов задач.") from error

    async def get_by_id(self, attachment_id: int) -> TaskAttachment | None:
        """Возвращает метаданные файла по идентификатору."""
        try:
            return await self.db_session.get(TaskAttachment, attachment_id)
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить файл id=%s.", attachment_id, exc_info=True)
            raise TaskAttachmentsRepositoryError(
                f"Ошибка получения файла задачи id={attachment_id}."
            ) from error

    async def get_by_id_for_task(
        self,
        attachment_id: int,
        task_id: int,
    ) -> TaskAttachment | None:
        """Возвращает файл только в пределах указанной задачи.

        Args:
            attachment_id: Идентификатор файла.
            task_id: Идентификатор задачи-владельца.

        Returns:
            Найденный файл или ``None``.

        Raises:
            TaskAttachmentsRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(TaskAttachment).where(
                    TaskAttachment.id == attachment_id,
                    TaskAttachment.task_id == task_id,
                )
            )
            return result.scalar_one_or_none()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить файл задачи id=%s.", attachment_id, exc_info=True)
            raise TaskAttachmentsRepositoryError(
                f"Ошибка получения файла задачи id={attachment_id}."
            ) from error

    async def get_count_for_task(self, task_id: int) -> int:
        """Возвращает количество файлов задачи.

        Args:
            task_id: Идентификатор задачи.

        Returns:
            Количество файлов задачи.

        Raises:
            TaskAttachmentsRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(func.count())
                .select_from(TaskAttachment)
                .where(TaskAttachment.task_id == task_id)
            )
            return int(result.scalar_one())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось подсчитать файлы задачи id=%s.", task_id, exc_info=True)
            raise TaskAttachmentsRepositoryError(
                f"Ошибка подсчёта файлов задачи id={task_id}."
            ) from error

    async def save(
        self,
        *,
        task_id: int,
        original_name: str,
        storage_key: str,
        content_type: str,
        size: int,
        caption: str | None = None,
        uploaded_by_user_id: int | None = None,
    ) -> TaskAttachment:
        """Сохраняет метаданные нового файла задачи.

        Args:
            task_id: Идентификатор задачи.
            original_name: Исходное имя файла.
            storage_key: Уникальный относительный ключ хранилища.
            content_type: MIME-тип файла.
            size: Размер файла в байтах.

        Returns:
            Сохранённая модель файла.

        Raises:
            TaskAttachmentsRepositoryError: Если сохранить метаданные не удалось.
        """
        try:
            attachment = TaskAttachment(
                task_id=task_id,
                original_name=original_name,
                storage_key=storage_key,
                content_type=content_type,
                size=size,
                caption=caption,
                uploaded_by_user_id=uploaded_by_user_id,
            )
            self.db_session.add(attachment)
            await self.db_session.flush()
            return attachment
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось сохранить файл задачи id=%s.", task_id, exc_info=True)
            raise TaskAttachmentsRepositoryError(
                f"Ошибка сохранения файла задачи id={task_id}."
            ) from error

    async def get_many_for_task(
        self, *, task_id: int, attachment_ids: list[int]
    ) -> list[TaskAttachment]:
        """Читает ограниченный набор существующих оригиналов одной задачи.

        Args:
            task_id: Задача-владелец всех выбранных оригиналов.
            attachment_ids: Проверенные уникальные идентификаторы, не более десяти.
        Returns:
            Найденные оригиналы без запросов на каждый файл.
        Raises:
            TaskAttachmentsRepositoryError: SQL завершился ошибкой.
        """
        try:
            result = await self.db_session.execute(
                select(TaskAttachment).where(
                    TaskAttachment.task_id == task_id, TaskAttachment.id.in_(attachment_ids)
                )
            )
            return list(result.scalars())
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise TaskAttachmentsRepositoryError(
                "Не удалось прочитать выбранные файлы задачи."
            ) from error

    async def update_caption(
        self, *, task_id: int, attachment_id: int, caption: str | None
    ) -> TaskAttachment | None:
        """Меняет только подпись оригинала с ограничением задачи и RETURNING.

        Args:
            task_id: Задача-владелец.
            attachment_id: Оригинал для изменения подписи.
            caption: Нормализованная подпись либо None.
        Returns:
            Изменённый файл или None для чужого/удалённого оригинала.
        Raises:
            TaskAttachmentsRepositoryError: UPDATE завершился ошибкой.
        """
        try:
            result = await self.db_session.execute(
                update(TaskAttachment)
                .where(TaskAttachment.task_id == task_id, TaskAttachment.id == attachment_id)
                .values(caption=caption)
                .returning(TaskAttachment)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise TaskAttachmentsRepositoryError(
                "Не удалось изменить подпись файла задачи."
            ) from error

    async def delete(self, attachment: TaskAttachment) -> None:
        """Удаляет метаданные файла задачи.

        Args:
            attachment: Удаляемая ORM-модель файла.

        Returns:
            ``None`` после успешного удаления.

        Raises:
            TaskAttachmentsRepositoryError: Если удалить метаданные не удалось.
        """
        try:
            await self.db_session.delete(attachment)
            await self.db_session.flush()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось удалить файл задачи id=%s.", attachment.id, exc_info=True)
            raise TaskAttachmentsRepositoryError(
                f"Ошибка удаления файла задачи id={attachment.id}."
            ) from error
