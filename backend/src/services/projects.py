import logging
from datetime import date, timedelta

from src.db.models.knowledge_index_jobs import KnowledgeEntityType
from src.db.models.project_members import ProjectRole
from src.db.models.project_stages import ProjectStage
from src.db.models.tasks import Task
from src.exceptions.knowledge import KnowledgeEventsServiceError
from src.exceptions.project_boards import ProjectBoardsRepositoryError
from src.exceptions.project_stages import ProjectStagesRepositoryError
from src.exceptions.projects import (
    ProjectDeadlineConflictError,
    ProjectKeyAlreadyExistsRepositoryError,
    ProjectKeyConflictError,
    ProjectMemberUserNotFoundError,
    ProjectNotFoundError,
    ProjectsRepositoryError,
    ProjectsServiceError,
    ProjectValidationError,
)
from src.exceptions.storage import TaskAttachmentStorageError
from src.exceptions.tasks import TasksRepositoryError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.exceptions.users import UsersRepositoryError
from src.repositories.project_boards import ProjectBoardsRepository
from src.repositories.project_deadline_changes import ProjectDeadlineChangesRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.schemas.projects import (
    ProjectDeadlineChangeSchema,
    ProjectDefaultsSchema,
    ProjectDescriptionSchema,
    ProjectSchema,
    ProjectStatsSchema,
    StageBreakdownSchema,
)
from src.services.board_defaults import ensure_default_board
from src.services.knowledge_events import KnowledgeEvents
from src.storage.task_attachments import TaskAttachmentStorage
from src.utils.deadlines import DUE_SOON_DAYS, is_task_due_soon, is_task_overdue
from src.utils.project_description import compose_project_description

logger = logging.getLogger(__name__)
PROJECT_POINT_FIELDS = frozenset({"name", "description_md"})

DEFAULT_STAGES: tuple[dict, ...] = (
    {"name": "Бэклог", "color": "#7d8793", "is_done_stage": False},
    {"name": "В работе", "color": "#58a6ff", "is_done_stage": False},
    {"name": "Ревью", "color": "#a371f7", "is_done_stage": False},
    {"name": "Тестирование", "color": "#d29922", "is_done_stage": False},
    {"name": "Готово", "color": "#3fb950", "is_done_stage": True},
)

RepositoryErrors = (
    UsersRepositoryError,
    ProjectsRepositoryError,
    ProjectStagesRepositoryError,
    ProjectBoardsRepositoryError,
    TasksRepositoryError,
    KnowledgeEventsServiceError,
    UnitOfWorkRepositoryError,
)


