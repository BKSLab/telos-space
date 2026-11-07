"""Поиск оригиналов и markdown-документов через единый read-сервис."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from src.api.v1.responses import NOT_FOUND_RESPONSE, SERVER_ERROR_RESPONSE, VALIDATION_RESPONSE
from src.dependencies.access import require_project_access, require_task_access
from src.dependencies.auth import PrincipalDep
from src.dependencies.file_search import FileSearchServiceDep
from src.exceptions.access import AccessServiceError
from src.exceptions.file_processing import FileSearchServiceError
from src.schemas.file_search import FileSearchPageSchema, FileSearchScope

logger = logging.getLogger(__name__)
router = APIRouter(tags=["file-search"])
SearchQuery = Annotated[
    str,
    Query(
        min_length=1,
        max_length=2000,
        description="Слова, фраза или websearch-запрос.",
        examples=["поставка оборудования"],
    ),
]
SearchLimit = Annotated[
    int, Query(ge=1, le=100, description="Число первичных источников на странице.", examples=[30])
]
SearchOffset = Annotated[int, Query(ge=0, description="Смещение страницы.", examples=[0])]
RESPONSES = {
    401: {"description": "Требуется вход."},
    403: {"description": "Недостаточно scopes."},
    404: NOT_FOUND_RESPONSE,
    422: VALIDATION_RESPONSE,
    500: SERVER_ERROR_RESPONSE,
}


@router.get(
    "/projects/{project_id}/files/search",
    dependencies=[Depends(require_project_access)],
    summary="Найти файлы проекта",
    description="Ищет по названию, подписи и извлечённому тексту оригиналов и документов. Доступность embeddings и Qdrant не влияет на поиск.",
    operation_id="searchProjectFiles",
    response_description="Страница совпавших первичных источников.",
    response_model=FileSearchPageSchema,
    responses=RESPONSES,
)
async def search_project_files(
    project_id: Annotated[int, Path(gt=0, description="Текущий проект.", examples=[2])],
    principal: PrincipalDep,
    service: FileSearchServiceDep,
    q: SearchQuery,
    scope: Annotated[
        FileSearchScope, Query(description="Виды источников.", examples=["all"])
    ] = "all",
    task_id: Annotated[
        int | None,
        Query(
            gt=0,
            description="Задача текущего проекта: её вложения и связанные документы.",
            examples=[7],
        ),
    ] = None,
    limit: SearchLimit = 30,
    offset: SearchOffset = 0,
) -> FileSearchPageSchema:
    """Возвращает страницу актуально доступных файлов и документов проекта."""
    logger.info("🚀 Поиск файлов проекта %s.", project_id)
    try:
        result = await service.search_project(
            project_id=project_id,
            user_id=principal.user_id,
            query=q,
            scope=scope,
            task_id=task_id,
            limit=limit,
            offset=offset,
        )
        logger.info("✅ Поиск проекта %s: %s источников.", project_id, result.total)
        return result
    except (AccessServiceError, FileSearchServiceError) as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "/tasks/{task_id}/files/search",
    dependencies=[Depends(require_task_access)],
    summary="Найти файлы задачи",
    description="Ищет собственные вложения задачи и связанные markdown-документы в её текущем проекте. Поддерживает личные задачи.",
    operation_id="searchTaskFiles",
    response_description="Страница совпавших первичных источников.",
    response_model=FileSearchPageSchema,
    responses=RESPONSES,
)
async def search_task_files(
    task_id: Annotated[int, Path(gt=0, description="Задача, включая личную.", examples=[7])],
    principal: PrincipalDep,
    service: FileSearchServiceDep,
    q: SearchQuery,
    limit: SearchLimit = 30,
    offset: SearchOffset = 0,
) -> FileSearchPageSchema:
    """Возвращает доступные вложения и связанные документы текущей задачи."""
    logger.info("🚀 Поиск файлов задачи %s.", task_id)
    try:
        result = await service.search_task(
            task_id=task_id, user_id=principal.user_id, query=q, limit=limit, offset=offset
        )
        logger.info("✅ Поиск задачи %s: %s источников.", task_id, result.total)
        return result
    except (AccessServiceError, FileSearchServiceError) as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
