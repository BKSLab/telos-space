"""Metadata редактора и promotion: единственная транзакционная граница."""

import logging

from src.db.models.boards import Board
from src.exceptions.board_editor import (
    BoardAlreadyPromotedError,
    BoardEditorValidationError,
    BoardObjectLockedError,
)
from src.exceptions.boards import (
    BoardNotFoundError,
    BoardRevisionConflictError,
    BoardsRepositoryError,
    BoardsServiceError,
)
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.repositories.board_editor import BoardEditorRepository, BoardPromotionUnitOfWork
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.board_editor import (
    BoardEditorMetadata,
    BoardEditorState,
    BoardEditorWrite,
    BoardPromotionResult,
    BoardPromotionWrite,
)
from src.schemas.boards import BoardSchema
from src.services.access import AccessService
from src.services.projects import ProjectsService

logger = logging.getLogger(__name__)


def require_objects_unlocked(board: Board, refs: set[tuple[str, int]]) -> None:
    """UX-lock, не ACL. Вызывать под row lock перед изменением/удалением объектов."""
    metadata = BoardEditorMetadata.model_validate(board.editor_metadata)
    affected = set(refs)
    for group in metadata.groups:
        members = {item.key() for item in group.members}
        if affected & members:
            affected.update(members)
    if affected & {item.key() for item in metadata.locked}:
        raise BoardObjectLockedError("Выделение содержит заблокированные объекты.")


def metadata_without_objects(
    metadata: BoardEditorMetadata,
    refs: set[tuple[str, int]],
) -> BoardEditorMetadata:
    """После удаления или конверсии объекта убрать старые refs в той же транзакции."""
    groups = []
    for group in metadata.groups:
        remaining = [ref for ref in group.members if ref.key() not in refs]
        if len(remaining) >= 2:
            groups.append(group.model_copy(update={"members": remaining}))
    return metadata.model_copy(
        update={
            "groups": groups,
            "locked": [ref for ref in metadata.locked if ref.key() not in refs],
        }
    )


class BoardEditorService:
    def __init__(
        self,
        *,
        repository: BoardEditorRepository,
        access: AccessService,
        unit_of_work: UnitOfWork,
        projects: ProjectsService,
    ):
        if not isinstance(projects.unit_of_work, BoardPromotionUnitOfWork):
            raise ValueError("Promotion требует отложенный commit ProjectsService.")
        if (
            projects.unit_of_work.db_session is not repository.db_session
            or unit_of_work.db_session is not repository.db_session
        ):
            raise ValueError("Promotion должен использовать одну общую сессию БД.")
        self.repository = repository
        self.access = access
        self.unit_of_work = unit_of_work
        self.projects = projects

    async def _require_board(
        self,
        *,
        board_id: int,
        project_id: int | None,
        user_id: int,
        write: bool,
    ) -> Board:
        board = await self.repository.get_board(board_id, lock=True)
        if board is None or board.project_id != project_id:
            raise BoardNotFoundError(board_id)
        if project_id is None:
            if board.created_by_user_id != user_id:
                raise BoardNotFoundError(board_id)
        elif write:
            await self.access.ensure_project_access(project_id=project_id, user_id=user_id)
        else:
            await self.access.ensure_project_access(project_id=project_id, user_id=user_id)
        return board

    @staticmethod
    def _state(board: Board) -> BoardEditorState:
        return BoardEditorState(
            board_id=board.id,
            revision=board.revision,
            metadata=BoardEditorMetadata.model_validate(board.editor_metadata),
        )

    @staticmethod
    def _revision(board: Board, expected: int) -> None:
        if board.revision != expected:
            raise BoardRevisionConflictError(board.id, expected)

    async def get_metadata(
        self,
        *,
        board_id: int,
        project_id: int | None,
        user_id: int,
    ) -> BoardEditorState:
        try:
            board = await self._require_board(
                board_id=board_id,
                project_id=project_id,
                user_id=user_id,
                write=False,
            )
            return self._state(board)
        except BoardsRepositoryError as error:
            logger.error("❌ Ошибка чтения metadata доски id=%s.", board_id, exc_info=True)
            raise BoardsServiceError(str(error)) from error

    async def put_metadata(
        self,
        *,
        board_id: int,
        project_id: int | None,
        user_id: int,
        data: BoardEditorWrite,
    ) -> BoardEditorState:
        try:
            board = await self._require_board(
                board_id=board_id,
                project_id=project_id,
                user_id=user_id,
                write=True,
            )
            self._revision(board, data.expected_revision)
            refs = {ref.key() for ref in data.metadata.locked}
            refs.update(ref.key() for group in data.metadata.groups for ref in group.members)
            if not await self.repository.refs_exist(board.id, refs):
                raise BoardEditorValidationError(
                    "Metadata ссылается на чужой или удалённый объект."
                )
            await self.repository.save_metadata(board, data.metadata)
            result = self._state(board)
            await self.unit_of_work.commit()
            return result
        except (BoardsRepositoryError, UnitOfWorkRepositoryError) as error:
            await self.unit_of_work.rollback()
            logger.error("❌ Ошибка записи metadata доски id=%s.", board_id, exc_info=True)
            raise BoardsServiceError(str(error)) from error
        except Exception:
            await self.unit_of_work.rollback()
            raise

    async def promote(
        self,
        *,
        board_id: int,
        user_id: int,
        data: BoardPromotionWrite,
    ) -> BoardPromotionResult:
        try:
            board = await self.repository.get_board(board_id, lock=True)
            if board is None or board.created_by_user_id != user_id:
                raise BoardNotFoundError(board_id)
            if board.project_id is not None:
                raise BoardAlreadyPromotedError("Повторный перенос доски в проект.")
            self._revision(board, data.expected_revision)
            project = await self.projects.create_project(
                data=data.project.model_dump(),
                owner_id=user_id,
            )
            removed = await self.repository.remove_project_default_boards(project.id)
            if len(removed) != 1:
                raise BoardEditorValidationError(
                    "Не удалось заменить пустую основную доску проекта."
                )
            await self.repository.move_stickers_to_project(board.id, project.id)
            await self.repository.attach_to_new_project(board, project.id)
            result = BoardPromotionResult(
                project=project,
                board=BoardSchema.model_validate(board),
            )
            await self.unit_of_work.commit()
            return result
        except (BoardsRepositoryError, UnitOfWorkRepositoryError) as error:
            await self.unit_of_work.rollback()
            logger.error("❌ Ошибка создания проекта из доски id=%s.", board_id, exc_info=True)
            raise BoardsServiceError(str(error)) from error
        except Exception:
            await self.unit_of_work.rollback()
            raise
