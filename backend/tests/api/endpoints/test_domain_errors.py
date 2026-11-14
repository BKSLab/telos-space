"""Отображение доменных ошибок сервиса в HTTP-ответ.

Правило одно на весь транспорт: эндпоинт переводит перечисленные ошибки
своего сервиса в статус и формулировку, а всё непредусмотренное отдаёт
как 500 без подробностей. Поэтому проверка — одна таблица отображений, а
не отдельный тест на каждую пару «ошибка, маршрут»: таблица читается как
спецификация и показывает сразу все расхождения.
"""

from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from main import app
from src.dependencies.document_catalog import (
    get_document_catalog_principal,
    get_document_catalog_service,
)
from src.dependencies.services import (
    get_dashboard_service,
    get_project_stages_service,
    get_projects_service,
    get_task_activity_service,
    get_task_comments_service,
    get_tasks_service,
    get_wbs_nodes_service,
)
from src.exceptions.dashboard import DashboardServiceError
from src.exceptions.document_catalog import (
    CatalogDocumentConflictError,
    CatalogDocumentValidationError,
    DocumentCatalogServiceError,
)
from src.exceptions.project_stages import (
    ProjectLastStageDeleteError,
    ProjectStageHasTasksError,
    ProjectStagesServiceError,
)
from src.exceptions.projects import (
    ProjectKeyConflictError,
    ProjectNotFoundError,
    ProjectsServiceError,
)
from src.exceptions.task_activity import TaskActivityServiceError
from src.exceptions.task_comments import TaskCommentNotFoundError, TaskCommentsServiceError
from src.exceptions.tasks import TaskNotFoundError, TasksServiceError
from src.exceptions.wbs_nodes import (
    WbsNodeCycleError,
    WbsNodeForeignProjectError,
    WbsNodeNotFoundError,
    WbsNodesServiceError,
)
from src.services.dashboard import DashboardService
from src.services.document_catalog import DocumentCatalogService
from src.services.project_stages import ProjectStagesService
from src.services.projects import ProjectsService
from src.services.task_activity import TaskActivityService
from src.services.task_comments import TaskCommentsService
from src.services.tasks import TasksService
from src.services.wbs_nodes import WbsNodesService


@dataclass(frozen=True)
class Mapping:
    """Одна строка спецификации «доменная ошибка → HTTP-ответ».

    Attributes:
        dependency: Фабрика сервиса, которую подменяет тест.
        service: Класс сервиса для строгой заглушки.
        method: Метод сервиса, поднимающий ошибку.
        error: Сама доменная ошибка.
        request: Метод и путь запроса.
        status: Ожидаемый код ответа.
        payload: Тело запроса, если оно нужно маршруту.
        detail: Фрагмент, который обязан остаться в формулировке ответа.
    """

    dependency: object
    service: type
    method: str
    error: Exception
    request: tuple[str, str]
    status: int
    payload: dict | None = field(default=None)
    detail: str | None = field(default=None)


DOMAIN_MAPPINGS = (
    Mapping(
        dependency=get_projects_service,
        service=ProjectsService,
        method="create_project",
        error=ProjectKeyConflictError(key="PROJ"),
        request=("post", "/api/v1/projects"),
        payload={"key": "PROJ", "name": "Тестовый проект"},
        status=409,
        detail="PROJ",
    ),
    Mapping(
        dependency=get_projects_service,
        service=ProjectsService,
        method="get_project",
        error=ProjectNotFoundError(project_id=999),
        request=("get", "/api/v1/projects/999"),
        status=404,
    ),
    Mapping(
        dependency=get_project_stages_service,
        service=ProjectStagesService,
        method="delete_stage",
        error=ProjectStageHasTasksError(stage_id=2),
        request=("delete", "/api/v1/stages/2"),
        status=409,
    ),
    Mapping(
        dependency=get_project_stages_service,
        service=ProjectStagesService,
        method="delete_stage",
        error=ProjectLastStageDeleteError(stage_id=2),
        request=("delete", "/api/v1/stages/2"),
        status=409,
        detail="последнюю",
    ),
    Mapping(
        dependency=get_tasks_service,
        service=TasksService,
        method="get_task",
        error=TaskNotFoundError(task_id=999),
        request=("get", "/api/v1/tasks/999"),
        status=404,
    ),
    Mapping(
        dependency=get_task_comments_service,
        service=TaskCommentsService,
        method="delete_comment",
        error=TaskCommentNotFoundError(comment_id=999),
        request=("delete", "/api/v1/comments/999"),
        status=404,
    ),
    Mapping(
        dependency=get_wbs_nodes_service,
        service=WbsNodesService,
        method="update_node",
        error=WbsNodeNotFoundError(node_id=999),
        request=("patch", "/api/v1/projects/1/wbs/nodes/999"),
        payload={"title": "Новое название"},
        status=404,
    ),
    Mapping(
        dependency=get_wbs_nodes_service,
        service=WbsNodesService,
        method="move_node",
        error=WbsNodeCycleError(node_id=3, parent_id=7),
        request=("post", "/api/v1/projects/1/wbs/nodes/3/move"),
        payload={"parent_id": 7, "before_id": None},
        status=409,
        detail="подраздел",
    ),
    Mapping(
        dependency=get_wbs_nodes_service,
        service=WbsNodesService,
        method="assign_task",
        error=WbsNodeForeignProjectError(node_id=5, project_id=1),
        request=("post", "/api/v1/projects/1/wbs/tasks/2/assign"),
        payload={"wbs_node_id": 5},
        status=409,
    ),
    Mapping(
        dependency=get_document_catalog_service,
        service=DocumentCatalogService,
        method="create",
        error=CatalogDocumentConflictError(),
        request=("post", "/api/v1/documents"),
        payload={
            "request_id": "00000000-0000-4000-8000-000000000001",
            "project_id": 1,
            "title": "Требования",
        },
        status=409,
    ),
    Mapping(
        dependency=get_document_catalog_service,
        service=DocumentCatalogService,
        method="update",
        error=CatalogDocumentConflictError(),
        request=("patch", "/api/v1/documents/d1_ZG9jdW1lbnQ6MQ"),
        payload={"expected_revision": 1, "title": "Требования"},
        status=409,
    ),
    Mapping(
        dependency=get_document_catalog_service,
        service=DocumentCatalogService,
        method="link",
        error=CatalogDocumentValidationError("Нужен документ того же проекта."),
        request=("post", "/api/v1/documents/d1_ZG9jdW1lbnQ6MQ/tasks/2"),
        payload=None,
        status=422,
    ),
)

