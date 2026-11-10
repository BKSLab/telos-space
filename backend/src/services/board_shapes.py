"""Фигуры доски: рисование, привязки стрелок, пакет операций, замена стикером.

Геометрию стрелки и линии (рамку по точкам) считает сервер, стиль
дополняется умолчаниями, а привязки проверяются по той же доске. Пакет
выполняется одной транзакцией: ревизии сверяются заранее по заблокированным
строкам, и любая ошибка откатывает все операции.
"""

import logging
from typing import Any

from pydantic import ValidationError

from src.db.models.board_shapes import LINEAR_KINDS, BoardShape
from src.db.models.board_stickers import DEFAULT_STICKER_COLOR
from src.db.models.boards import Board
from src.exceptions.board_shapes import (
    BoardBatchConflictError,
    BoardShapeInvalidError,
    BoardShapeNotFoundError,
    BoardShapeRevisionConflictError,
    BoardShapesRepositoryError,
    BoardShapesServiceError,
)
from src.exceptions.board_stickers import BoardStickersRepositoryError
from src.exceptions.boards import (
    BoardNotFoundError,
    BoardsRepositoryError,
    BoardsServiceError,
)
from src.exceptions.tasks import TasksRepositoryError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.repositories.board_editor import BoardEditorRepository
from src.repositories.board_shapes import BoardShapesRepository
from src.repositories.board_stickers import BoardStickersRepository
from src.repositories.boards import BoardsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.board_editor import BoardEditorMetadata, BoardObjectRef
from src.schemas.board_shapes import (
    MAX_SHAPE_COORDINATE,
    MAX_SHAPES_PER_BOARD,
    BoardBatchResultSchema,
    BoardBatchSchema,
    BoardShapeChangesSchema,
    BoardShapeCreateSchema,
    BoardShapeRefInput,
    BoardShapeSchema,
    BoardShapeToStickerResultSchema,
    BoardShapeToStickerSchema,
    BoardShapeUpdateSchema,
    CreateShapeOperation,
    CreateStickerOperation,
    DeleteShapeOperation,
    DeleteStickerOperation,
    MoveStickerOperation,
    UpdateShapeOperation,
    UpdateStickerOperation,
    complete_style,
)
from src.schemas.board_stickers import MAX_STICKER_SIZE, MIN_STICKER_SIZE
from src.services.board_editor_batch import metadata_with_inserted_objects
from src.services.board_mutations import (
    locked_object_refs,
    mutation_is_locked,
    rewrite_metadata_refs,
)
from src.services.board_stickers import to_sticker_schema

logger = logging.getLogger(__name__)
RepositoryErrors = (
    BoardShapesRepositoryError,
    BoardsRepositoryError,
    BoardStickersRepositoryError,
    UnitOfWorkRepositoryError,
    TasksRepositoryError,
)
DEFAULT_STICKER_BODY = "Новый стикер"


def to_shape_schema(shape: BoardShape) -> BoardShapeSchema:
    """Преобразует фигуру в ответ с полным стилем."""
    return BoardShapeSchema(
        id=shape.id,
        board_id=shape.board_id,
        kind=shape.kind,
        x=shape.x,
        y=shape.y,
        w=shape.w,
        h=shape.h,
        points=shape.points,
        start_ref=shape.start_ref,
        end_ref=shape.end_ref,
        style=complete_style(shape.kind, None, shape.style),
        label=shape.label,
        z_index=shape.z_index,
        created_by_user_id=shape.created_by_user_id,
        revision=shape.revision,
        created_at=shape.created_at,
        updated_at=shape.updated_at,
    )


def _line_geometry(points: list[list[float]]) -> dict[str, Any]:
    """Рамка стрелки или линии по её точкам."""
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return {
        "points": [[float(x), float(y)] for x, y in points],
        "x": min(xs),
        "y": min(ys),
        "w": max(xs) - min(xs),
        "h": max(ys) - min(ys),
    }


