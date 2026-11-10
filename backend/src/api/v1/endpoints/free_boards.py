"""Свободные (личные) доски: холст со стикерами и фигурами без проекта.

Доска доступна только её автору (``created_by_user_id``); чужая доска
неотличима от несуществующей — любой запрос к ней отвечает 404. Связей
стикеров с задачами у свободной доски нет: проекта нет, задачи недоступны.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.v1.responses import (
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
from src.dependencies.auth import PrincipalDep, require_write_scope
from src.dependencies.services import (
    BoardShapesServiceDep,
    BoardsServiceDep,
    BoardStickersServiceDep,
)
from src.exceptions.board_stickers import BoardStickersServiceError
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
from src.schemas.board_stickers import (
    BoardStickerCreateSchema,
    BoardStickerPositionUpdateSchema,
    BoardStickerSchema,
    BoardStickerUpdateSchema,
)
from src.schemas.boards import (
    BoardContentSchema,
    BoardCreateSchema,
    BoardSchema,
    BoardUpdateSchema,
)

router = APIRouter(tags=["boards"])
logger = logging.getLogger(__name__)
BoardIdPath = Annotated[int, Path(gt=0, description="Идентификатор доски.")]
StickerIdPath = Annotated[int, Path(gt=0, description="Идентификатор стикера.")]
ShapeIdPath = Annotated[int, Path(gt=0, description="Идентификатор фигуры.")]
BoardErrors = (BoardsServiceError, BoardStickersServiceError)
WRITE_RESPONSES = {
    404: NOT_FOUND_RESPONSE,
    409: CONFLICT_RESPONSE,
    422: VALIDATION_RESPONSE,
    500: SERVER_ERROR_RESPONSE,
}


def _http_error(
    error: BoardsServiceError, action: str, board_id: int | None
) -> HTTPException:
    """Логирует отказ по уровню серьёзности и переводит его в HTTP-ответ."""
    if error.status_code >= 500:
        logger.exception("❌ Ошибка %s свободной доски id=%s.", action, board_id)
    else:
        logger.info("ℹ️ %s свободной доски id=%s отклонён: %s", action, board_id, error)
    return HTTPException(status_code=error.status_code, detail=error.detail)


@router.get(
    "/boards",
    response_model=list[BoardSchema],
    responses={500: SERVER_ERROR_RESPONSE},
    summary="Получить мои свободные доски",
    operation_id="listFreeBoards",
)
async def list_free_boards(
    service: BoardsServiceDep,
    principal: PrincipalDep,
) -> list[BoardSchema]:
    """Возвращает активные личные доски пользователя; пустой список — без страховки."""
    logger.info("🚀 Запрос GET /boards пользователя id=%s.", principal.user_id)
    try:
        result = await service.list_free_boards(user_id=principal.user_id)
        logger.info("✅ Свободных досок пользователя id=%s: %s.", principal.user_id, len(result))
        return result
    except BoardsServiceError as error:
        raise _http_error(error, "GET списка", None) from error


@router.post(
    "/boards",
    dependencies=[Depends(require_write_scope)],
    response_model=BoardSchema,
    status_code=status.HTTP_201_CREATED,
    responses={422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    summary="Создать свободную доску",
    operation_id="createFreeBoard",
)
async def create_free_board(
    data: BoardCreateSchema,
    service: BoardsServiceDep,
    principal: PrincipalDep,
) -> BoardSchema:
    """Создаёт личную доску последней в списке пользователя."""
    logger.info("🚀 Запрос POST /boards пользователя id=%s.", principal.user_id)
    try:
        result = await service.create_free_board(data=data, user_id=principal.user_id)
        logger.info(
            "✅ Создана свободная доска id=%s пользователя id=%s.", result.id, principal.user_id
        )
        return result
    except BoardsServiceError as error:
        raise _http_error(error, "POST", None) from error


@router.patch(
    "/boards/{board_id}",
    dependencies=[Depends(require_write_scope)],
    response_model=BoardSchema,
    responses=WRITE_RESPONSES,
    summary="Переименовать или переставить свободную доску",
    operation_id="updateFreeBoard",
)
async def update_free_board(
    board_id: BoardIdPath,
    data: BoardUpdateSchema,
    service: BoardsServiceDep,
    principal: PrincipalDep,
) -> BoardSchema:
    """Меняет название или позицию личной доски по optimistic revision."""
    logger.info("🚀 Запрос PATCH свободной доски id=%s.", board_id)
    try:
        result = await service.update_free_board(
            board_id=board_id, data=data, user_id=principal.user_id
        )
        logger.info("✅ Изменена свободная доска id=%s, revision=%s.", result.id, result.revision)
        return result
    except BoardsServiceError as error:
        raise _http_error(error, "PATCH", board_id) from error


@router.delete(
    "/boards/{board_id}",
    dependencies=[Depends(require_write_scope)],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=WRITE_RESPONSES,
    summary="Удалить свободную доску",
    operation_id="deleteFreeBoard",
)
async def delete_free_board(
    board_id: BoardIdPath,
    service: BoardsServiceDep,
    principal: PrincipalDep,
    revision: Annotated[int, Query(ge=1, description="Ожидаемая ревизия доски.")],
) -> None:
    """Мягко удаляет личную доску; последнюю удалить можно."""
    logger.info("🚀 Запрос DELETE свободной доски id=%s.", board_id)
    try:
        await service.delete_free_board(
            board_id=board_id, revision=revision, user_id=principal.user_id
        )
        logger.info("✅ Свободная доска id=%s удалена.", board_id)
    except BoardsServiceError as error:
        raise _http_error(error, "DELETE", board_id) from error


@router.get(
    "/boards/{board_id}/content",
    response_model=BoardContentSchema,
    responses={404: NOT_FOUND_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    summary="Получить содержимое свободной доски",
    operation_id="getFreeBoardContent",
)
async def get_free_board_content(
    board_id: BoardIdPath,
    service: BoardsServiceDep,
    principal: PrincipalDep,
) -> BoardContentSchema:
    """Возвращает личную доску, её стикеры и фигуры одним запросом."""
    logger.info("🚀 Запрос GET содержимого свободной доски id=%s.", board_id)
    try:
        result = await service.get_free_content(board_id=board_id, user_id=principal.user_id)
        logger.info(
            "✅ Свободная доска id=%s: стикеров %s, фигур %s.",
            board_id,
            len(result.stickers),
            len(result.shapes),
        )
        return result
    except BoardsServiceError as error:
        raise _http_error(error, "GET content", board_id) from error


@router.post(
    "/boards/{board_id}/stickers",
    dependencies=[Depends(require_write_scope)],
    response_model=BoardStickerSchema,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_RESPONSES,
    summary="Создать стикер свободной доски",
    operation_id="createFreeBoardSticker",
)
async def create_free_board_sticker(
    board_id: BoardIdPath,
    data: BoardStickerCreateSchema,
    service: BoardStickersServiceDep,
    principal: PrincipalDep,
) -> BoardStickerSchema:
    """Создаёт стикер на личной доске; связи с задачами недоступны (422)."""
    logger.info("🚀 Запрос POST стикера свободной доски id=%s.", board_id)
    try:
        result = await service.create_sticker(
            project_id=None,
            board_id=board_id,
            data=data,
            author_id=principal.user_id,
            author_username=principal.username,
            author_display_name=principal.full_name,
        )
        logger.info("✅ Создан стикер id=%s свободной доски id=%s.", result.id, board_id)
        return result
    except BoardErrors as error:
        raise _http_error(error, "POST стикера", board_id) from error


@router.patch(
    "/boards/{board_id}/stickers/{sticker_id}",
    dependencies=[Depends(require_write_scope)],
    response_model=BoardStickerSchema,
    responses=WRITE_RESPONSES,
    summary="Изменить стикер свободной доски",
    operation_id="updateFreeBoardSticker",
)
async def update_free_board_sticker(
    board_id: BoardIdPath,
    sticker_id: StickerIdPath,
    data: BoardStickerUpdateSchema,
    service: BoardStickersServiceDep,
    principal: PrincipalDep,
) -> BoardStickerSchema:
    """Изменяет стикер личной доски по optimistic revision."""
    logger.info("🚀 Запрос PATCH стикера id=%s свободной доски id=%s.", sticker_id, board_id)
    try:
        result = await service.update_sticker(
            project_id=None,
            board_id=board_id,
            sticker_id=sticker_id,
            data=data,
            user_id=principal.user_id,
        )
        logger.info("✅ Изменён стикер id=%s, revision=%s.", result.id, result.revision)
        return result
    except BoardErrors as error:
        raise _http_error(error, "PATCH стикера", board_id) from error


@router.patch(
    "/boards/{board_id}/stickers/{sticker_id}/position",
    dependencies=[Depends(require_write_scope)],
    response_model=BoardStickerSchema,
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    summary="Переместить стикер на свободной доске",
    operation_id="moveFreeBoardSticker",
)
async def move_free_board_sticker(
    board_id: BoardIdPath,
    sticker_id: StickerIdPath,
    data: BoardStickerPositionUpdateSchema,
    service: BoardStickersServiceDep,
    principal: PrincipalDep,
) -> BoardStickerSchema:
    """Сохраняет координаты стикера личной доски."""
    logger.info(
        "🚀 Запрос PATCH позиции стикера id=%s свободной доски id=%s.", sticker_id, board_id
    )
    try:
        result = await service.move_sticker(
            project_id=None,
            board_id=board_id,
            sticker_id=sticker_id,
            data=data,
            user_id=principal.user_id,
        )
        return result
    except BoardErrors as error:
        raise _http_error(error, "PATCH позиции стикера", board_id) from error


@router.delete(
    "/boards/{board_id}/stickers/{sticker_id}",
    dependencies=[Depends(require_write_scope)],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=WRITE_RESPONSES,
    summary="Удалить стикер свободной доски",
    operation_id="deleteFreeBoardSticker",
)
async def delete_free_board_sticker(
    board_id: BoardIdPath,
    sticker_id: StickerIdPath,
    service: BoardStickersServiceDep,
    principal: PrincipalDep,
    revision: Annotated[int, Query(ge=1, description="Ожидаемая ревизия стикера.")],
) -> None:
    """Удаляет стикер личной доски по optimistic revision."""
    logger.info("🚀 Запрос DELETE стикера id=%s свободной доски id=%s.", sticker_id, board_id)
    try:
        await service.delete_sticker(
            project_id=None,
            board_id=board_id,
            sticker_id=sticker_id,
            revision=revision,
            user_id=principal.user_id,
        )
        logger.info("✅ Удалён стикер id=%s свободной доски id=%s.", sticker_id, board_id)
    except BoardErrors as error:
        raise _http_error(error, "DELETE стикера", board_id) from error


@router.post(
    "/boards/{board_id}/shapes",
    dependencies=[Depends(require_write_scope)],
    response_model=BoardShapeSchema,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_RESPONSES,
    summary="Нарисовать фигуру на свободной доске",
    operation_id="createFreeBoardShape",
)
async def create_free_board_shape(
    board_id: BoardIdPath,
    data: BoardShapeCreateSchema,
    service: BoardShapesServiceDep,
    principal: PrincipalDep,
) -> BoardShapeSchema:
    """Создаёт фигуру, стрелку, линию или надпись на личной доске."""
    logger.info("🚀 Запрос POST фигуры %s на свободной доске id=%s.", data.kind.value, board_id)
    try:
        return await service.create_shape(
            project_id=None, board_id=board_id, data=data, author_id=principal.user_id
        )
    except BoardsServiceError as error:
        raise _http_error(error, "POST фигуры", board_id) from error


@router.patch(
    "/boards/{board_id}/shapes/{shape_id}",
    dependencies=[Depends(require_write_scope)],
    response_model=BoardShapeSchema,
    responses=WRITE_RESPONSES,
    summary="Изменить фигуру свободной доски",
    operation_id="updateFreeBoardShape",
)
async def update_free_board_shape(
    board_id: BoardIdPath,
    shape_id: ShapeIdPath,
    data: BoardShapeUpdateSchema,
    service: BoardShapesServiceDep,
    principal: PrincipalDep,
) -> BoardShapeSchema:
    """Частично меняет фигуру; revision не передан — last-write-wins."""
    logger.info("🚀 Запрос PATCH фигуры id=%s на свободной доске id=%s.", shape_id, board_id)
    try:
        return await service.update_shape(
            project_id=None,
            board_id=board_id,
            shape_id=shape_id,
            data=data,
            user_id=principal.user_id,
        )
    except BoardsServiceError as error:
        raise _http_error(error, "PATCH фигуры", board_id) from error


@router.delete(
    "/boards/{board_id}/shapes/{shape_id}",
    dependencies=[Depends(require_write_scope)],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=WRITE_RESPONSES,
    summary="Удалить фигуру свободной доски",
    operation_id="deleteFreeBoardShape",
)
async def delete_free_board_shape(
    board_id: BoardIdPath,
    shape_id: ShapeIdPath,
    service: BoardShapesServiceDep,
    principal: PrincipalDep,
    revision: Annotated[
        int | None, Query(ge=1, description="Ожидаемая ревизия; без неё — last-write-wins.")
    ] = None,
) -> None:
    """Удаляет фигуру и отцепляет стрелки, которые на неё ссылались."""
    logger.info("🚀 Запрос DELETE фигуры id=%s на свободной доске id=%s.", shape_id, board_id)
    try:
        await service.delete_shape(
            project_id=None,
            board_id=board_id,
            shape_id=shape_id,
            revision=revision,
            user_id=principal.user_id,
        )
        logger.info("✅ Фигура id=%s удалена.", shape_id)
    except BoardsServiceError as error:
        raise _http_error(error, "DELETE фигуры", board_id) from error


@router.post(
    "/boards/{board_id}/batch",
    dependencies=[Depends(require_write_scope)],
    response_model=BoardBatchResultSchema,
    responses=WRITE_RESPONSES,
    summary="Применить пакет изменений свободной доски",
    operation_id="applyFreeBoardBatch",
)
async def apply_free_board_batch(
    board_id: BoardIdPath,
    data: BoardBatchSchema,
    service: BoardShapesServiceDep,
    principal: PrincipalDep,
) -> BoardBatchResultSchema:
    """Выполняет до 500 операций одной транзакцией: всё или ничего."""
    logger.info(
        "🚀 Запрос пакета из %s операций на свободной доске id=%s.", len(data.ops), board_id
    )
    try:
        return await service.apply_batch(
            project_id=None,
            board_id=board_id,
            data=data,
            author_id=principal.user_id,
            author_username=principal.username,
            author_display_name=principal.full_name,
        )
    except BoardsServiceError as error:
        raise _http_error(error, "пакет", board_id) from error


@router.post(
    "/boards/{board_id}/shapes/{shape_id}/to-sticker",
    dependencies=[Depends(require_write_scope)],
    response_model=BoardShapeToStickerResultSchema,
    responses=WRITE_RESPONSES,
    summary="Заменить фигуру стикером на свободной доске",
    operation_id="convertFreeBoardShapeToSticker",
)
async def convert_free_board_shape_to_sticker(
    board_id: BoardIdPath,
    shape_id: ShapeIdPath,
    data: BoardShapeToStickerSchema,
    service: BoardShapesServiceDep,
    principal: PrincipalDep,
) -> BoardShapeToStickerResultSchema:
    """Ставит стикер на место фигуры и перецепляет на него её стрелки."""
    logger.info(
        "🚀 Запрос замены фигуры id=%s стикером на свободной доске id=%s.", shape_id, board_id
    )
    try:
        return await service.shape_to_sticker(
            project_id=None,
            board_id=board_id,
            shape_id=shape_id,
            data=data,
            author_id=principal.user_id,
            author_username=principal.username,
            author_display_name=principal.full_name,
        )
    except BoardsServiceError as error:
        raise _http_error(error, "замена фигуры", board_id) from error
