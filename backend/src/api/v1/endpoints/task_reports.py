"""Отчёт по задаче с обычными правами чтения и записи задачи."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from src.dependencies.access import require_project_access
from src.dependencies.auth import PrincipalDep, require_write_scope
from src.dependencies.task_reports import TaskReportsServiceDep
from src.exceptions.base import ServiceError
from src.schemas.task_reports import (
    ProjectTaskReportsSchema,
    TaskReportFromCommentSchema,
    TaskReportSchema,
    TaskReportWriteSchema,
)

router = APIRouter(tags=["task-reports"])
TaskId = Annotated[int, Path(gt=0, description="Идентификатор задачи.")]
ERRORS = {
    code: {"description": detail, "content": {"application/json": {"example": {"detail": detail}}}}
    for code, detail in {
        401: "Требуется действительная учётная запись.",
        403: "Нет права изменять эту задачу.",
        404: "Задача или источник недоступны.",
        409: "Прочитанная версия или данные повторного запроса изменились.",
        410: "Ранее созданная задача удалена.",
        422: "Некорректные поля запроса.",
        500: "Не удалось сохранить результат.",
        503: "Интеграция временно недоступна.",
    }.items()
}


@router.get(
    "/tasks/{task_id}/report",
    response_model=TaskReportSchema,
    summary="Прочитать отчёт по задаче",
    description="Последняя публикация результата; доступ определяется правами на задачу.",
    operation_id="getTaskReport",
    response_description="Текст, версия и источник.",
    responses=ERRORS,
)
async def get_report(
    task_id: TaskId, principal: PrincipalDep, service: TaskReportsServiceDep
) -> TaskReportSchema:
    """Читает текущую версию.

    Args:
        principal: Подтверждённый пользователь и права запроса.
        service: Сценарий с проверками доступа и владельцем транзакции.

    Returns:
        Последняя публикация или пустой отчёт версии0.

    Raises:
        HTTPException: Типизированный отказ в доступе или ошибка операции.
    """
    try:
        return await service.get(task_id, principal)
    except ServiceError as error:
        raise HTTPException(error.status_code, detail=error.detail) from error


@router.get(
    "/tasks/{task_id}/report/history",
    response_model=list[TaskReportSchema],
    summary="История отчёта",
    description="Последние 50 полных версий либо конкретная публикация по ID или номеру версии с теми же правами задачи.",
    operation_id="getTaskReportHistory",
    response_description="Снимки публикаций.",
    responses=ERRORS,
)
async def report_history(
    task_id: TaskId,
    principal: PrincipalDep,
    service: TaskReportsServiceDep,
    report_id: Annotated[
        int | None, Query(gt=0, le=2**31 - 1, description="Точный ID публикации.")
    ] = None,
    revision: Annotated[
        int | None, Query(gt=0, le=2**31 - 1, description="Номер версии отчёта.")
    ] = None,
) -> list[TaskReportSchema]:
    """Читает историю публикаций.

    Args:
        principal: Подтверждённый пользователь и права запроса.
        service: Сценарий с проверками доступа и владельцем транзакции.

    Returns:
        Последние50 полных публикаций.

    Raises:
        HTTPException: Типизированный отказ в доступе или ошибка операции.
    """
    try:
        return await service.history(task_id, principal, report_id=report_id, revision=revision)
    except ServiceError as error:
        raise HTTPException(error.status_code, detail=error.detail) from error


@router.post(
    "/tasks/{task_id}/report",
    response_model=TaskReportSchema,
    summary="Опубликовать отчёт",
    description="Публикует текст новой версией, сохраняя прежние версии.",
    operation_id="publishTaskReport",
    response_description="Опубликованная версия.",
    responses=ERRORS,
    dependencies=[Depends(require_write_scope)],
)
async def publish_report(
    task_id: TaskId,
    data: TaskReportWriteSchema,
    principal: PrincipalDep,
    service: TaskReportsServiceDep,
) -> TaskReportSchema:
    """Публикует ручной результат.

    Args:
        principal: Подтверждённый пользователь и права запроса.
        service: Сценарий с проверками доступа и владельцем транзакции.

    Returns:
        Сохранённая полная версия отчёта.

    Raises:
        HTTPException: Типизированный отказ в доступе или ошибка операции.
    """
    try:
        return await service.publish(task_id, principal, data.expected_revision, data.body_md)
    except ServiceError as error:
        raise HTTPException(error.status_code, detail=error.detail) from error


@router.post(
    "/tasks/{task_id}/report/from-comment",
    response_model=TaskReportSchema,
    summary="Считать комментарий отчётом",
    description="Копирует прочитанный комментарий этой задачи в новую публикацию.",
    operation_id="publishCommentAsTaskReport",
    response_description="Отчёт со снимком автора источника.",
    responses=ERRORS,
    dependencies=[Depends(require_write_scope)],
)
async def report_from_comment(
    task_id: TaskId,
    data: TaskReportFromCommentSchema,
    principal: PrincipalDep,
    service: TaskReportsServiceDep,
) -> TaskReportSchema:
    """Публикует снимок комментария.

    Args:
        principal: Подтверждённый пользователь и права запроса.
        service: Сценарий с проверками доступа и владельцем транзакции.

    Returns:
        Новая публикация со снимком источника.

    Raises:
        HTTPException: Типизированный отказ в доступе или ошибка операции.
    """
    try:
        return await service.publish(
            task_id,
            principal,
            data.expected_revision,
            comment_id=data.comment_id,
            expected_comment_body_md=data.expected_comment_body_md,
        )
    except ServiceError as error:
        raise HTTPException(error.status_code, detail=error.detail) from error


@router.get(
    "/projects/{project_id}/task-reports",
    dependencies=[Depends(require_project_access)],
    response_model=ProjectTaskReportsSchema,
    summary="Актуальные отчёты задач проекта",
    description="Все опубликованные непустые результаты, по одной текущей версии на задачу. Счётчики относятся ко всему проекту; next_offset — продолжение.",
    operation_id="listProjectTaskReports",
    responses=ERRORS,
)
async def project_reports(
    project_id: Annotated[int, Path(gt=0)],
    principal: PrincipalDep,
    service: TaskReportsServiceDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ProjectTaskReportsSchema:
    """Читает перечень для полной проектной сводки."""
    try:
        return await service.list_project(project_id, principal, offset=offset, limit=limit)
    except ServiceError as error:
        raise HTTPException(error.status_code, detail=error.detail) from error
