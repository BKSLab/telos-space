import logging
from typing import Any

from sqlalchemy import Result, delete, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models.board_stickers import BoardSticker, BoardStickerTaskLink
from src.db.models.boards import Board
from src.exceptions.board_stickers import BoardStickersRepositoryError

logger = logging.getLogger(__name__)


class BoardStickersRepository:
    """Репозиторий стикеров проекта и их связей с задачами."""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    @staticmethod
    def _with_task_links(statement):
        """Загружает связи задач и обновляет уже заполненную identity map."""
        return statement.options(selectinload(BoardSticker.task_links)).execution_options(
            populate_existing=True
        )

    @staticmethod
    def _on_active_board(statement):
        """Оставляет только стикеры неудалённых досок."""
        return statement.join(Board, Board.id == BoardSticker.board_id).where(
            Board.archived_at.is_(None)
        )

    async def list_by_project_id(self, project_id: int) -> list[BoardSticker]:
        """Возвращает стикеры всех активных досок проекта от новых к старым."""
        try:
            statement = self._with_task_links(
                self._on_active_board(select(BoardSticker))
                .where(BoardSticker.project_id == project_id)
                .order_by(BoardSticker.created_at.desc(), BoardSticker.id.desc())
            )
            result: Result = await self.db_session.execute(statement)
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить стикеры проекта id=%s.",
                project_id,
                exc_info=True,
            )
            raise BoardStickersRepositoryError(
                f"Ошибка получения стикеров проекта id={project_id}."
            ) from error

    async def list_by_board_id(self, board_id: int) -> list[BoardSticker]:
        """Возвращает стикеры доски снизу вверх по слоям."""
        try:
            statement = self._with_task_links(
                select(BoardSticker)
                .where(BoardSticker.board_id == board_id)
                .order_by(BoardSticker.z_index, BoardSticker.id)
            )
            result: Result = await self.db_session.execute(statement)
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить стикеры доски id=%s.", board_id, exc_info=True)
            raise BoardStickersRepositoryError(
                f"Ошибка получения стикеров доски id={board_id}."
            ) from error

    async def list_by_ids(
        self,
        *,
        board_id: int,
        sticker_ids: set[int],
        for_share: bool = False,
        for_update: bool = False,
    ) -> list[BoardSticker]:
        """Возвращает стикеры доски из набора id.

        Args:
            board_id: Доска стикеров; чужие стикеры не возвращаются.
            sticker_ids: Нужные стикеры.
            for_share: Заблокировать строки от удаления, пока на них ставится привязка.
        """
        if not sticker_ids:
            return []
        try:
            statement = self._with_task_links(
                select(BoardSticker)
                .where(BoardSticker.board_id == board_id, BoardSticker.id.in_(sticker_ids))
                .order_by(BoardSticker.id)
            )
            if for_update:
                statement = statement.with_for_update(of=BoardSticker)
            elif for_share:
                statement = statement.with_for_update(read=True, of=BoardSticker)
            result: Result = await self.db_session.execute(statement)
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить стикеры доски id=%s.", board_id, exc_info=True)
            raise BoardStickersRepositoryError(
                f"Ошибка получения стикеров доски id={board_id}."
            ) from error

    async def get_by_id(
        self, *, project_id: int | None, sticker_id: int, board_id: int | None = None
    ) -> BoardSticker | None:
        """Возвращает стикер активной доски внутри проекта.

        Args:
            project_id: Проект стикера.
            sticker_id: Идентификатор стикера.
            board_id: Если передан — стикер обязан лежать на этой доске.
        """
        try:
            statement = self._on_active_board(select(BoardSticker)).where(
                BoardSticker.id == sticker_id,
                BoardSticker.project_id == project_id,
            )
            if board_id is not None:
                statement = statement.where(BoardSticker.board_id == board_id)
            result: Result = await self.db_session.execute(self._with_task_links(statement))
            return result.scalar_one_or_none()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить стикер id=%s.", sticker_id, exc_info=True)
            raise BoardStickersRepositoryError(
                f"Ошибка получения стикера id={sticker_id}."
            ) from error

    async def insert(self, *, data: dict[str, Any]) -> int:
        """Вставляет стикер и возвращает его идентификатор.

        Связи с задачами и чтение готовой карточки — отдельные операции:
        порядок и транзакция принадлежат сервису.

        Args:
            data: Поля нового стикера.

        Returns:
            Идентификатор созданного стикера.

        Raises:
            BoardStickersRepositoryError: Если вставка не удалась.
        """
        try:
            sticker = BoardSticker(**data, revision=1)
            self.db_session.add(sticker)
            await self.db_session.flush()
            return sticker.id
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось создать стикер проекта.", exc_info=True)
            raise BoardStickersRepositoryError("Ошибка создания стикера проекта.") from error

    async def replace_task_links(self, *, sticker_id: int, task_ids: list[int]) -> None:
        """Заменяет набор связей стикера с задачами.

        Args:
            sticker_id: Идентификатор стикера.
            task_ids: Новый набор задач.

        Raises:
            BoardStickersRepositoryError: Если изменить связи не удалось.
        """
        try:
            await self.db_session.execute(
                delete(BoardStickerTaskLink).where(
                    BoardStickerTaskLink.sticker_id == sticker_id
                )
            )
            if task_ids:
                self.db_session.add_all(
                    [
                        BoardStickerTaskLink(sticker_id=sticker_id, task_id=task_id)
                        for task_id in task_ids
                    ]
                )
            await self.db_session.flush()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось изменить связи стикера id=%s.", sticker_id, exc_info=True)
            raise BoardStickersRepositoryError(
                f"Ошибка изменения связей стикера id={sticker_id}."
            ) from error

    async def update_fields(
        self,
        *,
        project_id: int | None,
        sticker_id: int,
        expected_revision: int,
        changes: dict[str, Any],
    ) -> bool:
        """Обновляет совпавшую ревизию стикера одним запросом.

        Совпадение ревизии проверяется самим `UPDATE`: отдельная проверка
        перед записью оставляла бы окно для параллельной правки.

        Args:
            project_id: Проект стикера.
            sticker_id: Идентификатор стикера.
            expected_revision: Ревизия, которую видел клиент.
            changes: Изменяемые поля.

        Returns:
            ``True``, если ревизия совпала и запись изменена.

        Raises:
            BoardStickersRepositoryError: Если обновление не удалось.
        """
        try:
            statement = (
                update(BoardSticker)
                .where(
                    BoardSticker.id == sticker_id,
                    BoardSticker.project_id == project_id,
                    BoardSticker.revision == expected_revision,
                )
                .values(
                    **changes,
                    revision=BoardSticker.revision + 1,
                    updated_at=func.now(),
                )
                .returning(BoardSticker.id)
            )
            result: Result = await self.db_session.execute(statement)
            return result.scalar_one_or_none() is not None
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось изменить стикер id=%s.", sticker_id, exc_info=True)
            raise BoardStickersRepositoryError(
                f"Ошибка изменения стикера id={sticker_id}."
            ) from error

    async def delete(
        self,
        *,
        project_id: int | None,
        sticker_id: int,
        expected_revision: int,
    ) -> bool:
        """Удаляет только совпавшую ревизию стикера; task links каскадируются."""
        try:
            statement = (
                delete(BoardSticker)
                .where(
                    BoardSticker.id == sticker_id,
                    BoardSticker.project_id == project_id,
                    BoardSticker.revision == expected_revision,
                )
                .returning(BoardSticker.id)
            )
            result: Result = await self.db_session.execute(statement)
            return result.scalar_one_or_none() is not None
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось удалить стикер id=%s.", sticker_id, exc_info=True)
            raise BoardStickersRepositoryError(
                f"Ошибка удаления стикера id={sticker_id}."
            ) from error

    async def update_position(
        self,
        *,
        project_id: int | None,
        sticker_id: int,
        canvas_x: float,
        canvas_y: float,
        width: float | None = None,
        height: float | None = None,
        z_index: int | None = None,
    ) -> bool:
        """Перемещает стикер, не меняя ревизию и дату его содержимого.

        Args:
            project_id: Проект стикера.
            sticker_id: Идентификатор стикера.
            canvas_x: Новая координата по горизонтали.
            canvas_y: Новая координата по вертикали.

        Returns:
            ``True``, если стикер найден и перемещён.

        Raises:
            BoardStickersRepositoryError: Если перемещение не удалось.
        """
        try:
            values: dict[str, Any] = {
                "canvas_x": canvas_x,
                "canvas_y": canvas_y,
                "updated_at": BoardSticker.updated_at,
            }
            if width is not None:
                values["width"] = width
            if height is not None:
                values["height"] = height
            if z_index is not None:
                values["z_index"] = z_index
            statement = (
                update(BoardSticker)
                .where(
                    BoardSticker.id == sticker_id,
                    BoardSticker.project_id == project_id,
                )
                .values(**values)
                .returning(BoardSticker.id)
            )
            result: Result = await self.db_session.execute(statement)
            return result.scalar_one_or_none() is not None
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось переместить стикер id=%s.", sticker_id, exc_info=True)
            raise BoardStickersRepositoryError(
                f"Ошибка перемещения стикера id={sticker_id}."
            ) from error
