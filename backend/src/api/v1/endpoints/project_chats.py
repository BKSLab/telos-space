"""HTTP-адаптер истории, поиска и команд общего чата."""

from contextlib import contextmanager
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, File, HTTPException, Path, Query, UploadFile
from fastapi.responses import FileResponse

from src.api.v1.responses import VALIDATION_RESPONSE
from src.api.v1.uploads import read_upload
from src.dependencies.access import ProjectIdPath
from src.dependencies.auth import (
    AuthorizationHeaderDep,
    SessionCookieDep,
)
from src.dependencies.chat import (
    ChatCommandsServiceDep,
    ChatFilesServiceDep,
    ChatServiceDep,
    require_chat_access,
    require_chat_write_scope,
)
from src.dependencies.chat import ChatPrincipalDep as PrincipalDep
from src.exceptions.auth import AuthServiceError
from src.exceptions.project_chats import ChatServiceError
from src.schemas.project_chats import (
    ChatAttachmentView,
    ChatCommand,
    ChatEntityRef,
    ChatEntityView,
    ChatEventPage,
    ChatMessageCreate,
    ChatMessageDelete,
    ChatMessageEdit,
    ChatMessagePage,
    ChatMessageView,
    ChatReactionChange,
    ChatReadUpdate,
    ProjectChatView,
)
from src.services.chat_attachments import MAX_FILE_SIZE
from src.utils.api_tokens import extract_bearer_secret

ERRORS = {
    401: {"description": "Требуется вход."},
    403: {"description": "Недостаточно прав."},
    404: {"description": "Чат или объект недоступен."},
    409: {"description": "Версия сообщения изменилась."},
    422: VALIDATION_RESPONSE,
    429: {"description": "Слишком много действий."},
    503: {"description": "Realtime временно недоступен."},
}
READ_DOCS = dict(responses=ERRORS, response_description="Данные общего чата проекта.")
WRITE_DOCS = dict(**READ_DOCS, dependencies=[Depends(require_chat_write_scope)])
router = APIRouter(
    prefix="/projects/{project_id}/chat",
    tags=["project-chat"],
    dependencies=[Depends(require_chat_access)],
)
download_router = APIRouter(prefix="/projects/{project_id}/chat", tags=["project-chat"])
MessageId = Annotated[int, Path(gt=0, description="ID сообщения текущего проекта.")]
Limit = Annotated[int, Query(ge=1, le=100, description="Максимум сообщений страницы.")]
Cursor = Annotated[int | None, Query(ge=0, description="Серверный seq сообщения.")]


@contextmanager
def chat_errors():
    """Переводит доменные ошибки в транспортный ответ без внутренних деталей."""
    try:
        yield
    except (ChatServiceError, AuthServiceError) as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "",
    response_model=ProjectChatView,
    summary="Получить общий чат проекта",
    description="Возвращает состав команды, непрочитанное и курсор событий. Для старых проектов чат автоматически не создаётся.",
    operation_id="getProjectChat",
    **READ_DOCS,
)
async def get_chat(project_id: ProjectIdPath, principal: PrincipalDep, service: ChatServiceDep):
    """Читает состояние общего чата для текущего пользователя."""
    with chat_errors():
        return await service.get_chat(project_id, principal.user_id)


@router.get(
    "/messages",
    response_model=ChatMessagePage,
    summary="Получить историю чата",
    description="Keyset-пагинация before/after; around возвращает окружение указанного сообщения.",
    operation_id="getProjectChatMessages",
    **READ_DOCS,
)
async def get_messages(
    project_id: ProjectIdPath,
    principal: PrincipalDep,
    service: ChatServiceDep,
    limit: Limit = 50,
    before: Cursor = None,
    after: Cursor = None,
    around: Annotated[int | None, Query(gt=0)] = None,
):
    """Возвращает ограниченную страницу постоянной истории."""
    with chat_errors():
        return await service.history(
            project_id, principal.user_id, before=before, after=after, around=around, limit=limit
        )


@router.get(
    "/search",
    response_model=ChatMessagePage,
    summary="Поиск по чату",
    description="Полнотекстовый поиск PostgreSQL внутри доступного проекта, включая русскую морфологию.",
    operation_id="searchProjectChat",
    **READ_DOCS,
)
async def search_messages(
    project_id: ProjectIdPath,
    principal: PrincipalDep,
    service: ChatServiceDep,
    q: Annotated[str, Query(min_length=2, max_length=200)],
    limit: Limit = 30,
    before: Cursor = None,
):
    """Ищет только неудалённые сообщения текущего проекта."""
    with chat_errors():
        return await service.history(
            project_id, principal.user_id, query=q, before=before, limit=limit
        )


