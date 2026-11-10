"""Арис: личный диалог виджета и решение по действиям, предложенным в чате проекта."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile, status

from src.agent.tools import AgentToolContext
from src.api.v1.responses import (
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
from src.dependencies.access import ProjectIdPath
from src.dependencies.auth import PrincipalDep, require_write_scope
from src.dependencies.chat import (
    ChatPrincipalDep,
    require_chat_access,
    require_chat_write_scope,
)
from src.dependencies.services import (
    AgentActionsServiceDep,
    AgentConversationsServiceDep,
    AgentFilesServiceDep,
)
from src.exceptions.agent_conversations import AgentConversationsServiceError
from src.exceptions.agent_files import AgentFilesServiceError
from src.exceptions.agent_tools import AgentToolsServiceError
from src.schemas.agent_conversations import (
    AgentConversationListSchema,
    AgentConversationSchema,
    AgentMessageAcceptedSchema,
    AgentMessageCreateSchema,
    AgentMessagePageSchema,
    AgentMessageSchema,
)
from src.schemas.agent_files import AgentFileSchema
from src.schemas.agent_tools import AgentToolDecisionSchema, AgentToolRunSchema

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/aris/conversations", tags=["aris"])
chat_router = APIRouter(
    prefix="/projects/{project_id}/chat/aris",
    tags=["project-chat"],
    dependencies=[Depends(require_chat_access)],
)


@router.post(
    "/{conversation_id}/actions/{action_id}/decision",
    dependencies=[Depends(require_write_scope)],
    response_model=AgentToolRunSchema,
    summary="Решение по действию Ариса в виджете",
    description="Выполняет либо отклоняет действие, которое Арис предложил в личном диалоге. Параметры нельзя подменить при подтверждении; после решения Арис продолжает ответ (статус реплики снова queued).",
    response_description="Сохранённый результат решения.",
    operation_id="decideArisAction",
    responses={
        401: {"description": "Требуется вход."},
        403: {"description": "Токен разрешает только чтение."},
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def decide_agent_action(
    conversation_id: Annotated[int, Path(gt=0, description="ID диалога виджета.")],
    action_id: Annotated[UUID, Path(description="ID действия из ответа Ариса.")],
    data: AgentToolDecisionSchema,
    principal: PrincipalDep,
    service: AgentActionsServiceDep,
) -> AgentToolRunSchema:
    """Применяет решение пользователя к неизменяемому набору параметров.

    Args:
        conversation_id: Диалог виджета.
        action_id: Сохранённое действие.
        data: Решение участника.
        principal: Аутентифицированный автор решения.
        service: Общий сценарий инструментов.
    Returns:
        Состояние и результат действия.
    """
    logger.info("🚀 Решение по действию id=%s, участник=%s.", action_id, principal.user_id)
    try:
        result = await service.decide(
            # Проект действия берётся из самого действия, а не из маршрута.
            context=AgentToolContext(project_id=None, user_id=principal.user_id, can_write=True),
            conversation_id=conversation_id,
            action_id=action_id,
            decision=data.decision,
        )
        logger.info("✅ Решение по действию id=%s сохранено.", action_id)
        return result
    except AgentToolsServiceError as error:
        logger.exception("❌ Не удалось принять решение по действию id=%s.", action_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


ConversationIdPath = Annotated[int, Path(gt=0, description="ID диалога виджета Ариса.")]
ERRORS = {
    401: {
        "description": "Требуется вход.",
        "content": {"application/json": {"example": {"detail": "Необходимо войти."}}},
    },
    404: NOT_FOUND_RESPONSE,
    422: VALIDATION_RESPONSE,
    500: SERVER_ERROR_RESPONSE,
}
WRITE_ERRORS = {
    **ERRORS,
    403: {
        "description": "Токен разрешает только чтение.",
        "content": {"application/json": {"example": {"detail": "Недостаточно прав токена."}}},
    },
    409: CONFLICT_RESPONSE,
}


@router.post(
    "/{conversation_id}/files",
    dependencies=[Depends(require_write_scope)],
    response_model=AgentFileSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Загрузить файл для сообщения Арису",
    description="Принимает до 10 МБ в диалог виджета; до 20 загрузок на диалог. Файл ещё не прикреплён к задаче и не индексируется в общие знания проекта; прикрепить его Арис может в проекте открытой страницы.",
    operation_id="uploadArisFile",
    response_description="Приватная ссылка для последующей отправки сообщения.",
    responses=WRITE_ERRORS,
)
async def upload_agent_file(
    conversation_id: ConversationIdPath,
    principal: PrincipalDep,
    service: AgentFilesServiceDep,
    file: Annotated[
        UploadFile, File(description="Файл в одном из разрешённых форматов вложений задач.")
    ],
) -> AgentFileSchema:
    """Сохраняет выбранный участником файл в его личном диалоге.

    Args:
        conversation_id: Диалог участника.
        principal: Автор загрузки.
        service: Сценарий загрузок.
        file: Multipart-файл.
    Returns:
        Метаданные без внутреннего пути.
    """
    logger.info("🚀 Загрузка файла в диалог id=%s.", conversation_id)
    try:
        result = await service.upload(
            user_id=principal.user_id,
            conversation_id=conversation_id,
            file_name=file.filename or "",
            content_type=file.content_type,
            content=await file.read(service.max_file_size + 1),
        )
        logger.info("✅ Файл диалога сохранён id=%s.", result.id)
        return result
    except AgentFilesServiceError as error:
        logger.exception("❌ Не удалось загрузить файл диалога id=%s.", conversation_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    finally:
        await file.close()


@router.delete(
    "/{conversation_id}/files/{file_id}",
    dependencies=[Depends(require_write_scope)],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить неотправленный файл",
    description="Удаляет загрузку из черновика. Файлы отправленных сообщений сохраняются вместе с историей.",
    operation_id="deleteArisFileDraft",
    response_description="Черновик файла удалён.",
    responses=WRITE_ERRORS,
)
async def delete_agent_file_draft(
    conversation_id: ConversationIdPath,
    file_id: Annotated[UUID, Path(description="ID неотправленного файла.")],
    principal: PrincipalDep,
    service: AgentFilesServiceDep,
) -> None:
    """Удаляет загрузку до отправки сообщения.

    Args:
        conversation_id: Диалог участника.
        file_id: Загрузка из черновика.
        principal: Автор загрузки.
        service: Сценарий загрузок.
    """
    logger.info("🚀 Удаление загрузки id=%s.", file_id)
    try:
        await service.delete_draft(
            user_id=principal.user_id,
            conversation_id=conversation_id,
            file_id=file_id,
        )
        logger.info("✅ Загрузка id=%s удалена.", file_id)
    except AgentFilesServiceError as error:
        logger.exception("❌ Не удалось удалить загрузку id=%s.", file_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "",
    response_model=AgentConversationListSchema,
    summary="Мои диалоги с Арисом",
    description="Диалоги виджета текущего пользователя от недавно изменённых к старым. Первый в списке — текущий диалог; пустой список — диалога ещё нет, создайте его.",
    response_description="Страница диалогов от недавних к ранним.",
    operation_id="listArisConversations",
    responses=ERRORS,
)
async def list_agent_conversations(
    principal: PrincipalDep,
    service: AgentConversationsServiceDep,
    offset: Annotated[int, Query(ge=0, description="Смещение страницы.")] = 0,
    limit: Annotated[int, Query(ge=1, le=50, description="Число диалогов на странице.")] = 30,
) -> AgentConversationListSchema:
    """Возвращает диалоги пользователя с Арисом.

    Args:
        principal: Аутентифицированный участник.
        service: Сценарии диалога.
        offset: Смещение страницы.
        limit: Размер страницы.
    Returns:
        Диалоги и продолжение списка.
    """
    logger.info("🚀 Список диалогов Ариса, пользователь=%s.", principal.user_id)
    try:
        result = await service.list_conversations(
            user_id=principal.user_id, offset=offset, limit=limit
        )
        logger.info("✅ Список диалогов Ариса получен.")
        return result
    except AgentConversationsServiceError as error:
        logger.exception("❌ Не удалось прочитать диалоги Ариса.")
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "",
    dependencies=[Depends(require_write_scope)],
    status_code=status.HTTP_201_CREATED,
    response_model=AgentConversationSchema,
    summary="Начать новый диалог с Арисом",
    description="Создаёт новый диалог виджета; он становится текущим. Прежние диалоги остаются в списке.",
    response_description="Сохранённый пустой диалог.",
    operation_id="createArisConversation",
    responses=WRITE_ERRORS,
)
async def create_agent_conversation(
    principal: PrincipalDep, service: AgentConversationsServiceDep
) -> AgentConversationSchema:
    """Начинает отдельный разговор с пустой историей.

    Args:
        principal: Аутентифицированный участник.
        service: Сценарии диалога.
    Returns:
        Метаданные созданного разговора.
    """
    logger.info("🚀 Новый диалог Ариса, пользователь=%s.", principal.user_id)
    try:
        result = await service.create_conversation(user_id=principal.user_id)
        logger.info("✅ Создан диалог id=%s.", result.id)
        return result
    except AgentConversationsServiceError as error:
        logger.exception("❌ Не удалось создать диалог Ариса.")
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "/{conversation_id}/messages",
    response_model=AgentMessagePageSchema,
    summary="Прочитать переписку",
    description="Возвращает реплики, источники и состояние ожидаемого ответа. История доступна только владельцу диалога.",
    response_description="Страница реплик в хронологическом порядке.",
    operation_id="getArisMessages",
    responses=ERRORS,
)
async def get_agent_messages(
    conversation_id: ConversationIdPath,
    principal: PrincipalDep,
    service: AgentConversationsServiceDep,
    before_id: Annotated[
        int | None, Query(gt=0, description="Читать реплики раньше этого ID.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Размер страницы переписки.")] = 50,
) -> AgentMessagePageSchema:
    """Восстанавливает диалог после открытия страницы.

    Args:
        conversation_id: Диалог участника.
        principal: Аутентифицированный участник.
        service: Сценарии диалога.
        before_id: Курсор предыдущей страницы.
        limit: Размер страницы.
    Returns:
        Реплики и курсор продолжения.
    """
    logger.info("🚀 Чтение диалога id=%s.", conversation_id)
    try:
        result = await service.get_messages(
            user_id=principal.user_id,
            conversation_id=conversation_id,
            before_id=before_id,
            limit=limit,
        )
        logger.info("✅ Диалог id=%s прочитан.", conversation_id)
        return result
    except AgentConversationsServiceError as error:
        logger.exception("❌ Не удалось прочитать диалог id=%s.", conversation_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/{conversation_id}/messages",
    dependencies=[Depends(require_write_scope)],
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AgentMessageAcceptedSchema,
    summary="Написать Арису",
    description="Сохраняет вопрос с контекстом страницы (page_context) и ставит ответ Ариса в постоянную очередь. Повтор request_id с тем же текстом возвращает прежние сообщения. Клиент не передаёт историю или автора; готовый ответ — при чтении переписки.",
    response_description="Вопрос и ожидающий ответ; результат доступен при чтении переписки.",
    operation_id="sendArisMessage",
    responses=WRITE_ERRORS,
)
async def send_agent_message(
    conversation_id: ConversationIdPath,
    data: AgentMessageCreateSchema,
    principal: PrincipalDep,
    service: AgentConversationsServiceDep,
) -> AgentMessageAcceptedSchema:
    """Сохраняет вопрос до обращения к модели.

    Args:
        conversation_id: Диалог участника.
        data: Текст и ключ отправки.
        principal: Аутентифицированный участник.
        service: Сценарии диалога.
    Returns:
        Постоянное состояние отправленного вопроса.
    """
    logger.info("🚀 Вопрос агенту в диалоге id=%s.", conversation_id)
    try:
        result = await service.send_message(
            user_id=principal.user_id,
            conversation_id=conversation_id,
            data=data,
        )
        logger.info(
            "✅ Вопрос диалога id=%s сохранён, ответ id=%s.",
            conversation_id,
            result.assistant_message.id,
        )
        return result
    except AgentConversationsServiceError as error:
        logger.exception("❌ Не удалось сохранить вопрос диалога id=%s.", conversation_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/{conversation_id}/messages/{message_id}/retry",
    dependencies=[Depends(require_write_scope)],
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AgentMessageSchema,
    summary="Повторить подготовку ответа",
    description="Повторяет последний неудачный ответ без создания нового вопроса. Начатый повтор возвращается идемпотентно.",
    response_description="Состояние ответа после повторной постановки в очередь.",
    operation_id="retryArisMessage",
    responses=WRITE_ERRORS,
)
async def retry_agent_message(
    conversation_id: ConversationIdPath,
    message_id: Annotated[int, Path(gt=0, description="ID последнего неудачного ответа.")],
    principal: PrincipalDep,
    service: AgentConversationsServiceDep,
) -> AgentMessageSchema:
    """Повторяет подготовку последнего ответа.

    Args:
        conversation_id: Диалог участника.
        message_id: Неудачный ответ.
        principal: Аутентифицированный участник.
        service: Сценарии диалога.
    Returns:
        Ожидающая реплика.
    """
    logger.info("🚀 Повтор ответа id=%s в диалоге id=%s.", message_id, conversation_id)
    try:
        result = await service.retry_message(
            user_id=principal.user_id,
            conversation_id=conversation_id,
            message_id=message_id,
        )
        logger.info("✅ Повтор ответа id=%s принят.", message_id)
        return result
    except AgentConversationsServiceError as error:
        logger.exception("❌ Не удалось повторить ответ id=%s.", message_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@chat_router.post(
    "/actions/{action_id}/decision",
    dependencies=[Depends(require_chat_write_scope)],
    response_model=AgentToolRunSchema,
    summary="Решение по действию Ариса в чате проекта",
    description="Подтверждает или отклоняет действие, которое Арис предложил в общем чате. Решить может автор просьбы или участник с правами на само действие; действие выполняется от имени решающего с его правами, параметры неизменны. Карточка обновится у всей команды событием message.updated.",
    response_description="Сохранённый результат решения.",
    operation_id="decideProjectChatArisAction",
    responses={
        401: {"description": "Требуется вход."},
        403: {"description": "Токен разрешает только чтение."},
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def decide_project_chat_aris_action(
    project_id: ProjectIdPath,
    action_id: Annotated[UUID, Path(description="ID действия из карточки сообщения Ариса.")],
    data: AgentToolDecisionSchema,
    principal: ChatPrincipalDep,
    service: AgentActionsServiceDep,
) -> AgentToolRunSchema:
    """Применяет решение участника к действию из общего чата.

    Args:
        project_id: Проект чата.
        action_id: Сохранённое действие.
        data: Решение участника.
        principal: Решающий участник.
        service: Общий сценарий инструментов.
    Returns:
        Состояние и результат действия.
    """
    logger.info(
        "🚀 Решение по действию Ариса в чате id=%s, участник=%s.", action_id, principal.user_id
    )
    try:
        result = await service.decide_in_chat(
            context=AgentToolContext(
                project_id=project_id, user_id=principal.user_id, can_write=True
            ),
            action_id=action_id,
            decision=data.decision,
        )
        logger.info("✅ Решение по действию Ариса в чате id=%s сохранено.", action_id)
        return result
    except AgentToolsServiceError as error:
        logger.exception("❌ Не удалось принять решение по действию id=%s.", action_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
