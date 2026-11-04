"""Разрешение доступа к объектам проекта.

Задачи, стадии, документы, комментарии и связи принадлежат проекту, поэтому
любой вопрос о доступе сводится к одному: состоит ли пользователь в проекте
этого объекта.

Правило здесь, а не в Depends-слое: доступ — бизнес-инвариант, одинаковый
для HTTP и для MCP. Реализация в транспортном адаптере означала бы две
независимые реализации прав, которые однажды разойдутся молча.
"""

import logging
from dataclasses import dataclass

from src.db.models.project_members import ProjectRole
from src.exceptions.access import (
    AccessServiceError,
    ProjectOwnerRequiredError,
    ResourceNotAvailableError,
)
from src.exceptions.base import RepositoryError
from src.repositories.document_links import DocumentLinksRepository
from src.repositories.documents import DocumentsRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.tasks import TasksRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AccessGrant:
    """Подтверждённый доступ к объекту.

    Наружу отдаются только идентификаторы: persistence-модель не должна
    подниматься в транспортный слой вместе с разрешением доступа.

    Attributes:
        project_id: Проект, которому принадлежит объект.
        resource_id: Идентификатор самого объекта.
        is_owner: Является ли пользователь владельцем проекта.
    """

    project_id: int | None
    resource_id: int
    is_owner: bool
    can_write: bool = True


