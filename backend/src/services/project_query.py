"""Read-сценарии проекта, общие для транспортов.

MCP-инструменты чтения раньше сами ходили в репозитории, фильтровали и
агрегировали. Из-за этого правила «что считать завершённой задачей» и
«как выглядит карточка проекта» существовали в транспорте — там, где их
не увидит ни один другой канал.

Здесь те же сценарии описаны один раз и отдают неизменяемые DTO. Ни ORM,
ни сессия наружу не поднимаются.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

from src.db.models.projects import Project
from src.exceptions.base import RepositoryError
from src.exceptions.projects import ProjectNotFoundError, ProjectsServiceError
from src.exceptions.task_comments import TaskCommentNotFoundError
from src.exceptions.tasks import TaskNotFoundError
from src.knowledge.catalog import SourceType, build_catalog
from src.knowledge.context import read_catalog, retrieve
from src.knowledge.documents import build_wbs_paths
from src.schemas.knowledge import KnowledgeReadRequest
from src.services.db_scope import ProjectQueryScope, ProjectQueryScopeFactory
from src.utils.checklists import checklist_context
from src.utils.task_filters import TaskFilters, task_matches

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProjectSummaryDto:
    """Краткая карточка проекта."""

    project_key: str
    name: str
    status: str
    start_date: date | None
    due_date: date | None


@dataclass(frozen=True, slots=True)
class ProjectStageDto:
    """Стадия проекта вместе с числом задач в ней."""

    name: str
    is_done_stage: bool
    task_count: int


@dataclass(frozen=True, slots=True)
class ProjectOverviewDto:
    """Подробная карточка проекта со стадиями."""

    summary: ProjectSummaryDto
    description: str | None
    total_tasks: int
    stages: list[ProjectStageDto]


@dataclass(frozen=True, slots=True)
class TaskSummaryDto:
    """Краткая карточка задачи."""

    task_key: str
    title: str
    stage: str | None
    is_done: bool
    priority: str
    assignee: str | None
    due_date: date | None
    checklist: dict | None = None
    task_id: int | None = None
    start_date: date | None = None


@dataclass(frozen=True, slots=True)
class TaskPageDto:
    """Страница списка задач: элементы и сколько всего подошло."""

    items: list[TaskSummaryDto]
    total: int
    offset: int
    limit: int

    @property
    def has_more(self) -> bool:
        """Есть ли задачи за пределами страницы."""
        return self.offset + len(self.items) < self.total


@dataclass(frozen=True, slots=True)
class TaskDetailsDto:
    """Подробная карточка задачи."""

    summary: TaskSummaryDto
    role: str | None
    wbs_path: str | None
    description: str | None
    comment_count: int
    revision: int = 1
    start_date: date | None = None


@dataclass(frozen=True, slots=True)
class CommentDto:
    """Комментарий задачи."""

    task_key: str
    author: str
    created_at: datetime
    body: str | None
    comment_id: int | None = None


@dataclass(frozen=True, slots=True)
class MilestoneDto:
    """Веха проекта, включая системную веху его дедлайна."""

    title: str
    due_date: date
    status: str
    description: str | None
    is_system: bool


@dataclass(frozen=True, slots=True)
class StageRefDto:
    """Стадия проекта, найденная по названию."""

    stage_id: int
    name: str
    is_done_stage: bool


@dataclass(frozen=True, slots=True)
class ResolvedTask:
    """Задача, найденная по отображаемому ключу."""

    task_id: int
    project_id: int
    task_key: str


def build_task_key(project_key: str, number: int) -> str:
    """Возвращает отображаемый идентификатор задачи вида ``PROJ-142``."""
    return f"{project_key}-{number}"


def resolve_stage_reference(stages: list[StageRefDto], stage_name: str) -> StageRefDto:
    """Находит стадию по имени одинаково для первого вызова и обычного чтения.

    Args:
        stages: Стадии уже проверенного проекта.
        stage_name: Название без учёта регистра и внешних пробелов.
    Returns:
        Стадия проекта.
    Raises:
        UnknownStageError: Если название отсутствует в проекте.
    """
    wanted = stage_name.strip().casefold()
    for stage in stages:
        if stage.name.casefold() == wanted:
            return stage
    raise UnknownStageError(stage_name=stage_name, known=[item.name for item in stages])


class ProjectQueryService:
    """Отвечает на вопросы о проекте, не изменяя его состояние."""

    def __init__(self, *, scope: ProjectQueryScopeFactory) -> None:
        """Создаёт сервис чтения проекта.

        Args:
            scope: Фабрика короткой области работы с базой.
        """
        self.scope = scope

    async def read_knowledge(self, *, project_id: int, request: KnowledgeReadRequest) -> dict:
        """Читает любой источник или связь в границах разрешённого проекта."""
        try:
            async with self.scope() as db:
                catalog = build_catalog(project_id, await db.sources.get_project_rows(project_id))
                if not catalog.sources:
                    raise ProjectNotFoundError(project_id=project_id)
                hits = (
                    await db.sources.search(
                        project_id,
                        request.query or "",
                        entity_type=request.entity_type,
                        limit=request.limit + 1,
                        offset=request.offset,
                    )
                    if request.name == "search_sources"
                    else None
                )
                return read_catalog(catalog, request, search_hits=hits)
        except RepositoryError as error:
            raise ProjectsServiceError(str(error)) from error

    async def search_knowledge(
        self,
        *,
        project_id: int,
        query: str,
        semantic_hits: list,
        entity_type: SourceType | None,
        limit: int,
        target_chars: int,
        overlap_chars: int,
    ) -> list[dict]:
        """Общий гибридный поиск с проверкой каждого типа источника по PostgreSQL."""
        try:
            async with self.scope() as db:
                catalog = build_catalog(project_id, await db.sources.get_project_rows(project_id))
                if not catalog.sources:
                    raise ProjectNotFoundError(project_id=project_id)
                fts_hits = await db.sources.search(
                    project_id, query, entity_type=entity_type, limit=limit
                )
            return retrieve(
                catalog,
                fts_hits=fts_hits,
                semantic_hits=semantic_hits,
                query=query,
                target_chars=target_chars,
                overlap_chars=overlap_chars,
                limit=limit,
            )
        except RepositoryError as error:
            raise ProjectsServiceError(str(error)) from error

    async def list_accessible_projects(self, *, user_id: int) -> list[ProjectSummaryDto]:
        """Возвращает проекты, доступные пользователю.

        Args:
            user_id: Пользователь запроса.

        Returns:
            Краткие карточки доступных проектов.

        Raises:
            ProjectsServiceError: Если прочитать проекты не удалось.
        """
        try:
            async with self.scope() as db:
                allowed = await db.members.get_project_ids_for_user(user_id=user_id)
                projects = await db.projects.get_all()
        except RepositoryError as error:
            logger.error("❌ Не удалось получить список проектов.", exc_info=True)
            raise ProjectsServiceError(str(error)) from error
        return [_project_summary(item) for item in projects if item.id in allowed]

    async def resolve_project_id(self, *, project_key: str) -> int:
        """Находит проект по отображаемому ключу.

        Args:
            project_key: Ключ проекта, например ``PROJ``.

        Returns:
            Идентификатор проекта.

        Raises:
            ProjectNotFoundError: Если проект с таким ключом не найден.
            ProjectsServiceError: Если прочитать проект не удалось.
        """
        normalized = (project_key or "").strip().upper()
        if not normalized:
            raise ProjectNotFoundError(project_id=0)
        try:
            async with self.scope() as db:
                project = await db.projects.get_by_key(normalized)
        except RepositoryError as error:
            logger.error("❌ Не удалось получить проект %s.", normalized, exc_info=True)
            raise ProjectsServiceError(str(error)) from error
        if project is None:
            raise ProjectNotFoundError(project_id=0)
        return project.id

    async def resolve_task(self, *, project_id: int, project_key: str, number: int) -> ResolvedTask:
        """Находит задачу проекта по её номеру.

        Args:
            project_id: Проект задачи.
            project_key: Ключ проекта для сборки отображаемого ключа.
            number: Номер задачи внутри проекта.

        Returns:
            Идентификаторы задачи и её отображаемый ключ.

        Raises:
            TaskNotFoundError: Если задача не найдена в проекте.
            ProjectsServiceError: Если прочитать задачи не удалось.
        """
        try:
            async with self.scope() as db:
                task = await db.tasks.get_by_project_number(project_id=project_id, number=number)
        except RepositoryError as error:
            logger.error("❌ Не удалось получить задачу проекта id=%s.", project_id, exc_info=True)
            raise ProjectsServiceError(str(error)) from error
        if task is None:
            raise TaskNotFoundError(task_id=number)
        return ResolvedTask(
            task_id=task.id,
            project_id=project_id,
            task_key=build_task_key(project_key, task.number),
        )

    async def get_project_overview(self, *, project_id: int) -> ProjectOverviewDto:
        """Возвращает карточку проекта со стадиями и числом задач.

        Raises:
            ProjectNotFoundError: Если проект не найден.
            ProjectsServiceError: Если прочитать состав проекта не удалось.
        """
        try:
            async with self.scope() as db:
                project = await self._require_project(db, project_id=project_id)
                stages = await db.stages.get_by_project(project_id)
                tasks = await db.tasks.get_by_project(project_id=project_id)
        except RepositoryError as error:
            logger.error("❌ Не удалось получить состав проекта id=%s.", project_id, exc_info=True)
            raise ProjectsServiceError(str(error)) from error

        counts = Counter(task.stage_id for task in tasks)
        return ProjectOverviewDto(
            summary=_project_summary(project),
            description=project.description_md,
            total_tasks=len(tasks),
            stages=[
                ProjectStageDto(
                    name=stage.name,
                    is_done_stage=stage.is_done_stage,
                    task_count=counts.get(stage.id, 0),
                )
                for stage in stages
            ],
        )

    async def list_tasks(
        self,
        *,
        project_id: int,
        stage_name: str | None = None,
        assignee: str | None = None,
        only_open: bool = False,
        limit: int,
        offset: int = 0,
        query: str | None = None,
        filters: TaskFilters | None = None,
    ) -> TaskPageDto:
        """Возвращает страницу задач проекта с фильтрами.

        Args:
            project_id: Проект.
            stage_name: Название стадии.
            assignee: Имя исполнителя целиком.
            only_open: Только незавершённые.
            limit: Размер страницы.
            offset: Сколько подошедших задач пропустить.
            query: Текст — лексический поиск по задачам и комментариям.
            filters: Остальные фильтры (приоритет, сроки, завершённость).

        Raises:
            ProjectStageNotFoundError: Если стадия с таким названием не найдена.
            ProjectsServiceError: Если прочитать задачи не удалось.
        """
        text = query.strip() if query else ""
        try:
            async with self.scope() as db:
                project = await self._require_project(db, project_id=project_id)
                stages = await db.stages.get_by_project(project_id)
                matching_ids = (
                    await db.tasks.search_ids(project_id=project_id, search=text) if text else None
                )
                tasks = (
                    []
                    if matching_ids is not None and not matching_ids
                    else await db.tasks.get_by_project(project_id=project_id, task_ids=matching_ids)
                )
        except RepositoryError as error:
            logger.error("❌ Не удалось получить задачи проекта id=%s.", project_id, exc_info=True)
            raise ProjectsServiceError(str(error)) from error

        stage_by_id = {item.id: item for item in stages}
        if stage_name is not None:
            wanted = stage_name.strip().casefold()
            matched = {item.id for item in stages if item.name.casefold() == wanted}
            if not matched:
                raise UnknownStageError(
                    stage_name=stage_name,
                    known=[item.name for item in stages],
                )
            tasks = [task for task in tasks if task.stage_id in matched]
        combined = filters or TaskFilters()
        if assignee is not None or only_open:
            combined = TaskFilters(
                assignee=assignee if assignee is not None else combined.assignee,
                priority=combined.priority,
                is_done=False if only_open else combined.is_done,
                due_from=combined.due_from,
                due_to=combined.due_to,
                without_due_date=combined.without_due_date,
            )
        done_stage_ids = {item.id for item in stages if item.is_done_stage}
        tasks = [
            task for task in tasks if task_matches(task, combined, done_stage_ids=done_stage_ids)
        ]
        page = tasks[offset : offset + limit]
        return TaskPageDto(
            items=[
                _task_summary(task, project_key=project.key, stage=stage_by_id.get(task.stage_id))
                for task in page
            ],
            total=len(tasks),
            offset=offset,
            limit=limit,
        )

    async def get_task_details(self, *, task_id: int) -> TaskDetailsDto:
        """Возвращает подробную карточку задачи."""
        try:
            async with self.scope() as db:
                task = await db.tasks.get_by_id(task_id=task_id)
                if task is None:
                    raise TaskNotFoundError(task_id=task_id)
                project = await self._require_project(db, project_id=task.project_id)
                stages = await db.stages.get_by_project(task.project_id)
                comments = await db.comments.get_for_task(task.id)
                wbs_path = None
                if task.wbs_node_id is not None:
                    nodes = await db.wbs_nodes.get_by_project(task.project_id)
                    wbs_path = build_wbs_paths(nodes).get(task.wbs_node_id)
        except RepositoryError as error:
            logger.error("❌ Не удалось получить задачу id=%s.", task_id, exc_info=True)
            raise ProjectsServiceError(str(error)) from error

        stage_by_id = {item.id: item for item in stages}
        return TaskDetailsDto(
            summary=_task_summary(
                task,
                project_key=project.key,
                stage=stage_by_id.get(task.stage_id),
            ),
            role=task.role.value if task.role else None,
            wbs_path=wbs_path,
            description=task.description_md,
            comment_count=len(comments),
            revision=getattr(task, "revision", 1),
            start_date=task.start_date,
        )

    async def list_comments(self, *, task_id: int, task_key: str, limit: int) -> list[CommentDto]:
        """Возвращает комментарии задачи в порядке добавления."""
        try:
            async with self.scope() as db:
                comments = await db.comments.get_for_task(task_id)
        except RepositoryError as error:
            logger.error("❌ Не удалось получить комментарии задачи id=%s.", task_id, exc_info=True)
            raise ProjectsServiceError(str(error)) from error
        return [
            CommentDto(
                task_key=task_key,
                author=comment.author_name,
                created_at=comment.created_at,
                body=comment.body_md,
                comment_id=comment.id,
            )
            for comment in comments[:limit]
        ]

    async def get_comment(
        self, *, task_id: int, project_id: int, task_key: str, comment_id: int
    ) -> CommentDto:
        """Возвращает полный комментарий в проверенном контексте задачи.

        Args:
            task_id: Задача, доступ к которой проверен вызывающим сервисом доступа.
            project_id: Проверенный проект; задача должна всё ещё принадлежать ему.
            task_key: Отображаемый ключ задачи.
            comment_id: Идентификатор комментария из списка задачи.

        Returns:
            Комментарий с исходным текстом без нормализации и обрезки.

        Raises:
            TaskNotFoundError: Если задача удалена или перемещена в другой контекст.
            TaskCommentNotFoundError: Если комментарий отсутствует или из другой задачи.
            ProjectsServiceError: Если прочитать комментарий не удалось.
        """
        try:
            async with self.scope() as db:
                task = await db.tasks.get_by_id(task_id=task_id)
                if task is None or task.project_id != project_id:
                    raise TaskNotFoundError(task_id=task_id)
                comment = await db.comments.get_by_id(comment_id=comment_id)
                if comment is None or comment.task_id != task_id:
                    raise TaskCommentNotFoundError(comment_id)
                return CommentDto(
                    task_key=task_key,
                    author=comment.author_name,
                    created_at=comment.created_at,
                    body=comment.body_md,
                    comment_id=comment.id,
                )
        except RepositoryError as error:
            logger.error(
                "❌ Не удалось получить комментарий id=%s задачи id=%s.",
                comment_id, task_id, exc_info=True,
            )
            raise ProjectsServiceError(str(error)) from error

    async def search_tasks(
        self,
        *,
        project_id: int,
        query: str,
        limit: int,
    ) -> list[TaskSummaryDto]:
        """Ищет задачи проекта лексическим поиском PostgreSQL."""
        try:
            async with self.scope() as db:
                project = await self._require_project(db, project_id=project_id)
                matching_ids = await db.tasks.search_ids(
                    project_id=project_id,
                    search=query.strip(),
                )
                if not matching_ids:
                    return []
                stages = await db.stages.get_by_project(project_id)
                tasks = await db.tasks.get_by_project(
                    project_id=project_id,
                    task_ids=matching_ids,
                )
        except RepositoryError as error:
            logger.error("❌ Не удалось выполнить поиск задач.", exc_info=True)
            raise ProjectsServiceError(str(error)) from error

        stage_by_id = {item.id: item for item in stages}
        return [
            _task_summary(task, project_key=project.key, stage=stage_by_id.get(task.stage_id))
            for task in tasks[:limit]
        ]

    async def list_milestones(self, *, project_id: int, limit: int) -> list[MilestoneDto]:
        """Возвращает вехи проекта вместе с системной вехой его дедлайна.

        Дедлайн проекта — не запись в таблице вех, но для пользователя это
        такая же веха. Склейка двух источников принадлежит сервису: иначе
        каждый транспорт собирал бы её сам и по-своему.
        """
        try:
            async with self.scope() as db:
                project = await self._require_project(db, project_id=project_id)
                milestones = await db.milestones.get_by_project(project_id)
        except RepositoryError as error:
            logger.error("❌ Не удалось получить вехи проекта id=%s.", project_id, exc_info=True)
            raise ProjectsServiceError(str(error)) from error

        result = [
            MilestoneDto(
                title=item.title,
                due_date=item.due_date,
                status=item.status.value,
                description=item.description_md,
                is_system=False,
            )
            for item in milestones[:limit]
        ]
        if project.due_date is not None:
            result.append(
                MilestoneDto(
                    title="Дедлайн проекта",
                    due_date=project.due_date,
                    status=("ACHIEVED" if str(project.status.value) == "COMPLETED" else "PLANNED"),
                    description=None,
                    is_system=True,
                )
            )
        return result

    async def resolve_stage(self, *, project_id: int, stage_name: str) -> StageRefDto:
        """Находит стадию проекта по названию без учёта регистра.

        Правило поиска стадии одинаково для всех каналов, поэтому живёт
        здесь, а не в обработчике инструмента.

        Raises:
            UnknownStageError: Если стадии с таким названием нет.
            ProjectsServiceError: Если прочитать стадии не удалось.
        """
        try:
            async with self.scope() as db:
                stages = await db.stages.get_by_project(project_id)
        except RepositoryError as error:
            logger.error("❌ Не удалось получить стадии проекта id=%s.", project_id, exc_info=True)
            raise ProjectsServiceError(str(error)) from error

        return resolve_stage_reference(
            [StageRefDto(stage.id, stage.name, stage.is_done_stage) for stage in stages],
            stage_name,
        )

    async def get_project_key(self, *, project_id: int) -> str:
        """Возвращает отображаемый ключ проекта."""
        async with self.scope() as db:
            project = await self._require_project(db, project_id=project_id)
            return project.key

    async def get_task_keys(self, *, project_id: int, task_ids: set[int]) -> dict[int, str]:
        """Разрешает ключи связанных задач одним пакетным чтением.

        Args:
            project_id: Доступный проект.
            task_ids: Идентификаторы связанных задач.

        Returns:
            Ключи только существующих задач указанного проекта.

        Raises:
            ProjectsServiceError: Ошибка чтения связей.
        """
        try:
            async with self.scope() as db:
                project = await self._require_project(db, project_id=project_id)
                tasks = await db.tasks.get_by_ids(task_ids)
                return {
                    task.id: build_task_key(project.key, task.number)
                    for task in tasks
                    if task.project_id == project_id
                }
        except RepositoryError as error:
            logger.exception(
                "❌ Не удалось получить ключи связанных задач проекта id=%s.", project_id
            )
            raise ProjectsServiceError(str(error)) from error

    @staticmethod
    async def _require_project(db: ProjectQueryScope, *, project_id: int) -> Project:
        """Возвращает проект или поднимает доменную ошибку."""
        project = await db.projects.get_by_id(project_id=project_id)
        if project is None:
            raise ProjectNotFoundError(project_id=project_id)
        return project


class UnknownStageError(ProjectsServiceError):
    """Стадия с таким названием в проекте не найдена.

    Список известных стадий возвращается вместе с ошибкой: без него
    вызывающему пришлось бы угадывать написание.
    """

    def __init__(self, *, stage_name: str, known: list[str]) -> None:
        self.stage_name = stage_name
        self.known = known
        super().__init__(error_details=f"Стадия {stage_name!r} не найдена.")

    @property
    def detail(self) -> str:
        return f"Стадия не найдена. Доступные стадии: {', '.join(self.known)}."


def _project_summary(project) -> ProjectSummaryDto:
    """Собирает краткую карточку проекта из модели."""
    return ProjectSummaryDto(
        project_key=project.key,
        name=project.name,
        status=project.status.value,
        start_date=project.start_date,
        due_date=project.due_date,
    )


def _task_summary(task, *, project_key: str, stage) -> TaskSummaryDto:
    """Собирает краткую карточку задачи из модели."""
    return TaskSummaryDto(
        checklist=checklist_context(getattr(task, "checklist", None)),
        task_key=build_task_key(project_key, task.number),
        title=task.title,
        stage=stage.name if stage else None,
        is_done=bool(stage and stage.is_done_stage),
        priority=task.priority.value,
        assignee=task.assignee,
        due_date=task.due_date,
        task_id=task.id,
        start_date=getattr(task, "start_date", None),
    )
