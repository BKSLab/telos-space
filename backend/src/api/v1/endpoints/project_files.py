"""HTTP-адаптер самостоятельных файлов проекта."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, Response, UploadFile
from fastapi.responses import FileResponse

from src.api.v1.responses import NOT_FOUND_RESPONSE, SERVER_ERROR_RESPONSE, VALIDATION_RESPONSE
from src.api.v1.uploads import read_upload
from src.dependencies.access import require_project_access
from src.dependencies.auth import (
    AuthorizationHeaderDep,
    PrincipalDep,
    SessionCookieDep,
    require_write_scope,
)
from src.dependencies.file_comments import FilePreviewsServiceDep
from src.dependencies.scopes import ProjectFilesServiceDep
from src.exceptions.access import AccessServiceError
from src.exceptions.api_tokens import InsufficientTokenScopeError
from src.exceptions.auth import AuthServiceError
from src.exceptions.project_files import ProjectFilesServiceError
from src.schemas.file_metadata import FileCaptionUpdateSchema
from src.schemas.project_files import ProjectFileDetailSchema, ProjectFileSchema
from src.services.file_metadata import FILE_PREVIEW_ERRORS
from src.services.project_files import PROJECT_FILE_DOWNLOAD_ERRORS
from src.utils.api_scopes import SCOPE_PROJECTS_WRITE
from src.utils.api_tokens import extract_bearer_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["project-files"])
ProjectId = Annotated[int, Path(gt=0, description="Проект-владелец файла.", examples=[2])]
FileId = Annotated[int, Path(gt=0, description="Идентификатор файла проекта.", examples=[5])]
RESPONSES = {
    401: {"description": "Требуется вход."},
    403: {"description": "Недостаточно scopes."},
    404: NOT_FOUND_RESPONSE,
    422: VALIDATION_RESPONSE,
    500: SERVER_ERROR_RESPONSE,
}
FILE_ERRORS = (ProjectFilesServiceError, AccessServiceError)


@router.get(
    "/projects/{project_id}/files",
    dependencies=[Depends(require_project_access)],
    summary="Получить файлы проекта",
    description="Возвращает страницу файлов с состояниями обработки и поиском по имени и тексту.",
    operation_id="getProjectFiles",
    response_description="Метаданные файлов и их состояний.",
    response_model=list[ProjectFileSchema],
    responses=RESPONSES,
)
async def get_project_files(
    project_id: ProjectId,
    principal: PrincipalDep,
    service: ProjectFilesServiceDep,
    search: Annotated[
        str | None,
        Query(
            max_length=2000,
            description="Поиск по имени и извлечённому тексту.",
            examples=["требования"],
        ),
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=200, description="Размер страницы.", examples=[100])
    ] = 100,
    offset: Annotated[int, Query(ge=0, description="Смещение страницы.", examples=[0])] = 0,
) -> list[ProjectFileSchema]:
    """Возвращает файлы доступного пользователю проекта.

    Args:
        project_id: Проект-владелец файлов.
        principal: Аутентифицированный локальный пользователь.
        service: Сервис файлов проекта.
        search: Запрос по имени и извлечённому тексту.
        limit: Размер страницы.
        offset: Смещение страницы.
    Returns:
        Метаданные и состояния файлов.
    Raises:
        HTTPException: Проект недоступен или данные не удалось прочитать.
    """
    logger.info("🚀 Чтение файлов проекта %s.", project_id)
    try:
        result = await service.list_files(
            project_id=project_id,
            user_id=principal.user_id,
            search=search,
            limit=limit,
            offset=offset,
        )
        logger.info("✅ Получено %s файлов проекта %s.", len(result), project_id)
        return result
    except (*FILE_ERRORS, AuthServiceError, InsufficientTokenScopeError) as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/projects/{project_id}/files",
    status_code=201,
    summary="Загрузить файл проекта",
    description="Сохраняет оригинал и атомарно ставит обработку в очередь. Распознавание выполняется в фоне.",
    operation_id="uploadProjectFile",
    response_description="Сохранённый оригинал с состоянием ожидания.",
    response_model=ProjectFileSchema,
    responses={
        **RESPONSES,
        413: {"description": "Превышен размер файла."},
        415: {"description": "Неподдерживаемый формат."},
    },
)
async def upload_project_file(
    project_id: ProjectId,
    service: ProjectFilesServiceDep,
    file: Annotated[UploadFile, File(description="Поддерживаемый документ или изображение.")],
    caption: Annotated[
        str | None, Form(max_length=4000, description="Пользовательская подпись оригинала.")
    ] = None,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> ProjectFileSchema:
    """Принимает ограниченный multipart-файл и гарантированно закрывает его.

    Args:
        project_id: Проект-владелец оригинала.
        principal: Пользователь, определяющий автора загрузки.
        service: Сервис файлов проекта.
        file: Multipart-файл поддерживаемого формата.
    Returns:
        Метаданные сохранённого оригинала с состоянием ожидания.
    Raises:
        HTTPException: Доступ запрещён, файл невалиден или сохранение не удалось.
    """
    logger.info("🚀 Загрузка файла проекта %s.", project_id)
    try:
        uploaded = await read_upload(file, max_size=service.max_file_size)
        result = await service.upload(
            project_id=project_id,
            file_name=uploaded.name,
            content=uploaded.content,
            caption=caption,
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
        )
        logger.info("✅ Файл %s проекта %s сохранён.", result.id, project_id)
        return result
    except (*FILE_ERRORS, AuthServiceError, InsufficientTokenScopeError) as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    finally:
        await file.close()


@router.get(
    "/projects/{project_id}/files/{file_id}",
    dependencies=[Depends(require_project_access)],
    summary="Прочитать файл проекта",
    description="Возвращает метаданные и страницу сохранённого текста без вызова внешних моделей.",
    operation_id="getProjectFile",
    response_description="Метаданные и фрагмент извлечённого текста.",
    response_model=ProjectFileDetailSchema,
    responses=RESPONSES,
)
async def get_project_file(
    project_id: ProjectId,
    file_id: FileId,
    principal: PrincipalDep,
    service: ProjectFilesServiceDep,
    offset: Annotated[
        int, Query(ge=0, description="Позиция в извлечённом тексте.", examples=[0])
    ] = 0,
    max_chars: Annotated[
        int, Query(ge=1, le=32000, description="Максимум символов фрагмента.", examples=[8000])
    ] = 8000,
) -> ProjectFileDetailSchema:
    """Читает страницу текста оригинала в текущем проекте.

    Args:
        project_id: Проект-владелец файла.
        file_id: Идентификатор файла.
        principal: Пользователь запроса.
        service: Сервис файлов проекта.
        offset: Начальная позиция текста.
        max_chars: Максимальный размер фрагмента.
    Returns:
        Метаданные, фрагмент текста и позицию продолжения.
    Raises:
        HTTPException: Файл недоступен или чтение завершилось ошибкой.
    """
    logger.info("🚀 Чтение файла %s проекта %s.", file_id, project_id)
    try:
        result = await service.get_file(
            project_id=project_id,
            file_id=file_id,
            user_id=principal.user_id,
            offset=offset,
            max_chars=max_chars,
        )
        result.can_edit = result.can_edit and principal.allows(SCOPE_PROJECTS_WRITE)
        logger.info("✅ Файл %s прочитан.", file_id)
        return result
    except FILE_ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "/projects/{project_id}/files/{file_id}/content",
    summary="Скачать оригинал файла проекта",
    description="Проверяет доступ в короткой области БД и отдаёт оригинал как вложение.",
    operation_id="downloadProjectFile",
    response_description="Бинарный оригинал файла.",
    response_class=FileResponse,
    responses=RESPONSES,
)
async def download_project_file(
    project_id: ProjectId,
    file_id: FileId,
    service: ProjectFilesServiceDep,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> FileResponse:
    """Передаёт оригинал после освобождения соединения PostgreSQL.

    Args:
        project_id: Проект-владелец оригинала.
        file_id: Идентификатор оригинала.
        service: Сервис с собственной короткой auth/access областью.
        session_cookie: Cookie сессии.
        authorization: Bearer-заголовок внешнего клиента.
    Returns:
        Защищённый ответ со скачиванием оригинала.
    Raises:
        HTTPException: Учётные данные, scopes или доступ не допускают скачивание.
    """
    logger.info("🚀 Скачивание файла %s проекта %s.", file_id, project_id)
    try:
        download = await service.prepare_download(
            project_id=project_id,
            file_id=file_id,
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
        )
        logger.info("✅ Выдача файла %s подготовлена.", file_id)
        return FileResponse(
            path=download.path,
            media_type=download.media_type,
            filename=download.filename,
            content_disposition_type="attachment",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )
    except PROJECT_FILE_DOWNLOAD_ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/projects/{project_id}/files/{file_id}/retry",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    status_code=202,
    summary="Повторить обработку файла проекта",
    description="Ставит незавершённые этапы в устойчивую очередь, сохраняя уже извлечённый текст.",
    operation_id="retryProjectFile",
    response_description="Файл, поставленный в очередь.",
    response_model=ProjectFileSchema,
    responses=RESPONSES,
)
async def retry_project_file(
    project_id: ProjectId, file_id: FileId, principal: PrincipalDep, service: ProjectFilesServiceDep
) -> ProjectFileSchema:
    """Запрашивает повтор обработки доступного файла.

    Args:
        project_id: Проект-владелец файла.
        file_id: Файл для повторной обработки.
        principal: Пользователь запроса.
        service: Сервис файлов проекта.
    Returns:
        Метаданные файла с состоянием ожидания.
    Raises:
        HTTPException: Файл недоступен или задание не удалось сохранить.
    """
    logger.info("🚀 Повтор обработки файла %s проекта %s.", file_id, project_id)
    try:
        result = await service.retry(
            project_id=project_id, file_id=file_id, user_id=principal.user_id
        )
        logger.info("✅ Повтор файла %s поставлен в очередь.", file_id)
        return result
    except FILE_ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.delete(
    "/projects/{project_id}/files/{file_id}",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    status_code=204,
    summary="Удалить файл проекта",
    description="Сразу скрывает источник и ставит очистку оригинала и производных данных в очередь.",
    operation_id="deleteProjectFile",
    response_description="Файл скрыт; очистка поставлена в очередь.",
    responses=RESPONSES,
)
async def delete_project_file(
    project_id: ProjectId, file_id: FileId, principal: PrincipalDep, service: ProjectFilesServiceDep
) -> Response:
    """Снимает доступ к файлу до фоновой очистки.

    Args:
        project_id: Проект-владелец файла.
        file_id: Удаляемый файл.
        principal: Пользователь запроса.
        service: Сервис файлов проекта.
    Returns:
        Пустой ответ после фиксации удаления.
    Raises:
        HTTPException: Файл недоступен или удаление не удалось зафиксировать.
    """
    logger.info("🚀 Удаление файла %s проекта %s.", file_id, project_id)
    try:
        await service.delete_file(project_id=project_id, file_id=file_id, user_id=principal.user_id)
        logger.info("✅ Файл %s скрыт и ожидает очистки.", file_id)
        return Response(status_code=204)
    except FILE_ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.patch(
    "/projects/{project_id}/files/{file_id}",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    summary="Изменить подпись файла проекта",
    description="Меняет пользовательскую подпись того же оригинала, сохраняя извлечённый текст.",
    operation_id="updateProjectFileCaption",
    response_description="Метаданные файла с новой подписью.",
    response_model=ProjectFileSchema,
    responses=RESPONSES,
)
async def update_project_file_caption(
    project_id: ProjectId,
    file_id: FileId,
    data: FileCaptionUpdateSchema,
    principal: PrincipalDep,
    service: ProjectFilesServiceDep,
) -> ProjectFileSchema:
    """Меняет подпись доступного оригинала проекта.

    Args:
        project_id: Текущий проект-владелец.
        file_id: Оригинал для изменения подписи.
        data: Новая подпись либо null для её удаления.
        principal: Аутентифицированный локальный пользователь.
        service: Общий сервис метаданных проекта.
    Returns:
        Метаданные того же файла с актуальными состояниями обработки.
    Raises:
        HTTPException: Доступ отсутствует или подпись не удалось сохранить.
    """
    logger.info("🚀 Изменение подписи файла %s проекта %s.", file_id, project_id)
    try:
        result = await service.update_caption(
            project_id=project_id, file_id=file_id, user_id=principal.user_id, caption=data.caption
        )
        logger.info("✅ Подпись файла %s изменена.", file_id)
        return result
    except FILE_ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "/projects/{project_id}/files/{file_id}/preview",
    summary="Открыть миниатюру файла проекта",
    description="Проверяет текущие права и выдаёт ограниченную локальную JPEG-миниатюру без AI API.",
    operation_id="getProjectFilePreview",
    response_description="Защищённая JPEG-миниатюра оригинала.",
    response_class=FileResponse,
    responses=RESPONSES,
)
async def get_project_file_preview(
    project_id: ProjectId,
    file_id: FileId,
    service: FilePreviewsServiceDep,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> FileResponse:
    """Отдаёт миниатюру после закрытия короткой auth/access области.

    Args:
        project_id: Проект-владелец оригинала.
        file_id: Оригинал для предпросмотра.
        service: Сервис защищённых локальных миниатюр.
        session_cookie: Cookie сессии.
        authorization: Bearer-заголовок с приоритетом перед cookie.
    Returns:
        Ограниченный безопасный растр без активного содержимого оригинала.
    Raises:
        HTTPException: Пользователь, оригинал или миниатюра недоступны.
    """
    logger.info("🚀 Миниатюра файла %s проекта %s.", file_id, project_id)
    try:
        download = await service.prepare_project(
            project_id=project_id,
            file_id=file_id,
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
        )
        logger.info("✅ Миниатюра файла %s подготовлена.", file_id)
        return FileResponse(
            path=download.path,
            media_type=download.media_type,
            filename=download.filename,
            content_disposition_type="inline",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )
    except FILE_PREVIEW_ERRORS as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
