import logging
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile, status
from pydantic import ValidationError

from src.api.v1.responses import (
    CONFLICT_RESPONSE,
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
from src.api.v1.uploads import close_uploads, read_uploads
from src.dependencies.access import (
    require_project_access,
    require_task_access,
    require_task_deletion,
)
from src.dependencies.auth import PrincipalDep, require_write_scope
from src.dependencies.services import (
    AccessServiceDep,
    DocumentLinksServiceDep,
    ProjectQueryServiceDep,
    TaskDescriptionServiceDep,
    TasksServiceDep,
)
from src.dependencies.task_attention import TaskAttentionServiceDep
from src.exceptions.access import AccessServiceError
from src.exceptions.document_links import DocumentLinksServiceError
from src.exceptions.knowledge import KnowledgeProviderError
from src.exceptions.project_stages import ProjectStagesServiceError
from src.exceptions.projects import ProjectsServiceError
from src.exceptions.task_attention import TaskAttentionServiceError
from src.exceptions.tasks import TasksServiceError
from src.exceptions.wbs_nodes import WbsNodesServiceError
from src.schemas.document_links import LinkedDocumentSchema
from src.schemas.enums import TaskPriority
from src.schemas.tasks import (
    MyTaskSchema,
    PersonalTaskCreateSchema,
    ProjectBaselineResultSchema,
    TaskCreateSchema,
    TaskDetailSchema,
    TaskMoveSchema,
    TaskRephraseFile,
    TaskRephraseRequestSchema,
    TaskRephraseResultSchema,
    TaskSchema,
    TaskStatusUpdateSchema,
    TaskUpdateSchema,
)
from src.utils.task_filters import TaskFilters

router = APIRouter(tags=["tasks"])
logger = logging.getLogger(__name__)
MyTasksRole = Literal["all", "doing", "assigned", "watching"]

TaskErrors = (
    TasksServiceError,
    ProjectsServiceError,
    ProjectStagesServiceError,
    WbsNodesServiceError,
)


@router.get(
    path="/projects/{project_id}/tasks",
    dependencies=[Depends(require_project_access)],
    status_code=status.HTTP_200_OK,
    summary="Получить задачи проекта",
    description=(
        "Возвращает задачи проекта. Фильтры: стадия, текст (название, описание, "
        "комментарии, номер), исполнитель, приоритет, открытые/завершённые, срок от и до, "
        "задачи без срока — те же правила, что у MCP-инструмента tt_list_tasks."
    ),
    operation_id="getProjectTasks",
    response_description="Список задач с контекстом комментариев и поиска.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=list[TaskSchema],
)
async def get_tasks(
    project_id: Annotated[int, Path(gt=0, description="Идентификатор проекта.")],
    service: TasksServiceDep,
    stage_id: Annotated[
        int | None,
        Query(gt=0, description="Идентификатор стадии для фильтрации."),
    ] = None,
    search: Annotated[
        str | None,
        Query(max_length=200, description="Поиск по задаче, комментарию или номеру."),
    ] = None,
    assignee: Annotated[
        str | None, Query(max_length=255, description="Имя исполнителя целиком.")
    ] = None,
    priority: Annotated[TaskPriority | None, Query(description="Приоритет задачи.")] = None,
    is_done: Annotated[
        bool | None, Query(description="true — только завершённые, false — только открытые.")
    ] = None,
    due_from: Annotated[date | None, Query(description="Срок не раньше даты.")] = None,
    due_to: Annotated[date | None, Query(description="Срок не позже даты.")] = None,
    without_due_date: Annotated[bool, Query(description="Только задачи без срока.")] = False,
) -> list[TaskSchema]:
    """Получает задачи проекта.

    Args:
        project_id: Идентификатор проекта.
        service: Сервис задач.
        stage_id: Опциональный фильтр стадии.
        search: Опциональная поисковая строка.

    Returns:
        Список задач.

    Raises:
        HTTPException: Если проект не найден или получить задачи не удалось.
    """
    logger.info(
        "🚀 Запрос GET /projects/%s/tasks. stage_id=%s, search=%s.",
        project_id,
        stage_id,
        search,
    )
    try:
        result = await service.get_task_list(
            project_id=project_id,
            stage_id=stage_id,
            search=search,
            filters=TaskFilters(
                assignee=assignee,
                priority=priority.value if priority else None,
                is_done=is_done,
                due_from=due_from,
                due_to=due_to,
                without_due_date=without_due_date,
            ),
        )
        logger.info("✅ Задачи проекта id=%s получены. Найдено: %s.", project_id, len(result))
        return result
    except TaskErrors as error:
        logger.exception("❌ Ошибка GET /projects/%s/tasks. Детали: %s", project_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/projects/{project_id}/tasks",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    status_code=status.HTTP_201_CREATED,
    summary="Создать задачу",
    description="Создаёт задачу в проекте и выдаёт ей сквозной номер вида KEY-42. Необязательный checklist сохраняется вместе с задачей одной транзакцией.",
    operation_id="createProjectTask",
    response_description="Созданная задача.",
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=TaskSchema,
)
async def create_task(
    project_id: Annotated[int, Path(gt=0, description="Идентификатор проекта.")],
    data: TaskCreateSchema,
    principal: PrincipalDep,
    service: TasksServiceDep,
) -> TaskSchema:
    """Создаёт задачу проекта.

    Args:
        project_id: Идентификатор проекта.
        data: Поля новой задачи.
        service: Сервис задач.

    Returns:
        Созданная задача.

    Raises:
        HTTPException: Если проект или стадия не найдены, либо создать задачу не удалось.
    """
    logger.info("🚀 Запрос POST /projects/%s/tasks. Заголовок: %s.", project_id, data.title)
    try:
        result = await service.create_task(
            project_id=project_id,
            data=data.model_dump(),
            created_by_user_id=principal.user_id,
        )
        logger.info("✅ Задача создана. id=%s, ключ=%s.", result.id, result.key)
        return result
    except TaskErrors as error:
        logger.exception("❌ Ошибка POST /projects/%s/tasks. Детали: %s", project_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/projects/{project_id}/tasks/rephrase",
    dependencies=[Depends(require_project_access)],
    status_code=status.HTTP_200_OK,
    summary="Переформулировать описание задачи",
    description=(
        "Возвращает улучшенный черновик с учётом проекта, других задач, выбранных "
        "документов и ещё не загруженных файлов. Ничего не сохраняет."
    ),
    operation_id="rephraseTaskDescription",
    responses={422: VALIDATION_RESPONSE, 503: SERVER_ERROR_RESPONSE},
    response_model=TaskRephraseResultSchema,
)
async def rephrase_task_description(
    project_id: Annotated[int, Path(gt=0, description="Идентификатор проекта.")],
    payload: Annotated[str, Form(description="JSON с черновиком и document_ids.")],
    service: TaskDescriptionServiceDep,
    files: Annotated[
        list[UploadFile] | None,
        File(description="Новые документы, выбранные в форме задачи."),
    ] = None,
) -> TaskRephraseResultSchema:
    """Переформулирует черновик, не изменяя сохранённые данные.

    Транспорт разбирает multipart и читает файлы с ограничением; сколько
    файлов допустимо, какие расширения индексируются и что считать
    пустым файлом — доменные правила, и они остаются в сервисе.
    """
    uploads = files or []
    try:
        try:
            data = TaskRephraseRequestSchema.model_validate_json(payload)
        except ValidationError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Некорректные данные для переформулирования.",
            ) from error

        read_files = await read_uploads(uploads, max_size=service.max_file_size)
        return await service.rephrase(
            project_id=project_id,
            data=data,
            files=[
                TaskRephraseFile(name=item.name, content=item.content) for item in read_files
            ],
        )
    except (TasksServiceError, KnowledgeProviderError) as error:
        logger.exception(
            "❌ Не удалось переформулировать описание в проекте id=%s.",
            project_id,
        )
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    finally:
        await close_uploads(uploads)


