import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.v1.responses import (
    CONFLICT_RESPONSE,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
from src.dependencies.access import (
    ProjectIdPath,
    require_project_access,
    require_project_ownership,
)
from src.dependencies.auth import PrincipalDep, require_write_scope
from src.dependencies.services import ProjectHistoryServiceDep, ProjectsServiceDep
from src.exceptions.projects import ProjectsServiceError
from src.schemas.project_history import ProjectHistoryPage, ProjectHistoryQuery
from src.schemas.projects import (
    ProjectContextHistoryPageSchema,
    ProjectCreateSchema,
    ProjectDeadlineChangeSchema,
    ProjectDefaultsSchema,
    ProjectSchema,
    ProjectStatsSchema,
    ProjectUpdateSchema,
)

router = APIRouter(prefix="/projects", tags=["projects"])
logger = logging.getLogger(__name__)


@router.get(
    "/{project_id}/history",
    dependencies=[Depends(require_project_access)],
    operation_id="getProjectHistory",
    summary="Получить объединённую историю проекта",
    description="Читает доступные источники истории проекта от новых к старым. Для API-ключа требуются projects:read, tasks:read и knowledge:read.",
    response_description="Страница событий, смещение следующей страницы и запрошенный день более ранних событий.",
    responses={
        401: {"description": "Требуется авторизация."},
        403: FORBIDDEN_RESPONSE,
        404: NOT_FOUND_RESPONSE,
        422: VALIDATION_RESPONSE,
    },
)
async def get_project_history(
    project_id: ProjectIdPath,
    service: ProjectHistoryServiceDep,
    filters: Annotated[ProjectHistoryQuery, Query()],
) -> ProjectHistoryPage:
    return await service.read(project_id, filters)


@router.get(
    path="",
    status_code=status.HTTP_200_OK,
    summary="Получить проекты",
    description="Возвращает все проекты трекера в порядке отображения.",
    operation_id="getProjects",
    response_description="Список проектов.",
    responses={500: SERVER_ERROR_RESPONSE},
    response_model=list[ProjectSchema],
)
async def get_projects(principal: PrincipalDep, service: ProjectsServiceDep) -> list[ProjectSchema]:
    """Получает список проектов.

    Args:
        service: Сервис проектов.

    Returns:
        Список проектов.

    Raises:
        HTTPException: Если получить проекты не удалось.
    """
    logger.info("🚀 Запрос GET /projects.")
    try:
        result = await service.get_project_list(user_id=principal.user_id)
        logger.info("✅ Проекты получены. Найдено: %s.", len(result))
        return result
    except ProjectsServiceError as error:
        logger.exception("❌ Ошибка GET /projects. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/defaults",
    response_model=ProjectDefaultsSchema,
    summary="Получить начальные настройки проекта",
    description="Возвращает стандартные стадии для редактирования до создания проекта.",
    operation_id="getProjectDefaults",
    response_description="Начальные стадии канбана.",
    responses={401: {"description": "Требуется авторизация."}},
)
async def get_project_defaults(
    principal: PrincipalDep, service: ProjectsServiceDep
) -> ProjectDefaultsSchema:
    """Возвращает настройки формы.

    Args:
        principal: Авторизованный пользователь.
        service: Сервис проектов.

    Returns:
        Набор стадий по умолчанию.
    """
    return service.get_defaults()


@router.post(
    path="",
    dependencies=[Depends(require_write_scope)],
    status_code=status.HTTP_201_CREATED,
    summary="Создать проект",
    description="Создаёт паспорт, команду и стадии канбана одной транзакцией. Создатель становится руководителем.",
    operation_id="createProject",
    response_description="Созданный проект.",
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=ProjectSchema,
)
async def create_project(
    data: ProjectCreateSchema,
    principal: PrincipalDep,
    service: ProjectsServiceDep,
) -> ProjectSchema:
    """Создаёт проект.

    Args:
        data: Поля нового проекта.
        service: Сервис проектов.

    Returns:
        Созданный проект.

    Raises:
        HTTPException: Если код проекта занят или создать проект не удалось.
    """
    logger.info("🚀 Запрос POST /projects. Код: %s.", data.key)
    try:
        result = await service.create_project(data=data.model_dump(), owner_id=principal.user_id)
        logger.info("✅ Проект создан. id=%s.", result.id)
        return result
    except ProjectsServiceError as error:
        logger.exception("❌ Ошибка POST /projects. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/{project_id}",
    dependencies=[Depends(require_project_access)],
    status_code=status.HTTP_200_OK,
    summary="Получить проект",
    description="Возвращает карточку проекта по идентификатору.",
    operation_id="getProject",
    response_description="Карточка проекта.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=ProjectSchema,
)
async def get_project(
    project_id: ProjectIdPath,
    service: ProjectsServiceDep,
) -> ProjectSchema:
    """Получает проект по идентификатору.

    Args:
        project: Проект, доступ к которому уже проверен.
        service: Сервис проектов.

    Returns:
        Карточка проекта.

    Raises:
        HTTPException: Если проект не найден или получить его не удалось.
    """
    logger.info("🚀 Запрос GET /projects/%s.", project_id)
    try:
        result = await service.get_project(project_id=project_id)
        logger.info("✅ Проект id=%s получен.", project_id)
        return result
    except ProjectsServiceError as error:
        logger.exception("❌ Ошибка GET /projects/%s. Детали: %s", project_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/{project_id}/stats",
    dependencies=[Depends(require_project_access)],
    status_code=status.HTTP_200_OK,
    summary="Получить показатели проекта",
    description="Возвращает прогресс, сроки и распределение задач по стадиям проекта.",
    operation_id="getProjectStats",
    response_description="Показатели проекта.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=ProjectStatsSchema,
)
async def get_project_stats(
    project_id: ProjectIdPath,
    service: ProjectsServiceDep,
) -> ProjectStatsSchema:
    """Получает показатели проекта.

    Args:
        project: Проект, доступ к которому уже проверен.
        service: Сервис проектов.

    Returns:
        Показатели проекта.

    Raises:
        HTTPException: Если проект не найден или собрать показатели не удалось.
    """
    logger.info("🚀 Запрос GET /projects/%s/stats.", project_id)
    try:
        result = await service.get_project_stats(project_id=project_id)
        logger.info("✅ Показатели проекта id=%s собраны.", project_id)
        return result
    except ProjectsServiceError as error:
        logger.exception("❌ Ошибка GET /projects/%s/stats. Детали: %s", project_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.patch(
    path="/{project_id}",
    dependencies=[Depends(require_project_ownership), Depends(require_write_scope)],
    status_code=status.HTTP_200_OK,
    summary="Изменить проект",
    description="Обновляет паспорт проекта. После первого назначения срока любое изменение окончания требует due_date_comment и записывается в историю.",
    operation_id="updateProject",
    response_description="Обновлённый проект.",
    responses={
        403: FORBIDDEN_RESPONSE,
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=ProjectSchema,
)
async def update_project(
    project_id: ProjectIdPath,
    data: ProjectUpdateSchema,
    principal: PrincipalDep,
    service: ProjectsServiceDep,
) -> ProjectSchema:
    """Обновляет проект.

    Args:
        project: Проект, доступ к которому уже проверен.
        data: Изменяемые поля проекта.
        service: Сервис проектов.

    Returns:
        Обновлённый проект.

    Raises:
        HTTPException: Если проект не найден или обновить его не удалось.
    """
    logger.info("🚀 Запрос PATCH /projects/%s.", project_id)
    try:
        result = await service.update_project(
            project_id=project_id,
            data=data.model_dump(exclude_unset=True),
            updated_by_user_id=principal.user_id,
        )
        logger.info("✅ Проект id=%s обновлён.", project_id)
        return result
    except ProjectsServiceError as error:
        logger.exception("❌ Ошибка PATCH /projects/%s. Детали: %s", project_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/{project_id}/context-history",
    dependencies=[Depends(require_project_access)],
    response_model=ProjectContextHistoryPageSchema,
    summary="Получить историю паспорта проекта",
    description="Возвращает версии, серверных авторов и изменённые поля. Полный исторический текст в метаданные не включается; дата прежнего baseline означает время его фиксации.",
    operation_id="getProjectContextHistory",
    response_description="Страница истории паспорта без полного текста снимков.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
)
async def get_project_context_history(
    project_id: ProjectIdPath,
    service: ProjectsServiceDep,
    limit: Annotated[int, Query(ge=1, le=50, description="Размер страницы истории.")] = 20,
    offset: Annotated[int, Query(ge=0, description="Число пропущенных версий.")] = 0,
) -> ProjectContextHistoryPageSchema:
    """Читает метаданные версий для участника доступного проекта.

    Args:
        project_id: Проект, доступ к которому проверен общей зависимостью.
        service: Доменный сервис проекта.
        limit: Размер страницы до 50 записей.
        offset: Число пропущенных записей.
    Returns:
        Метаданные истории и ссылка на следующую страницу через offset.
    Raises:
        HTTPException: Проект отсутствует или историю прочитать не удалось.
    """
    try:
        return await service.get_context_history(project_id, limit=limit, offset=offset)
    except ProjectsServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/{project_id}/deadline-history",
    dependencies=[Depends(require_project_access)],
    response_model=list[ProjectDeadlineChangeSchema],
    summary="Получить историю срока проекта",
    description="Возвращает назначения и пересмотры даты окончания с причиной и автором, от новых к старым.",
    operation_id="getProjectDeadlineHistory",
    response_description="История сроков проекта.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
)
async def get_project_deadline_history(
    project_id: ProjectIdPath,
    service: ProjectsServiceDep,
) -> list[ProjectDeadlineChangeSchema]:
    """Читает историю сроков проекта.

    Args:
        project_id: Идентификатор доступного проекта.
        service: Сервис проектов.

    Returns:
        Назначения и изменения даты окончания.

    Raises:
        HTTPException: Ошибка чтения или отсутствие проекта.
    """
    try:
        return await service.get_deadline_history(project_id)
    except ProjectsServiceError as error:
        logger.exception("❌ Не удалось прочитать историю срока проекта id=%s.", project_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.delete(
    path="/{project_id}",
    dependencies=[Depends(require_project_ownership), Depends(require_write_scope)],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить проект",
    description="Удаляет проект вместе с задачами, стадиями, структурой и документами.",
    operation_id="deleteProject",
    response_description="Проект удалён.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
)
async def delete_project(
    project_id: ProjectIdPath,
    service: ProjectsServiceDep,
) -> None:
    """Удаляет проект.

    Args:
        project: Проект, доступ к которому уже проверен.
        service: Сервис проектов.

    Returns:
        ``None`` после успешного удаления.

    Raises:
        HTTPException: Если проект не найден или удалить его не удалось.
    """
    logger.info("🚀 Запрос DELETE /projects/%s.", project_id)
    try:
        await service.delete_project(project_id=project_id)
        logger.info("✅ Проект id=%s удалён.", project_id)
    except ProjectsServiceError as error:
        logger.exception("❌ Ошибка DELETE /projects/%s. Детали: %s", project_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
