import logging

from src.db.models.board_stickers import BoardSticker
from src.db.models.boards import Board
from src.exceptions.board_shapes import BoardShapesRepositoryError
from src.exceptions.board_stickers import (
    BoardStickerLockedError,
    BoardStickerNotFoundError,
    BoardStickerRevisionConflictError,
    BoardStickersRepositoryError,
    BoardStickersServiceError,
    BoardStickerTaskMismatchError,
    FreeBoardTaskLinksError,
)
from src.exceptions.boards import BoardNotFoundError, BoardsRepositoryError
from src.exceptions.tasks import TasksRepositoryError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.repositories.board_editor import BoardEditorRepository
from src.repositories.board_shapes import BoardShapesRepository
from src.repositories.board_stickers import BoardStickersRepository
from src.repositories.boards import BoardsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.board_editor import BoardEditorMetadata
from src.schemas.board_stickers import (
    BoardStickerCreateSchema,
    BoardStickerPositionUpdateSchema,
    BoardStickerSchema,
    BoardStickerUpdateSchema,
)
from src.services.board_defaults import ensure_default_board
from src.services.board_mutations import mutation_is_locked, rewrite_metadata_refs

logger = logging.getLogger(__name__)
RepositoryErrors = (
    BoardStickersRepositoryError,
    BoardsRepositoryError,
    BoardShapesRepositoryError,
    TasksRepositoryError,
    UnitOfWorkRepositoryError,
)