class BoardShapesService:
    """Фигуры, стрелки, пакетные изменения и замена фигуры стикером."""

    def __init__(
        self,
        *,
        boards_repository: BoardsRepository,
        shapes_repository: BoardShapesRepository,
        stickers_repository: BoardStickersRepository,
        unit_of_work: UnitOfWork,
        tasks_repository: TasksRepository | None = None,
        editor_repository: BoardEditorRepository | None = None,
    ):
        self.boards_repository = boards_repository
        self.shapes_repository = shapes_repository
        self.stickers_repository = stickers_repository
        self.unit_of_work = unit_of_work
        self.tasks_repository = tasks_repository
        self.editor_repository = editor_repository

    async def create_shape(
        self, *, project_id: int | None, board_id: int, data: BoardShapeCreateSchema, author_id: int
    ) -> BoardShapeSchema:
        """Рисует одну фигуру на доске."""
        try:
            async with self._rollback_on_error():
                board = await self._require_board(project_id, board_id, user_id=author_id)
                await self._check_limit(board.id, added=1)
                shape = await self._insert(board, data, author_id=author_id, id_map={})
                await self._advance_board_revision(board)
                await self.unit_of_work.commit()
                logger.info("✅ Фигура id=%s на доске id=%s.", shape.id, board.id)
                return to_shape_schema(shape)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка создания фигуры на доске id=%s.", board_id, exc_info=True)
            raise BoardShapesServiceError(str(error)) from error

    async def update_shape(
        self,
        *,
        project_id: int | None,
        board_id: int,
        shape_id: int,
        data: BoardShapeUpdateSchema,
        user_id: int | None = None,
    ) -> BoardShapeSchema:
        """Меняет фигуру; без revision — last-write-wins."""
        try:
            async with self._rollback_on_error():
                board = await self._require_board(project_id, board_id, user_id=user_id)
                current = await self._lock_shape(board.id, shape_id)
                fields = data.model_fields_set & {"x", "y", "w", "h", "points"}
                geometry_changed = any(
                    getattr(data, name) != getattr(current, name) for name in fields
                )
                await self._check_locked(
                    board,
                    {("shape", shape_id)},
                    indirect={("shape", shape_id)} if geometry_changed else set(),
                )
                if data.revision is not None and current.revision != data.revision:
                    raise BoardShapeRevisionConflictError(shape_id, data.revision)
                shape = await self._update(board, current, data, id_map={})
                await self._advance_board_revision(board)
                await self.unit_of_work.commit()
                return to_shape_schema(shape)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка изменения фигуры id=%s.", shape_id, exc_info=True)
            raise BoardShapesServiceError(str(error)) from error

    async def delete_shape(
        self,
        *,
        project_id: int | None,
        board_id: int,
        shape_id: int,
        revision: int | None,
        user_id: int | None = None,
    ) -> None:
        """Удаляет фигуру и отцепляет стрелки, которые на неё ссылались."""
        try:
            async with self._rollback_on_error():
                board = await self._require_board(project_id, board_id, user_id=user_id)
                await self._check_locked(board, {("shape", shape_id)})
                current = await self._lock_shape(board.id, shape_id)
                if revision is not None and current.revision != revision:
                    raise BoardShapeRevisionConflictError(shape_id, revision)
                await self._delete(board.id, shape_id)
                revision = board.revision
                await self._rewrite_editor_metadata(board, {("shape", shape_id)})
                if board.revision == revision:
                    await self._advance_board_revision(board)
                await self.unit_of_work.commit()
        except RepositoryErrors as error:
            logger.error("❌ Ошибка удаления фигуры id=%s.", shape_id, exc_info=True)
            raise BoardShapesServiceError(str(error)) from error

    async def apply_batch(
        self,
        *,
        project_id: int | None,
        board_id: int,
        data: BoardBatchSchema,
        author_id: int,
        author_username: str = "",
        author_display_name: str = "",
    ) -> BoardBatchResultSchema:
        """Применяет пакет операций целиком или не применяет вовсе.

        Ревизии сверяются до первой записи по строкам, заблокированным до
        конца транзакции: чужая правка между проверкой и записью невозможна,
        а побочные эффекты самого пакета (отцепление стрелок повышает их
        ревизию) не превращаются в ложные конфликты.
        """
        try:
            async with self._rollback_on_error():
                board = await self._require_board(project_id, board_id, user_id=author_id)
                if data.expected_revision is not None:
                    if self.editor_repository is None:
                        raise BoardShapeInvalidError("Общий CAS доски недоступен.")
                    if data.expected_revision != board.revision:
                        raise BoardBatchConflictError([{"kind": "board", "id": board.id}])
                editor_change = data.editor_state or data.editor_insert
                if editor_change is not None:
                    if self.editor_repository is None:
                        raise BoardShapeInvalidError("Настройки редактора недоступны.")
                    if editor_change.expected_revision != board.revision:
                        raise BoardBatchConflictError([{"kind": "board", "id": board.id}])
                proposed = (
                    data.editor_state.metadata if data.editor_state else board.editor_metadata
                )
                changed_refs = {
                    (
                        "sticker"
                        if isinstance(
                            op,
                            MoveStickerOperation | UpdateStickerOperation | DeleteStickerOperation,
                        )
                        else "shape",
                        op.id,
                    )
                    for op in data.ops
                    if isinstance(
                        op,
                        MoveStickerOperation
                        | UpdateStickerOperation
                        | DeleteStickerOperation
                        | UpdateShapeOperation
                        | DeleteShapeOperation,
                    )
                }
                try:
                    lock_metadata = {
                        **proposed,
                        "locked": [ref for ref in proposed.get("locked", []) if "id" in ref],
                        "groups": [
                            {**g, "members": [ref for ref in g.get("members", []) if "id" in ref]}
                            for g in proposed.get("groups", [])
                        ],
                    }
                    await self._check_locked(
                        board, changed_refs, metadata=lock_metadata, ops=data.ops
                    )
                except (KeyError, TypeError, AttributeError) as error:
                    raise BoardShapeInvalidError("Недопустимые настройки редактора.") from error
                self._validate_client_ref_types(data)
                shapes = await self._lock_batch_shapes(board.id, data)
                sticker_ids = {
                    op.id
                    for op in data.ops
                    if isinstance(
                        op, MoveStickerOperation | UpdateStickerOperation | DeleteStickerOperation
                    )
                }
                stickers = await self.stickers_repository.list_by_ids(
                    board_id=board.id, sticker_ids=sticker_ids, for_update=True
                )
                self._raise_batch_conflicts(
                    data, shapes, {item.id: item.revision for item in stickers}
                )
                creates = sum(isinstance(op, CreateShapeOperation) for op in data.ops)
                deletes = {op.id for op in data.ops if isinstance(op, DeleteShapeOperation)}
                await self._check_limit(board.id, added=creates - len(deletes))

                id_map: dict[str, int] = {}
                touched: set[int] = set()
                deleted: list[int] = []
                moved: set[int] = set()
                deleted_stickers: list[int] = []
                for op in data.ops:
                    if isinstance(op, CreateShapeOperation):
                        shape = await self._insert(
                            board, op.data, author_id=author_id, id_map=id_map
                        )
                        if op.client_id is not None:
                            id_map[op.client_id] = shape.id
                        touched.add(shape.id)
                    elif isinstance(op, UpdateShapeOperation):
                        shape = await self._update(board, shapes[op.id], op.data, id_map=id_map)
                        shapes[op.id] = shape
                        touched.add(shape.id)
                    elif isinstance(op, DeleteShapeOperation):
                        touched.update(await self._delete(board.id, op.id))
                        deleted.append(op.id)
                    elif isinstance(op, CreateStickerOperation):
                        await self._validate_sticker_tasks(project_id, op.data.task_ids)
                        values = op.data.model_dump(exclude={"task_ids"})
                        sticker_id = await self.stickers_repository.insert(
                            data={
                                **values,
                                "project_id": project_id,
                                "board_id": board.id,
                                "created_by_user_id": author_id,
                                "created_by_username_snapshot": author_username,
                                "created_by_display_name_snapshot": author_display_name,
                            }
                        )
                        await self.stickers_repository.replace_task_links(
                            sticker_id=sticker_id,
                            task_ids=op.data.task_ids,
                        )
                        id_map[op.client_id] = sticker_id
                        moved.add(sticker_id)
                    elif isinstance(op, UpdateStickerOperation):
                        await self._validate_sticker_tasks(project_id, op.data.task_ids)
                        updated = await self.stickers_repository.update_fields(
                            project_id=project_id,
                            sticker_id=op.id,
                            expected_revision=op.revision,
                            changes=op.data.model_dump(exclude={"task_ids"}),
                        )
                        if not updated:
                            raise BoardBatchConflictError([{"kind": "sticker", "id": op.id}])
                        await self.stickers_repository.replace_task_links(
                            sticker_id=op.id,
                            task_ids=op.data.task_ids,
                        )
                        moved.add(op.id)
                    elif isinstance(op, DeleteStickerOperation):
                        deleted_ok = await self.stickers_repository.delete(
                            project_id=project_id,
                            sticker_id=op.id,
                            expected_revision=op.revision,
                        )
                        if not deleted_ok:
                            raise BoardBatchConflictError([{"kind": "sticker", "id": op.id}])
                        detached = await self.shapes_repository.detach_refs(
                            board_id=board.id,
                            target_type="sticker",
                            target_id=op.id,
                        )
                        touched.update(item.id for item in detached)
                        deleted_stickers.append(op.id)
                    elif isinstance(op, MoveStickerOperation) and op.revision is not None:
                        updated = await self.stickers_repository.update_fields(
                            project_id=project_id,
                            sticker_id=op.id,
                            expected_revision=op.revision,
                            changes=op.data.model_dump(exclude_none=True),
                        )
                        if not updated:
                            raise BoardBatchConflictError([{"kind": "sticker", "id": op.id}])
                        moved.add(op.id)
                    else:
                        await self.stickers_repository.update_position(
                            project_id=project_id,
                            sticker_id=op.id,
                            **op.data.model_dump(exclude_none=True),
                        )
                        moved.add(op.id)

                final_shapes = await self.shapes_repository.list_by_ids(
                    board_id=board.id, shape_ids=touched - set(deleted)
                )
                final_stickers = await self.stickers_repository.list_by_ids(
                    board_id=board.id, sticker_ids=moved - set(deleted_stickers)
                )
                editor_result = None
                if (
                    editor_change is not None
                    or deleted
                    or deleted_stickers
                    or self.editor_repository is not None
                ):
                    created_objects = {
                        op.client_id: BoardObjectRef(
                            type="sticker" if isinstance(op, CreateStickerOperation) else "shape",
                            id=id_map[op.client_id],
                        )
                        for op in data.ops
                        if isinstance(op, CreateShapeOperation | CreateStickerOperation)
                        and op.client_id is not None
                    }
                    metadata = self._batch_metadata(
                        board, data, created_objects, deleted, deleted_stickers
                    )
                    if self.editor_repository is not None:
                        refs = {ref.key() for group in metadata.groups for ref in group.members} | {
                            ref.key() for ref in metadata.locked
                        }
                        if not await self.editor_repository.refs_exist(board.id, refs):
                            raise BoardShapeInvalidError(
                                "Настройки ссылаются на объект не с этой доски."
                            )
                        await self.editor_repository.save_metadata(board, metadata)
                        editor_result = {
                            "board_id": board.id,
                            "revision": board.revision,
                            "metadata": metadata.model_dump(mode="json"),
                        }
                await self.unit_of_work.commit()
                logger.info(
                    "✅ Пакет доски id=%s: операций %s, создано %s, удалено %s.",
                    board.id,
                    len(data.ops),
                    len(id_map),
                    len(deleted),
                )
                return BoardBatchResultSchema(
                    shapes=[to_shape_schema(item) for item in final_shapes],
                    deleted_shape_ids=deleted,
                    stickers=[to_sticker_schema(item) for item in final_stickers],
                    id_map=id_map,
                    deleted_sticker_ids=deleted_stickers,
                    editor_state=editor_result,
                )
        except RepositoryErrors as error:
            logger.error("❌ Ошибка пакета доски id=%s.", board_id, exc_info=True)
            raise BoardShapesServiceError(str(error)) from error

    async def shape_to_sticker(
        self,
        *,
        project_id: int | None,
        board_id: int,
        shape_id: int,
        data: BoardShapeToStickerSchema,
        author_id: int,
        author_username: str,
        author_display_name: str,
    ) -> BoardShapeToStickerResultSchema:
        """Заменяет фигуру стикером и перецепляет на него её стрелки."""
        try:
            async with self._rollback_on_error():
                board = await self._require_board(project_id, board_id, user_id=author_id)
                await self._check_locked(board, {("shape", shape_id)})
                shape = await self._lock_shape(board.id, shape_id)
                if data.revision is not None and shape.revision != data.revision:
                    raise BoardShapeRevisionConflictError(shape_id, data.revision)
                if shape.kind in LINEAR_KINDS:
                    raise BoardShapeInvalidError("Стрелку или линию нельзя заменить стикером.")
                sticker_id = await self.stickers_repository.insert(
                    data={
                        "project_id": project_id,
                        "board_id": board.id,
                        "body": shape.label.strip() or DEFAULT_STICKER_BODY,
                        "color": data.color or DEFAULT_STICKER_COLOR,
                        "canvas_x": shape.x,
                        "canvas_y": shape.y,
                        "width": min(max(shape.w, MIN_STICKER_SIZE), MAX_STICKER_SIZE),
                        "height": min(max(shape.h, MIN_STICKER_SIZE), MAX_STICKER_SIZE),
                        "z_index": shape.z_index,
                        "created_by_user_id": author_id,
                        "created_by_username_snapshot": author_username,
                        "created_by_display_name_snapshot": author_display_name,
                    }
                )
                updated = await self.shapes_repository.retarget_shape_refs(
                    board_id=board.id, shape_id=shape_id, sticker_id=sticker_id
                )
                await self.shapes_repository.delete(board_id=board.id, shape_id=shape_id)
                revision = board.revision
                await self._rewrite_editor_metadata(
                    board, set(), {("shape", shape_id): {"type": "sticker", "id": sticker_id}}
                )
                if board.revision == revision:
                    await self._advance_board_revision(board)
                sticker = await self.stickers_repository.get_by_id(
                    project_id=project_id, sticker_id=sticker_id
                )
                await self.unit_of_work.commit()
                logger.info("✅ Фигура id=%s заменена стикером id=%s.", shape_id, sticker_id)
                return BoardShapeToStickerResultSchema(
                    sticker=to_sticker_schema(sticker),
                    updated_shapes=[to_shape_schema(item) for item in updated],
                )
        except RepositoryErrors as error:
            logger.error("❌ Ошибка замены фигуры id=%s стикером.", shape_id, exc_info=True)
            raise BoardShapesServiceError(str(error)) from error

    def _rollback_on_error(self):
        """Откатывает частично выполненный сценарий при доменной ошибке."""
        return _RollbackOnError(self.unit_of_work)

    async def _require_board(
        self, project_id: int | None, board_id: int, *, user_id: int | None = None
    ) -> Board:
        """Доска проекта; ``project_id=None`` — свободная доска, только её владельцу."""
        if self.editor_repository is not None:
            board = await self.editor_repository.get_board(board_id, lock=True)
            if (
                board is None
                or board.project_id != project_id
                or (project_id is None and board.created_by_user_id != user_id)
            ):
                raise BoardNotFoundError(board_id)
            return board
        if project_id is None:
            if user_id is None:
                raise BoardNotFoundError(board_id)
            board = await self.boards_repository.get_active_free(
                board_id=board_id, owner_user_id=user_id
            )
        else:
            board = await self.boards_repository.get_active(
                project_id=project_id, board_id=board_id
            )
        if board is None:
            raise BoardNotFoundError(board_id)
        return board

    async def _advance_board_revision(self, board):
        if self.editor_repository is not None:
            await self.editor_repository.bump_revision(board)

    async def _rewrite_editor_metadata(self, board, removed, replacement=None):
        if self.editor_repository is None:
            return
        updated = rewrite_metadata_refs(board.editor_metadata, removed, replacement)
        if updated != board.editor_metadata:
            await self.editor_repository.save_metadata(
                board, BoardEditorMetadata.model_validate(updated)
            )

    async def _check_locked(self, board, keys, metadata=None, ops=None, indirect=None):
        raw = metadata if metadata is not None else (board.editor_metadata or {})
        if not keys or not locked_object_refs(raw):
            return
        all_shapes = await self.shapes_repository.list_by_board(board.id)
        if ops is not None:
            shapes_by_id = {item.id: item for item in all_shapes}
            sticker_ids = {
                op.id for op in ops if isinstance(op, UpdateStickerOperation | MoveStickerOperation)
            }
            stickers = await self.stickers_repository.list_by_ids(
                board_id=board.id, sticker_ids=sticker_ids
            )
            stickers_by_id = {item.id: item for item in stickers}
            indirect = set()
            for op in ops:
                if isinstance(op, DeleteShapeOperation | DeleteStickerOperation):
                    indirect.add(
                        ("shape" if isinstance(op, DeleteShapeOperation) else "sticker", op.id)
                    )
                elif isinstance(op, UpdateShapeOperation):
                    current = shapes_by_id.get(op.id)
                    fields = op.data.model_fields_set & {"x", "y", "w", "h", "points"}
                    if current and any(
                        getattr(op.data, name) != getattr(current, name) for name in fields
                    ):
                        indirect.add(("shape", op.id))
                elif isinstance(op, UpdateStickerOperation | MoveStickerOperation):
                    current = stickers_by_id.get(op.id)
                    values = op.data.model_dump(exclude_none=True)
                    if current and any(
                        values[name] != getattr(current, name)
                        for name in ("canvas_x", "canvas_y", "width", "height")
                        if name in values
                    ):
                        indirect.add(("sticker", op.id))
        if mutation_is_locked(raw, keys, all_shapes, indirect):
            raise BoardShapeInvalidError(
                "Сначала разблокируйте объекты или связанные с ними стрелки."
            )

    @staticmethod
    def _batch_metadata(board, data, created_objects, deleted_shapes, deleted_stickers):
        raw = data.editor_state.metadata if data.editor_state else board.editor_metadata
        removed = {("shape", key) for key in deleted_shapes} | {
            ("sticker", key) for key in deleted_stickers
        }

        def resolve(ref):
            if "client_id" not in ref:
                return ref
            if set(ref) != {"type", "client_id"}:
                raise BoardShapeInvalidError("Недопустимая ссылка настроек редактора.")
            target = created_objects.get(ref["client_id"])
            if target is None or target.type != ref["type"]:
                raise BoardShapeInvalidError(
                    "Настройки ссылаются на объект вне создаваемого пакета."
                )
            return target.model_dump()

        try:
            groups = [
                {**group, "members": [resolve(ref) for ref in group["members"]]}
                for group in raw.get("groups", [])
            ]
            locked = [resolve(ref) for ref in raw.get("locked", [])]
            if data.editor_state is None:
                groups = [
                    {
                        **group,
                        "members": [
                            ref
                            for ref in group["members"]
                            if (ref["type"], ref["id"]) not in removed
                        ],
                    }
                    for group in groups
                ]
                groups = [group for group in groups if len(group["members"]) >= 2]
                locked = [ref for ref in locked if (ref["type"], ref["id"]) not in removed]
            result = BoardEditorMetadata.model_validate({**raw, "groups": groups, "locked": locked})
            if data.editor_insert is not None:
                result = metadata_with_inserted_objects(result, data.editor_insert, created_objects)
            return result
        except (ValidationError, KeyError, TypeError, AttributeError) as error:
            raise BoardShapeInvalidError("Недопустимые настройки редактора.") from error

    async def _lock_shape(self, board_id: int, shape_id: int) -> BoardShape:
        found = await self.shapes_repository.list_by_ids(
            board_id=board_id, shape_ids={shape_id}, for_update=True
        )
        if not found:
            raise BoardShapeNotFoundError(shape_id)
        return found[0]

    async def _lock_batch_shapes(
        self, board_id: int, data: BoardBatchSchema
    ) -> dict[int, BoardShape]:
        ids = {
            op.id for op in data.ops if isinstance(op, UpdateShapeOperation | DeleteShapeOperation)
        }
        found = await self.shapes_repository.list_by_ids(
            board_id=board_id, shape_ids=ids, for_update=True
        )
        return {shape.id: shape for shape in found}

    @staticmethod
    def _raise_batch_conflicts(
        data: BoardBatchSchema, shapes: dict[int, BoardShape], sticker_ids: dict[int, int]
    ) -> None:
        """Сверяет пакет с заблокированным состоянием; отсутствующий объект — тоже конфликт."""
        expected = {shape_id: shape.revision for shape_id, shape in shapes.items()}
        conflicts: list[dict] = []
        for op in data.ops:
            if isinstance(
                op, MoveStickerOperation | UpdateStickerOperation | DeleteStickerOperation
            ):
                if op.id not in sticker_ids or (
                    op.revision is not None and op.revision != sticker_ids[op.id]
                ):
                    conflicts.append({"kind": "sticker", "id": op.id})
                elif isinstance(op, UpdateStickerOperation) or (
                    isinstance(op, MoveStickerOperation) and op.revision is not None
                ):
                    sticker_ids[op.id] += 1
                elif isinstance(op, DeleteStickerOperation):
                    del sticker_ids[op.id]
                continue
            if not isinstance(op, UpdateShapeOperation | DeleteShapeOperation):
                continue
            if op.id not in expected or (
                op.revision is not None and op.revision != expected[op.id]
            ):
                conflicts.append({"kind": "shape", "id": op.id})
            elif isinstance(op, UpdateShapeOperation):
                expected[op.id] += 1
            else:
                del expected[op.id]
        if conflicts:
            unique = list({(item["kind"], item["id"]): item for item in conflicts}.values())
            logger.info("ℹ️ Пакет отклонён, конфликты: %s.", unique)
            raise BoardBatchConflictError(unique)

    @staticmethod
    def _validate_client_ref_types(data: BoardBatchSchema) -> None:
        kinds = {
            op.client_id: ("sticker" if isinstance(op, CreateStickerOperation) else "shape")
            for op in data.ops
            if isinstance(op, CreateShapeOperation | CreateStickerOperation)
            and op.client_id is not None
        }
        for op in data.ops:
            if not isinstance(op, CreateShapeOperation | UpdateShapeOperation):
                continue
            for ref in (op.data.start_ref, op.data.end_ref):
                if (
                    ref is not None
                    and ref.client_id is not None
                    and kinds.get(ref.client_id) != ref.type
                ):
                    raise BoardShapeInvalidError("Тип привязки не совпадает с объектом client_id.")

    async def _validate_sticker_tasks(self, project_id: int | None, task_ids: list[int]) -> None:
        if not task_ids:
            return
        if project_id is None:
            raise BoardShapeInvalidError("Привязка к задачам доступна только на доске проекта.")
        if self.tasks_repository is None:
            raise BoardShapeInvalidError("Проверка связей с задачами недоступна.")
        tasks = await self.tasks_repository.get_by_project(
            project_id=project_id,
            task_ids=set(task_ids),
        )
        if {item.id for item in tasks} != set(task_ids):
            raise BoardShapeInvalidError("Стикер ссылается на задачу не из этого проекта.")

    async def _check_limit(self, board_id: int, *, added: int) -> None:
        if added <= 0:
            return
        count = await self.shapes_repository.count_by_board(board_id)
        if count + added > MAX_SHAPES_PER_BOARD:
            raise BoardShapeInvalidError(
                f"На доске не больше {MAX_SHAPES_PER_BOARD} фигур; сейчас {count}."
            )

    async def _insert(
        self,
        board: Board,
        data: BoardShapeCreateSchema,
        *,
        author_id: int,
        id_map: dict[str, int],
    ) -> BoardShape:
        values: dict[str, Any] = {
            "board_id": board.id,
            "kind": data.kind,
            "style": complete_style(data.kind, data.style),
            "label": data.label,
            "z_index": data.z_index,
            "created_by_user_id": author_id,
        }
        if data.kind in LINEAR_KINDS:
            values.update(_line_geometry(data.points))
            refs = await self._resolve_refs(
                board.id,
                {"start_ref": data.start_ref, "end_ref": data.end_ref},
                id_map=id_map,
                self_id=None,
            )
            values.update(refs)
        else:
            values.update({"x": data.x, "y": data.y, "w": data.w, "h": data.h})
        return await self.shapes_repository.insert(data=values)

    async def _update(
        self,
        board: Board,
        current: BoardShape,
        data: BoardShapeChangesSchema,
        *,
        id_map: dict[str, int],
    ) -> BoardShape:
        fields = data.model_fields_set - {"revision"}
        changes: dict[str, Any] = {}
        if current.kind in LINEAR_KINDS:
            if "points" in fields:
                changes.update(_line_geometry(data.points))
            elif fields & {"w", "h"}:
                raise BoardShapeInvalidError(
                    "Размер стрелки и линии задаётся точками: передайте points."
                )
            elif fields & {"x", "y"}:
                changes.update(self._shifted(current, data))
            refs = {
                name: getattr(data, name) for name in ("start_ref", "end_ref") if name in fields
            }
            if refs:
                changes.update(
                    await self._resolve_refs(board.id, refs, id_map=id_map, self_id=current.id)
                )
        else:
            if fields & {"points", "start_ref", "end_ref"}:
                raise BoardShapeInvalidError("points и привязки бывают только у стрелки и линии.")
            changes.update(
                {name: getattr(data, name) for name in ("x", "y", "w", "h") if name in fields}
            )
        if "style" in fields:
            changes["style"] = complete_style(current.kind, data.style, current.style)
        for name in ("label", "z_index"):
            if name in fields:
                changes[name] = getattr(data, name)
        shape = await self.shapes_repository.update_fields(
            board_id=board.id, shape_id=current.id, changes=changes
        )
        if shape is None:
            raise BoardShapeNotFoundError(current.id)
        return shape

    @staticmethod
    def _shifted(current: BoardShape, data: BoardShapeChangesSchema) -> dict[str, Any]:
        """Переносит все точки стрелки на сдвиг её рамки."""
        dx = (data.x if data.x is not None else current.x) - current.x
        dy = (data.y if data.y is not None else current.y) - current.y
        points = [[x + dx, y + dy] for x, y in current.points]
        if any(abs(value) > MAX_SHAPE_COORDINATE for point in points for value in point):
            raise BoardShapeInvalidError("Стрелка выходит за пределы холста.")
        return _line_geometry(points)

    async def _delete(self, board_id: int, shape_id: int) -> set[int]:
        """Удаляет фигуру и возвращает id отцепленных от неё стрелок."""
        deleted = await self.shapes_repository.delete(board_id=board_id, shape_id=shape_id)
        if not deleted:
            raise BoardShapeNotFoundError(shape_id)
        detached = await self.shapes_repository.detach_refs(
            board_id=board_id, target_type="shape", target_id=shape_id
        )
        return {shape.id for shape in detached}

    async def _resolve_refs(
        self,
        board_id: int,
        refs: dict[str, BoardShapeRefInput | None],
        *,
        id_map: dict[str, int],
        self_id: int | None,
    ) -> dict[str, dict | None]:
        """Проверяет привязки: объект той же доски, не стрелка, не сама фигура.

        Цели блокируются FOR SHARE до конца транзакции: параллельное удаление
        дождётся этой записи и отцепит новую стрелку, а не оставит висячую ссылку.
        """
        resolved: dict[str, dict | None] = {}
        for name, ref in refs.items():
            if ref is None:
                resolved[name] = None
                continue
            target_id = ref.id
            if ref.client_id is not None:
                if ref.client_id not in id_map:
                    raise BoardShapeInvalidError(
                        f"client_id {ref.client_id!r} не создан раньше в этом пакете."
                    )
                target_id = id_map[ref.client_id]
            resolved[name] = {"type": ref.type, "id": target_id, "anchor": ref.anchor}

        targets = [item for item in resolved.values() if item is not None]
        shape_ids = {item["id"] for item in targets if item["type"] == "shape"}
        sticker_ids = {item["id"] for item in targets if item["type"] == "sticker"}
        if self_id is not None and self_id in shape_ids:
            raise BoardShapeInvalidError("Стрелка не может ссылаться на саму себя.")
        shapes = await self.shapes_repository.list_by_ids(
            board_id=board_id, shape_ids=shape_ids, for_share=True
        )
        stickers = await self.stickers_repository.list_by_ids(
            board_id=board_id, sticker_ids=sticker_ids, for_share=True
        )
        if {item.id for item in shapes} != shape_ids or {
            item.id for item in stickers
        } != sticker_ids:
            raise BoardShapeInvalidError("Привязка указывает на объект не с этой доски.")
        if any(item.kind in LINEAR_KINDS for item in shapes):
            raise BoardShapeInvalidError("Привязать стрелку можно к фигуре или стикеру.")
        return resolved


class _RollbackOnError:
    """Контекст сценария: доменная ошибка после частичной записи откатывает её."""

    def __init__(self, unit_of_work: UnitOfWork):
        self.unit_of_work = unit_of_work

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        if exc is not None and isinstance(exc, BoardsServiceError):
            await self.unit_of_work.rollback()
        return False
