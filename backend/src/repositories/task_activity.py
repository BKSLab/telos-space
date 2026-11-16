import logging

from sqlalchemy import Result, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.task_activity import TaskActivity, TaskActivityEventType
from src.db.models.tasks import Task
from src.exceptions.task_activity import TaskActivityRepositoryError
from src.utils.request_actor import current_request_actor

logger = logging.getLogger(__name__)


class TaskActivityRepository:
    """Репозиторий неизменяемой истории задач."""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def get_for_task(self, task_id: int) -> list[TaskActivity]:
        """Возвращает историю задачи в хронологическом порядке.

        Args:
            task_id: Идентификатор задачи.

        Returns:
            События истории задачи.

        Raises:
            TaskActivityRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(TaskActivity)
                .where(TaskActivity.task_id == task_id)
                .order_by(TaskActivity.created_at)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить историю задачи id=%s.", task_id, exc_info=True)
            raise TaskActivityRepositoryError(
                f"Ошибка получения истории задачи id={task_id}."
            ) from error

    async def get_recent_by_project(
        self,
        project_id: int,
        limit: int = 30,
    ) -> list[TaskActivity]:
        """Возвращает последние события задач одного проекта."""
        try:
            result: Result = await self.db_session.execute(
                select(TaskActivity)
                .join(Task, Task.id == TaskActivity.task_id)
                .where(Task.project_id == project_id)
                .order_by(TaskActivity.created_at.desc(), TaskActivity.id.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить историю проекта id=%s.", project_id, exc_info=True)
            raise TaskActivityRepositoryError(
                f"Ошибка получения истории проекта id={project_id}."
            ) from error

    async def get_count_by_project(self, *, project_id: int) -> int:
        """Считает всю историю для указания границ аналитического среза.

        Args:
            project_id: Проект области анализа.

        Returns:
            Число событий всех задач проекта.

        Raises:
            TaskActivityRepositoryError: Ошибка чтения истории.
        """
        try:
            result = await self.db_session.execute(
                select(func.count(TaskActivity.id))
                .join(Task, Task.id == TaskActivity.task_id)
                .where(Task.project_id == project_id)
            )
            return result.scalar_one()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось посчитать историю проекта id=%s.", project_id, exc_info=True
            )
            raise TaskActivityRepositoryError(
                f"Ошибка подсчёта истории проекта id={project_id}."
            ) from error

    async def get_recent_due_date_changes(
        self,
        *,
        project_id: int,
        limit: int = 12,
    ) -> list[TaskActivity]:
        """Возвращает последние переносы дедлайнов задач проекта."""
        try:
            result: Result = await self.db_session.execute(
                select(TaskActivity)
                .join(Task, Task.id == TaskActivity.task_id)
                .where(
                    Task.project_id == project_id,
                    (
                        (TaskActivity.event_type == TaskActivityEventType.DUE_DATE_CHANGED)
                        | (
                            (TaskActivity.event_type == TaskActivityEventType.DATES_CHANGED)
                            & func.split_part(TaskActivity.from_value, "; завершение: ", 2)
                            .is_distinct_from(
                                func.split_part(TaskActivity.to_value, "; завершение: ", 2)
                            )
                        )
                    ),
                )
                .order_by(TaskActivity.created_at.desc(), TaskActivity.id.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить переносы сроков проекта id=%s.",
                project_id,
                exc_info=True,
            )
            raise TaskActivityRepositoryError(
                f"Ошибка получения переносов сроков проекта id={project_id}."
            ) from error

    async def save(
        self,
        task_id: int,
        event_type: TaskActivityEventType,
        from_value: str | None,
        to_value: str | None,
        reason: str | None = None,
    ) -> TaskActivity:
        """Сохраняет событие истории задачи.

        Args:
            task_id: Идентификатор задачи.
            event_type: Тип события.
            from_value: Предыдущее значение.
            to_value: Новое значение.
            reason: Причина изменения назначенных дат или None.

        Returns:
            Сохранённое событие.

        Raises:
            TaskActivityRepositoryError: Если сохранить событие не удалось.
        """
        # Автор берётся из контекста запроса: его ставит аутентификация
        # HTTP и MCP, поэтому ни один сценарий не может забыть подпись.
        actor = current_request_actor()
        try:
            activity = TaskActivity(
                task_id=task_id,
                event_type=event_type,
                from_value=from_value,
                to_value=to_value,
                reason=reason,
                actor_user_id=actor.user_id if actor else None,
                actor_name=actor.name if actor else None,
                via_api_key_label=actor.via_api_key_label if actor else None,
            )
            self.db_session.add(activity)
            await self.db_session.flush()
            return activity
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось сохранить историю задачи id=%s.", task_id, exc_info=True)
            raise TaskActivityRepositoryError(
                f"Ошибка сохранения истории задачи id={task_id}."
            ) from error
