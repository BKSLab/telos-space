"""AI-помощь с формулировками полей риска без записи в реестр."""

import json

from src.clients.llm import LlmClient
from src.exceptions.ai import AiProviderError
from src.exceptions.api_keys import ApiKeyRouteNotAllowedError
from src.exceptions.clients import ClientError
from src.exceptions.project_risks import ProjectRiskServiceError
from src.exceptions.projects import ProjectNotFoundError
from src.prompts.risk_formulation import (
    RISK_DESCRIPTION_PROMPT,
    RISK_MITIGATION_PROMPT,
    RISK_RESPONSE_PROMPT,
)
from src.schemas.risk_formulation import (
    RiskFormulationField,
    RiskFormulationRequest,
    RiskFormulationResponse,
)
from src.services.risk_suggestions import RiskSuggestionScopeFactory


class RiskFormulationService:
    """Формирует один редактируемый текст поля риска."""

    def __init__(self, *, scope: RiskSuggestionScopeFactory, llm_client: LlmClient) -> None:
        self.scope, self.llm_client = scope, llm_client

    async def suggest(
        self,
        *,
        project_id: int,
        data: RiskFormulationRequest,
        session_token: str | None,
        bearer_secret: str | None,
    ) -> RiskFormulationResponse:
        async with self.scope() as db:
            principal = await db.auth.resolve_principal(
                session_token=session_token, bearer_secret=bearer_secret
            )
            if getattr(principal, "api_key", None) is not None:
                raise ApiKeyRouteNotAllowedError('risk formulation')
            await db.access.ensure_project_access(project_id=project_id, user_id=principal.user_id)
            if await db.projects.get_by_id(project_id) is None:
                raise ProjectNotFoundError(project_id)
        prompts = {
            RiskFormulationField.DESCRIPTION: RISK_DESCRIPTION_PROMPT,
            RiskFormulationField.MITIGATION_PLAN: RISK_MITIGATION_PROMPT,
            RiskFormulationField.RESPONSE_PLAN: RISK_RESPONSE_PROMPT,
        }
        content = json.dumps(data.model_dump(mode="json"), ensure_ascii=False)
        try:
            output = await self.llm_client.get_structured_response(
                system_prompt=prompts[data.field],
                content=content,
                schema=RiskFormulationResponse,
                max_completion_tokens=1200,
            )
            result = RiskFormulationResponse.model_validate(output)
            if result.field != data.field:
                result = result.model_copy(update={"field": data.field})
            return result
        except ClientError as error:
            raise AiProviderError(str(error)) from error
        except Exception as error:
            raise ProjectRiskServiceError("Модель вернула некорректную формулировку.") from error
