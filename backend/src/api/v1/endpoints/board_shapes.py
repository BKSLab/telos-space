import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.v1.responses import (
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
from src.dependencies.access import ProjectIdPath, require_project_access
from src.dependencies.auth import PrincipalDep, require_write_scope
from src.dependencies.services import BoardShapesServiceDep
from src.exceptions.boards import BoardsServiceError
from src.schemas.board_shapes import (
    BoardBatchResultSchema,
    BoardBatchSchema,
    BoardShapeCreateSchema,
    BoardShapeSchema,
    BoardShapeToStickerResultSchema,
    BoardShapeToStickerSchema,
    BoardShapeUpdateSchema,
)

router = APIRouter(tags=["boards"])
logger = logging.getLogger(__name__)
BoardIdPath = Annotated[int, Path(gt=0, description="Идентификатор доски.")]
ShapeIdPath = Annotated[int, Path(gt=0, description="Идентификатор фигуры.")]
WRITE_RESPONSES = {
    404: NOT_FOUND_RESPONSE,
    409: CONFLICT_RESPONSE,
    422: VALIDATION_RESPONSE,
    500: SERVER_ERROR_RESPONSE,
}


def _http_error(error: BoardsServiceError, action: str, board_id: int) -> HTTPException:
    """Логирует отказ по уровню серьёзности и переводит его в HTTP-ответ."""
    if error.status_code >= 500:
        logger.exception("❌ Ошибка %s на доске id=%s.", action, board_id)
    else:
        logger.info("ℹ️ %s на доске id=%s отклонён: %s", action, board_id, error)
    return HTTPException(status_code=error.status_code, detail=error.detail)


@router.post(
    "/projects/{project_id}/boards/{board_id}/shapes",
    dependencies=[Depends(require_project_access), Depends(require_write_scope)],
    response_model=BoardShapeSchema,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_RESPONSES,
    summary="Нарисовать фигуру на доске",
    operation_id="createBoardShape",
)
async def create_board_shape(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    data: BoardShapeCreateSchema,
    service: BoardShapesServiceDep,
    principal: PrincipalDep,
) -> BoardShapeSchema:
    """Создаёт фигуру, стрелку, линию или надпись."""
    logger.info("🚀 Запрос POST фигуры %s на доске id=%s.", data.kind.value, board_id)
    try:
        return await service.create_shape(
            project_id=project_id, board_id=board_id, data=data, author_id=principal.user_id
        )
    except BoardsServiceError as error:
        raise _http_error(error, "POST фигуры", board_id) from error


@router.patch(
    "/projects/{project_id}/boards/{board_id}/shapes/{shape_id}",
    dependencies=[Depends(require_project_access), Depends(require_write_scope)],
    response_model=BoardShapeSchema,
    responses=WRITE_RESPONSES,
    summary="Изменить фигуру доски",
    operation_id="updateBoardShape",
)
async def update_board_shape(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    shape_id: ShapeIdPath,
    data: BoardShapeUpdateSchema,
    service: BoardShapesServiceDep,
) -> BoardShapeSchema:
    """Частично меняет фигуру; revision не передан — last-write-wins."""
    logger.info("🚀 Запрос PATCH фигуры id=%s на доске id=%s.", shape_id, board_id)
    try:
        return await service.update_shape(
            project_id=project_id, board_id=board_id, shape_id=shape_id, data=data
        )
    except BoardsServiceError as error:
        raise _http_error(error, "PATCH фигуры", board_id) from error


@router.delete(
    "/projects/{project_id}/boards/{board_id}/shapes/{shape_id}",
    dependencies=[Depends(require_project_access), Depends(require_write_scope)],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=WRITE_RESPONSES,
    summary="Удалить фигуру доски",
    operation_id="deleteBoardShape",
)
async def delete_board_shape(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    shape_id: ShapeIdPath,
    service: BoardShapesServiceDep,
    revision: Annotated[
        int | None, Query(ge=1, description="Ожидаемая ревизия; без неё — last-write-wins.")
    ] = None,
) -> None:
    """Удаляет фигуру и отцепляет стрелки, которые на неё ссылались."""
    logger.info("🚀 Запрос DELETE фигуры id=%s на доске id=%s.", shape_id, board_id)
    try:
        await service.delete_shape(
            project_id=project_id, board_id=board_id, shape_id=shape_id, revision=revision
        )
        logger.info("✅ Фигура id=%s удалена.", shape_id)
    except BoardsServiceError as error:
        raise _http_error(error, "DELETE фигуры", board_id) from error


@router.post(
    "/projects/{project_id}/boards/{board_id}/batch",
    dependencies=[Depends(require_project_access), Depends(require_write_scope)],
    response_model=BoardBatchResultSchema,
    responses=WRITE_RESPONSES,
    summary="Применить пакет изменений доски",
    operation_id="applyBoardBatch",
)
async def apply_board_batch(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    data: BoardBatchSchema,
    service: BoardShapesServiceDep,
    principal: PrincipalDep,
) -> BoardBatchResultSchema:
    """Выполняет до 500 операций одной транзакцией: всё или ничего."""
    logger.info("🚀 Запрос пакета из %s операций на доске id=%s.", len(data.ops), board_id)
    try:
        return await service.apply_batch(
            project_id=project_id,
            board_id=board_id,
            data=data,
            author_id=principal.user_id,
            author_username=principal.username,
            author_display_name=principal.full_name,
        )
    except BoardsServiceError as error:
        raise _http_error(error, "пакет", board_id) from error


@router.post(
    "/projects/{project_id}/boards/{board_id}/shapes/{shape_id}/to-sticker",
    dependencies=[Depends(require_project_access), Depends(require_write_scope)],
    response_model=BoardShapeToStickerResultSchema,
    responses=WRITE_RESPONSES,
    summary="Заменить фигуру стикером",
    operation_id="convertBoardShapeToSticker",
)
async def convert_board_shape_to_sticker(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    shape_id: ShapeIdPath,
    data: BoardShapeToStickerSchema,
    service: BoardShapesServiceDep,
    principal: PrincipalDep,
) -> BoardShapeToStickerResultSchema:
    """Ставит стикер на место фигуры и перецепляет на него её стрелки."""
    logger.info("🚀 Запрос замены фигуры id=%s стикером.", shape_id)
    try:
        return await service.shape_to_sticker(
            project_id=project_id,
            board_id=board_id,
            shape_id=shape_id,
            data=data,
            author_id=principal.user_id,
            author_username=principal.username,
            author_display_name=principal.full_name,
        )
    except BoardsServiceError as error:
        raise _http_error(error, "замена фигуры", board_id) from error
