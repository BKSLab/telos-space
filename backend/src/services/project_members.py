import logging

from src.db.models.knowledge_index_jobs import KnowledgeEntityType
from src.db.models.project_members import ProjectMember, ProjectRole
from src.db.models.task_participants import TaskParticipantRole
from src.exceptions.knowledge import KnowledgeEventsServiceError
from src.exceptions.project_risks import ProjectRiskRepositoryError
from src.exceptions.projects import (
    ProjectMemberAlreadyExistsError,
    ProjectMemberAlreadyExistsRepositoryError,
    ProjectMemberNotFoundError,
    ProjectMemberUserNotFoundError,
    ProjectOwnerRemovalError,
    ProjectsRepositoryError,
    ProjectsServiceError,
)
from src.exceptions.tasks import TasksRepositoryError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.exceptions.users import UsersRepositoryError
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_risks import ProjectRiskRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.schemas.project_members import ProjectMemberSchema
from src.services.auth import to_user_summary
from src.services.knowledge_events import KnowledgeEvents
from src.services.users import UsersService

logger = logging.getLogger(__name__)

RepositoryErrors = (
    ProjectsRepositoryError,
    TasksRepositoryError,
    UsersRepositoryError,
    UnitOfWorkRepositoryError,
    ProjectRiskRepositoryError,
    KnowledgeEventsServiceError,
)


class ProjectMembersService:
    """Сервис состава проектной команды без каталога или поиска пользователей."""

    def __init__(
        self,
        members_repository: ProjectMembersRepository,
        users_repository: UsersRepository,
        participants_repository: TaskParticipantsRepository,
        tasks_repository: TasksRepository,
        unit_of_work: UnitOfWork,
        users_service: UsersService,
        risks_repository: ProjectRiskRepository,
        knowledge_events: KnowledgeEvents,
    ):
        self.members_repository = members_repository
        self.users_repository = users_repository
        self.participants_repository = participants_repository
        self.tasks_repository = tasks_repository
        self.unit_of_work = unit_of_work
        self.users_service = users_service
        self.risks_repository = risks_repository
        self.knowledge_events = knowledge_events

    async def get_member_list(self, project_id: int) -> list[ProjectMemberSchema]:
        """Возвращает только участников указанного доступного проекта."""
        try:
            members = await self.members_repository.get_for_project(project_id=project_id)
            members.sort(key=lambda item: (item.role is not ProjectRole.OWNER, item.id))
            return [to_project_member_schema(member) for member in members]
        except RepositoryErrors as error:
            logger.error(
                "❌ Ошибка получения команды проекта id=%s.",
                project_id,
                exc_info=True,
            )
            raise ProjectsServiceError(str(error)) from error

    async def add_member(self, project_id: int, username: str) -> ProjectMemberSchema:
        """Сразу добавляет активного пользователя по точному логину."""
        try:
            user = await self.users_repository.get_by_username(username=username)
            if user is None or not user.is_active:
                raise ProjectMemberUserNotFoundError(username=username)
            existing = await self.members_repository.get(project_id=project_id, user_id=user.id)
            if existing is not None:
                raise ProjectMemberAlreadyExistsError(user_id=user.id)
            member = await self.members_repository.save(
                data={
                    "project_id": project_id,
                    "user_id": user.id,
                    "role": ProjectRole.MEMBER,
                }
            )
            await self.unit_of_work.commit()
            return to_project_member_schema(member, user=user)
        except ProjectMemberAlreadyExistsRepositoryError as error:
            raise ProjectMemberAlreadyExistsError(user_id=error.user_id) from error
        except RepositoryErrors as error:
            logger.error(
                "❌ Ошибка добавления пользователя в проект id=%s.",
                project_id,
                exc_info=True,
            )
            raise ProjectsServiceError(str(error)) from error

    async def resolve_member_user_id(self, *, project_id: int, username: str) -> int:
        """Разрешает точный логин только внутри команды проекта.

        Каталог пользователей наружу не раскрывается: несуществующий,
        отключённый и не входящий в команду логин отвечают одинаково —
        иначе перебором выяснялся бы состав системы.

        Args:
            project_id: Проект, в котором ищется участник.
            username: Точный логин.

        Returns:
            Идентификатор пользователя-участника.

        Raises:
            ProjectMemberUserNotFoundError: Если активный участник не найден.
            ProjectsServiceError: Если проверить участие не удалось.
        """
        try:
            user = await self.users_repository.get_by_username(username=username.strip())
            member = (
                await self.members_repository.get(project_id=project_id, user_id=user.id)
                if user is not None and user.is_active
                else None
            )
        except RepositoryErrors as error:
            logger.error(
                "❌ Не удалось проверить участие пользователя в проекте id=%s.",
                project_id,
                exc_info=True,
            )
            raise ProjectsServiceError(str(error)) from error
        if user is None or member is None:
            raise ProjectMemberUserNotFoundError(username=username)
        return user.id

    async def get_member_avatar(self, project_id: int, user_id: int) -> tuple[bytes, str]:
        """Отдаёт фотографию, только пока пользователь состоит в проекте."""
        try:
            member = await self.members_repository.get(project_id=project_id, user_id=user_id)
            if member is None:
                raise ProjectMemberNotFoundError(user_id=user_id)
            return await self.users_service.get_avatar(user_id=user_id)
        except ProjectsRepositoryError as error:
            logger.error(
                "❌ Ошибка получения фотографии участника id=%s проекта id=%s.",
                user_id,
                project_id,
                exc_info=True,
            )
            raise ProjectsServiceError(str(error)) from error

    async def remove_member(self, project_id: int, user_id: int) -> None:
        """Удаляет участника и его назначения, не позволяя удалить владельца."""
        try:
            member = await self.members_repository.get(
                project_id=project_id, user_id=user_id, for_update=True
            )
            if member is None:
                raise ProjectMemberNotFoundError(user_id=user_id)
            if member.role is ProjectRole.OWNER:
                raise ProjectOwnerRemovalError()

            assignments = await self.participants_repository.get_for_project_member(
                project_member_id=member.id,
            )
            executor_task_ids = {
                assignment.task_id
                for assignment in assignments
                if assignment.role is TaskParticipantRole.EXECUTOR
            }
            await self.tasks_repository.clear_assignees(
                task_ids=sorted(executor_task_ids)
            )
            await self.tasks_repository.bump_revisions(
                task_ids={assignment.task_id for assignment in assignments} - executor_task_ids
            )
            # Все ролевые назначения удалятся через FK ON DELETE CASCADE.
            risk_ids = await self.risks_repository.clear_owner(
                project_id=project_id, user_id=user_id
            )
            await self.knowledge_events.upsert_many(
                project_id=project_id, entity_type=KnowledgeEntityType.RISK, entity_ids=risk_ids
            )
            await self.members_repository.delete(member=member)
            await self.unit_of_work.commit()
        except RepositoryErrors as error:
            logger.error(
                "❌ Ошибка удаления пользователя из проекта id=%s.",
                project_id,
                exc_info=True,
            )
            await self.unit_of_work.rollback()
            raise ProjectsServiceError(str(error)) from error


def to_project_member_schema(
    member: ProjectMember,
    *,
    user=None,
) -> ProjectMemberSchema:
    """Преобразует участие в публичную карточку без контактов пользователя."""
    member_user = user or member.user
    return ProjectMemberSchema(
        id=member.id,
        project_id=member.project_id,
        role=member.role,
        user=to_user_summary(member_user),
        created_at=member.created_at,
    )
