import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from src.api.v1.responses import (
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
from src.dependencies.access import require_project_access
from src.dependencies.auth import require_write_scope
from src.dependencies.services import WbsNodesServiceDep, WbsSuggestionServiceDep
from src.exceptions.ai import AiProviderError
from src.exceptions.knowledge import KnowledgeServiceError
from src.exceptions.projects import ProjectsServiceError
from src.exceptions.tasks import TasksServiceError
from src.exceptions.wbs_nodes import WbsNodesServiceError
from src.schemas.http_errors import HttpErrorResponseSchema
from src.schemas.tasks import TaskCompactSchema
from src.schemas.wbs_nodes import (
    WbsNodeCreateSchema,
    WbsNodeDeleteResultSchema,
    WbsNodeMoveSchema,
    WbsNodeSchema,
    WbsNodeUpdateSchema,
    WbsStructureSchema,
    WbsTaskAssignSchema,
    WbsTaskPlacementSchema,
)
from src.schemas.wbs_suggestion import (
    WbsSuggestionApplyResultSchema,
    WbsSuggestionApplySchema,
    WbsSuggestionSchema,
)

router = APIRouter(prefix="/projects/{project_id}/wbs", tags=["wbs"])
logger = logging.getLogger(__name__)

WbsErrors = (WbsNodesServiceError, ProjectsServiceError, TasksServiceError)
# Предложение ИСР дополнительно зависит от доступности AI-контура.
SuggestionErrors = (*WbsErrors, KnowledgeServiceError, AiProviderError)

ProjectIdPath = Annotated[int, Path(gt=0, description="Идентификатор проекта.")]
NodeIdPath = Annotated[int, Path(gt=0, description="Идентификатор раздела ИСР.")]
TaskIdPath = Annotated[int, Path(gt=0, description="Идентификатор задачи.")]


@router.get(
    path="",
    dependencies=[Depends(require_project_access)],
    status_code=status.HTTP_200_OK,
    summary="Получить структуру ИСР",
    description=(
        "Возвращает разделы, компактные задачи и сводку одним ответом, "
        "чтобы клиент собрал структуру без дополнительных запросов."
    ),
    operation_id="getProjectWbs",
    response_description="Структура ИСР проекта.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=WbsStructureSchema,
)
async def get_structure(
    project_id: ProjectIdPath,
    service: WbsNodesServiceDep,
) -> WbsStructureSchema:
    """Получает структуру ИСР проекта.

    Args:
        project_id: Идентификатор проекта.
        service: Сервис структуры ИСР.

    Returns:
        Разделы, задачи и сводка структуры.

    Raises:
        HTTPException: Если проект не найден или собрать структуру не удалось.
    """
    logger.info("🚀 Запрос GET /projects/%s/wbs.", project_id)
    try:
        result = await service.get_structure(project_id=project_id)
        logger.info(
            "✅ Структура проекта id=%s получена. Разделов: %s, задач: %s.",
            project_id,
            result.stats.total_nodes,
            result.stats.total_tasks,
        )
        return result
    except WbsErrors as error:
        logger.exception("❌ Ошибка GET /projects/%s/wbs. Детали: %s", project_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/nodes",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    status_code=status.HTTP_201_CREATED,
    summary="Создать раздел ИСР",
    description="Создаёт структурный раздел в конце выбранного уровня.",
    operation_id="createWbsNode",
    response_description="Созданный раздел.",
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=WbsNodeSchema,
)
async def create_node(
    project_id: ProjectIdPath,
    data: WbsNodeCreateSchema,
    service: WbsNodesServiceDep,
) -> WbsNodeSchema:
    """Создаёт раздел ИСР.

    Args:
        project_id: Идентификатор проекта.
        data: Название и родитель нового раздела.
        service: Сервис структуры ИСР.

    Returns:
        Созданный раздел.

    Raises:
        HTTPException: Если проект или родитель не найдены, либо создать раздел не удалось.
    """
    logger.info(
        "🚀 Запрос POST /projects/%s/wbs/nodes. Название: %s, родитель: %s.",
        project_id,
        data.title,
        data.parent_id,
    )
    try:
        result = await service.create_node(
            project_id=project_id,
            title=data.title,
            parent_id=data.parent_id,
        )
        logger.info("✅ Раздел ИСР создан. id=%s.", result.id)
        return result
    except WbsErrors as error:
        logger.exception("❌ Ошибка POST /projects/%s/wbs/nodes. Детали: %s", project_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.patch(
    path="/nodes/{node_id}",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    status_code=status.HTTP_200_OK,
    summary="Переименовать раздел ИСР",
    description="Изменяет название структурного раздела.",
    operation_id="updateWbsNode",
    response_description="Обновлённый раздел.",
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=WbsNodeSchema,
)
async def update_node(
    project_id: ProjectIdPath,
    node_id: NodeIdPath,
    data: WbsNodeUpdateSchema,
    service: WbsNodesServiceDep,
) -> WbsNodeSchema:
    """Переименовывает раздел ИСР.

    Args:
        project_id: Идентификатор проекта.
        node_id: Идентификатор раздела.
        data: Новое название раздела.
        service: Сервис структуры ИСР.

    Returns:
        Обновлённый раздел.

    Raises:
        HTTPException: Если раздел не найден или обновить его не удалось.
    """
    logger.info("🚀 Запрос PATCH /projects/%s/wbs/nodes/%s.", project_id, node_id)
    try:
        result = await service.update_node(
            project_id=project_id,
            node_id=node_id,
            title=data.title,
        )
        logger.info("✅ Раздел ИСР id=%s переименован.", node_id)
        return result
    except WbsErrors as error:
        logger.exception(
            "❌ Ошибка PATCH /projects/%s/wbs/nodes/%s. Детали: %s",
            project_id,
            node_id,
            error,
        )
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/nodes/{node_id}/move",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    status_code=status.HTTP_200_OK,
    summary="Переместить раздел ИСР",
    description=("Меняет родителя и порядок раздела. Позицию внутри уровня рассчитывает backend."),
    operation_id="moveWbsNode",
    response_description="Перемещённый раздел.",
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=WbsNodeSchema,
)
async def move_node(
    project_id: ProjectIdPath,
    node_id: NodeIdPath,
    data: WbsNodeMoveSchema,
    service: WbsNodesServiceDep,
) -> WbsNodeSchema:
    """Переносит раздел ИСР в структуре.

    Args:
        project_id: Идентификатор проекта.
        node_id: Перемещаемый раздел.
        data: Новый родитель и сосед для вставки.
        service: Сервис структуры ИСР.

    Returns:
        Перемещённый раздел.

    Raises:
        HTTPException: Если раздел не найден, перенос создаёт цикл или операция не удалась.
    """
    logger.info(
        "🚀 Запрос POST /projects/%s/wbs/nodes/%s/move. Родитель: %s, перед: %s.",
        project_id,
        node_id,
        data.parent_id,
        data.before_id,
    )
    try:
        result = await service.move_node(
            project_id=project_id,
            node_id=node_id,
            parent_id=data.parent_id,
            before_id=data.before_id,
        )
        logger.info("✅ Раздел ИСР id=%s перемещён.", node_id)
        return result
    except WbsErrors as error:
        logger.exception(
            "❌ Ошибка POST /projects/%s/wbs/nodes/%s/move. Детали: %s",
            project_id,
            node_id,
            error,
        )
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.delete(
    path="/nodes/{node_id}",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    status_code=status.HTTP_200_OK,
    summary="Удалить раздел ИСР",
    description=(
        "Удаляет раздел с подразделами. Задачи не удаляются, а возвращаются в пул нераспределённых."
    ),
    operation_id="deleteWbsNode",
    response_description="Количество удалённых разделов и освобождённых задач.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=WbsNodeDeleteResultSchema,
)
async def delete_node(
    project_id: ProjectIdPath,
    node_id: NodeIdPath,
    service: WbsNodesServiceDep,
) -> WbsNodeDeleteResultSchema:
    """Удаляет раздел ИСР вместе с подразделами.

    Args:
        project_id: Идентификатор проекта.
        node_id: Идентификатор раздела.
        service: Сервис структуры ИСР.

    Returns:
        Количество удалённых разделов и освобождённых задач.

    Raises:
        HTTPException: Если раздел не найден или удалить его не удалось.
    """
    logger.info("🚀 Запрос DELETE /projects/%s/wbs/nodes/%s.", project_id, node_id)
    try:
        result = await service.delete_node(project_id=project_id, node_id=node_id)
        logger.info(
            "✅ Раздел ИСР id=%s удалён. Разделов: %s, задач в пул: %s.",
            node_id,
            result.deleted_nodes,
            result.released_tasks,
        )
        return result
    except WbsErrors as error:
        logger.exception(
            "❌ Ошибка DELETE /projects/%s/wbs/nodes/%s. Детали: %s",
            project_id,
            node_id,
            error,
        )
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/tasks/{task_id}/assign",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    status_code=status.HTTP_200_OK,
    summary="Назначить задачу в раздел",
    description="Помещает задачу проекта в указанный раздел ИСР.",
    operation_id="assignTaskToWbsNode",
    response_description="Обновлённая задача.",
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=TaskCompactSchema,
)
async def assign_task(
    project_id: ProjectIdPath,
    task_id: TaskIdPath,
    data: WbsTaskAssignSchema,
    service: WbsNodesServiceDep,
) -> TaskCompactSchema:
    """Назначает задачу в раздел ИСР.

    Args:
        project_id: Идентификатор проекта.
        task_id: Идентификатор задачи.
        data: Целевой раздел ИСР.
        service: Сервис структуры ИСР.

    Returns:
        Компактное представление обновлённой задачи.

    Raises:
        HTTPException: Если задача или раздел не найдены, либо назначить не удалось.
    """
    logger.info(
        "🚀 Запрос POST /projects/%s/wbs/tasks/%s/assign. Раздел: %s.",
        project_id,
        task_id,
        data.wbs_node_id,
    )
    try:
        result = await service.assign_task(
            project_id=project_id,
            task_id=task_id,
            wbs_node_id=data.wbs_node_id,
            before_task_id=data.before_task_id,
        )
        logger.info("✅ Задача id=%s назначена в раздел %s.", task_id, data.wbs_node_id)
        return result
    except WbsErrors as error:
        logger.exception(
            "❌ Ошибка POST /projects/%s/wbs/tasks/%s/assign. Детали: %s",
            project_id,
            task_id,
            error,
        )
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/suggestion",
    dependencies=[Depends(require_project_access)],
    status_code=status.HTTP_200_OK,
    summary="Предложить структуру ИСР",
    description=(
        "Просит модель разложить существующие задачи проекта по разделам. "
        "Проект не изменяется: ответ возвращается как черновик, который "
        "пользователь правит и применяет отдельным запросом."
    ),
    operation_id="suggestProjectWbs",
    response_description="Черновик структуры ИСР.",
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
        503: {
            "model": HttpErrorResponseSchema,
            "description": "AI-провайдер временно недоступен.",
            "content": {"application/json": {"example": {"detail": AiProviderError.detail}}},
        },
    },
    response_model=WbsSuggestionSchema,
)
async def suggest_structure(
    project_id: ProjectIdPath,
    service: WbsSuggestionServiceDep,
) -> WbsSuggestionSchema:
    """Возвращает предложенную моделью структуру ИСР.

    Args:
        project_id: Идентификатор проекта.
        service: Сервис предложения структуры ИСР.

    Returns:
        Черновик разделов и размещения задач.

    Raises:
        HTTPException: Если проект не найден, задач нет или модель недоступна.
    """
    logger.info("🚀 Запрос POST /projects/%s/wbs/suggestion.", project_id)
    try:
        result = await service.suggest(project_id=project_id)
        logger.info(
            "✅ Предложение ИСР готово. Разделов: %s, задач: %s.",
            len(result.nodes),
            len(result.assignments),
        )
        return result
    except SuggestionErrors as error:
        logger.exception(
            "❌ Ошибка POST /projects/%s/wbs/suggestion. Детали: %s",
            project_id,
            error,
        )
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/suggestion/apply",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    status_code=status.HTTP_200_OK,
    summary="Применить предложенную структуру ИСР",
    description=(
        "Создаёт разделы черновика и переносит в них задачи одной транзакцией. "
        "Существующие разделы не удаляются."
    ),
    operation_id="applyProjectWbsSuggestion",
    response_description="Количество созданных разделов и перенесённых задач.",
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=WbsSuggestionApplyResultSchema,
)
async def apply_suggestion(
    project_id: ProjectIdPath,
    data: WbsSuggestionApplySchema,
    service: WbsSuggestionServiceDep,
) -> WbsSuggestionApplyResultSchema:
    """Применяет отредактированный пользователем черновик ИСР.

    Args:
        project_id: Идентификатор проекта.
        data: Разделы и размещение задач черновика.
        service: Сервис предложения структуры ИСР.

    Returns:
        Количество созданных разделов и перенесённых задач.

    Raises:
        HTTPException: Если проект не найден или черновик не проходит проверку.
    """
    logger.info(
        "🚀 Запрос POST /projects/%s/wbs/suggestion/apply. Разделов: %s, задач: %s.",
        project_id,
        len(data.nodes),
        len(data.assignments),
    )
    try:
        result = await service.apply(
            project_id=project_id,
            nodes=data.nodes,
            assignments=data.assignments,
        )
        logger.info(
            "✅ Предложение ИСР применено. Разделов: %s, задач: %s.",
            result.created_nodes,
            result.assigned_tasks,
        )
        return result
    except SuggestionErrors as error:
        logger.exception(
            "❌ Ошибка POST /projects/%s/wbs/suggestion/apply. Детали: %s",
            project_id,
            error,
        )
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/tasks/{task_id}/placement",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    status_code=status.HTTP_200_OK,
    summary="Разместить задачу в структуре или на холсте",
    description=(
        "Единая операция перетаскивания карточки: в раздел ИСР, на свободный "
        "холст или обратно в список-пул. Порядок внутри раздела задаётся "
        "соседом, перед которым встаёт задача."
    ),
    operation_id="placeWbsTask",
    response_description="Обновлённая задача.",
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=TaskCompactSchema,
)
async def place_task(
    project_id: ProjectIdPath,
    task_id: TaskIdPath,
    data: WbsTaskPlacementSchema,
    service: WbsNodesServiceDep,
) -> TaskCompactSchema:
    """Размещает задачу в разделе ИСР, на холсте или в списке-пуле.

    Args:
        project_id: Идентификатор проекта.
        task_id: Идентификатор задачи.
        data: Целевое размещение задачи.
        service: Сервис структуры ИСР.

    Returns:
        Компактное представление обновлённой задачи.

    Raises:
        HTTPException: Если задача или раздел не найдены, либо разместить не удалось.
    """
    logger.info(
        "🚀 Запрос POST /projects/%s/wbs/tasks/%s/placement. Раздел: %s, перед: %s.",
        project_id,
        task_id,
        data.wbs_node_id,
        data.before_task_id,
    )
    try:
        result = await service.place_task(
            project_id=project_id,
            task_id=task_id,
            wbs_node_id=data.wbs_node_id,
            before_task_id=data.before_task_id,
            canvas_x=data.canvas_x,
            canvas_y=data.canvas_y,
        )
        logger.info("✅ Задача id=%s размещена. Раздел: %s.", task_id, data.wbs_node_id)
        return result
    except WbsErrors as error:
        logger.exception(
            "❌ Ошибка POST /projects/%s/wbs/tasks/%s/placement. Детали: %s",
            project_id,
            task_id,
            error,
        )
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.delete(
    path="/tasks/{task_id}/assignment",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    status_code=status.HTTP_200_OK,
    summary="Убрать задачу из структуры",
    description="Снимает привязку задачи к разделу. Сама задача остаётся в проекте.",
    operation_id="unassignTaskFromWbsNode",
    response_description="Обновлённая задача.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=TaskCompactSchema,
)
async def unassign_task(
    project_id: ProjectIdPath,
    task_id: TaskIdPath,
    service: WbsNodesServiceDep,
) -> TaskCompactSchema:
    """Возвращает задачу в пул нераспределённых.

    Args:
        project_id: Идентификатор проекта.
        task_id: Идентификатор задачи.
        service: Сервис структуры ИСР.

    Returns:
        Компактное представление обновлённой задачи.

    Raises:
        HTTPException: Если задача не найдена или снять привязку не удалось.
    """
    logger.info("🚀 Запрос DELETE /projects/%s/wbs/tasks/%s/assignment.", project_id, task_id)
    try:
        result = await service.unassign_task(project_id=project_id, task_id=task_id)
        logger.info("✅ Задача id=%s возвращена в пул нераспределённых.", task_id)
        return result
    except WbsErrors as error:
        logger.exception(
            "❌ Ошибка DELETE /projects/%s/wbs/tasks/%s/assignment. Детали: %s",
            project_id,
            task_id,
            error,
        )
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