# Непредусмотренный сбой сервиса: наружу уходит 500 с непустой, но
# ничего не раскрывающей формулировкой.
SERVICE_FAILURE_MAPPINGS = (
    Mapping(
        dependency=get_dashboard_service,
        service=DashboardService,
        method="get_overview",
        error=DashboardServiceError("Ошибка сценария"),
        request=("get", "/api/v1/dashboard"),
        status=500,
    ),
    Mapping(
        dependency=get_projects_service,
        service=ProjectsService,
        method="get_project_list",
        error=ProjectsServiceError("Ошибка сценария"),
        request=("get", "/api/v1/projects"),
        status=500,
    ),
    Mapping(
        dependency=get_project_stages_service,
        service=ProjectStagesService,
        method="get_stage_list",
        error=ProjectStagesServiceError("Ошибка сценария"),
        request=("get", "/api/v1/projects/1/stages"),
        status=500,
    ),
    Mapping(
        dependency=get_tasks_service,
        service=TasksService,
        method="get_task_list",
        error=TasksServiceError("Ошибка сценария"),
        request=("get", "/api/v1/projects/1/tasks"),
        status=500,
    ),
    Mapping(
        dependency=get_wbs_nodes_service,
        service=WbsNodesService,
        method="get_structure",
        error=WbsNodesServiceError("Ошибка сценария"),
        request=("get", "/api/v1/projects/1/wbs"),
        status=500,
    ),
    Mapping(
        dependency=get_document_catalog_service,
        service=DocumentCatalogService,
        method="list",
        error=DocumentCatalogServiceError("Ошибка сценария"),
        request=("get", "/api/v1/documents?project_id=1"),
        payload=None,
        status=500,
    ),
    Mapping(
        dependency=get_document_catalog_service,
        service=DocumentCatalogService,
        method="unlink",
        error=DocumentCatalogServiceError("Ошибка сценария"),
        request=("delete", "/api/v1/documents/d1_ZG9jdW1lbnQ6MQ/tasks/2"),
        payload=None,
        status=500,
    ),
    Mapping(
        dependency=get_task_comments_service,
        service=TaskCommentsService,
        method="get_comments",
        error=TaskCommentsServiceError("Ошибка сценария"),
        request=("get", "/api/v1/tasks/1/comments"),
        status=500,
    ),
    Mapping(
        dependency=get_task_activity_service,
        service=TaskActivityService,
        method="get_activity",
        error=TaskActivityServiceError("Ошибка сценария"),
        request=("get", "/api/v1/tasks/1/activity"),
        status=500,
    ),
)


async def check(api_client: AsyncClient, mapping: Mapping, current_principal) -> str | None:
    """Проверяет одну строку спецификации и возвращает расхождение."""
    app.dependency_overrides[get_document_catalog_principal] = lambda: current_principal
    service = AsyncMock(spec=mapping.service)
    getattr(service, mapping.method).side_effect = mapping.error
    app.dependency_overrides[mapping.dependency] = lambda: service

    method, path = mapping.request
    request_kwargs = {"json": mapping.payload} if mapping.payload is not None else {}
    response = await getattr(api_client, method)(path, **request_kwargs)

    where = f"{type(mapping.error).__name__} на {method.upper()} {path}"
    if response.status_code != mapping.status:
        return f"{where}: {response.status_code} вместо {mapping.status}"
    body = response.json()
    if not body.get("detail"):
        return f"{where}: пустая формулировка ответа"
    if mapping.detail is not None and mapping.detail not in body["detail"]:
        return f"{where}: в ответе нет «{mapping.detail}» — {body['detail']}"
    return None


@pytest.mark.asyncio
async def test_domain_errors_map_to_their_documented_responses(
    api_client: AsyncClient,
    current_principal,
) -> None:
    """Каждая доменная ошибка отдаёт свой статус и сохраняет формулировку."""
    problems = [
        problem
        for mapping in DOMAIN_MAPPINGS
        if (problem := await check(api_client, mapping, current_principal)) is not None
    ]

    assert not problems, "Доменные ошибки отображены иначе:\n  " + "\n  ".join(problems)


@pytest.mark.asyncio
async def test_unforeseen_service_failure_maps_to_500(
    api_client: AsyncClient, current_principal
) -> None:
    """Сбой сервиса, не перечисленный эндпоинтом, уходит наружу как 500."""
    problems = [
        problem
        for mapping in SERVICE_FAILURE_MAPPINGS
        if (problem := await check(api_client, mapping, current_principal)) is not None
    ]

    assert not problems, "Сбой сервиса отображён иначе:\n  " + "\n  ".join(problems)
