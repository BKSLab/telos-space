"""Публикация результата и копирование комментария с сохранением истории."""

from src.exceptions.api_tokens import InsufficientTokenScopeError
from src.exceptions.projects import ProjectNotFoundError, ProjectsRepositoryError
from src.exceptions.task_comments import TaskCommentsRepositoryError
from src.exceptions.task_reports import (
    TaskReportConflictError,
    TaskReportSourceError,
    TaskReportsRepositoryError,
    TaskReportsServiceError,
)
from src.exceptions.tasks import TaskNotFoundError, TasksRepositoryError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.repositories.projects import ProjectsRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.task_reports import TaskReportsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.task_reports import (
    ProjectTaskReportSchema,
    ProjectTaskReportsSchema,
    TaskReportSchema,
)
from src.services.access import AccessService
from src.services.auth import Principal


class TaskReportsService:
    def __init__(
        self,
        reports: TaskReportsRepository,
        tasks: TasksRepository,
        comments: TaskCommentsRepository,
        access: AccessService,
        unit_of_work: UnitOfWork,
        projects: ProjectsRepository | None = None,
    ) -> None:
        self.reports, self.tasks, self.comments = reports, tasks, comments
        self.access, self.unit_of_work = access, unit_of_work
        self.projects = projects

    async def _authorize(self, task_id: int, actor: Principal, write: bool = False) -> None:
        grant = await self.access.ensure_task_access(task_id=task_id, user_id=actor.user_id)
        if write:
            if not actor.can_write:
                raise InsufficientTokenScopeError()
            self.access.require_write(grant)

    async def get(self, task_id: int, actor: Principal) -> TaskReportSchema:
        """Возвращает текущий результат с проверкой доступа."""
        await self._authorize(task_id, actor)
        try:
            row = await self.reports.latest(task_id)
            return (
                TaskReportSchema.model_validate(row) if row else TaskReportSchema(task_id=task_id)
            )
        except TaskReportsRepositoryError as error:
            raise TaskReportsServiceError(str(error)) from error

    async def history(
        self,
        task_id: int,
        actor: Principal,
        *,
        report_id: int | None = None,
        revision: int | None = None,
    ) -> list[TaskReportSchema]:
        """Возвращает последние 50 публикаций, не подменяя историю текущим текстом."""
        await self._authorize(task_id, actor)
        try:
            return [
                TaskReportSchema.model_validate(row)
                for row in await self.reports.history(
                    task_id, report_id=report_id, revision=revision
                )
            ]
        except TaskReportsRepositoryError as error:
            raise TaskReportsServiceError(str(error)) from error

    async def publish(
        self,
        task_id: int,
        actor: Principal,
        expected_revision: int,
        body_md: str = "",
        comment_id: int | None = None,
        expected_comment_body_md: str | None = None,
    ) -> TaskReportSchema:
        """Публикует полную версию атомарно; источником может быть любой комментарий этой задачи."""
        await self._authorize(task_id, actor, write=True)
        try:
            # Родительский замок сериализует и первые публикации, когда строки отчёта ещё нет.
            task = await self.tasks.get_for_update(task_id=task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            previous = await self.reports.latest(task_id)
            revision = previous.revision if previous else 0
            if expected_revision != revision:
                raise TaskReportConflictError("Прочитана устаревшая версия.")
            source = None
            if comment_id is not None:
                source = await self.comments.get_for_update(comment_id=comment_id)
                if source is None or source.task_id != task_id:
                    raise TaskReportSourceError("Чужой или отсутствующий комментарий.")
                if source.body_md != expected_comment_body_md:
                    raise TaskReportConflictError("Текст комментария изменился.")
                body_md = source.body_md
            row = await self.reports.append(
                {
                    "task_id": task_id,
                    "revision": revision + 1,
                    "body_md": body_md,
                    "author_id": actor.user_id,
                    "author_name": actor.full_name or actor.username,
                    "source_comment_id": source.id if source else None,
                    "source_comment_original_id": source.id if source else None,
                    "source_author_name": source.author_name if source else None,
                }
            )
            await self.unit_of_work.commit()
            return TaskReportSchema.model_validate(row)
        except (TaskReportConflictError, TaskReportSourceError, TaskNotFoundError):
            await self.unit_of_work.rollback()
            raise
        except (
            TaskReportsRepositoryError,
            TasksRepositoryError,
            TaskCommentsRepositoryError,
            UnitOfWorkRepositoryError,
        ) as error:
            await self.unit_of_work.rollback()
            raise TaskReportsServiceError(str(error)) from error

    async def list_project(
        self, project_id: int, actor: Principal, *, offset: int = 0, limit: int = 30
    ) -> ProjectTaskReportsSchema:
        """Читает все текущие отчёты проекта, сохраняя счётчики и пагинацию."""
        await self.access.ensure_project_access(project_id=project_id, user_id=actor.user_id)
        if self.projects is None:
            raise TaskReportsServiceError("Не настроено чтение проектов для отчётов.")
        try:
            project = await self.projects.get_by_id(project_id)
            if project is None:
                raise ProjectNotFoundError(project_id=project_id)
            tasks = sorted(
                await self.tasks.get_by_project(project_id=project_id), key=lambda row: row.number
            )
            latest = await self.reports.latest_for_tasks([task.id for task in tasks])
            items = [
                ProjectTaskReportSchema(
                    task_id=task.id,
                    task_key=f"{project.key}-{task.number}",
                    task_title=task.title,
                    report=TaskReportSchema.model_validate(latest[task.id]),
                )
                for task in tasks
                if task.id in latest and latest[task.id].body_md.strip()
            ]
            page = items[offset : offset + limit]
            return ProjectTaskReportsSchema(
                project_id=project_id,
                total_tasks=len(tasks),
                total_reports=len(items),
                without_report=len(tasks) - len(items),
                items=page,
                offset=offset,
                next_offset=offset + len(page) if offset + len(page) < len(items) else None,
            )
        except (TaskReportsRepositoryError, TasksRepositoryError, ProjectsRepositoryError) as error:
            raise TaskReportsServiceError(str(error)) from error
