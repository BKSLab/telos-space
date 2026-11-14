"""Multipart-адаптер атомарной отправки комментария с файлами."""

import json
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Path, UploadFile

from src.api.v1.responses import (
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
from src.api.v1.uploads import close_uploads, read_uploads
from src.dependencies.auth import AuthorizationHeaderDep, SessionCookieDep
from src.dependencies.file_comments import FileCommentsServiceDep
from src.exceptions.access import AccessServiceError
from src.exceptions.api_tokens import InsufficientTokenScopeError
from src.exceptions.auth import AuthServiceError
from src.exceptions.document_catalog import DocumentCatalogServiceError
from src.exceptions.file_comments import FileCommentsServiceError
from src.exceptions.task_attachments import TaskAttachmentsServiceError
from src.exceptions.tasks import TasksServiceError
from src.schemas.task_comments import CommentSchema
from src.services.file_comments import CommentFileUpload
from src.utils.api_tokens import extract_bearer_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["task-comments"])


def _json_list(value: str | None, *, field: str) -> list:
    if value is None:
        return []
    try:
        result = json.loads(value)
    except (ValueError, TypeError) as error:
        raise HTTPException(422, f"Поле {field} должно содержать JSON-массив.") from error
    if not isinstance(result, list):
        raise HTTPException(422, f"Поле {field} должно содержать JSON-массив.")
    return result


@router.post(
    "/tasks/{task_id}/comments/with-files",
    status_code=201,
    summary="Отправить комментарий с документами",
    description="Одной транзакцией сохраняет комментарий и до десяти документов (загрузки и существующие документы задачи), каждый новый до 10 МиБ. Требуется comments:write, а при наличии документов также tasks:write. Повтор request_id с прежними аргументами возвращает исходный результат.",
    operation_id="createTaskCommentWithFiles",
    response_description="Сохранённый комментарий с метаданными его файлов.",
    response_model=CommentSchema,
    responses={
        401: {"description": "Требуется вход."},
        403: {"description": "Недостаточно scopes."},
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        413: {"description": "Оригинал превышает 10 МиБ."},
        415: {"description": "Неподдерживаемый формат оригинала."},
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def create_task_comment_with_files(
    task_id: Annotated[
        int, Path(gt=0, description="Текущая задача комментария и оригиналов.", examples=[12])
    ],
    request_id: Annotated[
        UUID, Form(description="Стабильный UUID отправки, сохраняемый при повторе.")
    ],
    service: FileCommentsServiceDep,
    body_md: Annotated[
        str,
        Form(
            max_length=50_000, description="Markdown; допускается пустой текст при наличии файлов."
        ),
    ] = "",
    files: Annotated[
        list[UploadFile] | None, File(description="До десяти новых файлов, каждый до 10 МиБ.")
    ] = None,
    captions: Annotated[
        str | None, Form(description="JSON-массив подписей string/null в порядке новых files.")
    ] = None,
    document_refs: Annotated[
        str | None, Form(description="JSON-массив стабильных ссылок на документы той же задачи.")
    ] = None,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> CommentSchema:
    """Принимает ограниченный multipart и вызывает единый доменный сценарий.

    Args:
        task_id: Задача комментария и файлов.
        request_id: UUID одной отправки клиента.
        service: Составной сервис с собственной короткой auth/access областью.
        body_md: Текст комментария.
        files: Новые оригиналы, поле multipart files повторяется для каждого файла.
        captions: Подписи новых оригиналов в JSON.
        document_refs: Ссылки на существующие документы задачи в JSON.
        session_cookie: Cookie сессии.
        authorization: Bearer-токен с приоритетом перед cookie.
    Returns:
        Первоначальный результат идемпотентной отправки.
    Raises:
        HTTPException: Файлы/подписи/текст невалидны, права отозваны или запись не удалась.
    """
    logger.info("🚀 Отправка комментария с файлами задачи %s.", task_id)
    uploads = files or []
    try:
        refs = _json_list(document_refs, field="document_refs")
        labels = _json_list(captions, field="captions")
        if len(uploads) + len(refs) > service.max_files:
            raise HTTPException(422, "К комментарию можно добавить не более десяти документов.")
        if captions is not None and (
            len(labels) != len(uploads)
            or any(item is not None and not isinstance(item, str) for item in labels)
        ):
            raise HTTPException(422, "Подписи должны соответствовать новым файлам.")
        if any(not isinstance(item, str) for item in refs):
            raise HTTPException(422, "Ссылки на документы должны быть строками.")
        read = await read_uploads(uploads, max_size=service.max_file_size)
        result = await service.submit(
            task_id=task_id,
            request_id=request_id,
            body_md=body_md,
            document_refs=refs,
            uploads=[
                CommentFileUpload(
                    item.name,
                    item.content,
                    uploads[index].content_type,
                    labels[index] if labels else None,
                )
                for index, item in enumerate(read)
            ],
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
        )
        logger.info(
            "✅ Комментарий %s задачи %s отправлен с %s файлами.",
            result.id,
            task_id,
            len(result.document_refs),
        )
        return result
    except (
        FileCommentsServiceError,
        DocumentCatalogServiceError,
        TaskAttachmentsServiceError,
        TasksServiceError,
        AuthServiceError,
        InsufficientTokenScopeError,
        AccessServiceError,
    ) as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    finally:
        await close_uploads(uploads)
