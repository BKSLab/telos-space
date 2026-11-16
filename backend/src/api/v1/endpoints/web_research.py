"""Ограниченное публичное исследование и явное сохранение в документы проекта."""

from uuid import uuid4

from fastapi import APIRouter, HTTPException

from src.api.v1.responses import FORBIDDEN_RESPONSE, NOT_FOUND_RESPONSE, VALIDATION_RESPONSE
from src.dependencies.access import ProjectIdPath
from src.dependencies.context_auth import ContextPrincipalDep, ContextSessionPrincipalDep
from src.dependencies.web_research import WebResearchServiceDep
from src.exceptions.access import AccessServiceError
from src.exceptions.api_tokens import ApiTokensServiceError
from src.exceptions.auth import AuthServiceError
from src.exceptions.document_catalog import DocumentCatalogServiceError
from src.exceptions.web_research import WebResearchServiceError
from src.schemas.document_catalog import DocumentCatalogItemSchema
from src.schemas.web_research import (
    WebPageGrantSchema,
    WebReadSchema,
    WebResearchReadSchema,
    WebResearchSearchSchema,
    WebResearchStatusSchema,
    WebSaveSchema,
    WebSearchSchema,
    WebTopicApprovalSchema,
    WebTopicGrantSchema,
    WebUrlApprovalSchema,
)

router = APIRouter(prefix="/projects/{project_id}/web-research", tags=["web-research"])
ERRORS = (
    WebResearchServiceError,
    AccessServiceError,
    AuthServiceError,
    ApiTokensServiceError,
    DocumentCatalogServiceError,
)
RESPONSES = {
    401: {"description": "Требуется вход."},
    403: FORBIDDEN_RESPONSE,
    404: NOT_FOUND_RESPONSE,
    409: {"description": "Страница изменилась после чтения."},
    422: VALIDATION_RESPONSE,
    502: {"description": "Внешний источник недоступен."},
    503: {"description": "Внешнее исследование или провайдер не настроены."},
}


@router.get(
    "/status",
    response_model=WebResearchStatusSchema,
    operation_id="getWebResearchStatus",
    summary="Получить готовность внешнего исследования",
    description="Показывает готовность чтения и поиска без credentials провайдера.",
    response_description="Возможности публичного исследования.",
    responses=RESPONSES,
)
async def get_status(
    project_id: ProjectIdPath,
    service: WebResearchServiceDep,
    principal: ContextPrincipalDep,
) -> WebResearchStatusSchema:
    try:
        return await service.status(project_id=project_id, principal=principal)
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/topics",
    response_model=WebTopicGrantSchema,
    operation_id="approveWebTopic",
    summary="Разрешить публичную тему исследования",
    description="Только UI-сессия человека разрешает точный публичный запрос; Арис использует подтверждённое действие.",
    response_description="Короткоживущее разрешение точной темы.",
    responses=RESPONSES,
)
async def approve_topic(
    project_id: ProjectIdPath,
    data: WebTopicApprovalSchema,
    service: WebResearchServiceDep,
    principal: ContextSessionPrincipalDep,
) -> WebTopicGrantSchema:
    try:
        return await service.approve_topic(
            project_id=project_id,
            principal=principal,
            public_topic=data.query,
            approval_id=str(uuid4()),
        )
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/pages",
    response_model=WebPageGrantSchema,
    operation_id="approveWebPage",
    summary="Разрешить чтение точного публичного адреса",
    description="Человек вводит точный URL; модель не подставляет произвольный адрес в разрешение другой темы.",
    response_description="Разрешённая страница и срок.",
    responses=RESPONSES,
)
async def approve_page(
    project_id: ProjectIdPath,
    data: WebUrlApprovalSchema,
    service: WebResearchServiceDep,
    principal: ContextSessionPrincipalDep,
) -> WebPageGrantSchema:
    try:
        return await service.approve_url(
            project_id=project_id, principal=principal, url=data.url, approval_id=str(uuid4())
        )
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/search",
    response_model=WebResearchSearchSchema,
    operation_id="searchWebResearch",
    summary="Найти публичные материалы по разрешённой теме",
    description="Поиск принимает подписанную тему, возвращает кандидатов; страницы не считаются прочитанными.",
    response_description="Поисковые фрагменты и разрешённые маршруты чтения.",
    responses=RESPONSES,
)
async def search(
    project_id: ProjectIdPath,
    data: WebSearchSchema,
    service: WebResearchServiceDep,
    principal: ContextPrincipalDep,
) -> WebResearchSearchSchema:
    try:
        return await service.search(project_id=project_id, principal=principal, **data.model_dump())
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/read",
    response_model=WebResearchReadSchema,
    operation_id="readWebResearchPage",
    summary="Прочитать разрешённую публичную страницу",
    description="Читает текст с ограничениями SSRF/размера/времени и перепроверяет доступ после сети.",
    response_description="Текстовая проекция, хэш версии и границы полноты.",
    responses=RESPONSES,
)
async def read(
    project_id: ProjectIdPath,
    data: WebReadSchema,
    service: WebResearchServiceDep,
    principal: ContextPrincipalDep,
) -> WebResearchReadSchema:
    try:
        return await service.read(project_id=project_id, principal=principal, **data.model_dump())
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/documents",
    response_model=DocumentCatalogItemSchema,
    operation_id="saveWebResearch",
    summary="Сохранить прочитанную версию исследования",
    description="Явно создаёт документ через общий каталог с автором и квитанцией; изменение хэша даёт конфликт.",
    response_description="Документ с URL/временем/хэшем/покрытием внешнего материала.",
    responses=RESPONSES,
)
async def save(
    project_id: ProjectIdPath,
    data: WebSaveSchema,
    service: WebResearchServiceDep,
    principal: ContextPrincipalDep,
) -> DocumentCatalogItemSchema:
    try:
        return await service.save(project_id=project_id, principal=principal, **data.model_dump())
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
