import logging
from collections import defaultdict

from sqlalchemy import Result, delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.db.models.project_members import ProjectMember
from src.db.models.task_participants import TaskParticipant, TaskParticipantRole
from src.exceptions.tasks import TasksRepositoryError

logger = logging.getLogger(__name__)


class TaskParticipantsRepository:
    """Репозиторий ролевых назначений людей на задачи.

    У проектной задачи участник — член команды проекта, у задачи вне
    проекта — пользователь напрямую; запросы по человеку смотрят на обе
    ссылки.
    """

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def get_by_task_ids(
        self,
        task_ids: list[int],
    ) -> dict[int, list[TaskParticipant]]:
        """Возвращает назначения задач вместе с безопасной идентичностью пользователей."""
        if not task_ids:
            return {}
        try:
            result: Result = await self.db_session.execute(
                select(TaskParticipant)
                .options(
                    joinedload(TaskParticipant.project_member).joinedload(ProjectMember.user),
                    joinedload(TaskParticipant.user),
                )
                .where(TaskParticipant.task_id.in_(task_ids))
                .order_by(TaskParticipant.task_id, TaskParticipant.id)
            )
            grouped: defaultdict[int, list[TaskParticipant]] = defaultdict(list)
            for participant in result.scalars().all():
                grouped[participant.task_id].append(participant)
            return dict(grouped)
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить участников задач.", exc_info=True)
            raise TasksRepositoryError("Ошибка получения участников задач.") from error

    async def get_for_project_member(
        self,
        project_member_id: int,
        role: TaskParticipantRole | None = None,
    ) -> list[TaskParticipant]:
        """Возвращает назначения конкретного члена проектной команды."""
        try:
            stmt = select(TaskParticipant).where(
                TaskParticipant.project_member_id == project_member_id
            )
            if role is not None:
                stmt = stmt.where(TaskParticipant.role == role)
            result: Result = await self.db_session.execute(stmt.order_by(TaskParticipant.id))
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить назначения участника проекта id=%s.",
                project_member_id,
                exc_info=True,
            )
            raise TasksRepositoryError("Ошибка получения назначений участника.") from error

    async def get_roles_for_user(self, *, task_id: int, user_id: int) -> set[TaskParticipantRole]:
        """Возвращает роли пользователя в задаче вне проекта.

        Args:
            task_id: Идентификатор задачи.
            user_id: Идентификатор пользователя.

        Returns:
            Роли пользователя; пустое множество — он не участник.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(TaskParticipant.role).where(
                    TaskParticipant.task_id == task_id,
                    TaskParticipant.user_id == user_id,
                )
            )
            return set(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить роли пользователя id=%s в задаче id=%s.",
                user_id,
                task_id,
                exc_info=True,
            )
            raise TasksRepositoryError("Ошибка получения ролей участника задачи.") from error

    async def get_person_roles(
        self, *, task_ids: list[int], user_id: int
    ) -> dict[int, set[TaskParticipantRole]]:
        """Возвращает роли пользователя в задачах любого вида.

        Args:
            task_ids: Идентификаторы задач.
            user_id: Идентификатор пользователя.

        Returns:
            Роли пользователя по задачам, где он участник.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        if not task_ids:
            return {}
        try:
            result: Result = await self.db_session.execute(
                select(TaskParticipant.task_id, TaskParticipant.role)
                .outerjoin(ProjectMember, ProjectMember.id == TaskParticipant.project_member_id)
                .where(
                    TaskParticipant.task_id.in_(task_ids),
                    func.coalesce(TaskParticipant.user_id, ProjectMember.user_id) == user_id,
                )
            )
            roles: defaultdict[int, set[TaskParticipantRole]] = defaultdict(set)
            for task_id, role in result.all():
                roles[task_id].add(role)
            return dict(roles)
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить роли пользователя id=%s.", user_id, exc_info=True)
            raise TasksRepositoryError("Ошибка получения ролей пользователя в задачах.") from error

    async def delete_for_task(self, task_id: int) -> None:
        """Удаляет все ролевые назначения задачи одним запросом.

        Args:
            task_id: Идентификатор задачи.

        Raises:
            TasksRepositoryError: Если удалить назначения не удалось.
        """
        try:
            await self.db_session.execute(
                delete(TaskParticipant).where(TaskParticipant.task_id == task_id)
            )
            await self.db_session.flush()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось удалить участников задачи id=%s.",
                task_id,
                exc_info=True,
            )
            raise TasksRepositoryError("Ошибка удаления участников задачи.") from error

    async def delete_assignments(self, task_id: int, assignment_ids: list[int]) -> None:
        """Удаляет только снятые роли проверенной задачи, сохраняя остальные строки."""
        try:
            await self.db_session.execute(
                delete(TaskParticipant).where(
                    TaskParticipant.task_id == task_id, TaskParticipant.id.in_(assignment_ids)
                )
            )
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось снять роли задачи id=%s.", task_id, exc_info=True)
            raise TasksRepositoryError("Ошибка снятия ролей задачи.") from error

    async def save_many(self, task_id: int, assignments: list[dict]) -> None:
        """Добавляет набор ролевых назначений одной вставкой.

        Args:
            task_id: Идентификатор задачи.
            assignments: Роли и исполнители.

        Raises:
            TasksRepositoryError: Если сохранить назначения не удалось.
        """
        if not assignments:
            return
        try:
            self.db_session.add_all(
                [TaskParticipant(task_id=task_id, **assignment) for assignment in assignments]
            )
            await self.db_session.flush()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось сохранить участников задачи id=%s.",
                task_id,
                exc_info=True,
            )
            raise TasksRepositoryError("Ошибка сохранения участников задачи.") from error
