import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from src.api.v1.responses import NOT_FOUND_RESPONSE, SERVER_ERROR_RESPONSE, VALIDATION_RESPONSE
from src.dependencies.access import require_project_access
from src.dependencies.auth import require_write_scope
from src.dependencies.services import TaskDependenciesServiceDep
from src.exceptions.projects import ProjectsServiceError
from src.exceptions.task_dependencies import TaskDependenciesServiceError
from src.exceptions.tasks import TasksServiceError
from src.schemas.task_dependencies import TaskDependencyCreateSchema, TaskDependencySchema

router = APIRouter(tags=["task-dependencies"])
logger = logging.getLogger(__name__)

DependencyErrors = (TaskDependenciesServiceError, ProjectsServiceError, TasksServiceError)


@router.get(
    "/projects/{project_id}/task-dependencies",
    dependencies=[Depends(require_project_access)],
    response_model=list[TaskDependencySchema],
    responses={404: NOT_FOUND_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    summary="Получить зависимости задач проекта",
    description="Возвращает направленные связи Finish-to-Start между задачами доступного проекта.",
    operation_id="listProjectTaskDependencies",
    response_description="Зависимости задач проекта.",
)
async def list_task_dependencies(
    project_id: Annotated[int, Path(gt=0)],
    service: TaskDependenciesServiceDep,
) -> list[TaskDependencySchema]:
    """Возвращает направленные связи Finish-to-Start."""
    try:
        return await service.list_dependencies(project_id)
    except DependencyErrors as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    "/projects/{project_id}/task-dependencies",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    response_model=TaskDependencySchema,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: NOT_FOUND_RESPONSE,
        409: {"description": "Связь существует или создаёт цикл"},
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    summary="Создать зависимость задач",
    description="Создаёт связь Finish-to-Start после проверки принадлежности задач и отсутствия цикла.",
    operation_id="createProjectTaskDependency",
    response_description="Созданная зависимость задач.",
)
async def create_task_dependency(
    project_id: Annotated[int, Path(gt=0)],
    data: TaskDependencyCreateSchema,
    service: TaskDependenciesServiceDep,
) -> TaskDependencySchema:
    """Создаёт проверенную связь Finish-to-Start."""
    try:
        return await service.create_dependency(project_id, data.model_dump())
    except DependencyErrors as error:
        logger.info(
            "⚠️ Зависимость задач проекта id=%s отклонена: %s.",
            project_id,
            error.detail,
        )
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.delete(
    "/projects/{project_id}/task-dependencies/{dependency_id}",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: NOT_FOUND_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    summary="Удалить зависимость задач",
    description="Удаляет выбранную зависимость задач доступного проекта.",
    operation_id="deleteProjectTaskDependency",
    response_description="Зависимость удалена.",
)
async def delete_task_dependency(
    project_id: Annotated[int, Path(gt=0)],
    dependency_id: Annotated[int, Path(gt=0)],
    service: TaskDependenciesServiceDep,
) -> None:
    """Удаляет связь выбранного проекта."""
    try:
        await service.delete_dependency(project_id, dependency_id)
    except DependencyErrors as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