class ProjectsService:
    """Сервис сценариев работы с проектами."""

    def __init__(
        self,
        projects_repository: ProjectsRepository,
        members_repository: ProjectMembersRepository,
        stages_repository: ProjectStagesRepository,
        tasks_repository: TasksRepository,
        unit_of_work: UnitOfWork,
        attachment_storage: TaskAttachmentStorage,
        knowledge_events: KnowledgeEvents,
        users_repository: UsersRepository,
        deadline_changes_repository: ProjectDeadlineChangesRepository,
        boards_repository: ProjectBoardsRepository | None = None,
    ):
        self.projects_repository = projects_repository
        self.members_repository = members_repository
        self.stages_repository = stages_repository
        self.tasks_repository = tasks_repository
        self.unit_of_work = unit_of_work
        self.attachment_storage = attachment_storage
        self.knowledge_events = knowledge_events
        self.users_repository = users_repository
        self.deadline_changes_repository = deadline_changes_repository
        self.boards_repository = boards_repository

    def get_defaults(self) -> ProjectDefaultsSchema:
        """Возвращает стандартные стадии из того же источника, что использует создание."""
        return ProjectDefaultsSchema(stages=list(DEFAULT_STAGES))

    async def get_project_list(self, user_id: int) -> list[ProjectSchema]:
        """Возвращает проекты, доступные пользователю.

        Args:
            user_id: Идентификатор пользователя.

        Returns:
            Список проектов пользователя в порядке отображения.

        Raises:
            ProjectsServiceError: Если получить проекты не удалось.
        """
        try:
            allowed_ids = await self.members_repository.get_project_ids_for_user(user_id=user_id)
            projects = await self.projects_repository.get_all()
            return [
                ProjectSchema.model_validate(project)
                for project in projects
                if project.id in allowed_ids
            ]
        except RepositoryErrors as error:
            logger.error("❌ Ошибка получения списка проектов.", exc_info=True)
            raise ProjectsServiceError(str(error)) from error

    async def get_project(self, project_id: int) -> ProjectSchema:
        """Возвращает проект по идентификатору.

        Args:
            project_id: Идентификатор проекта.

        Returns:
            Карточка проекта.

        Raises:
            ProjectNotFoundError: Если проект не найден или недоступен.
            ProjectsServiceError: Если получить проект не удалось.
        """
        try:
            project = await self.projects_repository.get_by_id(project_id=project_id)
            if project is None:
                raise ProjectNotFoundError(project_id=project_id)
            return ProjectSchema.model_validate(project)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка получения проекта id=%s.", project_id, exc_info=True)
            raise ProjectsServiceError(str(error)) from error

    async def create_project(self, data: dict, owner_id: int) -> ProjectSchema:
        """Создаёт проект, делает автора владельцем и добавляет стадии.

        Владелец сразу получает участие с ролью OWNER, поэтому доступ к проекту
        проверяется одинаково и для него, и для будущих приглашённых.

        Args:
            data: Поля нового проекта.
            owner_id: Идентификатор пользователя-владельца.

        Returns:
            Созданный проект.

        Raises:
            ProjectKeyConflictError: Если код проекта уже занят.
            ProjectsServiceError: Если создать проект не удалось.
        """
        try:
            payload = self._description_payload(data)
            usernames = payload.pop("member_usernames", [])
            stages = payload.pop("stages", None)
            selected_users = {}
            for username in dict.fromkeys(name.strip().lower() for name in usernames):
                user = await self.users_repository.get_by_username(username=username)
                if user is None or not user.is_active:
                    raise ProjectMemberUserNotFoundError(username)
                if user.id != owner_id:
                    selected_users[user.id] = user
            payload["start_date"] = payload.get("start_date") or date.today()
            self._validate_period(payload["start_date"], payload.get("due_date"))
            payload["due_date_has_been_set"] = payload.get("due_date") is not None
            order_index = await self.projects_repository.get_max_order_index() + 1
            project = await self.projects_repository.save(
                data={**payload, "owner_id": owner_id, "order_index": order_index}
            )
            await self.members_repository.save(
                data={
                    "project_id": project.id,
                    "user_id": owner_id,
                    "role": ProjectRole.OWNER,
                }
            )
            await self.stages_repository.save_many(
                items=[
                    {
                        **stage,
                        "name": stage["name"].strip(),
                        "project_id": project.id,
                        "order_index": index,
                    }
                    for index, stage in enumerate(stages if stages is not None else DEFAULT_STAGES)
                ]
            )
            for user_id in selected_users:
                await self.members_repository.save(
                    data={
                        "project_id": project.id,
                        "user_id": user_id,
                        "role": ProjectRole.MEMBER,
                    }
                )
            if payload.get("due_date") is not None:
                await self._record_deadline(project.id, None, payload["due_date"], owner_id, None)
            await self.knowledge_events.reindex_project(project.id)
            if self.boards_repository is not None:
                await ensure_default_board(
                    self.boards_repository, project.id, created_by_user_id=owner_id
                )
            await self.unit_of_work.commit()
            logger.info("✅ Проект %s создан с командой и стадиями.", project.key)
            return ProjectSchema.model_validate(project)
        except ProjectKeyAlreadyExistsRepositoryError as error:
            logger.warning("⚠️ Конфликт кода проекта %s.", error.key)
            await self.unit_of_work.rollback()
            raise ProjectKeyConflictError(key=error.key) from error
        except RepositoryErrors as error:
            logger.error("❌ Ошибка создания проекта.", exc_info=True)
            await self.unit_of_work.rollback()
            raise ProjectsServiceError(str(error)) from error

        except ProjectsServiceError:
            await self.unit_of_work.rollback()
            raise

    async def update_project(
        self, project_id: int, data: dict, updated_by_user_id: int
    ) -> ProjectSchema:
        """Обновляет поля проекта.

        Args:
            project_id: Идентификатор проекта.
            data: Изменяемые поля проекта.
            updated_by_user_id: Автор изменения из авторизованного запроса.

        Returns:
            Обновлённый проект.

        Raises:
            ProjectNotFoundError: Если проект не найден или недоступен.
            ProjectKeyConflictError: Если новый код проекта уже занят.
            ProjectsServiceError: Если обновить проект не удалось.
        """
        try:
            project = await self.projects_repository.get_by_id(
                project_id=project_id, for_update=True
            )
            if project is None:
                raise ProjectNotFoundError(project_id=project_id)
            payload = self._description_payload(data)
            comment = (payload.pop("due_date_comment", None) or "").strip() or None
            has_expected_date = "expected_due_date" in payload
            expected_date = payload.pop("expected_due_date", None)
            new_due_date = payload.get("due_date", project.due_date)
            self._validate_period(payload.get("start_date", project.start_date), new_due_date)
            if new_due_date != project.due_date:
                if has_expected_date and expected_date != project.due_date:
                    raise ProjectDeadlineConflictError()
                if (
                    project.due_date is not None or getattr(project, "due_date_has_been_set", False)
                ) and not comment:
                    raise ProjectValidationError(
                        "Укажите причину изменения срока окончания проекта."
                    )
                await self._record_deadline(
                    project_id, project.due_date, new_due_date, updated_by_user_id, comment
                )
                payload["due_date_has_been_set"] = True
            updated = await self.projects_repository.update(project=project, data=payload)
            if "key" in payload:
                await self.knowledge_events.reindex_project(project_id)
            elif PROJECT_POINT_FIELDS.intersection(payload):
                await self.knowledge_events.upsert(
                    project_id=project_id,
                    entity_type=KnowledgeEntityType.PROJECT,
                    entity_id=project_id,
                )
            await self.unit_of_work.commit()
            return ProjectSchema.model_validate(updated)
        except ProjectKeyAlreadyExistsRepositoryError as error:
            logger.warning("⚠️ Конфликт кода проекта %s.", error.key)
            raise ProjectKeyConflictError(key=error.key) from error
        except RepositoryErrors as error:
            logger.error("❌ Ошибка обновления проекта id=%s.", project_id, exc_info=True)
            await self.unit_of_work.rollback()
            raise ProjectsServiceError(str(error)) from error
        except ProjectsServiceError:
            await self.unit_of_work.rollback()
            raise

    async def get_deadline_history(self, project_id: int) -> list[ProjectDeadlineChangeSchema]:
        """Читает историю сроков доступного проекта."""
        try:
            if await self.projects_repository.get_by_id(project_id) is None:
                raise ProjectNotFoundError(project_id)
            changes = await self.deadline_changes_repository.get_by_project(project_id)
            return [ProjectDeadlineChangeSchema.model_validate(change) for change in changes]
        except RepositoryErrors as error:
            logger.exception("❌ Не удалось получить историю срока проекта id=%s.", project_id)
            raise ProjectsServiceError(str(error)) from error

    async def _record_deadline(
        self,
        project_id: int,
        previous: date | None,
        current: date | None,
        user_id: int,
        comment: str | None,
    ) -> None:
        """Сохраняет снимок автора и дат в общей транзакции изменения."""
        user = await self.users_repository.get_by_id(user_id)
        if user is None:
            raise ProjectValidationError("Не удалось определить автора изменения срока.")
        name = (
            " ".join(part for part in (user.last_name, user.first_name, user.middle_name) if part)
            or user.username
        )
        await self.deadline_changes_repository.save(
            data={
                "project_id": project_id,
                "previous_due_date": previous,
                "new_due_date": current,
                "changed_by_user_id": user.id,
                "changed_by_name": name,
                "comment": comment,
            }
        )

    @staticmethod
    def _validate_period(start_date: date | None, due_date: date | None) -> None:
        if start_date and due_date and due_date < start_date:
            raise ProjectValidationError("Окончание проекта не может быть раньше начала.")

    @staticmethod
    def _description_payload(data: dict) -> dict:
        """Поддерживает структурированную форму и старый контракт description_md."""
        payload = dict(data)
        if payload.get("description_sections") is not None:
            sections = ProjectDescriptionSchema.model_validate(payload["description_sections"])
            payload["description_sections"] = sections.model_dump()
            payload["description_md"] = compose_project_description(sections)
        elif "description_md" in payload:
            payload["description_sections"] = None
        return payload

    async def transfer_ownership(
        self, project_id: int, new_owner_id: int, acting_user_id: int
    ) -> ProjectSchema:
        """Передаёт владение активному участнику, оставляя прежнего в команде.

        Args:
            project_id: Проект передачи.
            new_owner_id: Уже включённый в команду участник.
            acting_user_id: Текущий владелец, подтверждающий передачу.
        Returns:
            Проект с новым владельцем.
        Raises:
            ProjectValidationError: Если владелец сменился или получатель недоступен.
            ProjectsServiceError: Если записать передачу не удалось.
        """
        try:
            project = await self.projects_repository.get_by_id(
                project_id=project_id, for_update=True
            )
            if project is None:
                raise ProjectNotFoundError(project_id=project_id)
            if project.owner_id != acting_user_id:
                raise ProjectValidationError("Передачу может выполнить только текущий владелец.")
            member = await self.members_repository.get(
                project_id=project_id, user_id=new_owner_id, for_update=True
            )
            user = await self.users_repository.get_by_id(new_owner_id)
            if member is None or user is None or not user.is_active:
                raise ProjectValidationError(
                    "Новый владелец должен быть активным участником проекта."
                )
            if new_owner_id != project.owner_id:
                await self.members_repository.set_role(
                    project_id, project.owner_id, ProjectRole.MEMBER
                )
                await self.members_repository.set_role(project_id, new_owner_id, ProjectRole.OWNER)
                await self.projects_repository.update(project, {"owner_id": new_owner_id})
            await self.unit_of_work.commit()
            return ProjectSchema.model_validate(project)
        except RepositoryErrors as error:
            await self.unit_of_work.rollback()
            raise ProjectsServiceError(str(error)) from error

    async def delete_project(self, project_id: int) -> None:
        """Удаляет проект вместе с задачами, стадиями, структурой и документами.

        Args:
            project_id: Идентификатор проекта.

        Returns:
            ``None`` после успешного удаления.

        Raises:
            ProjectNotFoundError: Если проект не найден.
            ProjectsServiceError: Если удалить проект не удалось.
        """
        try:
            project = await self.projects_repository.get_by_id(project_id=project_id)
            if project is None:
                raise ProjectNotFoundError(project_id=project_id)
            tasks = await self.tasks_repository.get_by_project(project_id=project_id)
            task_ids = [task.id for task in tasks]
            await self.projects_repository.delete(project=project)
            await self.knowledge_events.delete_collection(project_id)
            await self.unit_of_work.commit()
            await self._cleanup_task_files(task_ids=task_ids)
            try:
                await self.attachment_storage.delete_project_file_directory(project_id)
            except TaskAttachmentStorageError:
                logger.warning(
                    "⚠️ Не удалось очистить файлы удалённого проекта %s.", project_id, exc_info=True
                )
            logger.info("✅ Проект id=%s удалён вместе с %s задачами.", project_id, len(task_ids))
        except RepositoryErrors as error:
            logger.error("❌ Ошибка удаления проекта id=%s.", project_id, exc_info=True)
            raise ProjectsServiceError(str(error)) from error

    async def get_project_stats(self, project_id: int) -> ProjectStatsSchema:
        """Собирает показатели одного проекта.

        Args:
            project_id: Идентификатор проекта.

        Returns:
            Показатели проекта с распределением по стадиям.

        Raises:
            ProjectNotFoundError: Если проект не найден или недоступен.
            ProjectsServiceError: Если собрать показатели не удалось.
        """
        try:
            project = await self.projects_repository.get_by_id(project_id=project_id)
            if project is None:
                raise ProjectNotFoundError(project_id=project_id)
            stages = await self.stages_repository.get_by_project(project_id=project_id)
            tasks = await self.tasks_repository.get_by_project(project_id=project_id)
            return build_project_stats(project_id=project_id, stages=stages, tasks=tasks)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка расчёта показателей проекта id=%s.", project_id, exc_info=True)
            raise ProjectsServiceError(str(error)) from error

    async def _cleanup_task_files(self, task_ids: list[int]) -> None:
        """Best-effort очищает каталоги файлов удалённых задач."""
        if self.attachment_storage is None:
            return
        for task_id in task_ids:
            try:
                await self.attachment_storage.delete_task_directory(task_id)
            except TaskAttachmentStorageError:
                logger.warning(
                    "⚠️ Не удалось очистить каталог файлов удалённой задачи id=%s.",
                    task_id,
                    exc_info=True,
                )


