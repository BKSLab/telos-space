"""Подтверждение действий агента по ссылке ``?confirm=<id>`` (#40793).

Ссылку получает пользователь, вызвавший Ариса через внешнего клиента. Решает только сам инициатор и только
из интерфейса (вход через локальную сессию): токенам и сервисным ключам эти методы закрыты,
иначе вызвавший агент подтвердил бы себе опасное действие сам.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from src.api.v1.responses import CONFLICT_RESPONSE, NOT_FOUND_RESPONSE, SERVER_ERROR_RESPONSE
from src.dependencies.auth import SessionPrincipalDep
from src.dependencies.services import AgentActionsServiceDep
from src.dependencies.settings import SettingsDep
from src.exceptions.agent_tools import AgentToolsServiceError
from src.schemas.agent_tools import AgentPendingActionSchema

logger = logging.getLogger(__name__)

CROSS_SITE_DECISION = "Решение принимается только со страницы TELOS SPACE."


def require_same_origin(request: Request, settings: SettingsDep) -> None:
    """Отклоняет решение, отправленное со стороннего сайта от имени вошедшего человека.

    Вход держится на cookie локальной сессии, поэтому чужая страница могла бы отправить
    форму на этот адрес. ``Sec-Fetch-Site: same-origin`` ставит сам браузер, страница
    его не подделает — так проходит и dev-прокси Vite, где ``Origin`` фронта не
    совпадает с ``Host`` бэкенда. Иначе сверяем ``Origin`` (браузер ставит его у POST)
    с адресом TELOS SPACE и разрешёнными источниками CORS, как у WebSocket чата.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site == "same-origin":
        return
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    allowed = {*settings.app.cors_origins, f"http://{host}", f"https://{host}"}
    if origin is not None and origin not in allowed:
        raise HTTPException(status_code=403, detail=CROSS_SITE_DECISION)
    if fetch_site in {"cross-site", "same-site"} and origin is None:
        raise HTTPException(status_code=403, detail=CROSS_SITE_DECISION)


router = APIRouter(prefix="/aris/pending", tags=["aris"])

ActionIdPath = Annotated[UUID, Path(description="ID действия из ссылки ?confirm=.")]
ERRORS = {
    401: {"description": "Требуется вход."},
    403: {"description": "Метод доступен только из интерфейса, не по токену или ключу."},
    404: NOT_FOUND_RESPONSE,
    500: SERVER_ERROR_RESPONSE,
}
DECISION_ERRORS = {
    **ERRORS,
    409: CONFLICT_RESPONSE,
    410: {
        "description": "Срок подтверждения истёк.",
        "content": {
            "application/json": {
                "example": {
                    "detail": "Срок подтверждения истёк. Попросите агента предложить действие заново."
                }
            }
        },
    },
}


@router.get(
    "/{action_id}",
    response_model=AgentPendingActionSchema,
    summary="Действие агента, ожидающее подтверждения",
    description="Что именно предложил сделать агент, в каком проекте, состояние и срок подтверждения. Доступно только пользователю, от чьего имени шёл запрос; чужое действие — 404.",
    response_description="Предложенное действие.",
    operation_id="getPendingArisAction",
    responses=ERRORS,
)
async def get_pending_action(
    action_id: ActionIdPath, principal: SessionPrincipalDep, service: AgentActionsServiceDep
) -> AgentPendingActionSchema:
    """Показывает действие его инициатору.

    Args:
        action_id: ID действия.
        principal: Вошедший через локальную сессию пользователь.
        service: Сценарии действий агента.
    Returns:
        Предложенное действие.
    """
    try:
        return await service.get_pending(user_id=principal.user_id, action_id=action_id)
    except AgentToolsServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


async def _decide(action_id: UUID, decision: str, principal, service) -> AgentPendingActionSchema:
    logger.info(
        "🚀 Решение %s по ссылке, действие=%s, участник=%s.",
        decision,
        action_id,
        principal.user_id,
    )
    try:
        result = await service.decide_pending(
            user_id=principal.user_id, action_id=action_id, decision=decision
        )
    except AgentToolsServiceError as error:
        logger.warning("❌ Решение по действию %s не принято: %s", action_id, error.detail)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    logger.info("✅ Действие %s: %s.", action_id, result.status)
    return result


@router.post(
    "/{action_id}/confirm",
    dependencies=[Depends(require_same_origin)],
    response_model=AgentPendingActionSchema,
    summary="Подтвердить действие агента",
    description="Выполняет действие ровно с сохранёнными параметрами — тело запроса не принимается. Повторное решение — 409, истёкший срок — 410, чужое действие — 404.",
    response_description="Действие после выполнения.",
    operation_id="confirmPendingArisAction",
    responses=DECISION_ERRORS,
)
async def confirm_pending_action(
    action_id: ActionIdPath, principal: SessionPrincipalDep, service: AgentActionsServiceDep
) -> AgentPendingActionSchema:
    """Выполняет сохранённое действие по решению инициатора."""
    return await _decide(action_id, "approve", principal, service)


@router.post(
    "/{action_id}/reject",
    dependencies=[Depends(require_same_origin)],
    response_model=AgentPendingActionSchema,
    summary="Отклонить действие агента",
    description="Закрывает предложение без изменений в проекте. Отклонить можно и просроченное; повторное решение — 409, чужое действие — 404.",
    response_description="Действие после отклонения.",
    operation_id="rejectPendingArisAction",
    responses=DECISION_ERRORS,
)
async def reject_pending_action(
    action_id: ActionIdPath, principal: SessionPrincipalDep, service: AgentActionsServiceDep
) -> AgentPendingActionSchema:
    """Отклоняет сохранённое действие по решению инициатора."""
    return await _decide(action_id, "reject", principal, service)
