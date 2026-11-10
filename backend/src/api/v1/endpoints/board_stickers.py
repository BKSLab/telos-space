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
from src.dependencies.services import BoardStickersServiceDep
from src.exceptions.board_stickers import BoardStickersServiceError
from src.exceptions.boards import BoardsServiceError
from src.schemas.board_stickers import (
    BoardStickerCreateSchema,
    BoardStickerPositionUpdateSchema,
    BoardStickerSchema,
    BoardStickerUpdateSchema,
)

router = APIRouter(tags=["boards"])
logger = logging.getLogger(__name__)
StickerIdPath = Annotated[int, Path(gt=0, description="Идентификатор стикера.")]
BoardIdPath = Annotated[int, Path(gt=0, description="Идентификатор доски.")]
StickerErrors = (BoardStickersServiceError, BoardsServiceError)


@router.get(
    "/projects/{project_id}/boards/{board_id}/stickers",
    dependencies=[Depends(require_project_access)],
    response_model=list[BoardStickerSchema],
    responses={404: NOT_FOUND_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    summary="Получить стикеры доски проекта",
    operation_id="listProjectBoardStickers",
)
async def list_project_stickers(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    service: BoardStickersServiceDep,
) -> list[BoardStickerSchema]:
    """Возвращает стикеры только доступного пользователю проекта."""
    logger.info("🚀 Запрос GET /projects/%s/boards/%s/stickers.", project_id, board_id)
    try:
        result = await service.list_stickers(project_id, board_id)
        logger.info("✅ Получено стикеров проекта id=%s: %s.", project_id, len(result))
        return result
    except StickerErrors as error:
        if error.status_code >= 500:
            logger.exception("❌ Ошибка GET стикеров доски id=%s.", board_id)
        else:
            logger.info("ℹ️ GET стикеров доски id=%s отклонён: %s", board_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/projects/{project_id}/boards/{board_id}/stickers",
    dependencies=[Depends(require_project_access), Depends(require_write_scope)],
    response_model=BoardStickerSchema,
    status_code=status.HTTP_201_CREATED,
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    summary="Создать стикер доски проекта",
    operation_id="createProjectBoardSticker",
)
async def create_project_sticker(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    data: BoardStickerCreateSchema,
    service: BoardStickersServiceDep,
    principal: PrincipalDep,
) -> BoardStickerSchema:
    """Создаёт общий стикер от имени текущего участника проекта."""
    logger.info("🚀 Запрос POST стикера проекта id=%s.", project_id)
    try:
        result = await service.create_sticker(
            project_id=project_id,
            board_id=board_id,
            data=data,
            author_id=principal.user_id,
            author_username=principal.username,
            author_display_name=principal.full_name,
        )
        logger.info("✅ Создан стикер id=%s проекта id=%s.", result.id, project_id)
        return result
    except StickerErrors as error:
        if error.status_code >= 500:
            logger.exception("❌ Ошибка POST стикера проекта id=%s.", project_id)
        else:
            logger.info("ℹ️ POST стикера проекта id=%s отклонён: %s", project_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.patch(
    "/projects/{project_id}/boards/{board_id}/stickers/{sticker_id}",
    dependencies=[Depends(require_project_access), Depends(require_write_scope)],
    response_model=BoardStickerSchema,
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    summary="Изменить стикер доски проекта",
    operation_id="updateProjectBoardSticker",
)
async def update_project_sticker(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    sticker_id: StickerIdPath,
    data: BoardStickerUpdateSchema,
    service: BoardStickersServiceDep,
) -> BoardStickerSchema:
    """Изменяет стикер доступного проекта по optimistic revision."""
    logger.info("🚀 Запрос PATCH стикера id=%s проекта id=%s.", sticker_id, project_id)
    try:
        result = await service.update_sticker(
            project_id=project_id,
            board_id=board_id,
            sticker_id=sticker_id,
            data=data,
        )
        logger.info("✅ Изменён стикер id=%s, revision=%s.", result.id, result.revision)
        return result
    except StickerErrors as error:
        if error.status_code >= 500:
            logger.exception("❌ Ошибка PATCH стикера id=%s.", sticker_id)
        else:
            logger.info("ℹ️ PATCH стикера id=%s отклонён: %s", sticker_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.patch(
    "/projects/{project_id}/boards/{board_id}/stickers/{sticker_id}/position",
    dependencies=[Depends(require_project_access), Depends(require_write_scope)],
    response_model=BoardStickerSchema,
    responses={
        404: NOT_FOUND_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    summary="Переместить стикер на доске проекта",
    operation_id="moveProjectBoardSticker",
)
async def move_project_sticker(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    sticker_id: StickerIdPath,
    data: BoardStickerPositionUpdateSchema,
    service: BoardStickersServiceDep,
) -> BoardStickerSchema:
    """Сохраняет координаты стикера доступного пользователю проекта."""
    logger.info("🚀 Запрос PATCH позиции стикера id=%s проекта id=%s.", sticker_id, project_id)
    try:
        result = await service.move_sticker(
            project_id=project_id,
            board_id=board_id,
            sticker_id=sticker_id,
            data=data,
        )
        logger.info(
            "✅ Перемещён стикер id=%s: (%s, %s).", result.id, result.canvas_x, result.canvas_y
        )
        return result
    except StickerErrors as error:
        if error.status_code >= 500:
            logger.exception("❌ Ошибка PATCH позиции стикера id=%s.", sticker_id)
        else:
            logger.info("ℹ️ PATCH позиции стикера id=%s отклонён: %s", sticker_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.delete(
    "/projects/{project_id}/boards/{board_id}/stickers/{sticker_id}",
    dependencies=[Depends(require_project_access), Depends(require_write_scope)],
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    summary="Удалить стикер доски проекта",
    operation_id="deleteProjectBoardSticker",
)
async def delete_project_sticker(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    sticker_id: StickerIdPath,
    service: BoardStickersServiceDep,
    revision: Annotated[int, Query(ge=1, description="Ожидаемая ревизия стикера.")],
) -> None:
    """Удаляет стикер доступного проекта по optimistic revision."""
    logger.info("🚀 Запрос DELETE стикера id=%s проекта id=%s.", sticker_id, project_id)
    try:
        await service.delete_sticker(
            project_id=project_id,
            board_id=board_id,
            sticker_id=sticker_id,
            revision=revision,
        )
        logger.info("✅ Удалён стикер id=%s проекта id=%s.", sticker_id, project_id)
    except StickerErrors as error:
        if error.status_code >= 500:
            logger.exception("❌ Ошибка DELETE стикера id=%s.", sticker_id)
        else:
            logger.info("ℹ️ DELETE стикера id=%s отклонён: %s", sticker_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
