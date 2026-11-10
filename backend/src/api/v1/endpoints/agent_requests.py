"""Вопрос Арису от внешнего агента — REST-двойник MCP ``telos_ask_aris``.

Внешний клиент передаёт сюда запрос о задачах пользователя:
Арис сам находит проект, проверяет права пользователя и выполняет запрос. Вопрос
встаёт в ту же постоянную очередь, что и чат проекта; вызов ждёт готового
ответа до ``AGENT_ASK_WAIT_SECONDS``, иначе отдаёт ``request_id`` для
``GET /aris/requests/{request_id}``. Результат видит только тот ключ и тот
пользователь, что задали вопрос.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path

from src.api.v1.responses import (
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
from src.dependencies.auth import PrincipalDep
from src.dependencies.services import AgentRequestsServiceDep
from src.dependencies.settings import SettingsDep
from src.exceptions.agent_conversations import AgentConversationsServiceError
from src.exceptions.auth import AuthServiceError
from src.schemas.agent_requests import AgentAskSchema, AgentRequestResultSchema
from src.services.agent_requests import principal_key_id, principal_key_scopes
from src.utils.api_scopes import SCOPE_AGENT_ASK

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/aris/requests", tags=["aris"])

RequestIdPath = Annotated[UUID, Path(description="ID вопроса из ответа POST /aris/requests.")]
ERRORS = {
    401: {"description": "Требуется ключ, токен или вход."},
    403: {"description": "У ключа нет права agent:ask."},
    404: NOT_FOUND_RESPONSE,
    500: SERVER_ERROR_RESPONSE,
}


@router.post(
    "",
    response_model=AgentRequestResultSchema,
    summary="Спросить Ариса — проектного менеджера",
    description="Вопрос Арису с правами локального пользователя. Изменения разрешены только дословным user_text; request_id обеспечивает безопасный повтор отправки.",
    response_description="Ответ либо состояние вопроса в очереди.",
    operation_id="createArisRequest",
    responses={**ERRORS, 409: CONFLICT_RESPONSE, 422: VALIDATION_RESPONSE},
)
async def create_aris_request(
    data: AgentAskSchema,
    principal: PrincipalDep,
    service: AgentRequestsServiceDep,
    settings: SettingsDep,
) -> AgentRequestResultSchema:
    """Ставит вопрос в очередь агента и ждёт ответа ограниченное время.

    Args:
        data: Пересказ, дословная реплика пользователя и ключ беседы.
        principal: Локальный пользователь — владелец ключа.
        service: Вопросы внешнего агента.
        settings: Срок ожидания ответа.
    Returns:
        Готовый ответ или состояние вопроса.
    """
    logger.info("🚀 Запрос POST /aris/requests, беседа=%s.", data.conversation_id)
    try:
        principal.require(SCOPE_AGENT_ASK)
        request_id = await service.ask_external(
            user_id=principal.user_id,
            api_key_id=principal_key_id(principal),
            data=data,
            key_scopes=principal_key_scopes(principal),
        )
        result = await service.wait_external_result(
            user_id=principal.user_id,
            api_key_id=principal_key_id(principal),
            request_id=request_id,
            timeout_seconds=settings.agent.agent_ask_wait_seconds,
        )
        logger.info("✅ POST /aris/requests: request_id=%s, status=%s.", request_id, result.status)
        return result
    except (AgentConversationsServiceError, AuthServiceError) as error:
        logger.warning("⚠️ POST /aris/requests отклонён: %s.", error.detail)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "/{request_id}",
    response_model=AgentRequestResultSchema,
    summary="Результат вопроса Арису",
    description=(
        "Состояние и результат вопроса: полный текст ответа, ссылки на задачи, выполненные "
        "действия (done) и действия, которые ждут решения пользователя по ссылке confirm_url "
        "(pending). Чужой вопрос — другого ключа или пользователя — отвечает 404."
    ),
    response_description="Состояние и результат вопроса.",
    operation_id="getArisRequest",
    responses=ERRORS,
)
async def get_aris_request(
    request_id: RequestIdPath,
    principal: PrincipalDep,
    service: AgentRequestsServiceDep,
) -> AgentRequestResultSchema:
    """Отдаёт результат вопроса тому, кто его задал.

    Args:
        request_id: ID вопроса.
        principal: Пользователь и ключ запроса.
        service: Вопросы внешнего агента.
    Returns:
        Состояние и результат.
    """
    try:
        principal.require(SCOPE_AGENT_ASK)
        return await service.get_external_result(
            user_id=principal.user_id, api_key_id=principal_key_id(principal), request_id=request_id
        )
    except (AgentConversationsServiceError, AuthServiceError) as error:
        logger.info("ℹ️ GET /aris/requests/%s: %s.", request_id, error.detail)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