@router.get(
    path="/tasks/by-key/{task_key}",
    status_code=status.HTTP_200_OK,
    summary="Получить задачу по ключу",
    description=(
        "Возвращает задачу по отображаемому ключу вида PROJ-142 — так её называют "
        "люди и агенты (MCP-инструменты работают с ключами). Задача чужого или "
        "несуществующего проекта неотличима: 404."
    ),
    operation_id="getTaskByKey",
    response_description="Задача.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=TaskDetailSchema,
)
async def get_task_by_key(
    task_key: Annotated[
        str, Path(min_length=3, max_length=64, description="Ключ задачи, например PROJ-142.")
    ],
    principal: PrincipalDep,
    query: ProjectQueryServiceDep,
    access: AccessServiceDep,
    service: TasksServiceDep,
) -> TaskSchema:
    """Находит задачу по ключу с той же проверкой участия, что и по id."""
    project_key, _, number = task_key.strip().rpartition("-")
    if not project_key or not number.isdigit():
        raise HTTPException(status_code=422, detail="Ключ задачи должен иметь вид PROJ-142.")
    try:
        project_id = await query.resolve_project_id(project_key=project_key)
        # Доступ к проекту проверяется раньше номера: иначе по разнице ответов
        # было бы видно, есть ли в чужом проекте задача с таким номером.
        await access.ensure_project_access(project_id=project_id, user_id=principal.user_id)
        resolved = await query.resolve_task(
            project_id=project_id, project_key=project_key.strip().upper(), number=int(number)
        )
        return await service.get_task(resolved.task_id)
    except AccessServiceError as error:
        raise HTTPException(status_code=404, detail="Задача не найдена.") from error
    except TaskErrors as error:
        if error.status_code == 404:
            raise HTTPException(status_code=404, detail="Задача не найдена.") from error
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/tasks/{task_id}",
    dependencies=[Depends(require_task_access)],
    status_code=status.HTTP_200_OK,
    summary="Получить задачу",
    description="Возвращает карточку задачи по идентификатору.",
    operation_id="getTask",
    response_description="Карточка задачи.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=TaskDetailSchema,
)
async def get_task(
    task_id: Annotated[int, Path(gt=0, description="Идентификатор задачи.")],
    service: TasksServiceDep,
) -> TaskDetailSchema:
    """Получает задачу по идентификатору.

    Args:
        task_id: Идентификатор задачи.
        service: Сервис задач.

    Returns:
        Карточка задачи.

    Raises:
        HTTPException: Если задача не найдена или получить её не удалось.
    """
    logger.info("🚀 Запрос GET /tasks/%s.", task_id)
    try:
        result = await service.get_task(task_id=task_id)
        logger.info("✅ Задача id=%s получена.", task_id)
        return result
    except TaskErrors as error:
        logger.exception("❌ Ошибка GET /tasks/%s. Детали: %s", task_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.patch(
    path="/tasks/{task_id}",
    dependencies=[Depends(require_write_scope), Depends(require_task_access)],
    status_code=status.HTTP_200_OK,
    summary="Изменить задачу",
    description="Частично обновляет поля задачи и фиксирует значимые изменения в истории. Передайте expected_revision из карточки задачи для защиты общих полей и назначений: устаревшая версия возвращает 409 без изменений. Без expected_revision сохраняется прежняя запись без проверки общей версии. Для изменения checklist передайте весь чек-лист и текущую checklist_revision; эта версия проверяется отдельно. null удаляет чек-лист, отсутствие поля сохраняет его.",
    operation_id="updateTask",
    response_description="Обновлённая задача с актуальной revision и новой checklist_revision, если чек-лист изменён.",
    responses={404: NOT_FOUND_RESPONSE, 409: CONFLICT_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=TaskSchema,
)
async def update_task(
    task_id: Annotated[int, Path(gt=0, description="Идентификатор задачи.")],
    data: TaskUpdateSchema,
    principal: PrincipalDep,
    service: TasksServiceDep,
) -> TaskSchema:
    """Обновляет задачу.

    Args:
        task_id: Идентификатор задачи.
        data: Изменяемые поля задачи.
        service: Сервис задач.

    Returns:
        Обновлённая задача.

    Raises:
        HTTPException: Если задача не найдена или обновить её не удалось.
    """
    logger.info("🚀 Запрос PATCH /tasks/%s.", task_id)
    try:
        result = await service.update_task(
            task_id=task_id,
            data=data.model_dump(exclude_unset=True),
            updated_by_user_id=principal.user_id,
        )
        logger.info("✅ Задача id=%s обновлена.", task_id)
        return result
    except TaskErrors as error:
        logger.exception("❌ Ошибка PATCH /tasks/%s. Детали: %s", task_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/tasks/{task_id}/baseline",
    dependencies=[Depends(require_write_scope), Depends(require_task_access)],
    status_code=status.HTTP_200_OK,
    summary="Зафиксировать baseline задачи",
    description="Копирует текущие плановые даты в отдельный утверждённый baseline.",
    operation_id="fixTaskBaseline",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=TaskSchema,
)
async def fix_task_baseline(
    task_id: Annotated[int, Path(gt=0, description="Идентификатор задачи.")],
    service: TasksServiceDep,
) -> TaskSchema:
    """Фиксирует текущий план задачи как baseline."""
    logger.info("🚀 Запрос POST /tasks/%s/baseline.", task_id)
    try:
        return await service.fix_baseline(task_id=task_id)
    except TaskErrors as error:
        logger.exception("❌ Ошибка POST /tasks/%s/baseline. Детали: %s", task_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.patch(
    path="/tasks/{task_id}/move",
    dependencies=[Depends(require_write_scope), Depends(require_task_access)],
    status_code=status.HTTP_200_OK,
    summary="Переместить задачу",
    description="Переносит задачу в другую стадию доски с сохранением позиции.",
    operation_id="moveTask",
    response_description="Перемещённая задача.",
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=TaskSchema,
)
async def move_task(
    task_id: Annotated[int, Path(gt=0, description="Идентификатор задачи.")],
    data: TaskMoveSchema,
    service: TasksServiceDep,
) -> TaskSchema:
    """Перемещает задачу по доске.

    Args:
        task_id: Идентификатор задачи.
        data: Целевая стадия и позиция.
        service: Сервис задач.

    Returns:
        Перемещённая задача.

    Raises:
        HTTPException: Если задача или стадия не найдены, либо переместить не удалось.
    """
    logger.info("🚀 Запрос PATCH /tasks/%s/move. Стадия: %s.", task_id, data.stage_id)
    try:
        result = await service.move_task(
            task_id=task_id,
            stage_id=data.stage_id,
            position=data.position,
        )
        logger.info("✅ Задача id=%s перемещена в стадию %s.", task_id, data.stage_id)
        return result
    except TaskErrors as error:
        logger.exception("❌ Ошибка PATCH /tasks/%s/move. Детали: %s", task_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.delete(
    path="/tasks/{task_id}",
    dependencies=[Depends(require_write_scope), Depends(require_task_deletion)],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить задачу",
    description="Удаляет задачу вместе с комментариями, историей и файлами.",
    operation_id="deleteTask",
    response_description="Задача удалена.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
)
async def delete_task(
    task_id: Annotated[int, Path(gt=0, description="Идентификатор задачи.")],
    service: TasksServiceDep,
) -> None:
    """Удаляет задачу.

    Args:
        task_id: Идентификатор задачи.
        service: Сервис задач.

    Returns:
        ``None`` после успешного удаления.

    Raises:
        HTTPException: Если задача не найдена или удалить её не удалось.
    """
    logger.info("🚀 Запрос DELETE /tasks/%s.", task_id)
    try:
        await service.delete_task(task_id=task_id)
        logger.info("✅ Задача id=%s удалена.", task_id)
    except TaskErrors as error:
        logger.exception("❌ Ошибка DELETE /tasks/%s. Детали: %s", task_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/tasks/{task_id}/links",
    dependencies=[Depends(require_task_access)],
    status_code=status.HTTP_200_OK,
    summary="Получить документы задачи",
    description="Возвращает документы, связанные с задачей.",
    operation_id="getTaskLinks",
    response_description="Связанные документы.",
    responses={404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=list[LinkedDocumentSchema],
)
async def get_task_links(
    task_id: Annotated[int, Path(gt=0, description="Идентификатор задачи.")],
    service: DocumentLinksServiceDep,
) -> list[LinkedDocumentSchema]:
    """Получает связанные с задачей документы.

    Args:
        task_id: Идентификатор задачи.
        service: Сервис связей документов.

    Returns:
        Список связанных документов.

    Raises:
        HTTPException: Если задача не найдена или получить связи не удалось.
    """
    logger.info("🚀 Запрос GET /tasks/%s/links.", task_id)
    try:
        result = await service.get_links_for_task(task_id=task_id)
        logger.info("✅ Связи задачи id=%s получены. Найдено: %s.", task_id, len(result))
        return result
    except (DocumentLinksServiceError, TasksServiceError) as error:
        logger.exception("❌ Ошибка GET /tasks/%s/links. Детали: %s", task_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/projects/{project_id}/baseline",
    dependencies=[Depends(require_write_scope), Depends(require_project_access)],
    status_code=status.HTTP_200_OK,
    summary="Зафиксировать план проекта",
    description=(
        "Копирует текущие плановые даты в baseline всем задачам проекта, у которых есть "
        "хотя бы одна дата, одной транзакцией. Права — как у фиксации baseline задачи. "
        "С dry_run=true только считает, у скольких задач план изменится, и ничего не пишет."
    ),
    operation_id="fixProjectBaseline",
    responses={
        403: FORBIDDEN_RESPONSE,
        404: NOT_FOUND_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=ProjectBaselineResultSchema,
)
async def fix_project_baseline(
    project_id: Annotated[int, Path(gt=0, description="Идентификатор проекта.")],
    service: TasksServiceDep,
    dry_run: Annotated[
        bool, Query(description="Только посчитать задачи, ничего не записывая.")
    ] = False,
) -> ProjectBaselineResultSchema:
    """Фиксирует текущий план всех задач проекта как baseline."""
    logger.info("🚀 Запрос POST /projects/%s/baseline dry_run=%s.", project_id, dry_run)
    try:
        return await service.fix_project_baseline(project_id=project_id, dry_run=dry_run)
    except TaskErrors as error:
        logger.exception("❌ Ошибка POST /projects/%s/baseline. Детали: %s", project_id, error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/tasks",
    dependencies=[Depends(require_write_scope)],
    status_code=status.HTTP_201_CREATED,
    summary="Создать разовую задачу",
    description=(
        "Создаёт задачу без проекта и без потока с номером вида T-123. Постановщик — "
        "текущий пользователь; исполнитель и наблюдатели — любые сотрудники, "
        "известные трекеру."
    ),
    operation_id="createPersonalTask",
    responses={
        403: FORBIDDEN_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=TaskSchema,
)
async def create_personal_task(
    data: PersonalTaskCreateSchema, principal: PrincipalDep, service: TasksServiceDep
) -> TaskSchema:
    """Создаёт разовую задачу."""
    logger.info("🚀 Запрос POST /tasks. Заголовок: %s.", data.title)
    try:
        result = await service.create_personal_task(
            data=data.model_dump(exclude={"stage_id", "role"}),
            created_by_user_id=principal.user_id,
        )
        logger.info("✅ Разовая задача создана. id=%s, ключ=%s.", result.id, result.key)
        return result
    except TaskErrors as error:
        logger.exception("❌ Ошибка POST /tasks.")
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/my/tasks",
    status_code=status.HTTP_200_OK,
    summary="Мои задачи",
    description=(
        "Всё, что на текущем пользователе: проектные задачи, задачи потоков и "
        "разовые. role: all — все роли, doing — исполнитель, assigned — "
        "постановщик, watching — наблюдатель. Закрытые по умолчанию скрыты."
    ),
    operation_id="getMyTasks",
    responses={422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=list[MyTaskSchema],
)
async def get_my_tasks(
    principal: PrincipalDep,
    service: TasksServiceDep,
    attention_service: TaskAttentionServiceDep,
    role: Annotated[MyTasksRole, Query(description="Моя роль в задаче.")] = "all",
    include_closed: Annotated[
        bool, Query(description="Показывать выполненные и отменённые.")
    ] = False,
    attention: Annotated[
        Literal["new", "overdue", "updated", "unread"] | None,
        Query(description="Непросмотренные события: фильтр применяется до ограничения списка."),
    ] = None,
) -> list[MyTaskSchema]:
    """Возвращает задачи текущего пользователя."""
    logger.info("🚀 Запрос GET /my/tasks. role=%s, include_closed=%s.", role, include_closed)
    try:
        result = await service.get_my_tasks(
            user_id=principal.user_id,
            role=role,
            include_closed=include_closed,
            **(
                {
                    "attention_task_ids": await attention_service.filter_ids(
                        principal.user_id, attention
                    )
                }
                if attention is not None
                else {}
            ),
        )
        logger.info("✅ Мои задачи: %s.", len(result))
        return result
    except (*TaskErrors, TaskAttentionServiceError) as error:
        logger.exception("❌ Ошибка GET /my/tasks.")
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.patch(
    path="/tasks/{task_id}/status",
    dependencies=[Depends(require_write_scope), Depends(require_task_access)],
    status_code=status.HTTP_200_OK,
    summary="Сменить статус задачи вне проекта",
    description=(
        "Меняет статус задачи потока или разовой задачи. У проектной задачи "
        "состояние задаёт стадия — для неё 409."
    ),
    operation_id="changeTaskStatus",
    responses={
        404: NOT_FOUND_RESPONSE,
        409: CONFLICT_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=TaskSchema,
)
async def change_task_status(
    task_id: Annotated[int, Path(gt=0, description="Идентификатор задачи.")],
    data: TaskStatusUpdateSchema,
    service: TasksServiceDep,
) -> TaskSchema:
    """Меняет статус задачи вне проекта."""
    logger.info("🚀 Запрос PATCH /tasks/%s/status. Статус: %s.", task_id, data.status.value)
    try:
        result = await service.change_status(task_id=task_id, status=data.status)
        logger.info("✅ Статус задачи id=%s изменён.", task_id)
        return result
    except TaskErrors as error:
        logger.exception("❌ Ошибка PATCH /tasks/%s/status.", task_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
