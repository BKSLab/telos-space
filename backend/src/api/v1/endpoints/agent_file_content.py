"""Защищённые оригиналы и миниатюры приватных файлов Ариса."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import FileResponse

from src.dependencies.agent_file_content import AgentFileContentServiceDep
from src.dependencies.auth import AuthorizationHeaderDep, SessionCookieDep
from src.exceptions.access import AccessServiceError
from src.exceptions.agent_files import AgentFilesServiceError
from src.exceptions.auth import AuthServiceError
from src.utils.api_tokens import extract_bearer_secret

router = APIRouter(tags=["agent-files"])
ConversationId = Annotated[int, Path(gt=0)]
ProjectId = Annotated[int, Path(gt=0)]


async def _response(
    service,
    conversation_id,
    file_id,
    session_cookie,
    authorization,
    *,
    project_id=None,
    thumbnail=False,
) -> FileResponse:
    """Транспорт получает готовый путь после закрытия короткой DB-фазы."""
    try:
        download = await service.prepare(
            conversation_id=conversation_id,
            file_id=file_id,
            project_id=project_id,
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
            thumbnail=thumbnail,
        )
    except (AuthServiceError, AccessServiceError, AgentFilesServiceError) as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    return FileResponse(
        download.path,
        filename=download.filename,
        media_type=download.media_type,
        content_disposition_type=download.disposition,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.get(
    "/tim/conversations/{conversation_id}/files/{file_id}/content",
    response_class=FileResponse,
    summary="Скачать приватный файл диалога",
    description="Доступен только владельцу диалога с актуальными правами; оригинал передаётся без DB-сессии.",
    operation_id="downloadAgentFile",
)
async def agent_file_content(
    conversation_id: ConversationId,
    file_id: UUID,
    service: AgentFileContentServiceDep,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> FileResponse:
    """Выдаёт оригинал собственного личного диалога."""
    return await _response(service, conversation_id, file_id, session_cookie, authorization)


@router.get(
    "/tim/conversations/{conversation_id}/files/{file_id}/preview",
    response_class=FileResponse,
    summary="Предпросмотр приватного изображения",
    description="Возвращает локальную миниатюру только владельцу диалога; vision не требуется.",
    operation_id="previewAgentFile",
)
async def agent_file_preview(
    conversation_id: ConversationId,
    file_id: UUID,
    service: AgentFileContentServiceDep,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> FileResponse:
    """Выдаёт миниатюру собственного личного диалога."""
    return await _response(
        service, conversation_id, file_id, session_cookie, authorization, thumbnail=True
    )


@router.get(
    "/projects/{project_id}/agent/conversations/{conversation_id}/files/{file_id}/content",
    response_class=FileResponse,
    summary="Скачать приватный файл проектного диалога",
    description="Проверяет владельца диалога и текущий доступ в проекте.",
    operation_id="downloadProjectAgentFile",
)
async def project_agent_file_content(
    project_id: ProjectId,
    conversation_id: ConversationId,
    file_id: UUID,
    service: AgentFileContentServiceDep,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> FileResponse:
    """Проектный контекст не открывает приватный файл другим участникам."""
    return await _response(
        service, conversation_id, file_id, session_cookie, authorization, project_id=project_id
    )


@router.get(
    "/projects/{project_id}/agent/conversations/{conversation_id}/files/{file_id}/preview",
    response_class=FileResponse,
    summary="Предпросмотр приватного изображения проектного диалога",
    description="Проверяет владельца диалога и текущий доступ; локальная миниатюра без vision.",
    operation_id="previewProjectAgentFile",
)
async def project_agent_file_preview(
    project_id: ProjectId,
    conversation_id: ConversationId,
    file_id: UUID,
    service: AgentFileContentServiceDep,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> FileResponse:
    """Не удерживает соединение во время создания миниатюры и выдачи."""
    return await _response(
        service,
        conversation_id,
        file_id,
        session_cookie,
        authorization,
        project_id=project_id,
        thumbnail=True,
    )
