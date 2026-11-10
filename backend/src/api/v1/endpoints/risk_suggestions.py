"""AI-предложения рисков с авторизацией внутри короткой DB-фазы."""

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path

from src.api.v1.responses import NOT_FOUND_RESPONSE, SERVER_ERROR_RESPONSE, VALIDATION_RESPONSE
from src.dependencies.auth import AuthorizationHeaderDep, SessionCookieDep
from src.dependencies.services import RiskSuggestionServiceDep
from src.exceptions.access import AccessServiceError
from src.exceptions.ai import AiProviderError
from src.exceptions.auth import AuthServiceError
from src.exceptions.project_risks import ProjectRiskServiceError
from src.exceptions.projects import ProjectNotFoundError
from src.schemas.risk_suggestions import RiskSuggestionsSchema
from src.utils.api_tokens import extract_bearer_secret

logger = logging.getLogger(__name__)
router = APIRouter(tags=["project-risks"])


@router.post(
    "/projects/{project_id}/risks/suggestions",
    response_model=RiskSuggestionsSchema,
    summary="Предложить риски по контексту проекта",
    description="Предлагает до пяти черновиков с основаниями. Не регистрирует риски; сохранение требует отдельного подтверждения в форме создания.",
    operation_id="suggestProjectRisks",
    response_description="Редактируемые предложения и основания.",
    responses={
        401: {
            "description": "Требуется авторизация.",
            "content": {"application/json": {"example": {"detail": "Требуется авторизация."}}},
        },
        404: NOT_FOUND_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
        503: {
            "description": "AI-провайдер недоступен.",
            "content": {
                "application/json": {"example": {"detail": "AI-сервис временно недоступен."}}
            },
        },
    },
)
async def suggest_project_risks(
    project_id: Annotated[int, Path(gt=0, description="Проект для анализа.", examples=[1])],
    service: RiskSuggestionServiceDep,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> RiskSuggestionsSchema:
    """Возвращает AI-черновики из доступного проекта.

    Args:
        project_id: Проект для анализа.
        service: Сценарий со встроенной авторизацией до внешнего вызова.
        session_cookie: Сессия пользователя.
        authorization: Заголовок API-токена.

    Returns:
        Предложения, которые ещё не сохранены в реестр.

    Raises:
        HTTPException: Ошибка доступа, источников или модели.
    """
    logger.info("🚀 Запрос предложений рисков проекта id=%s.", project_id)
    try:
        result = await service.suggest(
            project_id=project_id,
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
        )
        logger.info("✅ Получены предложения рисков проекта id=%s.", project_id)
        return result
    except (
        AuthServiceError,
        AccessServiceError,
        ProjectNotFoundError,
        ProjectRiskServiceError,
        AiProviderError,
    ) as error:
        logger.exception("❌ Ошибка предложения рисков проекта id=%s.", project_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
