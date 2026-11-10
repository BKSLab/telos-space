import logging

from fastapi import APIRouter, Depends, HTTPException, status

from src.agent.tools import AgentToolContext
from src.api.v1.responses import (
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
from src.dependencies.access import ProjectIdPath, require_project_access
from src.dependencies.auth import PrincipalDep, require_write_scope
from src.dependencies.services import ProjectAgentServiceDep
from src.exceptions.ai import AgentAnswerError, AiProviderError
from src.exceptions.knowledge import KnowledgeServiceError
from src.schemas.knowledge import (
    KnowledgeAnswerSchema,
    KnowledgeAskSchema,
    KnowledgeReindexSchema,
    KnowledgeStatusSchema,
)

router = APIRouter(prefix="/projects/{project_id}/knowledge", tags=["project-knowledge"])
logger = logging.getLogger(__name__)
KNOWLEDGE_ERRORS = {
    401: {"description": "Требуется вход."},
    403: FORBIDDEN_RESPONSE,
    404: NOT_FOUND_RESPONSE,
    422: VALIDATION_RESPONSE,
    500: SERVER_ERROR_RESPONSE,
    503: {"description": "База знаний отключена или AI-сервис недоступен."},
}


@router.get(
    "/status",
    dependencies=[Depends(require_project_access)],
    response_model=KnowledgeStatusSchema,
    summary="Получить состояние базы знаний проекта",
    description="Возвращает готовность и состояние индекса доступного проекта.",
    operation_id="getProjectKnowledgeStatus",
    response_description="Состояние индекса проекта.",
    responses=KNOWLEDGE_ERRORS,
)
async def get_knowledge_status(
    project_id: ProjectIdPath,
    service: ProjectAgentServiceDep,
) -> KnowledgeStatusSchema:
    """Возвращает готовность семантического индекса доступного проекта."""
    try:
        return await service.get_status(project_id)
    except (KnowledgeServiceError, AiProviderError) as error:
        logger.exception("❌ Не удалось получить статус базы знаний проекта id=%s.", project_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/ask",
    dependencies=[Depends(require_project_access)],
    response_model=KnowledgeAnswerSchema,
    summary="Задать вопрос по базе знаний проекта",
    description="Формирует ответ по знаниям проекта с правами текущего пользователя.",
    operation_id="askProjectKnowledge",
    response_description="Ответ и использованные источники.",
    responses={**KNOWLEDGE_ERRORS, 502: {"description": "Не удалось сформировать ответ."}},
)
async def ask_project_agent(
    project_id: ProjectIdPath,
    data: KnowledgeAskSchema,
    service: ProjectAgentServiceDep,
    principal: PrincipalDep,
) -> KnowledgeAnswerSchema:
    """Отвечает на вопрос только по данным доступного проекта."""
    try:
        return await service.ask(
            project_id=project_id,
            question=data.question,
            history=data.history,
            execution=AgentToolContext(project_id=project_id, user_id=principal.user_id),
        )
    except (KnowledgeServiceError, AiProviderError, AgentAnswerError) as error:
        logger.exception("❌ Арис проекта id=%s не ответил.", project_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/reindex",
    dependencies=[Depends(require_project_access), Depends(require_write_scope)],
    status_code=status.HTTP_202_ACCEPTED,
    response_model=KnowledgeReindexSchema,
    summary="Запустить переиндексацию знаний проекта",
    description="Ставит полную пересборку индекса доступного проекта в постоянную очередь.",
    operation_id="reindexProjectKnowledge",
    response_description="Подтверждение постановки переиндексации в очередь.",
    responses=KNOWLEDGE_ERRORS,
)
async def reindex_project_knowledge(
    project_id: ProjectIdPath,
    service: ProjectAgentServiceDep,
) -> KnowledgeReindexSchema:
    """Ставит полную пересборку индекса проекта в постоянную очередь."""
    try:
        await service.reindex(project_id)
        return KnowledgeReindexSchema()
    except KnowledgeServiceError as error:
        logger.exception("❌ Не удалось поставить reindex проекта id=%s.", project_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