class AccessService:
    """Проверяет право пользователя работать с объектом проекта."""

    def __init__(
        self,
        *,
        members_repository: ProjectMembersRepository,
        tasks_repository: TasksRepository,
        stages_repository: ProjectStagesRepository,
        documents_repository: DocumentsRepository,
        comments_repository: TaskCommentsRepository,
        links_repository: DocumentLinksRepository,
    ) -> None:
        self.members_repository = members_repository
        self.tasks_repository = tasks_repository
        self.stages_repository = stages_repository
        self.documents_repository = documents_repository
        self.comments_repository = comments_repository
        self.links_repository = links_repository

    async def ensure_project_access(self, *, project_id: int, user_id: int) -> AccessGrant:
        """Проверяет участие пользователя в проекте.

        Args:
            project_id: Идентификатор проекта.
            user_id: Идентификатор пользователя запроса.

        Returns:
            Разрешение с идентификатором проекта и признаком владельца.

        Raises:
            ResourceNotAvailableError: Если проект недоступен пользователю.
            AccessServiceError: Если проверить доступ не удалось.
        """
        return await self._authorize(project_id=project_id, user_id=user_id, resource="Проект")

    async def ensure_project_ownership(self, *, project_id: int, user_id: int) -> AccessGrant:
        """Проверяет, что пользователь владеет проектом.

        Args:
            project_id: Идентификатор проекта.
            user_id: Идентификатор пользователя запроса.

        Returns:
            Разрешение владельца проекта.

        Raises:
            ResourceNotAvailableError: Если проект недоступен пользователю.
            ProjectOwnerRequiredError: Если пользователь не владелец.
            AccessServiceError: Если проверить доступ не удалось.
        """
        grant = await self._authorize(
            project_id=project_id,
            user_id=user_id,
            resource="Проект",
        )
        if not grant.is_owner:
            raise ProjectOwnerRequiredError(project_id=project_id)
        return grant

    async def ensure_task_access(self, *, task_id: int, user_id: int) -> AccessGrant:
        """Проверяет доступ к задаче через проект, которому она принадлежит."""
        task = await self._load(self.tasks_repository.get_by_id, task_id=task_id)
        if task is None:
            raise ResourceNotAvailableError(resource="Задача", resource_id=task_id)
        if task.project_id is None:
            visible, owner = await self._load(
                self.tasks_repository.personal_access, task_id=task_id, user_id=user_id
            )
            if not visible:
                raise ResourceNotAvailableError(resource="Задача", resource_id=task_id)
            return AccessGrant(project_id=None, resource_id=task_id, is_owner=owner)
        grant = await self._authorize(
            project_id=task.project_id,
            user_id=user_id,
            resource="Задача",
        )
        return self._for_resource(grant, resource_id=task_id)

    async def ensure_project_write(self, *, project_id: int, user_id: int) -> AccessGrant:
        return await self.ensure_project_access(project_id=project_id, user_id=user_id)

    @staticmethod
    def require_write(grant: AccessGrant) -> None:
        if not grant.can_write:
            from src.exceptions.access import ResourceWriteRequiredError

            raise ResourceWriteRequiredError()

    async def ensure_task_deletion(self, *, task_id: int, user_id: int) -> AccessGrant:
        grant = await self.ensure_task_access(task_id=task_id, user_id=user_id)
        self.require_write(grant)
        if grant.project_id is None and not grant.is_owner:
            from src.exceptions.access import TaskOwnerRequiredError

            raise TaskOwnerRequiredError(task_id)
        return grant

    async def ensure_stage_access(self, *, stage_id: int, user_id: int) -> AccessGrant:
        """Проверяет доступ к стадии через проект, которому она принадлежит."""
        stage = await self._load(self.stages_repository.get_by_id, stage_id=stage_id)
        if stage is None:
            raise ResourceNotAvailableError(resource="Стадия", resource_id=stage_id)
        grant = await self._authorize(
            project_id=stage.project_id,
            user_id=user_id,
            resource="Стадия",
        )
        return self._for_resource(grant, resource_id=stage_id)

    async def ensure_document_access(self, *, document_id: int, user_id: int) -> AccessGrant:
        """Проверяет доступ к документу через проект, которому он принадлежит."""
        document = await self._load(
            self.documents_repository.get_by_id,
            document_id=document_id,
        )
        if document is None:
            raise ResourceNotAvailableError(resource="Документ", resource_id=document_id)
        grant = await self._authorize(
            project_id=document.project_id,
            user_id=user_id,
            resource="Документ",
        )
        return self._for_resource(grant, resource_id=document_id)

    async def ensure_comment_access(self, *, comment_id: int, user_id: int) -> AccessGrant:
        """Проверяет доступ к комментарию через задачу и её проект."""
        comment = await self._load(self.comments_repository.get_by_id, comment_id=comment_id)
        if comment is None:
            raise ResourceNotAvailableError(resource="Комментарий", resource_id=comment_id)
        grant = await self.ensure_task_access(task_id=comment.task_id, user_id=user_id)
        return self._for_resource(grant, resource_id=comment_id)

    async def ensure_link_access(self, *, link_id: int, user_id: int) -> AccessGrant:
        """Проверяет доступ к связи документа через документ и его проект."""
        link = await self._load(self.links_repository.get_by_id, link_id=link_id)
        if link is None:
            raise ResourceNotAvailableError(resource="Связь документа", resource_id=link_id)
        document = await self._load(
            self.documents_repository.get_by_id,
            document_id=link.document_id,
        )
        if document is None:
            raise ResourceNotAvailableError(resource="Связь документа", resource_id=link_id)
        grant = await self._authorize(
            project_id=document.project_id,
            user_id=user_id,
            resource="Связь документа",
        )
        return self._for_resource(grant, resource_id=link_id)

    async def ensure_comment_authorship(self, *, comment_id: int, user_id: int) -> AccessGrant:
        grant = await self.ensure_comment_access(comment_id=comment_id, user_id=user_id)
        comment = await self._load(self.comments_repository.get_by_id, comment_id=comment_id)
        if not grant.is_owner and comment.author_id != user_id:
            from src.exceptions.access import ResourceWriteRequiredError

            raise ResourceWriteRequiredError()
        return grant

    async def _authorize(self, *, project_id: int, user_id: int, resource: str) -> AccessGrant:
        """Проверяет членство пользователя в проекте объекта."""
        membership = await self._load(
            self.members_repository.get,
            project_id=project_id,
            user_id=user_id,
        )
        if membership is None:
            logger.info(
                "ℹ️ Пользователь id=%s обратился к недоступному проекту id=%s.",
                user_id,
                project_id,
            )
            raise ResourceNotAvailableError(resource=resource, resource_id=project_id)
        return AccessGrant(
            project_id=project_id,
            resource_id=project_id,
            is_owner=membership.role is ProjectRole.OWNER,
        )

    @staticmethod
    def _for_resource(grant: AccessGrant, *, resource_id: int) -> AccessGrant:
        """Переносит разрешение проекта на конкретный объект внутри него."""
        return AccessGrant(
            project_id=grant.project_id,
            resource_id=resource_id,
            is_owner=grant.is_owner,
            can_write=grant.can_write,
        )

    @staticmethod
    async def _load(query, **kwargs):
        """Выполняет чтение и преобразует ошибку репозитория в свою.

        Сбой базы не должен выглядеть как отказ в доступе: иначе временная
        недоступность PostgreSQL молча превратится в 404 для клиента.
        """
        try:
            return await query(**kwargs)
        except RepositoryError as error:
            logger.error("❌ Ошибка проверки доступа: %s", error, exc_info=True)
            raise AccessServiceError(str(error)) from error
