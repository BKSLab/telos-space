import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.v1.responses import SERVER_ERROR_RESPONSE
from src.dependencies.auth import PrincipalDep, require_write_scope
from src.dependencies.services import AnalyticsServiceDep
from src.exceptions.ai import AiProviderError
from src.exceptions.analytics import AnalyticsServiceError
from src.exceptions.projects import ProjectNotFoundError
from src.schemas.analytics import AnalyticsGenerateRequest, AnalyticsReportSchema

router = APIRouter(prefix="/dashboard/analytics", tags=["analytics"])
logger = logging.getLogger(__name__)


@router.get(
    path="",
    status_code=status.HTTP_200_OK,
    summary="Получить последний аналитический свод",
    description=(
        "Возвращает последний сохранённый свод по проекту или по всему портфелю "
        "пользователя. Если анализ ещё не запускали, возвращает null."
    ),
    operation_id="getAnalyticsReport",
    response_description="Последний аналитический свод или null.",
    responses={404: {"description": "Проект не найден."}, 500: SERVER_ERROR_RESPONSE},
    response_model=AnalyticsReportSchema | None,
)
async def get_analytics_report(
    principal: PrincipalDep,
    service: AnalyticsServiceDep,
    project_id: int | None = Query(
        None,
        gt=0,
        description="Проект свода; без параметра — свод по всему портфелю.",
    ),
) -> AnalyticsReportSchema | None:
    """Получает последний сохранённый аналитический свод.

    Args:
        principal: Принципал текущего запроса.
        service: Сервис аналитических сводов.
        project_id: Проект анализа или ``None`` для всего портфеля.

    Returns:
        Последний свод или ``None``, если анализ ещё не запускали.

    Raises:
        HTTPException: Если проект недоступен или прочитать свод не удалось.
    """
    logger.info("🚀 Запрос GET /dashboard/analytics. Проект: %s.", project_id)
    try:
        result = await service.get_latest(user_id=principal.user_id, project_id=project_id)
        logger.info("✅ Свод получен: %s.", "есть" if result else "ещё не формировался")
        return result
    except (ProjectNotFoundError, AnalyticsServiceError) as error:
        logger.exception("❌ Ошибка GET /dashboard/analytics. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="",
    dependencies=[Depends(require_write_scope)],
    status_code=status.HTTP_201_CREATED,
    summary="Сформировать аналитический свод",
    description=(
        "Собирает срез проектов, задач, комментариев, ИСР, стикеров и документов, "
        "просит модель разобрать состояние работ и сохраняет результат."
    ),
    operation_id="createAnalyticsReport",
    response_description="Сформированный аналитический свод.",
    responses={
        404: {"description": "Проект не найден."},
        409: {"description": "В выбранной области нет содержательных данных проекта."},
        502: {"description": "Модель не вернула пригодный свод."},
        503: {"description": "AI-сервис временно недоступен."},
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=AnalyticsReportSchema,
)
async def create_analytics_report(
    payload: AnalyticsGenerateRequest,
    principal: PrincipalDep,
    service: AnalyticsServiceDep,
) -> AnalyticsReportSchema:
    """Формирует новый аналитический свод дашборда.

    Args:
        payload: Область анализа.
        principal: Принципал текущего запроса.
        service: Сервис аналитических сводов.

    Returns:
        Сформированный свод.

    Raises:
        HTTPException: Если проект недоступен, анализировать нечего или модель
            не вернула пригодный ответ.
    """
    logger.info("🚀 Запрос POST /dashboard/analytics. Проект: %s.", payload.project_id)
    try:
        result = await service.generate(
            actor_id=principal.user_id,
            actor_name=principal.full_name,
            project_id=payload.project_id,
        )
        logger.info(
            "✅ Свод id=%s сформирован: находок %s, рекомендаций %s.",
            result.id,
            len(result.findings),
            len(result.recommendations),
        )
        return result
    except (ProjectNotFoundError, AiProviderError, AnalyticsServiceError) as error:
        logger.exception("❌ Ошибка POST /dashboard/analytics. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
