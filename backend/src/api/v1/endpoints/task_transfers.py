"""Проектные и разовые задачи."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.v1.responses import (
    CONFLICT_RESPONSE,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
from src.dependencies.access import require_task_access
from src.dependencies.auth import PrincipalDep, require_write_scope
from src.dependencies.services import TaskTransfersServiceDep
from src.exceptions.access import AccessServiceError
from src.exceptions.project_stages import ProjectStagesServiceError
from src.exceptions.projects import ProjectsServiceError
from src.exceptions.task_transfers import TaskTransfersServiceError
from src.exceptions.tasks import TasksServiceError
from src.schemas.task_transfers import (
    TaskTransferPreviewSchema,
    TaskTransferResultSchema,
    TaskTransferTargetSchema,
)

router = APIRouter(tags=["tasks"])
logger = logging.getLogger(__name__)

TransferErrors = (
    TaskTransfersServiceError,
    TasksServiceError,
    ProjectsServiceError,
    ProjectStagesServiceError,
    AccessServiceError,
)
TRANSFER_RESPONSES = {
    403: FORBIDDEN_RESPONSE,
    404: NOT_FOUND_RESPONSE,
    409: CONFLICT_RESPONSE,
    422: VALIDATION_RESPONSE,
    500: SERVER_ERROR_RESPONSE,
}


@router.get(
    path="/tasks/{task_id}/transfer/preview",
    dependencies=[Depends(require_task_access)],
    status_code=status.HTTP_200_OK,
    summary="Предпросмотр переноса задачи",
    description=(
        "Показывает, что сделает перенос в проект или в разовые задачи: новое место, "
        "стадию или статус, исполнителя, снимаемые связи со старым проектом и людей не из "
        "команды целевого проекта. Ничего не меняет."
    ),
    operation_id="previewTaskTransfer",
    response_description="План переноса.",
    responses=TRANSFER_RESPONSES,
    response_model=TaskTransferPreviewSchema,
)
async def preview_task_transfer(
    task_id: Annotated[int, Path(gt=0, description="Идентификатор задачи.")],
    target: Annotated[TaskTransferTargetSchema, Query()],
    principal: PrincipalDep,
    service: TaskTransfersServiceDep,
) -> TaskTransferPreviewSchema:
    """Показывает план переноса задачи.

    Args:
        task_id: Идентификатор задачи.
        target: Цель переноса.
        principal: Пользователь запроса.
        service: Сервис переноса.

    Returns:
        План переноса.

    Raises:
        HTTPException: Если перенос невозможен или собрать план не удалось.
    """
    logger.info("🚀 Запрос GET /tasks/%s/transfer/preview. Цель: %s.", task_id, target.kind.value)
    try:
        return await service.preview(task_id=task_id, target=target, user_id=principal.user_id)
    except TransferErrors as error:
        logger.warning("⚠️ Предпросмотр переноса задачи id=%s отклонён: %s", task_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/tasks/{task_id}/transfer",
    dependencies=[Depends(require_write_scope), Depends(require_task_access)],
    status_code=status.HTTP_200_OK,
    summary="Перенести задачу",
    description=(
        "Переносит задачу в другой проект (с выбранной стадией или по её состоянию) "
        "или делает разовой — одной транзакцией. Номер выдаётся заново счётчиком цели, "
        "прежний остаётся в истории. Зависимости, связи с документами, риски и стикеры "
        "старого проекта снимаются; комментарии, чек-лист, файлы, отчёты и история едут с "
        "задачей. Люди не из команды целевого проекта — 409, пока не передан "
        "remove_outsiders=true."
    ),
    operation_id="transferTask",
    response_description="Задача на новом месте, прежний и новый номер, снятые связи.",
    responses=TRANSFER_RESPONSES,
    response_model=TaskTransferResultSchema,
)
async def transfer_task(
    task_id: Annotated[int, Path(gt=0, description="Идентификатор задачи.")],
    target: TaskTransferTargetSchema,
    principal: PrincipalDep,
    service: TaskTransfersServiceDep,
) -> TaskTransferResultSchema:
    """Переносит задачу.

    Args:
        task_id: Идентификатор задачи.
        target: Цель переноса.
        principal: Пользователь запроса.
        service: Сервис переноса.

    Returns:
        Итог переноса.

    Raises:
        HTTPException: Если перенос невозможен или не удался.
    """
    logger.info("🚀 Запрос POST /tasks/%s/transfer. Цель: %s.", task_id, target.kind.value)
    try:
        result = await service.move(task_id=task_id, target=target, user_id=principal.user_id)
        logger.info("✅ Задача перенесена: %s → %s.", result.from_key, result.to_key)
        return result
    except TransferErrors as error:
        logger.warning("⚠️ Перенос задачи id=%s отклонён: %s", task_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