def build_project_stats(
    project_id: int,
    stages: list[ProjectStage],
    tasks: list[Task],
    today: date | None = None,
) -> ProjectStatsSchema:
    """Считает показатели проекта из его стадий и задач.

    Args:
        project_id: Идентификатор проекта.
        stages: Стадии проекта в порядке отображения.
        tasks: Задачи проекта.
        today: Дата, относительно которой считается просрочка.

    Returns:
        Показатели проекта с распределением по стадиям.
    """
    current_day = today or date.today()
    soon_until = current_day + timedelta(days=DUE_SOON_DAYS)
    done_stage_ids = {stage.id for stage in stages if stage.is_done_stage}
    backlog_stage_id = stages[0].id if stages else None

    counts_by_stage: dict[int, int] = {stage.id: 0 for stage in stages}
    done_tasks = 0
    in_progress_tasks = 0
    overdue_tasks = 0
    due_soon_tasks = 0
    unassigned_tasks = 0
    next_due_date: date | None = None

    for task in tasks:
        counts_by_stage[task.stage_id] = counts_by_stage.get(task.stage_id, 0) + 1
        if task.wbs_node_id is None:
            unassigned_tasks += 1

        is_done = task.stage_id in done_stage_ids
        if is_done:
            done_tasks += 1
            continue
        if task.stage_id != backlog_stage_id:
            in_progress_tasks += 1
        if task.due_date is None:
            continue
        if is_task_overdue(due_date=task.due_date, is_done=False, today=current_day):
            overdue_tasks += 1
            continue
        if is_task_due_soon(
            due_date=task.due_date,
            is_done=False,
            today=current_day,
            soon_until=soon_until,
        ):
            due_soon_tasks += 1
        if next_due_date is None or task.due_date < next_due_date:
            next_due_date = task.due_date

    total_tasks = len(tasks)
    return ProjectStatsSchema(
        project_id=project_id,
        total_tasks=total_tasks,
        done_tasks=done_tasks,
        in_progress_tasks=in_progress_tasks,
        overdue_tasks=overdue_tasks,
        due_soon_tasks=due_soon_tasks,
        unassigned_tasks=unassigned_tasks,
        completion_rate=(done_tasks / total_tasks) if total_tasks else 0.0,
        next_due_date=next_due_date,
        stage_breakdown=[
            StageBreakdownSchema(
                stage_id=stage.id,
                stage_name=stage.name,
                color=stage.color,
                is_done_stage=stage.is_done_stage,
                tasks_count=counts_by_stage.get(stage.id, 0),
            )
            for stage in stages
        ],
    )
