import logging

from src.db.models.knowledge_index_jobs import KnowledgeEntityType
from src.db.models.task_activity import TaskActivityEventType
from src.exceptions.knowledge import KnowledgeEventsServiceError
from src.exceptions.task_activity import TaskActivityRepositoryError
from src.exceptions.task_comments import (
    TaskCommentConflictError,
    TaskCommentNotFoundError,
    TaskCommentsRepositoryError,
    TaskCommentsServiceError,
)
from src.exceptions.tasks import TaskNotFoundError, TasksRepositoryError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.exceptions.users import UsersRepositoryError
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.schemas.task_comments import CommentSchema
from src.services.knowledge_events import KnowledgeEvents
from src.services.tasks import user_display_name

logger = logging.getLogger(__name__)


class TaskCommentsService:
    """Сервис сценариев работы с комментариями задач."""

    def __init__(
        self,
        comments_repository: TaskCommentsRepository,
        tasks_repository: TasksRepository,
        activity_repository: TaskActivityRepository,
        unit_of_work: UnitOfWork,
        knowledge_events: KnowledgeEvents,
        users_repository: UsersRepository | None = None,
        knowledge_enabled: bool = True,
    ):
        self.users_repository = users_repository
        self.comments_repository = comments_repository
        self.tasks_repository = tasks_repository
        self.activity_repository = activity_repository
        self.unit_of_work = unit_of_work
        self.knowledge_events = knowledge_events
        self.knowledge_enabled = knowledge_enabled

    async def get_comments(self, task_id: int) -> list[CommentSchema]:
        """Возвращает комментарии существующей задачи.

        Args:
            task_id: Идентификатор задачи.

        Returns:
            Комментарии задачи в хронологическом порядке.

        Raises:
            TaskNotFoundError: Если задача не найдена.
            TaskCommentsServiceError: Если получить комментарии не удалось.
        """
        try:
            task = await self.tasks_repository.get_by_id(task_id=task_id)
            if task is None:
                raise TaskNotFoundError(task_id=task_id)
            comments = await self.comments_repository.get_for_task(task_id=task_id)
            return await self._schemas(comments)
        except (TaskCommentsRepositoryError, TasksRepositoryError) as error:
            logger.error("❌ Ошибка получения комментариев задачи id=%s.", task_id, exc_info=True)
            raise TaskCommentsServiceError(str(error)) from error

    async def add_comment(
        self,
        task_id: int,
        author_name: str | None = None,
        body_md: str = "",
        author_id: int | None = None,
    ) -> CommentSchema:
        """Добавляет комментарий и событие истории задачи.

        Автор — всегда пользователь запроса (человек или владелец токена,
        от чьего имени работает агент). Его полное имя сохраняется снимком в
        ``author_name``: по нему ищет полнотекстовый поиск, и подпись
        переживает удаление пользователя.

        Args:
            task_id: Идентификатор задачи.
            author_id: Автор — текущий пользователь.
            body_md: Текст комментария в Markdown.

        Returns:
            Созданный комментарий.

        Raises:
            TaskNotFoundError: Если задача не найдена.
            TaskCommentsServiceError: Если добавить комментарий не удалось.
        """
        try:
            task = await self.tasks_repository.get_by_id(task_id=task_id)
            if task is None:
                raise TaskNotFoundError(task_id=task_id)
            author = (
                await self.users_repository.get_by_id(user_id=author_id)
                if self.users_repository is not None and author_id is not None
                else None
            )
            comment = await self.comments_repository.save(
                task_id=task_id,
                author_id=author_id,
                author_name=user_display_name(author) if author is not None else author_name,
                body_md=body_md,
            )
            await self.activity_repository.save(
                task_id=task_id,
                event_type=TaskActivityEventType.COMMENT_ADDED,
                from_value=None,
                to_value=body_md[:255],
            )
            await self.knowledge_events.upsert(
                project_id=task.project_id,
                entity_type=KnowledgeEntityType.COMMENT,
                entity_id=comment.id,
            )
            await self.unit_of_work.commit()
            return (
                await self._schemas(
                    [await self.comments_repository.get_by_id(comment_id=comment.id)]
                )
            )[0]
        except (
            TaskCommentsRepositoryError,
            TasksRepositoryError,
            TaskActivityRepositoryError,
            UsersRepositoryError,
            KnowledgeEventsServiceError,
            UnitOfWorkRepositoryError,
        ) as error:
            logger.error("❌ Ошибка добавления комментария задачи id=%s.", task_id, exc_info=True)
            raise TaskCommentsServiceError(str(error)) from error

    async def get_comment(self, comment_id: int) -> CommentSchema:
        """Возвращает комментарий для предпросмотра изменения."""
        try:
            item = await self.comments_repository.get_by_id(comment_id)
            if item is None:
                raise TaskCommentNotFoundError(comment_id)
            return (await self._schemas([item]))[0]
        except TaskCommentsRepositoryError as error:
            raise TaskCommentsServiceError(str(error)) from error

    async def update_comment(
        self, comment_id: int, body_md: str, expected_body_md: str
    ) -> CommentSchema:
        """Редактирует прочитанную версию комментария, сохраняя авторство.

        Args:
            comment_id: Проверенный транспортом комментарий.
            body_md: Новый текст.
            expected_body_md: Текст прочитанной версии для проверки конфликта.
        Returns:
            Изменённый комментарий.
        Raises:
            TaskCommentsServiceError: Если запись или индексация не удалась.
            TaskCommentConflictError: Если комментарий уже изменился.
        """
        if not body_md.strip() or "\x00" in body_md or "\x00" in expected_body_md:
            raise TaskCommentConflictError("Текст комментария некорректен.")
        try:
            comment = await self.comments_repository.update_text(
                comment_id, body_md, expected_body_md
            )
            if comment is None:
                raise TaskCommentConflictError("Комментарий изменён или удалён другим участником.")
            task = await self.tasks_repository.get_by_id(task_id=comment.task_id)
            await self.knowledge_events.upsert(
                project_id=task.project_id,
                entity_type=KnowledgeEntityType.COMMENT,
                entity_id=comment.id,
            )
            await self.unit_of_work.commit()
            return (
                await self._schemas(
                    [await self.comments_repository.get_by_id(comment_id=comment.id)]
                )
            )[0]
        except (
            TaskCommentsRepositoryError,
            TasksRepositoryError,
            KnowledgeEventsServiceError,
            UnitOfWorkRepositoryError,
        ) as error:
            raise TaskCommentsServiceError(str(error)) from error

    async def delete_comment(self, comment_id: int) -> None:
        """Удаляет комментарий по идентификатору.

        Args:
            comment_id: Идентификатор комментария.

        Returns:
            ``None`` после успешного удаления.

        Raises:
            TaskCommentNotFoundError: Если комментарий не найден.
            TaskCommentsServiceError: Если удалить комментарий не удалось.
        """
        try:
            comment = await self.comments_repository.get_by_id(comment_id=comment_id)
            if comment is None:
                raise TaskCommentNotFoundError(comment_id=comment_id)
            task = await self.tasks_repository.get_by_id(task_id=comment.task_id)
            await self.comments_repository.delete(comment=comment)
            if task is not None:
                await self.knowledge_events.delete(
                    project_id=task.project_id,
                    entity_type=KnowledgeEntityType.COMMENT,
                    entity_id=comment_id,
                )
            await self.unit_of_work.commit()
        except (
            TaskCommentsRepositoryError,
            TasksRepositoryError,
            KnowledgeEventsServiceError,
            UnitOfWorkRepositoryError,
        ) as error:
            logger.error("❌ Ошибка удаления комментария id=%s.", comment_id, exc_info=True)
            raise TaskCommentsServiceError(str(error)) from error

    async def _schemas(self, comments: list) -> list[CommentSchema]:
        result = [CommentSchema.model_validate(comment) for comment in comments]
        if not any(item.attachments for item in result):
            return result
        states = await self.comments_repository.get_attachment_states([item.id for item in result])
        for comment in result:
            for attachment in comment.attachments:
                state = states.get(attachment.id, {})
                if not self.knowledge_enabled:
                    state.update(index_status="disabled", index_detail=None)
                for key, value in state.items():
                    setattr(attachment, key, value)
        return result
