import logging
from typing import Any

from sqlalchemy import Result, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.boards import Board
from src.exceptions.boards import BoardsRepositoryError

logger = logging.getLogger(__name__)

# Пространство advisory-блокировок досок: первый ключ отличает их от других
# сценариев, второй — проект. Блокировка живёт до конца транзакции.
BOARDS_LOCK_NAMESPACE = 7301

# Свободные доски: второй ключ блокировки — владелец доски, а не проект.
FREE_BOARDS_LOCK_NAMESPACE = 7302


class BoardsRepository:
    """Репозиторий досок проекта."""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def _fail(self, message: str) -> BoardsRepositoryError:
        """Откатывает сессию и готовит ошибку слоя репозитория."""
        await self.db_session.rollback()
        logger.error("❌ %s", message, exc_info=True)
        return BoardsRepositoryError(message)

    @staticmethod
    def _active(project_id: int):
        """Активные доски проекта в порядке вкладок."""
        return (
            select(Board)
            .where(Board.project_id == project_id, Board.archived_at.is_(None))
            .order_by(Board.position, Board.id)
        )

    async def lock_project_boards(self, project_id: int) -> None:
        """Сериализует изменения набора досок проекта до конца транзакции.

        Без неё два параллельных запроса могли бы оба создать «Основную»
        или удалить две последние доски, каждый думая, что осталась ещё одна.
        """
        try:
            await self.db_session.execute(
                select(func.pg_advisory_xact_lock(BOARDS_LOCK_NAMESPACE, project_id))
            )
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(f"Ошибка блокировки досок проекта id={project_id}.") from error

    async def list_active(self, project_id: int) -> list[Board]:
        """Возвращает активные доски проекта по position, затем id."""
        try:
            result: Result = await self.db_session.execute(self._active(project_id))
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(f"Ошибка получения досок проекта id={project_id}.") from error

    async def get_first_active(self, project_id: int) -> Board | None:
        """Возвращает первую вкладку проекта."""
        try:
            result: Result = await self.db_session.execute(self._active(project_id).limit(1))
            return result.scalar_one_or_none()
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(
                f"Ошибка получения первой доски проекта id={project_id}."
            ) from error

    async def get_active(self, *, project_id: int, board_id: int) -> Board | None:
        """Возвращает активную доску только внутри указанного проекта."""
        try:
            result: Result = await self.db_session.execute(
                select(Board).where(
                    Board.id == board_id,
                    Board.project_id == project_id,
                    Board.archived_at.is_(None),
                )
            )
            return result.scalar_one_or_none()
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(f"Ошибка получения доски id={board_id}.") from error

    async def count_active(self, project_id: int) -> int:
        """Считает активные доски проекта."""
        try:
            result: Result = await self.db_session.execute(
                select(func.count(Board.id)).where(
                    Board.project_id == project_id, Board.archived_at.is_(None)
                )
            )
            return int(result.scalar_one())
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(f"Ошибка подсчёта досок проекта id={project_id}.") from error

    async def get_max_position(self, project_id: int) -> int:
        """Возвращает наибольшую позицию активной доски или -1, если досок нет."""
        try:
            result: Result = await self.db_session.execute(
                select(func.coalesce(func.max(Board.position), -1)).where(
                    Board.project_id == project_id, Board.archived_at.is_(None)
                )
            )
            return int(result.scalar_one())
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(
                f"Ошибка получения позиции досок проекта id={project_id}."
            ) from error

    async def insert(self, *, data: dict[str, Any]) -> Board:
        """Вставляет доску; серверные поля приходят из RETURNING."""
        try:
            board = Board(**data, revision=1)
            self.db_session.add(board)
            await self.db_session.flush()
            logger.info("💾 Создана доска id=%s проекта id=%s.", board.id, board.project_id)
            return board
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail("Ошибка создания доски проекта.") from error

    async def update_fields(
        self,
        *,
        project_id: int,
        board_id: int,
        expected_revision: int,
        changes: dict[str, Any],
    ) -> Board | None:
        """Меняет активную доску совпавшей ревизии одним запросом.

        Returns:
            Изменённая доска или ``None``, если ревизия не совпала.
        """
        try:
            result: Result = await self.db_session.execute(
                update(Board)
                .where(
                    Board.id == board_id,
                    Board.project_id == project_id,
                    Board.archived_at.is_(None),
                    Board.revision == expected_revision,
                )
                .values(**changes, revision=Board.revision + 1, updated_at=func.now())
                .returning(Board)
                .execution_options(populate_existing=True)
            )
            return result.scalar_one_or_none()
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(f"Ошибка изменения доски id={board_id}.") from error

    # --- Свободные (личные) доски: project_id IS NULL, владелец — автор. ---

    @staticmethod
    def _active_free(owner_user_id: int):
        """Активные свободные доски владельца в порядке вкладок."""
        return (
            select(Board)
            .where(
                Board.project_id.is_(None),
                Board.created_by_user_id == owner_user_id,
                Board.archived_at.is_(None),
            )
            .order_by(Board.position, Board.id)
        )

    async def lock_free_boards(self, owner_user_id: int) -> None:
        """Сериализует изменения набора свободных досок владельца до конца транзакции."""
        try:
            await self.db_session.execute(
                select(func.pg_advisory_xact_lock(FREE_BOARDS_LOCK_NAMESPACE, owner_user_id))
            )
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(
                f"Ошибка блокировки свободных досок пользователя id={owner_user_id}."
            ) from error

    async def list_active_free(self, owner_user_id: int) -> list[Board]:
        """Возвращает активные свободные доски владельца по position, затем id."""
        try:
            result: Result = await self.db_session.execute(self._active_free(owner_user_id))
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(
                f"Ошибка получения свободных досок пользователя id={owner_user_id}."
            ) from error

    async def get_active_free(self, *, board_id: int, owner_user_id: int) -> Board | None:
        """Возвращает активную свободную доску, только если она принадлежит владельцу."""
        try:
            result: Result = await self.db_session.execute(
                select(Board).where(
                    Board.id == board_id,
                    Board.project_id.is_(None),
                    Board.created_by_user_id == owner_user_id,
                    Board.archived_at.is_(None),
                )
            )
            return result.scalar_one_or_none()
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(f"Ошибка получения свободной доски id={board_id}.") from error

    async def count_active_free(self, owner_user_id: int) -> int:
        """Считает активные свободные доски владельца."""
        try:
            result: Result = await self.db_session.execute(
                select(func.count(Board.id)).where(
                    Board.project_id.is_(None),
                    Board.created_by_user_id == owner_user_id,
                    Board.archived_at.is_(None),
                )
            )
            return int(result.scalar_one())
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(
                f"Ошибка подсчёта свободных досок пользователя id={owner_user_id}."
            ) from error

    async def get_max_position_free(self, owner_user_id: int) -> int:
        """Возвращает наибольшую позицию свободной доски владельца или -1."""
        try:
            result: Result = await self.db_session.execute(
                select(func.coalesce(func.max(Board.position), -1)).where(
                    Board.project_id.is_(None),
                    Board.created_by_user_id == owner_user_id,
                    Board.archived_at.is_(None),
                )
            )
            return int(result.scalar_one())
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(
                f"Ошибка получения позиции свободных досок пользователя id={owner_user_id}."
            ) from error

    async def update_fields_free(
        self,
        *,
        board_id: int,
        owner_user_id: int,
        expected_revision: int,
        changes: dict[str, Any],
    ) -> Board | None:
        """Меняет активную свободную доску владельца совпавшей ревизии одним запросом."""
        try:
            result: Result = await self.db_session.execute(
                update(Board)
                .where(
                    Board.id == board_id,
                    Board.project_id.is_(None),
                    Board.created_by_user_id == owner_user_id,
                    Board.archived_at.is_(None),
                    Board.revision == expected_revision,
                )
                .values(**changes, revision=Board.revision + 1, updated_at=func.now())
                .returning(Board)
                .execution_options(populate_existing=True)
            )
            return result.scalar_one_or_none()
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(f"Ошибка изменения свободной доски id={board_id}.") from error

    async def archive_free(
        self, *, board_id: int, owner_user_id: int, expected_revision: int
    ) -> bool:
        """Мягко удаляет активную свободную доску владельца совпавшей ревизии."""
        try:
            result: Result = await self.db_session.execute(
                update(Board)
                .where(
                    Board.id == board_id,
                    Board.project_id.is_(None),
                    Board.created_by_user_id == owner_user_id,
                    Board.archived_at.is_(None),
                    Board.revision == expected_revision,
                )
                .values(
                    archived_at=func.now(),
                    revision=Board.revision + 1,
                    updated_at=func.now(),
                )
                .returning(Board.id)
            )
            archived = result.scalar_one_or_none() is not None
            if archived:
                logger.info(
                    "🗑️ Свободная доска id=%s пользователя id=%s в архиве.", board_id, owner_user_id
                )
            return archived
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(f"Ошибка удаления свободной доски id={board_id}.") from error

    async def archive(self, *, project_id: int, board_id: int, expected_revision: int) -> bool:
        """Мягко удаляет активную доску совпавшей ревизии."""
        try:
            result: Result = await self.db_session.execute(
                update(Board)
                .where(
                    Board.id == board_id,
                    Board.project_id == project_id,
                    Board.archived_at.is_(None),
                    Board.revision == expected_revision,
                )
                .values(
                    archived_at=func.now(),
                    revision=Board.revision + 1,
                    updated_at=func.now(),
                )
                .returning(Board.id)
            )
            archived = result.scalar_one_or_none() is not None
            if archived:
                logger.info("🗑️ Доска id=%s проекта id=%s в архиве.", board_id, project_id)
            return archived
        except (SQLAlchemyError, Exception) as error:
            raise await self._fail(f"Ошибка удаления доски id={board_id}.") from error