class BoardStickersService:
    """CRUD стикеров доски, авторство и безопасные связи с задачами проекта.

    ``board_id=None`` допустим только у агента: список и создание берут
    первую активную доску проекта, а правка находит стикер на любой
    активной доске. HTTP всегда передаёт доску из пути.
    """

    def __init__(
        self,
        *,
        stickers_repository: BoardStickersRepository,
        tasks_repository: TasksRepository,
        boards_repository: BoardsRepository,
        shapes_repository: BoardShapesRepository,
        unit_of_work: UnitOfWork,
        editor_repository: BoardEditorRepository | None = None,
    ):
        self.stickers_repository = stickers_repository
        self.tasks_repository = tasks_repository
        self.boards_repository = boards_repository
        self.shapes_repository = shapes_repository
        self.unit_of_work = unit_of_work
        self.editor_repository = editor_repository

    async def list_stickers(
        self, project_id: int | None, board_id: int | None = None, *, user_id: int | None = None
    ) -> list[BoardStickerSchema]:
        """Возвращает стикеры доски снизу вверх по слоям."""
        try:
            board = await self._resolve_board(project_id, board_id, user_id=user_id)
            stickers = await self.stickers_repository.list_by_board_id(board.id)
            if board_id is None:
                # Первая доска могла быть только что заведена страховкой.
                await self.unit_of_work.commit()
            return [to_sticker_schema(sticker) for sticker in stickers]
        except RepositoryErrors as error:
            logger.error(
                "❌ Ошибка получения стикеров проекта id=%s.",
                project_id,
                exc_info=True,
            )
            raise BoardStickersServiceError(str(error)) from error

    async def get_sticker(
        self,
        *,
        project_id: int | None,
        sticker_id: int,
        board_id: int | None = None,
        user_id: int | None = None,
    ) -> BoardStickerSchema:
        """Возвращает стикер активной доски проекта или свободной доски владельца."""
        try:
            await self._require_board_scope(project_id, board_id, user_id)
            return to_sticker_schema(await self._require_sticker(project_id, sticker_id, board_id))
        except RepositoryErrors as error:
            logger.error("❌ Ошибка получения стикера id=%s.", sticker_id, exc_info=True)
            raise BoardStickersServiceError(str(error)) from error

    async def create_sticker(
        self,
        *,
        project_id: int | None,
        data: BoardStickerCreateSchema,
        author_id: int,
        author_username: str,
        author_display_name: str,
        board_id: int | None = None,
    ) -> BoardStickerSchema:
        """Создаёт стикер с неизменяемым снимком автора."""
        try:
            board = await self._resolve_board(project_id, board_id, user_id=author_id)
            editor_board = await self._lock_editor_board(project_id, board.id, author_id)
            await self._validate_task_ids(project_id, data.task_ids)
            # Вставка, связи и чтение карточки — три операции одной
            # транзакции: их порядок принадлежит сценарию, а не репозиторию.
            sticker_id = await self.stickers_repository.insert(
                data={
                    "project_id": project_id,
                    "board_id": board.id,
                    "body": data.body,
                    "color": data.color,
                    "canvas_x": data.canvas_x,
                    "canvas_y": data.canvas_y,
                    "width": data.width,
                    "height": data.height,
                    "z_index": data.z_index,
                    "created_by_user_id": author_id,
                    "created_by_username_snapshot": author_username,
                    "created_by_display_name_snapshot": author_display_name,
                },
            )
            await self.stickers_repository.replace_task_links(
                sticker_id=sticker_id,
                task_ids=data.task_ids,
            )
            created = await self.stickers_repository.get_by_id(
                project_id=project_id,
                sticker_id=sticker_id,
            )
            await self._advance_board_revision(editor_board)
            await self.unit_of_work.commit()
            return to_sticker_schema(created)
        except RepositoryErrors as error:
            logger.error(
                "❌ Ошибка создания стикера проекта id=%s.",
                project_id,
                exc_info=True,
            )
            raise BoardStickersServiceError(str(error)) from error

    async def move_sticker(
        self,
        *,
        project_id: int | None,
        sticker_id: int,
        data: BoardStickerPositionUpdateSchema,
        board_id: int | None = None,
        user_id: int | None = None,
    ) -> BoardStickerSchema:
        """Сохраняет положение и слой стикера отдельно от версии его содержимого."""
        try:
            await self._require_board_scope(project_id, board_id, user_id)
            current = await self._require_sticker(project_id, sticker_id, board_id)
            editor_board = await self._guard_mutation(
                project_id, current.board_id, sticker_id, user_id
            )
            moved = await self.stickers_repository.update_position(
                project_id=project_id,
                sticker_id=sticker_id,
                **data.model_dump(exclude_none=True),
            )
            if not moved:
                raise BoardStickerNotFoundError(sticker_id)
            sticker = await self.stickers_repository.get_by_id(
                project_id=project_id,
                sticker_id=sticker_id,
            )
            await self._advance_board_revision(editor_board)
            await self.unit_of_work.commit()
            return to_sticker_schema(sticker)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка перемещения стикера id=%s.", sticker_id, exc_info=True)
            raise BoardStickersServiceError(str(error)) from error

    async def update_sticker(
        self,
        *,
        project_id: int | None,
        sticker_id: int,
        data: BoardStickerUpdateSchema,
        board_id: int | None = None,
        user_id: int | None = None,
    ) -> BoardStickerSchema:
        """Изменяет текст, цвет или задачи, не меняя автора стикера."""
        try:
            await self._require_board_scope(project_id, board_id, user_id)
            current = await self._require_sticker(project_id, sticker_id, board_id)
            editor_board = await self._guard_mutation(
                project_id, current.board_id, sticker_id, user_id, indirect=False
            )
            if current.revision != data.revision:
                raise BoardStickerRevisionConflictError(sticker_id, data.revision)

            task_ids = data.task_ids if "task_ids" in data.model_fields_set else None
            if task_ids is not None:
                await self._validate_task_ids(project_id, task_ids)
            changes = data.model_dump(
                exclude={"revision", "task_ids"},
                exclude_unset=True,
            )
            updated = await self.stickers_repository.update_fields(
                project_id=project_id,
                sticker_id=sticker_id,
                expected_revision=data.revision,
                changes=changes,
            )
            if not updated:
                raise BoardStickerRevisionConflictError(sticker_id, data.revision)
            if task_ids is not None:
                await self.stickers_repository.replace_task_links(
                    sticker_id=sticker_id,
                    task_ids=task_ids,
                )
            sticker = await self.stickers_repository.get_by_id(
                project_id=project_id,
                sticker_id=sticker_id,
            )
            await self._advance_board_revision(editor_board)
            await self.unit_of_work.commit()
            return to_sticker_schema(sticker)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка изменения стикера id=%s.", sticker_id, exc_info=True)
            raise BoardStickersServiceError(str(error)) from error

    async def delete_sticker(
        self,
        *,
        project_id: int | None,
        sticker_id: int,
        revision: int,
        board_id: int | None = None,
        user_id: int | None = None,
    ) -> None:
        """Удаляет стикер по актуальной ревизии и отцепляет от него стрелки."""
        try:
            await self._require_board_scope(project_id, board_id, user_id)
            current = await self._require_sticker(project_id, sticker_id, board_id)
            editor_board = await self._guard_mutation(
                project_id, current.board_id, sticker_id, user_id
            )
            if current.revision != revision:
                raise BoardStickerRevisionConflictError(sticker_id, revision)
            deleted = await self.stickers_repository.delete(
                project_id=project_id,
                sticker_id=sticker_id,
                expected_revision=revision,
            )
            if not deleted:
                raise BoardStickerRevisionConflictError(sticker_id, revision)
            await self.shapes_repository.detach_refs(
                board_id=current.board_id, target_type="sticker", target_id=sticker_id
            )
            if editor_board is not None:
                board = editor_board
                revision = board.revision
                raw = rewrite_metadata_refs(board.editor_metadata, {("sticker", sticker_id)})
                if raw != board.editor_metadata:
                    await self.editor_repository.save_metadata(
                        board, BoardEditorMetadata.model_validate(raw)
                    )
                if board.revision == revision:
                    await self._advance_board_revision(board)
            await self.unit_of_work.commit()
        except RepositoryErrors as error:
            logger.error("❌ Ошибка удаления стикера id=%s.", sticker_id, exc_info=True)
            raise BoardStickersServiceError(str(error)) from error

    async def _advance_board_revision(self, board):
        if board is not None:
            await self.editor_repository.bump_revision(board)

    async def _lock_editor_board(self, project_id, board_id, user_id):
        if self.editor_repository is None:
            return None
        board = await self.editor_repository.get_board(board_id, lock=True)
        if (
            board is None
            or board.project_id != project_id
            or (project_id is None and board.created_by_user_id != user_id)
        ):
            raise BoardNotFoundError(board_id)
        return board

    async def _guard_mutation(self, project_id, board_id, sticker_id, user_id, indirect=True):
        board = await self._lock_editor_board(project_id, board_id, user_id)
        if board is None:
            return
        shapes = await self.shapes_repository.list_by_board(board_id)
        if mutation_is_locked(
            board.editor_metadata,
            {("sticker", sticker_id)},
            shapes,
            {("sticker", sticker_id)} if indirect else set(),
        ):
            raise BoardStickerLockedError()
        return board

    async def _resolve_board(
        self, project_id: int | None, board_id: int | None, *, user_id: int | None
    ) -> Board:
        """Доска из пути или, если не указана, первая доска проекта.

        ``project_id=None`` — свободная доска: ``board_id`` обязателен,
        доска должна принадлежать пользователю; чужая неотличима от 404.
        """
        if project_id is None:
            if board_id is None or user_id is None:
                raise BoardNotFoundError(board_id or 0)
            board = await self.boards_repository.get_active_free(
                board_id=board_id, owner_user_id=user_id
            )
            if board is None:
                raise BoardNotFoundError(board_id)
            return board
        if board_id is None:
            return await ensure_default_board(
                self.boards_repository, project_id, created_by_user_id=user_id
            )
        board = await self.boards_repository.get_active(project_id=project_id, board_id=board_id)
        if board is None:
            raise BoardNotFoundError(board_id)
        return board

    async def _require_board_scope(
        self, project_id: int | None, board_id: int | None, user_id: int | None
    ) -> None:
        """У свободной доски проверяет владение доской до работы со стикерами.

        Проектный scope уже проверен guard'ом маршрута; свободная доска без
        этой проверки позволяла бы править стикеры по чужому board_id.
        """
        if project_id is None:
            await self._resolve_board(None, board_id, user_id=user_id)

    async def _require_sticker(
        self, project_id: int | None, sticker_id: int, board_id: int | None
    ) -> BoardSticker:
        """Стикер активной доски проекта (и указанной доски, если она задана) или 404."""
        sticker = await self.stickers_repository.get_by_id(
            project_id=project_id, sticker_id=sticker_id, board_id=board_id
        )
        if sticker is None:
            raise BoardStickerNotFoundError(sticker_id)
        return sticker

    async def _validate_task_ids(self, project_id: int | None, task_ids: list[int]) -> None:
        """Не позволяет ссылаться на отсутствующие или чужие задачи.

        У свободной доски проекта нет: любые связи с задачами отклоняются.
        """
        requested = set(task_ids)
        if not requested:
            return
        if project_id is None:
            raise FreeBoardTaskLinksError()
        tasks = await self.tasks_repository.get_by_project(
            project_id=project_id,
            task_ids=requested,
        )
        found = {task.id for task in tasks}
        if found != requested:
            raise BoardStickerTaskMismatchError(requested - found)


def to_sticker_schema(sticker: BoardSticker) -> BoardStickerSchema:
    """Преобразует ORM-сущность в безопасный transport contract."""
    return BoardStickerSchema(
        id=sticker.id,
        project_id=sticker.project_id,
        board_id=sticker.board_id,
        body=sticker.body,
        color=sticker.color,
        canvas_x=sticker.canvas_x,
        canvas_y=sticker.canvas_y,
        width=getattr(sticker, "width", 230.0),
        height=getattr(sticker, "height", 230.0),
        z_index=sticker.z_index,
        created_by_user_id=sticker.created_by_user_id,
        created_by_username_snapshot=sticker.created_by_username_snapshot,
        created_by_display_name_snapshot=sticker.created_by_display_name_snapshot,
        task_ids=[link.task_id for link in sticker.task_links],
        revision=sticker.revision,
        created_at=sticker.created_at,
        updated_at=sticker.updated_at,
    )
