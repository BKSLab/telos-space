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
from src.dependencies.services import BoardsServiceDep
from src.exceptions.boards import BoardsServiceError
from src.schemas.boards import (
    BoardContentSchema,
    BoardCreateSchema,
    BoardSchema,
    BoardUpdateSchema,
)

router = APIRouter(tags=["boards"])
logger = logging.getLogger(__name__)
BoardIdPath = Annotated[int, Path(gt=0, description="Идентификатор доски.")]


def _http_error(
    error: BoardsServiceError, action: str, board_id: int | None
) -> HTTPException:
    """Логирует отказ по уровню серьёзности и переводит его в HTTP-ответ."""
    if error.status_code >= 500:
        logger.exception("❌ Ошибка %s доски id=%s.", action, board_id)
    else:
        logger.info("ℹ️ %s доски id=%s отклонён: %s", action, board_id, error)
    return HTTPException(status_code=error.status_code, detail=error.detail)


@router.get(
    "/projects/{project_id}/boards",
    dependencies=[Depends(require_project_access)],
    response_model=list[BoardSchema],
    responses={404: NOT_FOUND_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    summary="Получить доски проекта",
    operation_id="listProjectBoards",
)
async def list_project_boards(
    project_id: ProjectIdPath,
    service: BoardsServiceDep,
    principal: PrincipalDep,
) -> list[BoardSchema]:
    """Возвращает активные доски; у проекта без досок заводит «Основную»."""
    logger.info("🚀 Запрос GET /projects/%s/boards.", project_id)
    try:
        result = await service.list_boards(project_id, user_id=principal.user_id)
        logger.info("✅ Досок проекта id=%s: %s.", project_id, len(result))
        return result
    except BoardsServiceError as error:
        raise _http_error(error, "GET", None) from error


@router.post(
    "/projects/{project_id}/boards",
    dependencies=[Depends(require_project_access), Depends(require_write_scope)],
    response_model=BoardSchema,
    status_code=status.HTTP_201_CREATED,
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    summary="Создать доску проекта",
    operation_id="createProjectBoard",
)
async def create_project_board(
    project_id: ProjectIdPath,
    data: BoardCreateSchema,
    service: BoardsServiceDep,
    principal: PrincipalDep,
) -> BoardSchema:
    """Создаёт доску последней вкладкой."""
    logger.info("🚀 Запрос POST доски проекта id=%s.", project_id)
    try:
        result = await service.create_board(
            project_id=project_id, data=data, user_id=principal.user_id
        )
        logger.info("✅ Создана доска id=%s проекта id=%s.", result.id, project_id)
        return result
    except BoardsServiceError as error:
        raise _http_error(error, "POST", None) from error


@router.patch(
    "/projects/{project_id}/boards/{board_id}",
    dependencies=[Depends(require_project_access), Depends(require_write_scope)],
    response_model=BoardSchema,
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    summary="Переименовать или переставить доску",
    operation_id="updateProjectBoard",
)
async def update_project_board(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    data: BoardUpdateSchema,
    service: BoardsServiceDep,
) -> BoardSchema:
    """Меняет название или позицию доски по optimistic revision."""
    logger.info("🚀 Запрос PATCH доски id=%s проекта id=%s.", board_id, project_id)
    try:
        result = await service.update_board(project_id=project_id, board_id=board_id, data=data)
        logger.info("✅ Изменена доска id=%s, revision=%s.", result.id, result.revision)
        return result
    except BoardsServiceError as error:
        raise _http_error(error, "PATCH", board_id) from error


@router.delete(
    "/projects/{project_id}/boards/{board_id}",
    dependencies=[Depends(require_project_access), Depends(require_write_scope)],
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    summary="Удалить доску проекта",
    operation_id="deleteProjectBoard",
)
async def delete_project_board(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    service: BoardsServiceDep,
    revision: Annotated[int, Query(ge=1, description="Ожидаемая ревизия доски.")],
) -> None:
    """Мягко удаляет доску; последнюю доску проекта удалить нельзя (409)."""
    logger.info("🚀 Запрос DELETE доски id=%s проекта id=%s.", board_id, project_id)
    try:
        await service.delete_board(project_id=project_id, board_id=board_id, revision=revision)
        logger.info("✅ Доска id=%s удалена.", board_id)
    except BoardsServiceError as error:
        raise _http_error(error, "DELETE", board_id) from error


@router.get(
    "/projects/{project_id}/boards/{board_id}/content",
    dependencies=[Depends(require_project_access)],
    response_model=BoardContentSchema,
    responses={404: NOT_FOUND_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    summary="Получить содержимое доски",
    operation_id="getProjectBoardContent",
)
async def get_project_board_content(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    service: BoardsServiceDep,
) -> BoardContentSchema:
    """Возвращает доску, её стикеры и фигуры одним запросом."""
    logger.info("🚀 Запрос GET содержимого доски id=%s проекта id=%s.", board_id, project_id)
    try:
        result = await service.get_content(project_id=project_id, board_id=board_id)
        logger.info(
            "✅ Доска id=%s: стикеров %s, фигур %s.",
            board_id,
            len(result.stickers),
            len(result.shapes),
        )
        return result
    except BoardsServiceError as error:
        raise _http_error(error, "GET content", board_id) from error
