"""Bounded read/refresh сохранённой карты без запуска моделей на GET."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from src.api.v1.responses import FORBIDDEN_RESPONSE, NOT_FOUND_RESPONSE, VALIDATION_RESPONSE
from src.dependencies.access import ProjectIdPath
from src.dependencies.context_auth import ContextPrincipalDep
from src.dependencies.project_knowledge import ProjectKnowledgeServiceDep
from src.exceptions.project_knowledge import ProjectKnowledgeServiceError
from src.schemas.project_knowledge import (
    KnowledgeCategory,
    ProjectKnowledgeClaimSchema,
    ProjectKnowledgeMapSchema,
    ProjectKnowledgeStatusSchema,
)

router = APIRouter(prefix="/projects/{project_id}/knowledge/map", tags=["project-knowledge"])
RESPONSES = {
    401: {"description": "Требуется вход."},
    403: FORBIDDEN_RESPONSE,
    404: NOT_FOUND_RESPONSE,
    409: {"description": "Источники изменились во время чтения."},
    422: VALIDATION_RESPONSE,
}


@router.get(
    "",
    response_model=ProjectKnowledgeMapSchema,
    operation_id="readProjectKnowledgeMap",
    summary="Получить карту утверждений проекта",
    description="Читает ограниченную страницу актуальных утверждений с точными доказательствами и границами метода. Требует knowledge:read и projects:read.",
    response_description="Текущая карта, покрытие и серверный manifest чтения.",
    responses=RESPONSES,
)
async def read_map(
    project_id: ProjectIdPath,
    service: ProjectKnowledgeServiceDep,
    principal: ContextPrincipalDep,
    limit: Annotated[int, Query(ge=1, le=50, description="Размер страницы утверждений.")] = 20,
    offset: Annotated[int, Query(ge=0, description="Смещение страницы.")] = 0,
    category: Annotated[KnowledgeCategory | None, Query(description="Раздел карты.")] = None,
    exclude_category: Annotated[
        KnowledgeCategory | None, Query(description="Раздел, исключённый из выборки.")
    ] = None,
    q: Annotated[
        str | None,
        Query(max_length=200, pattern=r"^[^\x00]*$", description="Подстрока актуального утверждения."),
    ] = None,
) -> ProjectKnowledgeMapSchema:
    try:
        return await service.read_map(
            project_id=project_id,
            principal=principal,
            limit=limit,
            offset=offset,
            category=category,
            exclude_category=exclude_category,
            query=q,
        )
    except ProjectKnowledgeServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "/status",
    response_model=ProjectKnowledgeStatusSchema,
    operation_id="getProjectKnowledgeMapStatus",
    summary="Получить свежесть карты проекта",
    description="Показывает текущие поколения, охват и безопасную диагностику фоновой обработки.",
    response_description="Состояние карты без нового анализа.",
    responses=RESPONSES,
)
async def get_status(
    project_id: ProjectIdPath, service: ProjectKnowledgeServiceDep, principal: ContextPrincipalDep
) -> ProjectKnowledgeStatusSchema:
    try:
        return await service.get_status(project_id=project_id, principal=principal)
    except ProjectKnowledgeServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "/claims/{claim_id}",
    response_model=ProjectKnowledgeClaimSchema,
    operation_id="getProjectKnowledgeClaim",
    summary="Получить утверждение с доказательствами",
    description="Перепроверяет все версии доказательств. После изменения/удаления старый текст и цитаты скрываются.",
    response_description="Текущее утверждение либо безопасная отметка устаревания.",
    responses=RESPONSES,
)
async def get_claim(
    project_id: ProjectIdPath,
    claim_id: UUID,
    service: ProjectKnowledgeServiceDep,
    principal: ContextPrincipalDep,
) -> ProjectKnowledgeClaimSchema:
    try:
        return await service.get_claim(
            project_id=project_id, claim_id=claim_id, principal=principal
        )
    except ProjectKnowledgeServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/refresh",
    response_model=ProjectKnowledgeStatusSchema,
    status_code=202,
    operation_id="refreshProjectKnowledgeMap",
    summary="Поставить обновление карты в очередь",
    description="Владелец проекта с projects:write ставит задачу в существующую постоянную очередь. Исходный паспорт не меняется.",
    response_description="Состояние принятого обновления.",
    responses=RESPONSES,
)
async def refresh(
    project_id: ProjectIdPath, service: ProjectKnowledgeServiceDep, principal: ContextPrincipalDep
) -> ProjectKnowledgeStatusSchema:
    try:
        return await service.request_refresh(project_id=project_id, principal=principal)
    except ProjectKnowledgeServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
