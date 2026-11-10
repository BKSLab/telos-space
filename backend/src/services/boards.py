"""Доски проекта: вкладки холста и выдача содержимого доски одним запросом."""

import logging

from src.db.models.boards import Board
from src.exceptions.board_shapes import BoardShapesRepositoryError
from src.exceptions.board_stickers import BoardStickersRepositoryError
from src.exceptions.boards import (
    BoardNotFoundError,
    BoardRevisionConflictError,
    BoardsRepositoryError,
    BoardsServiceError,
    ProjectLastBoardError,
)
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.repositories.board_shapes import BoardShapesRepository
from src.repositories.board_stickers import BoardStickersRepository
from src.repositories.boards import BoardsRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.boards import (
    BoardContentSchema,
    BoardCreateSchema,
    BoardSchema,
    BoardUpdateSchema,
)
from src.services.board_defaults import ensure_default_board
from src.services.board_shapes import to_shape_schema
from src.services.board_stickers import to_sticker_schema

logger = logging.getLogger(__name__)
RepositoryErrors = (
    BoardsRepositoryError,
    BoardStickersRepositoryError,
    BoardShapesRepositoryError,
    UnitOfWorkRepositoryError,
)


class BoardsService:
    """Набор досок проекта: список, создание, переименование, мягкое удаление."""

    def __init__(
        self,
        *,
        boards_repository: BoardsRepository,
        stickers_repository: BoardStickersRepository,
        shapes_repository: BoardShapesRepository,
        unit_of_work: UnitOfWork,
    ):
        self.boards_repository = boards_repository
        self.stickers_repository = stickers_repository
        self.shapes_repository = shapes_repository
        self.unit_of_work = unit_of_work

    async def list_boards(self, project_id: int, *, user_id: int) -> list[BoardSchema]:
        """Возвращает активные доски; проекту без досок заводит «Основную»."""
        try:
            boards = await self.boards_repository.list_active(project_id)
            if not boards:
                await ensure_default_board(
                    self.boards_repository, project_id, created_by_user_id=user_id
                )
                boards = await self.boards_repository.list_active(project_id)
                await self.unit_of_work.commit()
            return [BoardSchema.model_validate(board) for board in boards]
        except RepositoryErrors as error:
            logger.error("❌ Ошибка получения досок проекта id=%s.", project_id, exc_info=True)
            raise BoardsServiceError(str(error)) from error

    async def create_board(
        self, *, project_id: int, data: BoardCreateSchema, user_id: int
    ) -> BoardSchema:
        """Создаёт доску последней вкладкой."""
        try:
            await self.boards_repository.lock_project_boards(project_id)
            position = await self.boards_repository.get_max_position(project_id) + 1
            board = await self.boards_repository.insert(
                data={
                    "project_id": project_id,
                    "title": data.title,
                    "position": position,
                    "created_by_user_id": user_id,
                }
            )
            await self.unit_of_work.commit()
            return BoardSchema.model_validate(board)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка создания доски проекта id=%s.", project_id, exc_info=True)
            raise BoardsServiceError(str(error)) from error

    async def update_board(
        self, *, project_id: int, board_id: int, data: BoardUpdateSchema
    ) -> BoardSchema:
        """Переименовывает или переставляет доску по ожидаемой ревизии."""
        try:
            board = await self.boards_repository.update_fields(
                project_id=project_id,
                board_id=board_id,
                expected_revision=data.revision,
                changes=data.model_dump(exclude={"revision"}, exclude_unset=True),
            )
            if board is None:
                await self._require_board(project_id, board_id)
                raise BoardRevisionConflictError(board_id, data.revision)
            await self.unit_of_work.commit()
            return BoardSchema.model_validate(board)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка изменения доски id=%s.", board_id, exc_info=True)
            raise BoardsServiceError(str(error)) from error

    async def delete_board(self, *, project_id: int, board_id: int, revision: int) -> None:
        """Мягко удаляет доску; последнюю активную удалить нельзя."""
        try:
            await self.boards_repository.lock_project_boards(project_id)
            board = await self._require_board(project_id, board_id)
            if board.revision != revision:
                raise BoardRevisionConflictError(board_id, revision)
            if await self.boards_repository.count_active(project_id) <= 1:
                raise ProjectLastBoardError(board_id)
            archived = await self.boards_repository.archive(
                project_id=project_id, board_id=board_id, expected_revision=revision
            )
            if not archived:
                raise BoardRevisionConflictError(board_id, revision)
            await self.unit_of_work.commit()
        except RepositoryErrors as error:
            logger.error("❌ Ошибка удаления доски id=%s.", board_id, exc_info=True)
            raise BoardsServiceError(str(error)) from error

    async def get_content(self, *, project_id: int, board_id: int) -> BoardContentSchema:
        """Возвращает доску, её стикеры и фигуры одним ответом."""
        try:
            board = await self._require_board(project_id, board_id)
            stickers = await self.stickers_repository.list_by_board_id(board_id)
            shapes = await self.shapes_repository.list_by_board(board_id)
            return BoardContentSchema(
                board=BoardSchema.model_validate(board),
                stickers=[to_sticker_schema(item) for item in stickers],
                shapes=[to_shape_schema(item) for item in shapes],
            )
        except RepositoryErrors as error:
            logger.error("❌ Ошибка чтения доски id=%s.", board_id, exc_info=True)
            raise BoardsServiceError(str(error)) from error

    async def _require_board(self, project_id: int, board_id: int) -> Board:
        """Возвращает активную доску проекта или 404."""
        board = await self.boards_repository.get_active(project_id=project_id, board_id=board_id)
        if board is None:
            raise BoardNotFoundError(board_id)
        return board

    # --- Свободные (личные) доски: project_id IS NULL, доступ только автору. ---

    async def list_free_boards(self, *, user_id: int) -> list[BoardSchema]:
        """Возвращает активные свободные доски пользователя; «Основную» не заводит."""
        try:
            boards = await self.boards_repository.list_active_free(user_id)
            return [BoardSchema.model_validate(board) for board in boards]
        except RepositoryErrors as error:
            logger.error(
                "❌ Ошибка получения свободных досок пользователя id=%s.", user_id, exc_info=True
            )
            raise BoardsServiceError(str(error)) from error

    async def create_free_board(
        self, *, data: BoardCreateSchema, user_id: int
    ) -> BoardSchema:
        """Создаёт свободную доску последней вкладкой среди досок владельца."""
        try:
            await self.boards_repository.lock_free_boards(user_id)
            position = await self.boards_repository.get_max_position_free(user_id) + 1
            board = await self.boards_repository.insert(
                data={
                    "project_id": None,
                    "title": data.title,
                    "position": position,
                    "created_by_user_id": user_id,
                }
            )
            await self.unit_of_work.commit()
            return BoardSchema.model_validate(board)
        except RepositoryErrors as error:
            logger.error(
                "❌ Ошибка создания свободной доски пользователя id=%s.", user_id, exc_info=True
            )
            raise BoardsServiceError(str(error)) from error

    async def update_free_board(
        self, *, board_id: int, data: BoardUpdateSchema, user_id: int
    ) -> BoardSchema:
        """Переименовывает или переставляет свободную доску владельца."""
        try:
            board = await self.boards_repository.update_fields_free(
                board_id=board_id,
                owner_user_id=user_id,
                expected_revision=data.revision,
                changes=data.model_dump(exclude={"revision"}, exclude_unset=True),
            )
            if board is None:
                await self._require_free_board(board_id, user_id)
                raise BoardRevisionConflictError(board_id, data.revision)
            await self.unit_of_work.commit()
            return BoardSchema.model_validate(board)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка изменения свободной доски id=%s.", board_id, exc_info=True)
            raise BoardsServiceError(str(error)) from error

    async def delete_free_board(self, *, board_id: int, revision: int, user_id: int) -> None:
        """Мягко удаляет свободную доску; последнюю удалить можно."""
        try:
            await self.boards_repository.lock_free_boards(user_id)
            board = await self._require_free_board(board_id, user_id)
            if board.revision != revision:
                raise BoardRevisionConflictError(board_id, revision)
            archived = await self.boards_repository.archive_free(
                board_id=board_id, owner_user_id=user_id, expected_revision=revision
            )
            if not archived:
                raise BoardRevisionConflictError(board_id, revision)
            await self.unit_of_work.commit()
        except RepositoryErrors as error:
            logger.error("❌ Ошибка удаления свободной доски id=%s.", board_id, exc_info=True)
            raise BoardsServiceError(str(error)) from error

    async def get_free_content(self, *, board_id: int, user_id: int) -> BoardContentSchema:
        """Возвращает свободную доску владельца, её стикеры и фигуры одним ответом."""
        try:
            board = await self._require_free_board(board_id, user_id)
            stickers = await self.stickers_repository.list_by_board_id(board_id)
            shapes = await self.shapes_repository.list_by_board(board_id)
            return BoardContentSchema(
                board=BoardSchema.model_validate(board),
                stickers=[to_sticker_schema(item) for item in stickers],
                shapes=[to_shape_schema(item) for item in shapes],
            )
        except RepositoryErrors as error:
            logger.error("❌ Ошибка чтения свободной доски id=%s.", board_id, exc_info=True)
            raise BoardsServiceError(str(error)) from error

    async def _require_free_board(self, board_id: int, user_id: int) -> Board:
        """Возвращает активную свободную доску владельца или 404.

        Чужая свободная доска неотличима от несуществующей: факт её наличия
        у другого пользователя не раскрывается.
        """
        board = await self.boards_repository.get_active_free(
            board_id=board_id, owner_user_id=user_id
        )
        if board is None:
            raise BoardNotFoundError(board_id)
        return board
