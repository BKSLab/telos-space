"""HTTP-граница персонального просмотра задач."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response

from src.api.v1.responses import VALIDATION_RESPONSE
from src.dependencies.access import TaskIdPath, require_task_access
from src.dependencies.auth import PrincipalDep, require_session
from src.dependencies.task_attention import TaskAttentionServiceDep
from src.exceptions.task_attention import TaskAttentionServiceError
from src.exceptions.tasks import TasksServiceError
from src.schemas.task_attention import TaskAttentionItem, TaskAttentionSnapshot, TaskViewSchema

logger = logging.getLogger(__name__)
router = APIRouter(tags=["task-attention"])
ERROR_RESPONSES = {
    401: {"description": "Нет входа"},
    403: {"description": "Операция недоступна этим учётным данным"},
    500: {"description": "Счётчики недоступны"},
}
TASK_ERRORS = ERROR_RESPONSES | {
    404: {"description": "Задача недоступна"},
    422: VALIDATION_RESPONSE,
}


@router.get(
    "/users/me/task-attention",
    response_model=TaskAttentionSnapshot,
    summary="Непросмотренные задачи",
    operation_id="getTaskAttention",
    description="Персональные новые задачи, нарушения срока и изменения до просмотра.",
    response_description="Счётчики уникальных задач и персональные признаки.",
    responses=ERROR_RESPONSES,
)
async def get_attention(
    principal: PrincipalDep,
    service: TaskAttentionServiceDep,
) -> TaskAttentionSnapshot:
    """Возвращает снимок без изменения признаков просмотра."""
    logger.info("🚀 Персональные счётчики задач: user_id=%s.", principal.user_id)
    try:
        snapshot = await service.snapshot(principal.user_id)
        logger.info(
            "✅ Счётчики получены: user_id=%s, total=%s.", principal.user_id, snapshot.total
        )
        return snapshot
    except TaskAttentionServiceError as error:
        raise HTTPException(error.status_code, error.detail) from error


@router.get(
    "/tasks/{task_id}/attention",
    dependencies=[Depends(require_task_access)],
    response_model=TaskAttentionItem,
    summary="Версия карточки для просмотра",
    operation_id="getTaskAttentionToken",
    description="Снимок перед загрузкой карточки; само чтение просмотром не является.",
    response_description="Подтверждаемая версия карточки.",
    responses=TASK_ERRORS,
)
async def get_token(
    task_id: TaskIdPath,
    principal: PrincipalDep,
    service: TaskAttentionServiceDep,
) -> TaskAttentionItem:
    """Проверка доступа одинакова с чтением самой задачи."""
    logger.info("🚀 Снимок просмотра: task_id=%s, user_id=%s.", task_id, principal.user_id)
    try:
        result = await service.token(principal.user_id, task_id)
        logger.info("✅ Снимок получен: task_id=%s, revision=%s.", task_id, result.revision)
        return result
    except (TaskAttentionServiceError, TasksServiceError) as error:
        raise HTTPException(error.status_code, error.detail) from error


@router.post(
    "/tasks/{task_id}/view",
    dependencies=[Depends(require_task_access), Depends(require_session)],
    status_code=204,
    summary="Подтвердить просмотр карточки",
    operation_id="markTaskViewed",
    description="Персональная отметка только переданной версии. Новые события не сбрасываются.",
    response_description="Просмотр сохранён.",
    responses=TASK_ERRORS
    | {409: {"description": "Версия из будущего или просрочка ещё не наступила"}},
)
async def mark_viewed(
    task_id: TaskIdPath,
    view: TaskViewSchema,
    principal: PrincipalDep,
    service: TaskAttentionServiceDep,
) -> Response:
    """Открытие карточки не требует права изменять доменное содержимое задачи."""
    logger.info(
        "🚀 Просмотр карточки: task_id=%s, user_id=%s, revision=%s.",
        task_id,
        principal.user_id,
        view.revision,
    )
    try:
        await service.mark_viewed(principal.user_id, task_id, view)
        logger.info("✅ Просмотр сохранён: task_id=%s, user_id=%s.", task_id, principal.user_id)
        return Response(status_code=204)
    except (TaskAttentionServiceError, TasksServiceError) as error:
        raise HTTPException(error.status_code, error.detail) from error
