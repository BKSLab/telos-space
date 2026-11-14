"""Генерация чек-листа; CRUD выполняется существующим API задач."""

import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Path, UploadFile
from pydantic import ValidationError

from src.api.v1.responses import NOT_FOUND_RESPONSE, SERVER_ERROR_RESPONSE, VALIDATION_RESPONSE
from src.api.v1.uploads import close_uploads, read_uploads
from src.dependencies.auth import AuthorizationHeaderDep, SessionCookieDep
from src.dependencies.services import ChecklistSuggestionServiceDep
from src.exceptions.access import AccessServiceError
from src.exceptions.ai import AiProviderError
from src.exceptions.auth import AuthServiceError
from src.exceptions.projects import ProjectNotFoundError
from src.exceptions.tasks import TasksServiceError
from src.schemas.task_checklists import ChecklistSuggestionRequestSchema, ChecklistSuggestionSchema
from src.schemas.tasks import TaskRephraseFile
from src.utils.api_tokens import extract_bearer_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["tasks"])


@router.post(
    "/projects/{project_id}/tasks/checklist-suggestion",
    response_model=ChecklistSuggestionSchema,
    summary="Предложить чек-лист задачи",
    description="Возвращает 3–5 редактируемых пунктов по черновику, документам и файлам. Существующие документы и вложения задачи читаются автоматически. Задача и чек-лист не изменяются.",
    operation_id="suggestTaskChecklist",
    response_description="Предложение и ограничения прочитанных источников.",
    responses={
        401: {"description": "Требуется авторизация."},
        404: NOT_FOUND_RESPONSE,
        413: {"description": "Превышен размер файла."},
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
        502: {"description": "Модель вернула непригодный чек-лист."},
        503: {"description": "AI-провайдер недоступен."},
    },
)
async def suggest_task_checklist(
    project_id: Annotated[int, Path(gt=0, description="Проект задачи.", examples=[1])],
    payload: Annotated[
        str,
        Form(
            description="JSON с названием, описанием, task_id, document_refs и текущим чек-листом."
        ),
    ],
    service: ChecklistSuggestionServiceDep,
    files: Annotated[
        list[UploadFile] | None, File(max_length=10, description="До 10 новых файлов из формы.")
    ] = None,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> ChecklistSuggestionSchema:
    """Разбирает multipart и передаёт данные сценарию с короткой авторизацией.

    Args:
        project_id: Проект задачи.
        payload: Черновик задачи в JSON.
        service: Сервис предложений.
        files: Новые файлы контекста.
        session_cookie: Сессия пользователя.
        authorization: API-токен.

    Returns:
        Несохранённый вариант чек-листа.

    Raises:
        HTTPException: Ошибка доступа, данных, файла или модели.
    """
    uploads = files or []
    try:
        try:
            data = ChecklistSuggestionRequestSchema.model_validate_json(payload)
        except ValidationError as error:
            raise HTTPException(
                status_code=422, detail="Некорректные данные для генерации чек-листа."
            ) from error
        read_files = await read_uploads(uploads, max_size=service.max_file_size)
        return await service.suggest(
            project_id=project_id,
            data=data,
            files=[TaskRephraseFile(name=item.name, content=item.content) for item in read_files],
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
        )
    except (
        AuthServiceError,
        AccessServiceError,
        ProjectNotFoundError,
        TasksServiceError,
        AiProviderError,
    ) as error:
        logger.exception("❌ Не удалось предложить чек-лист в проекте id=%s.", project_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    finally:
        await close_uploads(uploads)
