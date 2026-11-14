"""Единственные публичные маршруты пользовательских документов."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Path, Query, UploadFile
from fastapi.responses import FileResponse

from src.api.v1.responses import (
    CONFLICT_RESPONSE,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
from src.dependencies.document_catalog import DocumentCatalogPrincipalDep, DocumentCatalogServiceDep
from src.exceptions.access import AccessServiceError
from src.exceptions.api_tokens import InsufficientTokenScopeError
from src.exceptions.auth import AuthServiceError
from src.exceptions.document_catalog import DocumentCatalogServiceError
from src.schemas.document_catalog import (
    DocumentCatalogItemSchema,
    DocumentCatalogPageSchema,
    DocumentCreateRequestSchema,
    DocumentProcessingFilter,
    DocumentScope,
    DocumentSourceKind,
    DocumentTextPageSchema,
    DocumentUpdateRequestSchema,
)

router = APIRouter(prefix="/documents", tags=["documents"])
RESPONSES = {
    401: {"description": "Требуется действующая локальная сессия либо Bearer."},
    403: FORBIDDEN_RESPONSE,
    404: NOT_FOUND_RESPONSE,
    409: CONFLICT_RESPONSE,
    422: VALIDATION_RESPONSE,
    500: SERVER_ERROR_RESPONSE,
}
ERRORS = (DocumentCatalogServiceError, AccessServiceError, AuthServiceError, InsufficientTokenScopeError)
DocumentRefPath = Annotated[
    str, Path(min_length=5, max_length=128, description="Непрозрачная стабильная ссылка документа.")
]


@router.get(
    "",
    operation_id="listDocuments",
    summary="Получить каталог документов",
    description="Единый каталог доступных текстов и оригиналов с поиском и фильтрами связей.",
    response_description="Страница документов после проверки текущего доступа.",
    response_model=DocumentCatalogPageSchema,
    responses=RESPONSES,
)
async def list_documents(
    service: DocumentCatalogServiceDep,
    principal: DocumentCatalogPrincipalDep,
    project_id: Annotated[int | None, Query(gt=0)] = None,
    task_id: Annotated[int | None, Query(gt=0)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    scope: DocumentScope = "all",
    source_kind: DocumentSourceKind | None = None,
    format: Annotated[str | None, Query(max_length=16, pattern=r"^[a-zA-Z0-9]+$")] = None,
    processing: DocumentProcessingFilter = "all",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentCatalogPageSchema:
    """Читает текущий доступный каталог до пагинации."""
    try:
        return await service.list(
            user_id=principal.user_id,
            project_id=project_id,
            task_id=task_id,
            q=q,
            scope=scope,
            source_kind=source_kind,
            format=format,
            processing=processing,
            limit=limit,
            offset=offset,
            principal=principal,
        )
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "",
    status_code=201,
    operation_id="createDocument",
    summary="Создать текстовый документ",
    description="Создаёт Markdown у проекта либо задачи; request_id защищает от дублей.",
    response_description="Созданный или ранее созданный этим request_id документ.",
    response_model=DocumentCatalogItemSchema,
    responses=RESPONSES,
)
async def create_document(
    data: DocumentCreateRequestSchema,
    service: DocumentCatalogServiceDep,
    principal: DocumentCatalogPrincipalDep,
) -> DocumentCatalogItemSchema:
    """Создаёт один текстовый документ с серверным автором."""
    try:
        return await service.create(user_id=principal.user_id, data=data, principal=principal)
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/upload",
    status_code=201,
    operation_id="uploadDocument",
    summary="Загрузить документ",
    description="Сохраняет один оригинал и ставит фоновую обработку без создания Markdown-копии.",
    response_description="Документ с состояниями обработки и защищёнными ссылками.",
    response_model=DocumentCatalogItemSchema,
    responses=RESPONSES,
)
async def upload_document(
    service: DocumentCatalogServiceDep,
    principal: DocumentCatalogPrincipalDep,
    file: Annotated[UploadFile, File(description="Неизменяемый оригинал документа.")],
    request_id: Annotated[UUID, Form(description="Стабильный UUID одной загрузки.")],
    project_id: Annotated[int | None, Form(gt=0)] = None,
    owner_task_id: Annotated[int | None, Form(gt=0)] = None,
    title: Annotated[str | None, Form(min_length=1, max_length=255)] = None,
    caption: Annotated[str | None, Form(max_length=2000)] = None,
) -> DocumentCatalogItemSchema:
    """Принимает ограниченный оригинал и возвращает ответ до извлечения."""
    try:
        content = await file.read(service.max_file_size + 1)
        return await service.upload(
            user_id=principal.user_id,
            request_id=request_id,
            file_name=file.filename or "",
            content_type=file.content_type,
            content=content,
            project_id=project_id,
            owner_task_id=owner_task_id,
            title=title,
            caption=caption,
            principal=principal,
        )
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    finally:
        await file.close()


@router.get(
    "/{document_ref}",
    operation_id="getDocument",
    summary="Получить документ",
    description="Возвращает единые метаданные, владельца, связи и фактические стадии обработки.",
    response_description="Документ без загрузки полного текстового представления.",
    response_model=DocumentCatalogItemSchema,
    responses=RESPONSES,
)
async def get_document(
    document_ref: DocumentRefPath,
    service: DocumentCatalogServiceDep,
    principal: DocumentCatalogPrincipalDep,
) -> DocumentCatalogItemSchema:
    """Читает доступный документ по стабильной ссылке."""
    try:
        return await service.get(
            document_ref=document_ref, user_id=principal.user_id, principal=principal
        )
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "/{document_ref}/text",
    operation_id="readDocumentText",
    summary="Прочитать текст документа",
    description="Читает Markdown или сохранённый извлечённый текст без parser, vision и LLM.",
    response_description="Страница текста с хэшем полного представления и Unicode-смещениями.",
    response_model=DocumentTextPageSchema,
    responses=RESPONSES,
)
async def read_document_text(
    document_ref: DocumentRefPath,
    service: DocumentCatalogServiceDep,
    principal: DocumentCatalogPrincipalDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    max_chars: Annotated[int, Query(ge=1, le=8000)] = 8000,
) -> DocumentTextPageSchema:
    """Читает ограниченную страницу исходного представления."""
    try:
        return await service.read_text(
            document_ref=document_ref,
            user_id=principal.user_id,
            principal=principal,
            offset=offset,
            max_chars=max_chars,
        )
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.patch(
    "/{document_ref}",
    operation_id="updateDocument",
    summary="Изменить документ",
    description="Изменяет название, примечание и Markdown при совпадении expected_revision.",
    response_description="Новая версия того же документа.",
    response_model=DocumentCatalogItemSchema,
    responses=RESPONSES,
)
async def update_document(
    document_ref: DocumentRefPath,
    data: DocumentUpdateRequestSchema,
    service: DocumentCatalogServiceDep,
    principal: DocumentCatalogPrincipalDep,
) -> DocumentCatalogItemSchema:
    """Изменяет только прочитанную версию документа."""
    try:
        return await service.update(
            document_ref=document_ref, user_id=principal.user_id, data=data, principal=principal
        )
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.delete(
    "/{document_ref}",
    status_code=204,
    operation_id="deleteDocument",
    summary="Удалить документ",
    description="Удаляет документ и его независимые связи; отличается от снятия связи с задачей.",
    response_description="Документ удалён либо скрыт до фоновой очистки оригинала.",
    responses=RESPONSES,
)
async def delete_document(
    document_ref: DocumentRefPath,
    service: DocumentCatalogServiceDep,
    principal: DocumentCatalogPrincipalDep,
) -> None:
    """Удаляет сам доступный документ."""
    try:
        await service.delete(
            document_ref=document_ref, user_id=principal.user_id, principal=principal
        )
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/{document_ref}/retry",
    status_code=202,
    operation_id="retryDocument",
    summary="Повторить обработку документа",
    description="Ставит незавершённые стадии в обычную очередь с сохранением готового текста.",
    response_description="Документ с актуальными состояниями обработки.",
    response_model=DocumentCatalogItemSchema,
    responses=RESPONSES,
)
async def retry_document(
    document_ref: DocumentRefPath,
    service: DocumentCatalogServiceDep,
    principal: DocumentCatalogPrincipalDep,
) -> DocumentCatalogItemSchema:
    """Повторяет обработку без создания новых документов."""
    try:
        return await service.retry(
            document_ref=document_ref, user_id=principal.user_id, principal=principal
        )
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/{document_ref}/tasks/{task_id}",
    operation_id="linkDocumentTask",
    summary="Связать документ с задачей",
    description="Создаёт связь проектного документа с задачей того же проекта без копирования и смены доступа.",
    response_description="Документ с текущими связями.",
    response_model=DocumentCatalogItemSchema,
    responses=RESPONSES,
)
async def link_document_task(
    document_ref: DocumentRefPath,
    task_id: Annotated[int, Path(gt=0)],
    service: DocumentCatalogServiceDep,
    principal: DocumentCatalogPrincipalDep,
) -> DocumentCatalogItemSchema:
    """Создаёт независимую связь документа."""
    try:
        return await service.link(
            document_ref=document_ref,
            user_id=principal.user_id,
            task_id=task_id,
            principal=principal,
        )
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.delete(
    "/{document_ref}/tasks/{task_id}",
    operation_id="unlinkDocumentTask",
    summary="Убрать документ из задачи",
    description="Удаляет только независимую связь, сохраняя документ и оригинал.",
    response_description="Сохранённый документ с текущими связями.",
    response_model=DocumentCatalogItemSchema,
    responses=RESPONSES,
)
async def unlink_document_task(
    document_ref: DocumentRefPath,
    task_id: Annotated[int, Path(gt=0)],
    service: DocumentCatalogServiceDep,
    principal: DocumentCatalogPrincipalDep,
) -> DocumentCatalogItemSchema:
    """Снимает связь; владение задачей удалить как ссылку нельзя."""
    try:
        return await service.unlink(
            document_ref=document_ref,
            user_id=principal.user_id,
            task_id=task_id,
            principal=principal,
        )
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


async def _file(document_ref, service, principal, *, preview):
    try:
        result = await service.prepare_download(
            document_ref=document_ref,
            user_id=principal.user_id,
            preview=preview,
            principal=principal,
        )
        return FileResponse(
            result.path,
            media_type=result.media_type,
            filename=result.filename,
            content_disposition_type="inline" if preview else "attachment",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )
    except ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "/{document_ref}/content",
    operation_id="downloadDocument",
    summary="Скачать оригинал документа",
    description="Выдаёт защищённый локальный оригинал после освобождения DB-соединения.",
    response_description="Неизменяемый оригинал файла.",
    responses=RESPONSES,
)
async def download_document(
    document_ref: DocumentRefPath,
    service: DocumentCatalogServiceDep,
    principal: DocumentCatalogPrincipalDep,
) -> FileResponse:
    """Передаёт оригинал без удержания auth/access DB-области."""
    return await _file(document_ref, service, principal, preview=False)


@router.get(
    "/{document_ref}/preview",
    operation_id="previewDocument",
    summary="Показать миниатюру документа",
    description="Создаёт безопасную JPEG-миниатюру локально без vision и выдаёт после проверки прав.",
    response_description="Защищённая миниатюра поддерживаемого растра.",
    responses=RESPONSES,
)
async def preview_document(
    document_ref: DocumentRefPath,
    service: DocumentCatalogServiceDep,
    principal: DocumentCatalogPrincipalDep,
) -> FileResponse:
    """Показывает локальную миниатюру оригинала."""
    return await _file(document_ref, service, principal, preview=True)
