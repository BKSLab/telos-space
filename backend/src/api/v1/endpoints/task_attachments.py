import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, UploadFile, status
from fastapi.responses import FileResponse

from src.api.v1.responses import (
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
from src.dependencies.access import require_task_access
from src.dependencies.auth import AuthorizationHeaderDep, SessionCookieDep, require_write_scope
from src.dependencies.file_comments import FilePreviewsServiceDep, TaskFileMutationsServiceDep
from src.dependencies.scopes import AttachmentDownloadServiceDep
from src.dependencies.services import TaskAttachmentsServiceDep
from src.exceptions.access import AccessServiceError
from src.exceptions.api_tokens import InsufficientTokenScopeError
from src.exceptions.auth import AuthServiceError
from src.exceptions.task_attachments import TaskAttachmentsServiceError
from src.exceptions.tasks import TasksServiceError
from src.schemas.file_metadata import FileCaptionUpdateSchema
from src.schemas.task_attachments import TaskAttachmentSchema
from src.services.attachment_download import DOWNLOAD_ERRORS
from src.services.file_metadata import FILE_PREVIEW_ERRORS
from src.utils.api_tokens import extract_bearer_secret

router = APIRouter(prefix="", tags=["task-attachments"])
logger = logging.getLogger(__name__)

TOO_LARGE_RESPONSE = {
    "description": "Размер файла превышает установленный лимит.",
    "content": {"application/json": {"example": {"detail": "Размер файла превышает 10 МБ."}}},
}
UNSUPPORTED_TYPE_RESPONSE = {
    "description": "Тип файла не входит в разрешённый список.",
    "content": {"application/json": {"example": {"detail": "Тип файла не поддерживается."}}},
}


@router.get(
    path="/tasks/{task_id}/attachments",
    dependencies=[Depends(require_task_access)],
    status_code=status.HTTP_200_OK,
    summary="Получить файлы задачи",
    description="Возвращает метаданные всех файлов задачи в хронологическом порядке.",
    operation_id="getTaskAttachments",
    response_description="Список файлов задачи.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=list[TaskAttachmentSchema],
)
async def get_task_attachments(
    task_id: Annotated[int, Path(gt=0, description="Идентификатор задачи канбана.")],
    service: TaskAttachmentsServiceDep,
) -> list[TaskAttachmentSchema]:
    """Получает файлы задачи.

    Args:
        task_id: Идентификатор задачи.
        service: Сервис файлов задач.

    Returns:
        Метаданные прикреплённых файлов.

    Raises:
        HTTPException: Если задача не найдена или получить файлы не удалось.
    """
    logger.info("🚀 Запрос GET /tasks/%s/attachments.", task_id)
    try:
        result = await service.get_attachments(task_id=task_id)
        logger.info("✅ Файлы задачи id=%s получены. Найдено: %s.", task_id, len(result))
        return result
    except (TaskAttachmentsServiceError, TasksServiceError) as error:
        logger.exception("❌ Ошибка получения файлов задачи id=%s. Детали: %s", task_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/tasks/{task_id}/attachments",
    status_code=status.HTTP_201_CREATED,
    summary="Прикрепить файл к задаче",
    description="Проверяет и сохраняет один multipart-файл размером до 10 МБ.",
    operation_id="uploadTaskAttachment",
    response_description="Метаданные созданного файла.",
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        413: TOO_LARGE_RESPONSE,
        415: UNSUPPORTED_TYPE_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=TaskAttachmentSchema,
)
async def upload_task_attachment(
    task_id: Annotated[int, Path(gt=0, description="Идентификатор задачи канбана.")],
    file: Annotated[UploadFile, File(description="Файл задачи размером до 10 МБ.")],
    service: TaskFileMutationsServiceDep,
    caption: Annotated[str | None, Form(max_length=4000, description="Подпись оригинала.")] = None,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> TaskAttachmentSchema:
    """Прикрепляет multipart-файл к задаче.

    Args:
        task_id: Идентификатор задачи.
        file: Загружаемый файл.
        service: Сервис файлов задач.

    Returns:
        Метаданные созданного файла.

    Raises:
        HTTPException: Если задача не найдена, файл невалиден или сохранение не удалось.
    """
    logger.info("🚀 Запрос POST /tasks/%s/attachments. Файл: %r.", task_id, file.filename)
    try:
        content = await file.read(service.max_file_size + 1)
        result = await service.upload(
            task_id=task_id,
            file_name=file.filename or "",
            content_type=file.content_type,
            content=content,
            caption=caption,
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
        )
        logger.info("✅ Файл id=%s добавлен к задаче id=%s.", result.id, task_id)
        return result
    except (
        TaskAttachmentsServiceError,
        TasksServiceError,
        AuthServiceError,
        InsufficientTokenScopeError,
        AccessServiceError,
    ) as error:
        logger.exception("❌ Ошибка загрузки файла задачи id=%s. Детали: %s", task_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    finally:
        await file.close()


@router.get(
    path="/tasks/{task_id}/attachments/{attachment_id}/content",
    status_code=status.HTTP_200_OK,
    summary="Открыть или скачать файл задачи",
    description="Показывает безопасное растровое изображение inline, остальные файлы скачивает.",
    operation_id="getTaskAttachmentContent",
    response_description="Бинарное содержимое файла.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
)
async def get_task_attachment_content(
    task_id: Annotated[int, Path(gt=0, description="Идентификатор задачи канбана.")],
    attachment_id: Annotated[int, Path(gt=0, description="Идентификатор файла задачи.")],
    service: AttachmentDownloadServiceDep,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> FileResponse:
    """Возвращает физическое содержимое файла.

    Аутентификация, проверка доступа и чтение метаданных выполняются в
    короткой области базы внутри сервиса. В графе зависимостей маршрута
    нет ни `DbSessionDep`, ни построенных на ней зависимостей: иначе
    медленное скачивание удерживало бы соединение с PostgreSQL всё время
    передачи файла.

    Args:
        task_id: Идентификатор задачи.
        attachment_id: Идентификатор файла.
        service: Подготовка выдачи файла.
        session_cookie: Значение cookie сессии.
        authorization: Заголовок ``Authorization`` внешнего клиента.

    Returns:
        Потоковый файловый HTTP-ответ.

    Raises:
        HTTPException: Если файл не найден или недоступен.
    """
    logger.info(
        "🚀 Запрос GET /tasks/%s/attachments/%s/content.",
        task_id,
        attachment_id,
    )
    try:
        download = await service.prepare(
            task_id=task_id,
            attachment_id=attachment_id,
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
        )
    except DOWNLOAD_ERRORS as error:
        logger.info("ℹ️ Выдача файла id=%s отклонена: %s.", attachment_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error

    logger.info("✅ Содержимое файла id=%s подготовлено.", attachment_id)
    # Дальше начинается долгоживущая фаза: наружу уходят только
    # неизменяемые значения, ни одной ссылки на состояние базы.
    return FileResponse(
        path=download.path,
        media_type=download.media_type,
        filename=download.filename,
        content_disposition_type=download.disposition,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.patch(
    "/tasks/{task_id}/attachments/{attachment_id}",
    summary="Изменить подпись файла задачи",
    description="Обновляет подпись того же оригинала без повторного извлечения текста.",
    operation_id="updateTaskAttachmentCaption",
    response_description="Метаданные оригинала с новой подписью.",
    response_model=TaskAttachmentSchema,
    responses={
        401: {"description": "Требуется вход."},
        403: {"description": "Недостаточно scopes."},
        404: NOT_FOUND_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def update_task_attachment_caption(
    task_id: Annotated[int, Path(gt=0, description="Текущая задача оригинала.", examples=[12])],
    attachment_id: Annotated[int, Path(gt=0, description="Идентификатор оригинала.", examples=[4])],
    data: FileCaptionUpdateSchema,
    service: TaskFileMutationsServiceDep,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> TaskAttachmentSchema:
    """Меняет подпись через общую проверку актуального доступа.

    Args:
        task_id: Задача-владелец оригинала.
        attachment_id: Оригинал для правки подписи.
        data: Новая подпись; null удаляет подпись.
        service: Короткий сценарий записи метаданных.
        session_cookie: Cookie сессии.
        authorization: Bearer с приоритетом перед cookie.
    Returns:
        Метаданные того же файла задачи.
    Raises:
        HTTPException: Оригинал недоступен, подпись невалидна или запись не удалась.
    """
    logger.info("🚀 Изменение подписи файла %s задачи %s.", attachment_id, task_id)
    try:
        result = await service.update_caption(
            task_id=task_id,
            attachment_id=attachment_id,
            caption=data.caption,
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
        )
        logger.info("✅ Подпись файла %s задачи %s изменена.", attachment_id, task_id)
        return result
    except (
        TaskAttachmentsServiceError,
        TasksServiceError,
        AuthServiceError,
        InsufficientTokenScopeError,
        AccessServiceError,
    ) as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "/tasks/{task_id}/attachments/{attachment_id}/preview",
    summary="Открыть миниатюру файла задачи",
    description="После проверки актуального доступа выдаёт ограниченную локальную JPEG-миниатюру без внешних API.",
    operation_id="getTaskAttachmentPreview",
    response_description="Локальная JPEG-миниатюра безопасного растрового оригинала.",
    response_class=FileResponse,
    responses={
        401: {"description": "Требуется вход."},
        403: {"description": "Недостаточно scopes."},
        404: NOT_FOUND_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def get_task_attachment_preview(
    task_id: Annotated[int, Path(gt=0, description="Текущая задача оригинала.", examples=[12])],
    attachment_id: Annotated[int, Path(gt=0, description="Идентификатор оригинала.", examples=[4])],
    service: FilePreviewsServiceDep,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> FileResponse:
    """Передаёт миниатюру после закрытия DB-области и ограниченного декодирования.

    Args:
        task_id: Текущая задача оригинала.
        attachment_id: Оригинал для предпросмотра.
        service: Сервис auth/access и локальных миниатюр.
        session_cookie: Cookie сессии.
        authorization: Bearer с приоритетом перед cookie.
    Returns:
        Защищённый потоковый JPEG-ответ.
    Raises:
        HTTPException: Задача или миниатюра недоступны.
    """
    logger.info("🚀 Миниатюра файла %s задачи %s.", attachment_id, task_id)
    try:
        download = await service.prepare_task(
            task_id=task_id,
            attachment_id=attachment_id,
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
        )
        logger.info("✅ Миниатюра файла %s подготовлена.", attachment_id)
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


@router.post(
    "/tasks/{task_id}/attachments/{attachment_id}/retry",
    status_code=202,
    summary="Повторить обработку файла задачи",
    description="Требует tasks:write. Ставит обработку в устойчивую очередь, сохраняя готовый текст и действующий lease; не вызывает AI API синхронно.",
    operation_id="retryTaskAttachment",
    response_description="Метаданные файла и состояния запрошенного повтора.",
    response_model=TaskAttachmentSchema,
    responses={
        401: {"description": "Требуется вход."},
        403: {"description": "Недостаточно scopes."},
        404: NOT_FOUND_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def retry_task_attachment(
    task_id: Annotated[int, Path(gt=0, description="Текущая задача оригинала.", examples=[12])],
    attachment_id: Annotated[int, Path(gt=0, description="Идентификатор оригинала.", examples=[4])],
    service: TaskFileMutationsServiceDep,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> TaskAttachmentSchema:
    """Восстанавливает обработку существующего файла через общий доменный сервис.

    Args:
        task_id: Задача-владелец оригинала.
        attachment_id: Оригинал для повторной обработки.
        service: Короткий сценарий с общей UoW и durable очередью.
        session_cookie: Cookie сессии.
        authorization: Bearer-заголовок.
    Returns:
        Текущие метаданные оригинала после постановки в очередь.
    Raises:
        HTTPException: Оригинал недоступен или повтор не удалось зафиксировать.
    """
    logger.info("🚀 Повтор обработки файла %s задачи %s.", attachment_id, task_id)
    try:
        result = await service.retry(
            task_id=task_id,
            attachment_id=attachment_id,
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
        )
        logger.info("✅ Повтор файла %s поставлен в очередь.", attachment_id)
        return result
    except (
        TaskAttachmentsServiceError,
        TasksServiceError,
        AuthServiceError,
        InsufficientTokenScopeError,
        AccessServiceError,
    ) as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.delete(
    path="/tasks/{task_id}/attachments/{attachment_id}",
    dependencies=[Depends(require_write_scope), Depends(require_task_access)],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить файл задачи",
    description="Удаляет метаданные и физическое содержимое файла.",
    operation_id="deleteTaskAttachment",
    response_description="Файл удалён.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
)
async def delete_task_attachment(
    task_id: Annotated[int, Path(gt=0, description="Идентификатор задачи канбана.")],
    attachment_id: Annotated[int, Path(gt=0, description="Идентификатор файла задачи.")],
    service: TaskAttachmentsServiceDep,
) -> None:
    """Удаляет файл задачи.

    Args:
        task_id: Идентификатор задачи.
        attachment_id: Идентификатор файла.
        service: Сервис файлов задач.

    Returns:
        ``None`` после успешного удаления.

    Raises:
        HTTPException: Если файл не найден или удалить его не удалось.
    """
    logger.info("🚀 Запрос DELETE /tasks/%s/attachments/%s.", task_id, attachment_id)
    try:
        await service.delete_attachment(task_id=task_id, attachment_id=attachment_id)
        logger.info("✅ Файл id=%s удалён из задачи id=%s.", attachment_id, task_id)
    except TaskAttachmentsServiceError as error:
        logger.exception("❌ Ошибка удаления файла id=%s. Детали: %s", attachment_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
