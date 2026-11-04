"""Отдельные API metadata редактора и команды создания проекта из личной доски."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path

from src.dependencies.access import ProjectIdPath, require_project_access
from src.dependencies.auth import PrincipalDep, require_write_scope
from src.dependencies.board_editor import BoardEditorServiceDep
from src.exceptions.access import AccessServiceError
from src.exceptions.project_boards import ProjectBoardsServiceError
from src.exceptions.projects import ProjectsServiceError
from src.schemas.board_editor import (
    BoardEditorState,
    BoardEditorWrite,
    BoardPromotionResult,
    BoardPromotionWrite,
)

router = APIRouter(tags=["board-editor"])
BoardIdPath = Annotated[int, Path(gt=0)]
EditorErrors = (ProjectBoardsServiceError, ProjectsServiceError, AccessServiceError)


@router.get(
    "/projects/{project_id}/boards/{board_id}/editor-metadata",
    dependencies=[Depends(require_project_access)],
    response_model=BoardEditorState,
    operation_id="getProjectBoardEditorMetadata",
)
async def get_project_metadata(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    principal: PrincipalDep,
    service: BoardEditorServiceDep,
) -> BoardEditorState:
    """Возвращает группы, блокировки и ревизию редактора проектной доски."""
    try:
        return await service.get_metadata(
            project_id=project_id,
            board_id=board_id,
            user_id=principal.user_id,
        )
    except EditorErrors as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.put(
    "/projects/{project_id}/boards/{board_id}/editor-metadata",
    dependencies=[Depends(require_project_access), Depends(require_write_scope)],
    response_model=BoardEditorState,
    operation_id="putProjectBoardEditorMetadata",
)
async def put_project_metadata(
    project_id: ProjectIdPath,
    board_id: BoardIdPath,
    data: BoardEditorWrite,
    principal: PrincipalDep,
    service: BoardEditorServiceDep,
) -> BoardEditorState:
    """Обновляет группы и блокировки проектной доски с проверкой ревизии."""
    try:
        return await service.put_metadata(
            project_id=project_id,
            board_id=board_id,
            user_id=principal.user_id,
            data=data,
        )
    except EditorErrors as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "/boards/{board_id}/editor-metadata",
    response_model=BoardEditorState,
    operation_id="getFreeBoardEditorMetadata",
)
async def get_free_metadata(
    board_id: BoardIdPath,
    principal: PrincipalDep,
    service: BoardEditorServiceDep,
) -> BoardEditorState:
    """Возвращает группы, блокировки и ревизию редактора личной доски."""
    try:
        return await service.get_metadata(
            project_id=None,
            board_id=board_id,
            user_id=principal.user_id,
        )
    except EditorErrors as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.put(
    "/boards/{board_id}/editor-metadata",
    dependencies=[Depends(require_write_scope)],
    response_model=BoardEditorState,
    operation_id="putFreeBoardEditorMetadata",
)
async def put_free_metadata(
    board_id: BoardIdPath,
    data: BoardEditorWrite,
    principal: PrincipalDep,
    service: BoardEditorServiceDep,
) -> BoardEditorState:
    """Обновляет группы и блокировки личной доски с проверкой ревизии."""
    try:
        return await service.put_metadata(
            project_id=None,
            board_id=board_id,
            user_id=principal.user_id,
            data=data,
        )
    except EditorErrors as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/boards/{board_id}/create-project",
    dependencies=[Depends(require_write_scope)],
    response_model=BoardPromotionResult,
    status_code=201,
    operation_id="createProjectFromFreeBoard",
)
async def create_project_from_board(
    board_id: BoardIdPath,
    data: BoardPromotionWrite,
    principal: PrincipalDep,
    service: BoardEditorServiceDep,
) -> BoardPromotionResult:
    """Создаёт проект из личной доски и сохраняет её содержимое в проектном контексте."""
    try:
        return await service.promote(
            board_id=board_id,
            user_id=principal.user_id,
            data=data,
        )
    except EditorErrors as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