@router.get(
    "/events",
    response_model=ChatEventPage,
    summary="Восстановить события чата",
    description="Дельта после курсора включает новые сообщения, правки, удаления, реакции, прочтение и изменения карточек.",
    operation_id="getProjectChatEvents",
    **READ_DOCS,
)
async def get_events(
    project_id: ProjectIdPath,
    principal: PrincipalDep,
    service: ChatServiceDep,
    after: Annotated[int, Query(ge=0)] = 0,
):
    """Выдаёт долговечную дельту без периодического опроса сообщений."""
    with chat_errors():
        return await service.events(project_id, principal.user_id, after)


@router.get(
    "/entities",
    response_model=list[ChatEntityView],
    summary="Поиск объектов для сообщения",
    description="Возвращает доступные задачи, документы, риски, вехи и WBS-узлы только текущего проекта.",
    operation_id="searchProjectChatEntities",
    **READ_DOCS,
)
async def get_entities(
    project_id: ProjectIdPath,
    principal: PrincipalDep,
    service: ChatServiceDep,
    q: Annotated[str, Query(max_length=200)] = "",
):
    """Наполняет picker структурированных ссылок."""
    with chat_errors():
        return await service.lookup_entities(project_id, principal.user_id, q)


@router.post(
    "/entities/resolve",
    response_model=list[ChatEntityView],
    summary="Обновить карточки объектов",
    description="Пакетно читает актуальные поля уже показанных ссылок, не меняя данных.",
    operation_id="resolveProjectChatEntities",
    **READ_DOCS,
)
async def resolve_entities(
    project_id: ProjectIdPath,
    principal: PrincipalDep,
    service: ChatServiceDep,
    data: Annotated[list[ChatEntityRef], Body(max_length=100)],
):
    """Собирает актуальные карточки по структурированным идентификаторам."""
    with chat_errors():
        return await service.hydrate_entities(project_id, principal.user_id, data)


async def execute_http(service, project_id, principal, kind, data, message_id=None):
    """Строит тот же конверт, который приходит по WS."""
    return await service.execute(
        project_id,
        principal,
        "http",
        ChatCommand(
            type=kind, request_id=uuid4(), message_id=message_id, data=data.model_dump(mode="json")
        ),
    )


@router.post(
    "/messages",
    response_model=ChatMessageView,
    status_code=201,
    summary="Отправить сообщение",
    description="Идемпотентная отправка по client_message_id через тот же ChatService, что WebSocket.",
    operation_id="sendProjectChatMessage",
    **WRITE_DOCS,
)
async def send_message(
    project_id: ProjectIdPath,
    principal: PrincipalDep,
    service: ChatCommandsServiceDep,
    data: ChatMessageCreate,
):
    """REST-путь отправки для интеграций и тестирования протокола."""
    with chat_errors():
        return (await execute_http(service, project_id, principal, "message.send", data))["message"]


@router.patch(
    "/messages/{message_id}",
    response_model=ChatMessageView,
    summary="Изменить своё сообщение",
    description="Проверяет владельца, текущую версию и все ссылки. Сохраняет событие изменения.",
    operation_id="editProjectChatMessage",
    **WRITE_DOCS,
)
async def edit_message(
    project_id: ProjectIdPath,
    message_id: MessageId,
    principal: PrincipalDep,
    service: ChatCommandsServiceDep,
    data: ChatMessageEdit,
):
    """Применяет правку к указанной версии сообщения."""
    with chat_errors():
        return (
            await execute_http(service, project_id, principal, "message.edit", data, message_id)
        )["message"]


@router.delete(
    "/messages/{message_id}",
    response_model=ChatMessageView,
    summary="Удалить своё сообщение",
    description="Мягкое удаление сохраняет ответы, но скрывает содержимое, файлы и карточки.",
    operation_id="deleteProjectChatMessage",
    **WRITE_DOCS,
)
async def delete_message(
    project_id: ProjectIdPath,
    message_id: MessageId,
    principal: PrincipalDep,
    service: ChatCommandsServiceDep,
    data: ChatMessageDelete,
):
    """Оставляет tombstone на месте удалённого сообщения."""
    with chat_errors():
        return (
            await execute_http(service, project_id, principal, "message.delete", data, message_id)
        )["message"]


