"""AI-помощь с формулировками полей риска."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path

from src.api.v1.responses import NOT_FOUND_RESPONSE, SERVER_ERROR_RESPONSE, VALIDATION_RESPONSE
from src.dependencies.auth import AuthorizationHeaderDep, SessionCookieDep
from src.dependencies.services import RiskFormulationServiceDep
from src.exceptions.access import AccessServiceError
from src.exceptions.ai import AiProviderError
from src.exceptions.auth import AuthServiceError
from src.exceptions.project_risks import ProjectRiskServiceError
from src.exceptions.projects import ProjectNotFoundError
from src.schemas.risk_formulation import RiskFormulationRequest, RiskFormulationResponse
from src.utils.api_tokens import extract_bearer_secret

router = APIRouter(tags=["project-risks"])


@router.post(
    "/projects/{project_id}/risks/field-suggestion",
    response_model=RiskFormulationResponse,
    summary="Сформулировать поле риска",
    operation_id="suggestRiskField",
    description="Возвращает редактируемую формулировку описания, плана митигации или плана реагирования. Ничего не сохраняет.",
    responses={
        401: {"description": "Требуется авторизация."},
        404: NOT_FOUND_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
        503: {"description": "AI-провайдер недоступен."},
    },
)
async def suggest_risk_field(
    project_id: Annotated[int, Path(gt=0)],
    data: RiskFormulationRequest,
    service: RiskFormulationServiceDep,
    session_cookie: SessionCookieDep = None,
    authorization: AuthorizationHeaderDep = None,
) -> RiskFormulationResponse:
    """Формирует один текст по текущим полям формы риска."""
    try:
        return await service.suggest(
            project_id=project_id,
            data=data,
            session_token=session_cookie,
            bearer_secret=extract_bearer_secret(authorization),
        )
    except (
        AuthServiceError,
        AccessServiceError,
        ProjectNotFoundError,
        ProjectRiskServiceError,
        AiProviderError,
    ) as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