@router.put(
    "/messages/{message_id}/reaction",
    response_model=ChatMessageView,
    summary="Установить или снять реакцию",
    description="Явное active=true/false делает повтор команды идемпотентным.",
    operation_id="setProjectChatReaction",
    **WRITE_DOCS,
)
async def set_reaction(
    project_id: ProjectIdPath,
    message_id: MessageId,
    principal: PrincipalDep,
    service: ChatCommandsServiceDep,
    data: ChatReactionChange,
):
    """Меняет только реакцию текущего пользователя."""
    with chat_errors():
        return (
            await execute_http(service, project_id, principal, "reaction.set", data, message_id)
        )["message"]


@router.put(
    "/read",
    response_model=dict,
    summary="Отметить прочитанное",
    description="Продвигает границу только вперёд до фактически показанного сообщения.",
    operation_id="markProjectChatRead",
    **WRITE_DOCS,
)
async def mark_read(
    project_id: ProjectIdPath,
    principal: PrincipalDep,
    service: ChatCommandsServiceDep,
    data: ChatReadUpdate,
):
    """Сохраняет личную границу для всех вкладок участника."""
    with chat_errors():
        return await execute_http(service, project_id, principal, "read.set", data)


@router.post(
    "/attachments",
    response_model=ChatAttachmentView,
    status_code=201,
    summary="Загрузить вложение чата",
    description="Создаёт защищённый черновик до отправки сообщения; размер до 10 МБ.",
    operation_id="uploadProjectChatAttachment",
    **WRITE_DOCS,
)
async def upload_attachment(
    project_id: ProjectIdPath,
    principal: PrincipalDep,
    service: ChatFilesServiceDep,
    file: Annotated[UploadFile, File()],
):
    """Ограничивает чтение multipart и гарантированно закрывает upload."""
    try:
        upload = await read_upload(file, max_size=MAX_FILE_SIZE)
        with chat_errors():
            return await service.upload(project_id, principal.user_id, upload.name, upload.content)
    finally:
        await file.close()


@router.delete(
    "/attachments/{file_id}",
    status_code=204,
    summary="Удалить неотправленное вложение",
    description="Удаляет только собственный черновик; отправленные вложения скрываются вместе с сообщением.",
    operation_id="deleteProjectChatAttachment",
    **WRITE_DOCS,
)
async def delete_attachment(
    project_id: ProjectIdPath, file_id: UUID, principal: PrincipalDep, service: ChatFilesServiceDep
):
    """Удаляет черновик файла под блокировкой отправки сообщения."""
    with chat_errors():
        await service.delete_draft(project_id, principal.user_id, file_id)


@download_router.get(
    "/attachments/{file_id}/preview",
    response_class=FileResponse,
    summary="Предпросмотр изображения чата",
    description="Возвращает локальную миниатюру после проверки видимости файла; vision не требуется.",
    operation_id="previewProjectChatAttachment",
    **READ_DOCS,
)
async def attachment_preview(
    project_id: ProjectIdPath,
    file_id: UUID,
    service: ChatFilesServiceDep,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
):
    """Миниатюра защищена теми же правами, что и оригинал."""
    with chat_errors():
        result = await service.content(
            project_id,
            file_id,
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
            thumbnail=True,
        )
    return FileResponse(
        result.path,
        filename=result.original_name,
        media_type=result.content_type,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@download_router.get(
    "/attachments/{file_id}/content",
    response_class=FileResponse,
    summary="Скачать или просмотреть вложение",
    description="Проверяет сессию, membership и видимость сообщения в коротком DB-scope; поток файла не удерживает БД.",
    operation_id="downloadProjectChatAttachment",
    **READ_DOCS,
)
async def attachment_content(
    project_id: ProjectIdPath,
    file_id: UUID,
    service: ChatFilesServiceDep,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
    inline: bool = False,
):
    """Отдаёт приватный файл с запретом кеширования и MIME sniffing."""
    with chat_errors():
        result = await service.content(
            project_id,
            file_id,
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
        )
    return FileResponse(
        result.path,
        filename=result.original_name,
        media_type=result.content_type,
        content_disposition_type="inline" if inline and result.previewable else "attachment",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )
