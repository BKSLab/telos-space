import logging
from datetime import UTC, datetime

from pydantic import ValidationError

from src.db.models.knowledge_index_jobs import KnowledgeEntityType
from src.db.models.project_members import ProjectMember, ProjectRole
from src.db.models.task_activity import TaskActivityEventType
from src.db.models.task_participants import TaskParticipant, TaskParticipantRole
from src.db.models.tasks import Task
from src.exceptions.knowledge import KnowledgeEventsServiceError
from src.exceptions.project_stages import (
    ProjectStageForeignProjectError,
    ProjectStageNotFoundError,
    ProjectStagesRepositoryError,
)
from src.exceptions.projects import ProjectNotFoundError, ProjectsRepositoryError
from src.exceptions.storage import TaskAttachmentStorageError
from src.exceptions.task_activity import TaskActivityRepositoryError
from src.exceptions.task_comments import TaskCommentsRepositoryError
from src.exceptions.task_reports import TaskReportsRepositoryError
from src.exceptions.tasks import (
    TaskChecklistConflictError,
    TaskChecklistValidationError,
    TaskDateRangeError,
    TaskManagerRequiredError,
    TaskNotFoundError,
    TaskNumberAllocationError,
    TaskNumberAlreadyExistsRepositoryError,
    TaskOutsideProjectError,
    TaskParticipantNotProjectMemberError,
    TaskPersonNotFoundError,
    TaskReporterPermissionError,
    TaskRevisionConflictError,
    TaskRevisionValidationError,
    TasksRepositoryError,
    TasksServiceError,
    TaskStatusOfProjectTaskError,
    TaskVersionConflictError,
)
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.exceptions.users import UsersRepositoryError
from src.exceptions.wbs_nodes import (
    WbsNodeForeignProjectError,
    WbsNodeNotFoundError,
    WbsNodesRepositoryError,
)
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.task_reports import TaskReportsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.repositories.wbs_nodes import WbsNodesRepository
from src.schemas.enums import TaskKind, TaskStatus
from src.schemas.task_checklists import TaskChecklistSchema
from src.schemas.task_reports import TaskReportSchema
from src.schemas.tasks import (
    MyTaskSchema,
    ProjectBaselineResultSchema,
    TaskContextSchema,
    TaskDetailSchema,
    TaskParticipantSchema,
    TaskSchema,
)
from src.services.auth import to_user_summary
from src.services.knowledge_events import KnowledgeEvents
from src.storage.task_attachments import TaskAttachmentStorage
from src.utils.checklists import checklist_text
from src.utils.task_filters import TaskFilters, task_matches

logger = logging.getLogger(__name__)
TASK_SEMANTIC_FIELDS = frozenset({"title", "description_md", "checklist"})
TASK_PARTICIPANT_FIELDS = frozenset({"executor_id", "reporter_id", "observer_ids"})

POSITION_STEP = 1000.0
NUMBER_ALLOCATION_ATTEMPTS = 5
PERSONAL_TASK_KEY = "T"
MY_TASKS_LIMIT = 500
STATUS_LABELS = {
    TaskStatus.NEW: "Новая",
    TaskStatus.IN_PROGRESS: "В работе",
    TaskStatus.REVIEW: "На проверке",
    TaskStatus.DONE: "Готово",
    TaskStatus.CANCELLED: "Отменена",
}
MY_TASK_ROLES = {
    "all": frozenset(TaskParticipantRole),
    "doing": frozenset({TaskParticipantRole.EXECUTOR}),
    "assigned": frozenset({TaskParticipantRole.REPORTER}),
    "watching": frozenset({TaskParticipantRole.OBSERVER}),
}

RepositoryErrors = (
    ProjectsRepositoryError,
    ProjectStagesRepositoryError,
    TasksRepositoryError,
    TaskReportsRepositoryError,
    TaskActivityRepositoryError,
    TaskCommentsRepositoryError,
    KnowledgeEventsServiceError,
    UnitOfWorkRepositoryError,
    WbsNodesRepositoryError,
    UsersRepositoryError,
)


class TasksService:
    """Задачи проектов и разовые задачи с локальными назначениями."""

    def __init__(
        self,
        tasks_repository: TasksRepository,
        members_repository: ProjectMembersRepository,
        participants_repository: TaskParticipantsRepository,
        projects_repository: ProjectsRepository,
        stages_repository: ProjectStagesRepository,
        comments_repository: TaskCommentsRepository,
        activity_repository: TaskActivityRepository,
        wbs_nodes_repository: WbsNodesRepository,
        unit_of_work: UnitOfWork,
        attachment_storage: TaskAttachmentStorage,
        knowledge_events: KnowledgeEvents,
        users_repository: UsersRepository | None = None,
        reports_repository: TaskReportsRepository | None = None,
    ):
        self.tasks_repository = tasks_repository
        self.members_repository = members_repository
        self.participants_repository = participants_repository
        self.projects_repository = projects_repository
        self.stages_repository = stages_repository
        self.comments_repository = comments_repository
        self.activity_repository = activity_repository
        self.wbs_nodes_repository = wbs_nodes_repository
        self.unit_of_work = unit_of_work
        self.attachment_storage = attachment_storage
        self.knowledge_events = knowledge_events
        self.users_repository = users_repository
        self.reports_repository = reports_repository

    async def get_task_list(
        self,
        project_id: int,
        stage_id: int | None = None,
        search: str | None = None,
        filters: TaskFilters | None = None,
    ) -> list[TaskSchema]:
        """Возвращает задачи проекта с контекстом комментариев и поиска.

        Args:
            project_id: Идентификатор проекта.
            stage_id: Опциональный фильтр по стадии.
            search: Опциональный поисковый запрос.
            filters: Исполнитель, приоритет, завершённость и сроки — те же
                правила, что у инструмента telos_list_tasks.

        Returns:
            Карточки задач в сохранённом порядке.

        Raises:
            ProjectNotFoundError: Если проект не найден.
            TasksServiceError: Если получить задачи не удалось.
        """
        try:
            project = await self._get_project(project_id=project_id)
            search_text = search.strip() if search else ""
            matching_ids = (
                await self._search_task_ids(project_id=project_id, search=search_text)
                if search_text
                else None
            )
            tasks = await self.tasks_repository.get_by_project(
                project_id=project_id,
                stage_id=stage_id,
                task_ids=matching_ids,
            )
            if filters is not None and not filters.empty:
                stages = await self.stages_repository.get_by_project(project_id)
                done = {item.id for item in stages if item.is_done_stage}
                tasks = [task for task in tasks if task_matches(task, filters, done_stage_ids=done)]
            task_ids = [task.id for task in tasks]
            comment_context = await self._build_comment_context(task_ids=task_ids)
            participant_context = await self.participants_repository.get_by_task_ids(task_ids)
            task_highlights = (
                await self.tasks_repository.get_search_highlights(
                    task_ids=task_ids,
                    search=search_text,
                )
                if search_text
                else {}
            )
            comment_highlights = (
                await self.comments_repository.get_search_highlights(
                    task_ids=task_ids,
                    search=search_text,
                )
                if search_text
                else {}
            )

            result: list[TaskSchema] = []
            for task in tasks:
                schema = to_task_schema(
                    task=task,
                    project_key=project.key,
                    participants=participant_context.get(task.id, []),
                )
                schema.comments_count, schema.last_comment = comment_context.get(task.id, (0, None))
                highlight = task_highlights.get(task.id) or comment_highlights.get(task.id)
                for field, value in (highlight or {}).items():
                    setattr(schema, field, value)
                result.append(schema)
            return result
        except RepositoryErrors as error:
            logger.error("❌ Ошибка получения задач проекта id=%s.", project_id, exc_info=True)
            raise TasksServiceError(str(error)) from error

    async def get_task(self, task_id: int) -> TaskDetailSchema:
        """Возвращает задачу по идентификатору.

        Args:
            task_id: Идентификатор задачи.

        Returns:
            Карточка задачи.

        Raises:
            TaskNotFoundError: Если задача не найдена.
            TasksServiceError: Если получить задачу не удалось.
        """
        try:
            task = await self._get_task(task_id=task_id)
            key_prefix = await self._key_prefix(task)
            participant_context = await self.participants_repository.get_by_task_ids([task.id])
            schema = to_task_schema(
                task=task,
                project_key=key_prefix,
                participants=participant_context.get(task.id, []),
            )
            comment_context = await self._build_comment_context(task_ids=[task.id])
            schema.comments_count, schema.last_comment = comment_context.get(task.id, (0, None))
            report = (
                await self.reports_repository.latest(task.id)
                if self.reports_repository is not None
                else None
            )
            return TaskDetailSchema(
                **schema.model_dump(),
                report=TaskReportSchema.model_validate(report) if report else None,
            )
        except RepositoryErrors as error:
            logger.error("❌ Ошибка получения задачи id=%s.", task_id, exc_info=True)
            raise TasksServiceError(str(error)) from error

    async def create_task(
        self,
        project_id: int,
        data: dict,
        created_by_user_id: int | None = None,
    ) -> TaskSchema:
        """Создаёт задачу в проекте и выдаёт ей сквозной номер.

        Args:
            project_id: Идентификатор проекта.
            data: Поля создаваемой задачи.

        Returns:
            Созданная задача.

        Raises:
            ProjectNotFoundError: Если проект не найден.
            ProjectStageNotFoundError: Если у проекта нет стадий или стадия не найдена.
            WbsNodeNotFoundError: Если указанный раздел ИСР не найден.
            TasksServiceError: Если создать задачу не удалось.
        """
        try:
            project = await self._get_project(project_id=project_id)
            payload = dict(data)
            if "checklist" in payload:
                payload["checklist"] = _normalize_checklist(payload["checklist"])
                payload["checklist_revision"] = 1 if payload["checklist"] is not None else 0
            participant_data = self._extract_participant_data(payload)
            if created_by_user_id is not None and participant_data.get("reporter_id") is None:
                participant_data["reporter_id"] = created_by_user_id
            if (
                created_by_user_id is not None
                and participant_data.get("reporter_id") != created_by_user_id
            ):
                await self._ensure_reporter_change_allowed(
                    project_id=project_id,
                    user_id=created_by_user_id,
                )
            assignments, selected_members = await self._resolve_participant_assignments(
                project_id=project_id,
                executor_id=participant_data.get("executor_id"),
                reporter_id=participant_data.get("reporter_id"),
                observer_ids=participant_data.get("observer_ids") or [],
            )
            if "executor_id" in participant_data:
                executor = selected_members.get(participant_data.get("executor_id"))
                payload["assignee"] = _member_display_name(executor) if executor else None
            self._validate_schedule(
                start_date=payload.get("start_date"),
                due_date=payload.get("due_date"),
            )
            payload["project_id"] = project_id
            payload["stage_id"] = await self._resolve_stage_id(
                project_id=project_id,
                stage_id=payload.get("stage_id"),
            )
            wbs_node_id = payload.get("wbs_node_id")
            if wbs_node_id is not None:
                await self._ensure_wbs_node_in_project(
                    node_id=wbs_node_id,
                    project_id=project_id,
                )
            max_position = await self.tasks_repository.get_max_position_by_stage(
                stage_id=payload["stage_id"]
            )
            payload["position"] = max_position + POSITION_STEP

            task = await self._save_with_number(project_id=project_id, payload=payload)
            if participant_data:
                await self._replace_participants(
                    task_id=task.id,
                    assignments=assignments,
                )
            await self.knowledge_events.upsert(
                project_id=project_id,
                entity_type=KnowledgeEntityType.TASK,
                entity_id=task.id,
            )
            await self.unit_of_work.commit()
            logger.info("✅ Задача %s-%s создана.", project.key, task.number)
            return await self._to_task_schema(task=task, project_key=project.key)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка создания задачи в проекте id=%s.", project_id, exc_info=True)
            await self.unit_of_work.rollback()
            raise TasksServiceError(str(error)) from error

    async def update_task(
        self,
        task_id: int,
        data: dict,
        updated_by_user_id: int | None = None,
    ) -> TaskSchema:
        """Обновляет задачу и фиксирует значимые изменения в истории.

        Args:
            task_id: Идентификатор задачи.
            data: Изменяемые поля и необязательная expected_revision из чтения задачи.

        Returns:
            Обновлённая задача.

        Raises:
            TaskNotFoundError: Если задача не найдена.
            TasksServiceError: Если обновить задачу не удалось.
        """
        try:
            payload = dict(data)
            expected_task_revision = payload.pop("expected_revision", None)
            if expected_task_revision is not None and (
                type(expected_task_revision) is not int or expected_task_revision < 1
            ):
                raise TaskRevisionValidationError("Недопустимая expected_revision.")
            if "checklist" in payload:
                expected_revision = payload.pop("checklist_revision", None)
                if type(expected_revision) is not int or expected_revision < 0:
                    raise TaskChecklistValidationError("Не указана текущая версия чек-листа.")
                payload["checklist"] = _normalize_checklist(payload["checklist"])
            elif "checklist_revision" in payload:
                raise TaskChecklistValidationError("Нельзя менять версию отдельно.")

            # Блокировка и populate_existing исключают проверку по старому ORM-снимку.
            task = await self.tasks_repository.get_for_update(task_id=task_id)
            if task is None:
                raise TaskNotFoundError(task_id=task_id)
            if expected_task_revision is not None and task.revision != expected_task_revision:
                await self.unit_of_work.rollback()
                raise TaskRevisionConflictError("Версия задачи изменилась после чтения.")
            if "checklist" in payload:
                if task.checklist_revision != expected_revision:
                    await self.unit_of_work.rollback()
                    raise TaskChecklistConflictError("Версия чек-листа изменилась.")
                payload["checklist_revision"] = expected_revision + 1
            key_prefix = await self._key_prefix(task)
            participant_patch = self._extract_participant_data(payload)
            self._validate_schedule(
                start_date=payload.get("start_date", task.start_date),
                due_date=payload.get("due_date", task.due_date),
            )
            assignments: list[dict] | None = None
            if participant_patch:
                current = (await self.participants_repository.get_by_task_ids([task.id])).get(
                    task.id,
                    [],
                )
                current_roles = _participant_user_ids(current)
                executor_id = participant_patch.get("executor_id", current_roles["executor_id"])
                reporter_id = participant_patch.get("reporter_id", current_roles["reporter_id"])
                if (
                    updated_by_user_id is not None
                    and "reporter_id" in participant_patch
                    and reporter_id != current_roles["reporter_id"]
                ):
                    if task.project_id is None:
                        await self._ensure_outside_reporter_change_allowed(
                            task=task,
                            user_id=updated_by_user_id,
                            current_reporter_id=current_roles["reporter_id"],
                        )
                    else:
                        await self._ensure_reporter_change_allowed(
                            project_id=task.project_id,
                            user_id=updated_by_user_id,
                        )
                observer_ids = participant_patch.get(
                    "observer_ids",
                    current_roles["observer_ids"],
                )
                if task.project_id is None:
                    assignments, executor_name = await self._resolve_user_assignments(
                        executor_id=executor_id,
                        reporter_id=reporter_id,
                        observer_ids=observer_ids or [],
                    )
                    if "executor_id" in participant_patch:
                        payload["assignee"] = executor_name
                else:
                    assignments, selected_members = await self._resolve_participant_assignments(
                        project_id=task.project_id,
                        executor_id=executor_id,
                        reporter_id=reporter_id,
                        observer_ids=observer_ids or [],
                    )
                    if "executor_id" in participant_patch:
                        executor = selected_members.get(executor_id)
                        payload["assignee"] = _member_display_name(executor) if executor else None

            await self._record_field_changes(task=task, data=payload)
            updated = (
                await self.tasks_repository.update(task=task, data=payload)
                if payload or assignments is not None
                else task
            )
            if assignments is not None:
                await self._replace_participants(
                    task_id=task.id,
                    assignments=assignments,
                )
            if TASK_SEMANTIC_FIELDS.intersection(payload):
                await self.knowledge_events.upsert(
                    project_id=updated.project_id,
                    entity_type=KnowledgeEntityType.TASK,
                    entity_id=updated.id,
                )
            await self.unit_of_work.commit()
            return await self._to_task_schema(task=updated, project_key=key_prefix)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка обновления задачи id=%s.", task_id, exc_info=True)
            await self.unit_of_work.rollback()
            raise TasksServiceError(str(error)) from error

    async def move_task(
        self,
        task_id: int,
        stage_id: int,
        position: float | None = None,
    ) -> TaskSchema:
        """Перемещает задачу по доске и фиксирует смену стадии.

        Args:
            task_id: Идентификатор задачи.
            stage_id: Идентификатор целевой стадии.
            position: Позиция внутри стадии; без значения задача встаёт в конец.

        Returns:
            Перемещённая задача.

        Raises:
            TaskNotFoundError: Если задача не найдена.
            ProjectStageNotFoundError: Если целевая стадия не найдена.
            ProjectStageForeignProjectError: Если стадия принадлежит другому проекту.
            TasksServiceError: Если переместить задачу не удалось.
        """
        try:
            task = await self._get_task(task_id=task_id)
            if task.project_id is None:
                raise TaskOutsideProjectError(task_id=task_id)
            project = await self._get_project(project_id=task.project_id)
            target_stage = await self.stages_repository.get_by_id(stage_id=stage_id)
            if target_stage is None:
                raise ProjectStageNotFoundError(stage_id=stage_id)
            if target_stage.project_id != task.project_id:
                raise ProjectStageForeignProjectError(
                    stage_id=stage_id,
                    project_id=task.project_id,
                )
            if task.stage_id == stage_id and position is None:
                return await self._to_task_schema(task=task, project_key=project.key)

            target_position = position
            if target_position is None:
                max_position = await self.tasks_repository.get_max_position_by_stage(
                    stage_id=stage_id
                )
                target_position = max_position + POSITION_STEP
            if task.stage_id != stage_id:
                current_stage = await self.stages_repository.get_by_id(stage_id=task.stage_id)
                await self.activity_repository.save(
                    task_id=task_id,
                    event_type=TaskActivityEventType.STAGE_CHANGED,
                    from_value=current_stage.name if current_stage else None,
                    to_value=target_stage.name,
                )
            update_data = {"stage_id": stage_id, "position": target_position}
            if target_stage.is_done_stage and task.completed_at is None:
                update_data["completed_at"] = datetime.now(UTC)
            elif not target_stage.is_done_stage and task.completed_at is not None:
                update_data["completed_at"] = None
            updated = await self.tasks_repository.update(
                task=task,
                data=update_data,
            )
            await self.unit_of_work.commit()
            return await self._to_task_schema(task=updated, project_key=project.key)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка перемещения задачи id=%s.", task_id, exc_info=True)
            raise TasksServiceError(str(error)) from error

    async def fix_baseline(self, task_id: int) -> TaskSchema:
        """Фиксирует текущий план задачи отдельной явной операцией."""
        try:
            task = await self._get_task(task_id=task_id)
            key_prefix = await self._key_prefix(task)
            previous = _baseline_value(task.baseline_start_date, task.baseline_due_date)
            current = _baseline_value(task.start_date, task.due_date)
            if previous != current:
                await self.activity_repository.save(
                    task_id=task.id,
                    event_type=TaskActivityEventType.BASELINE_CHANGED,
                    from_value=previous,
                    to_value=current,
                )
            updated = await self.tasks_repository.update(
                task=task,
                data={
                    "baseline_start_date": task.start_date,
                    "baseline_due_date": task.due_date,
                },
            )
            await self.unit_of_work.commit()
            return await self._to_task_schema(task=updated, project_key=key_prefix)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка фиксации baseline задачи id=%s.", task_id, exc_info=True)
            raise TasksServiceError(str(error)) from error

    async def fix_project_baseline(
        self, project_id: int, *, dry_run: bool = False
    ) -> ProjectBaselineResultSchema:
        """Фиксирует план всем задачам проекта с датами одной транзакцией.

        Задачи без обеих плановых дат не трогаются: у них нечего фиксировать,
        а пустой baseline затёр бы прежний. История пишется только там, где
        план действительно изменился. ``dry_run`` считает, ничего не записывая, —
        для текста подтверждения «перезапишет план у N задач».
        """
        try:
            await self._get_project(project_id=project_id)
            tasks = await self.tasks_repository.get_by_project(project_id=project_id)
            scheduled = [
                task for task in tasks if task.start_date is not None or task.due_date is not None
            ]
            changed = [
                task
                for task in scheduled
                if (task.baseline_start_date, task.baseline_due_date)
                != (task.start_date, task.due_date)
            ]
            if not dry_run:
                for task in changed:
                    await self.activity_repository.save(
                        task_id=task.id,
                        event_type=TaskActivityEventType.BASELINE_CHANGED,
                        from_value=_baseline_value(
                            task.baseline_start_date, task.baseline_due_date
                        ),
                        to_value=_baseline_value(task.start_date, task.due_date),
                    )
                    await self.tasks_repository.update(
                        task=task,
                        data={
                            "baseline_start_date": task.start_date,
                            "baseline_due_date": task.due_date,
                        },
                    )
                await self.unit_of_work.commit()
                logger.info(
                    "✅ План проекта id=%s зафиксирован. scheduled=%s, changed=%s.",
                    project_id,
                    len(scheduled),
                    len(changed),
                )
            return ProjectBaselineResultSchema(
                dry_run=dry_run,
                scheduled_count=len(scheduled),
                changed_count=len(changed),
                task_ids=[task.id for task in changed],
            )
        except RepositoryErrors as error:
            logger.error("❌ Ошибка фиксации плана проекта id=%s.", project_id, exc_info=True)
            raise TasksServiceError(str(error)) from error

    async def delete_task(
        self, task_id: int, *, expected_updated_at: datetime | None = None
    ) -> None:
        """Удаляет задачу вместе с её файлами.

        Args:
            task_id: Идентификатор задачи.
            expected_updated_at: Прочитанная версия для подтверждённого удаления агентом.

        Returns:
            ``None`` после успешного удаления.

        Raises:
            TaskNotFoundError: Если задача не найдена.
            TasksServiceError: Если удалить задачу не удалось.
        """
        try:
            task = (
                await self.tasks_repository.get_for_update(task_id=task_id)
                if expected_updated_at is not None
                else await self._get_task(task_id=task_id)
            )
            if task is None:
                raise TaskNotFoundError(task_id)
            if expected_updated_at is not None and task.updated_at != expected_updated_at:
                raise TaskVersionConflictError("Версия удаления устарела.")
            project_id = task.project_id
            await self.tasks_repository.delete(task=task)
            await self.knowledge_events.delete(
                project_id=project_id,
                entity_type=KnowledgeEntityType.TASK,
                entity_id=task_id,
            )
            await self.unit_of_work.commit()
            await self._cleanup_deleted_task_files(task_id=task_id)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка удаления задачи id=%s.", task_id, exc_info=True)
            raise TasksServiceError(str(error)) from error

    # Блок задач вне проекта: разовые задачи

    async def create_personal_task(
        self, *, data: dict, created_by_user_id: int, commit: bool = True
    ) -> TaskSchema:
        """Создаёт разовую задачу с автором в роли постановщика."""
        payload = dict(data)
        payload.pop("stage_id", None)
        payload.pop("role", None)
        for placement_field in ("wbs_node_id", "wbs_position", "canvas_x", "canvas_y"):
            payload.pop(placement_field, None)
        participants = self._extract_participant_data(payload)
        reporter_id = participants.get("reporter_id") or created_by_user_id
        if reporter_id != created_by_user_id:
            # Поставить задачу от чужого имени нельзя: постановщик — тот, кто её
            # создал; передать её потом может он сам.
            raise TaskManagerRequiredError()
        return await self.create_outside_project_task(
            key_prefix=PERSONAL_TASK_KEY,
            payload=payload,
            executor_id=participants.get("executor_id"),
            reporter_id=reporter_id,
            observer_ids=participants.get("observer_ids") or [],
            commit=commit,
        )

    async def create_outside_project_task(
        self,
        *,
        key_prefix: str,
        payload: dict,
        executor_id: int | None,
        reporter_id: int,
        observer_ids: list[int],
        commit: bool = True,
    ) -> TaskSchema:
        """Сохраняет разовую задачу, её назначения и номер в одной транзакции."""
        try:
            data = dict(payload)
            if "checklist" in data:
                data["checklist"] = _normalize_checklist(data["checklist"])
                data["checklist_revision"] = 1 if data["checklist"] is not None else 0
            self._validate_schedule(
                start_date=data.get("start_date"), due_date=data.get("due_date")
            )
            assignments, executor_name = await self._resolve_user_assignments(
                executor_id=executor_id,
                reporter_id=reporter_id,
                observer_ids=observer_ids,
            )
            data.update(
                {
                    "project_id": None,
                    "stage_id": None,
                    "status": TaskStatus.IN_PROGRESS if executor_id else TaskStatus.NEW,
                    "assignee": executor_name,
                    "position": 0.0,
                }
            )
            task = await self._save_outside_project(payload=data)
            await self.participants_repository.save_many(task.id, assignments)
            if commit:
                await self.unit_of_work.commit()
            logger.info("✅ Задача %s-%s создана вне проекта.", key_prefix, task.number)
            return await self._to_task_schema(task=task, project_key=key_prefix)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка создания задачи вне проекта.", exc_info=True)
            await self.unit_of_work.rollback()
            raise TasksServiceError(str(error)) from error

    async def change_status(self, *, task_id: int, status: TaskStatus) -> TaskSchema:
        """Меняет статус разовой задачи и сохраняет историю."""
        try:
            task = await self._get_task(task_id=task_id)
            if task.project_id is not None:
                raise TaskStatusOfProjectTaskError(task_id=task_id)
            key_prefix = await self._key_prefix(task)
            if task.status is status:
                return await self._to_task_schema(task=task, project_key=key_prefix)
            await self.activity_repository.save(
                task_id=task.id,
                event_type=TaskActivityEventType.STAGE_CHANGED,
                from_value=STATUS_LABELS.get(task.status),
                to_value=STATUS_LABELS[status],
            )
            changes: dict = {"status": status}
            if status is TaskStatus.DONE:
                changes["completed_at"] = datetime.now(UTC)
            elif task.completed_at is not None:
                changes["completed_at"] = None
            updated = await self.tasks_repository.update(task=task, data=changes)
            await self.unit_of_work.commit()
            logger.info("✅ Статус задачи id=%s: %s.", task_id, status.value)
            return await self._to_task_schema(task=updated, project_key=key_prefix)
        except RepositoryErrors as error:
            logger.error("❌ Ошибка смены статуса задачи id=%s.", task_id, exc_info=True)
            await self.unit_of_work.rollback()
            raise TasksServiceError(str(error)) from error

    async def get_my_tasks(
        self,
        *,
        user_id: int,
        role: str = "all",
        include_closed: bool = False,
        project_id: int | None = None,
        attention_task_ids: set[int] | None = None,
    ) -> list[MyTaskSchema]:
        """Возвращает задачи участника с фильтрами до ограничения списка.

        Args:
            user_id: Текущий локальный пользователь.
            role: Роль пользователя в задаче или все роли.
            include_closed: Включать выполненные и отменённые задачи.
            project_id: Ограничить список проектом; None включает разовые задачи.
            attention_task_ids: Задачи с выбранными непросмотренными событиями.

        Returns:
            Задачи пользователя, соответствующие всем выбранным фильтрам.

        Raises:
            TasksServiceError: Если список не удалось прочитать.
        """
        try:
            tasks = await self.tasks_repository.get_for_person(
                user_id=user_id,
                roles=set(MY_TASK_ROLES.get(role, MY_TASK_ROLES["all"])),
                include_closed=include_closed,
                project_id=project_id,
                limit=MY_TASKS_LIMIT,
                **(
                    {"attention_task_ids": attention_task_ids}
                    if attention_task_ids is not None
                    else {}
                ),
            )
            task_ids = [task.id for task in tasks]
            project_ids = {task.project_id for task in tasks if task.project_id is not None}
            projects = {
                item.id: item for item in await self.projects_repository.get_by_ids(project_ids)
            }
            stages_by_project: dict[int, list] = {}
            for stage in await self.stages_repository.get_by_project_ids(project_ids):
                stages_by_project.setdefault(stage.project_id, []).append(stage)
            participants = await self.participants_repository.get_by_task_ids(task_ids)
            my_roles = await self.participants_repository.get_person_roles(
                task_ids=task_ids, user_id=user_id
            )
            result: list[MyTaskSchema] = []
            for task in tasks:
                context, key_prefix = _task_context(task, projects=projects)
                state, label = task_state(task, stages_by_project.get(task.project_id, []))
                schema = to_task_schema(
                    task=task, project_key=key_prefix, participants=participants.get(task.id, [])
                )
                result.append(
                    MyTaskSchema(
                        **schema.model_dump(),
                        context=context,
                        state=state,
                        state_label=label,
                        my_roles=sorted(my_roles.get(task.id, set()), key=_participant_role_order),
                    )
                )
            return result
        except RepositoryErrors as error:
            logger.error("❌ Ошибка списка задач пользователя id=%s.", user_id, exc_info=True)
            raise TasksServiceError(str(error)) from error

    async def _key_prefix(self, task: Task) -> str:
        """Ключ проекта или общий префикс разовых задач T."""
        if task.project_id is not None:
            return (await self._get_project(project_id=task.project_id)).key
        return PERSONAL_TASK_KEY

    async def _save_outside_project(self, *, payload: dict) -> Task:
        """Сохраняет задачу вне проекта со следующим номером её счётчика."""
        for _ in range(NUMBER_ALLOCATION_ATTEMPTS):
            await self.tasks_repository.lock_personal_numbers()
            number = await self.tasks_repository.get_next_personal_number()
            try:
                return await self.tasks_repository.save(data={**payload, "number": number})
            except TaskNumberAlreadyExistsRepositoryError:
                logger.warning("⚠️ Номер %s задачи вне проекта занят, повторяю выдачу.", number)
        raise TaskNumberAllocationError(project_id=0)

    async def _resolve_user_assignments(
        self,
        *,
        executor_id: int | None,
        reporter_id: int | None,
        observer_ids: list[int],
    ) -> tuple[list[dict], str | None]:
        """Проверяет людей задачи вне проекта и строит назначения по пользователям.

        Returns:
            Назначения и подпись исполнителя для совместимого поля ``assignee``.
        """
        requested: list[tuple[TaskParticipantRole, int]] = []
        if executor_id is not None:
            requested.append((TaskParticipantRole.EXECUTOR, executor_id))
        if reporter_id is not None:
            requested.append((TaskParticipantRole.REPORTER, reporter_id))
        requested.extend(
            (TaskParticipantRole.OBSERVER, user_id) for user_id in dict.fromkeys(observer_ids)
        )
        if not requested:
            return [], None
        users = {
            user.id: user
            for user in await self.users_repository.get_by_ids({uid for _, uid in requested})
            if user.is_active
        }
        for _, user_id in requested:
            if user_id not in users:
                raise TaskPersonNotFoundError(user_id=user_id)
        executor = users.get(executor_id) if executor_id is not None else None
        return (
            [{"user_id": user_id, "role": role} for role, user_id in requested],
            user_display_name(executor) if executor is not None else None,
        )

    async def _ensure_outside_reporter_change_allowed(
        self, *, task: Task, user_id: int, current_reporter_id: int | None
    ) -> None:
        """Постановщика задачи вне проекта меняет только её OWNER."""
        if current_reporter_id == user_id:
            return
        raise TaskManagerRequiredError()


    async def _get_task(self, task_id: int) -> Task:
        """Возвращает задачу или поднимает доменную ошибку."""
        task = await self.tasks_repository.get_by_id(task_id=task_id)
        if task is None:
            raise TaskNotFoundError(task_id=task_id)
        return task

    async def _get_project(self, project_id: int):
        """Возвращает проект или поднимает доменную ошибку."""
        project = await self.projects_repository.get_by_id(project_id=project_id)
        if project is None:
            raise ProjectNotFoundError(project_id=project_id)
        return project

    async def _resolve_stage_id(self, project_id: int, stage_id: int | None) -> int:
        """Возвращает стадию создаваемой задачи, проверяя её принадлежность проекту."""
        stages = await self.stages_repository.get_by_project(project_id=project_id)
        if not stages:
            raise ProjectStageNotFoundError(stage_id=0)
        if stage_id is None:
            return stages[0].id
        if stage_id not in {stage.id for stage in stages}:
            stage = await self.stages_repository.get_by_id(stage_id=stage_id)
            if stage is None:
                raise ProjectStageNotFoundError(stage_id=stage_id)
            raise ProjectStageForeignProjectError(stage_id=stage_id, project_id=project_id)
        return stage_id

    async def _save_with_number(self, project_id: int, payload: dict) -> Task:
        """Сохраняет задачу, повторяя попытку при гонке за номером."""
        for _ in range(NUMBER_ALLOCATION_ATTEMPTS):
            number = await self.tasks_repository.get_next_number(project_id=project_id)
            try:
                return await self.tasks_repository.save(data={**payload, "number": number})
            except TaskNumberAlreadyExistsRepositoryError:
                logger.warning(
                    "⚠️ Номер %s занят в проекте id=%s, повторяю выдачу.",
                    number,
                    project_id,
                )
        raise TaskNumberAllocationError(project_id=project_id)

    @staticmethod
    def _extract_participant_data(payload: dict) -> dict:
        """Извлекает API-поля участников, чтобы они не попали в ORM Task."""
        return {field: payload.pop(field) for field in TASK_PARTICIPANT_FIELDS if field in payload}

    async def _resolve_participant_assignments(
        self,
        *,
        project_id: int,
        executor_id: int | None,
        reporter_id: int | None,
        observer_ids: list[int],
    ) -> tuple[list[dict], dict[int, ProjectMember]]:
        """Проверяет принадлежность пользователей команде и строит назначения."""
        requested: list[tuple[TaskParticipantRole, int]] = []
        if executor_id is not None:
            requested.append((TaskParticipantRole.EXECUTOR, executor_id))
        if reporter_id is not None:
            requested.append((TaskParticipantRole.REPORTER, reporter_id))
        requested.extend(
            (TaskParticipantRole.OBSERVER, user_id) for user_id in dict.fromkeys(observer_ids)
        )
        if not requested:
            return [], {}

        members = await self.members_repository.get_for_project(project_id=project_id)
        members_by_user_id = {member.user_id: member for member in members}
        for _, user_id in requested:
            if user_id not in members_by_user_id:
                raise TaskParticipantNotProjectMemberError(user_id=user_id)

        return (
            [
                {
                    "project_member_id": members_by_user_id[user_id].id,
                    "role": role,
                }
                for role, user_id in requested
            ],
            members_by_user_id,
        )

    async def _ensure_reporter_change_allowed(self, *, project_id: int, user_id: int) -> None:
        """Разрешает смену постановщика только владельцу проекта."""
        membership = await self.members_repository.get(
            project_id=project_id,
            user_id=user_id,
        )
        if membership is None or membership.role is not ProjectRole.OWNER:
            raise TaskReporterPermissionError()

    async def _to_task_schema(self, task: Task, project_key: str) -> TaskSchema:
        """Собирает задачу вместе с ролевыми участниками."""
        participant_context = await self.participants_repository.get_by_task_ids([task.id])
        return to_task_schema(
            task=task,
            project_key=project_key,
            participants=participant_context.get(task.id, []),
        )

    async def _search_task_ids(self, project_id: int, search: str) -> set[int]:
        """Объединяет совпадения по задачам и их комментариям."""
        task_ids = await self.tasks_repository.search_ids(
            project_id=project_id,
            search=search,
        )
        comment_task_ids = await self.comments_repository.search_task_ids(search=search)
        if comment_task_ids:
            project_task_ids = {
                task.id
                for task in await self.tasks_repository.get_by_project(project_id=project_id)
            }
            task_ids.update(comment_task_ids & project_task_ids)
        return task_ids

    async def _build_comment_context(self, task_ids: list[int]) -> dict[int, tuple[int, str]]:
        """Строит карту количества и текста последнего комментария по задачам."""
        if not task_ids:
            return {}
        comments = await self.comments_repository.get_all()
        wanted = set(task_ids)
        context: dict[int, tuple[int, str]] = {}
        for comment in comments:
            if comment.task_id not in wanted:
                continue
            count, _ = context.get(comment.task_id, (0, ""))
            context[comment.task_id] = (count + 1, comment.body_md)
        return context

    async def _record_field_changes(self, task: Task, data: dict) -> None:
        """Фиксирует изменения значимых полей задачи в истории."""
        if "checklist" in data and data["checklist"] != task.checklist:
            await self.activity_repository.save(
                task_id=task.id,
                event_type=TaskActivityEventType.CHECKLIST_CHANGED,
                from_value=(checklist_text(task.checklist).splitlines() or [None])[0],
                to_value=(checklist_text(data["checklist"]).splitlines() or [None])[0],
            )
        if "due_date" in data and data["due_date"] != task.due_date:
            await self.activity_repository.save(
                task_id=task.id,
                event_type=TaskActivityEventType.DUE_DATE_CHANGED,
                from_value=str(task.due_date) if task.due_date else None,
                to_value=str(data["due_date"]) if data["due_date"] else None,
            )
        if "start_date" in data and data["start_date"] != task.start_date:
            await self.activity_repository.save(
                task_id=task.id,
                event_type=TaskActivityEventType.START_DATE_CHANGED,
                from_value=str(task.start_date) if task.start_date else None,
                to_value=str(data["start_date"]) if data["start_date"] else None,
            )
        if "description_md" in data and data["description_md"] != task.description_md:
            await self.activity_repository.save(
                task_id=task.id,
                event_type=TaskActivityEventType.DESCRIPTION_CHANGED,
                from_value=None,
                to_value=None,
            )
        if "priority" in data and data["priority"] != task.priority:
            await self.activity_repository.save(
                task_id=task.id,
                event_type=TaskActivityEventType.PRIORITY_CHANGED,
                from_value=task.priority.value,
                to_value=str(getattr(data["priority"], "value", data["priority"])),
            )
        if "assignee" in data and data["assignee"] != task.assignee:
            await self.activity_repository.save(
                task_id=task.id,
                event_type=TaskActivityEventType.ASSIGNEE_CHANGED,
                from_value=task.assignee,
                to_value=data["assignee"],
            )

    async def _cleanup_deleted_task_files(self, task_id: int) -> None:
        """Best-effort очищает физический каталог удалённой задачи."""
        if self.attachment_storage is None:
            return
        try:
            await self.attachment_storage.delete_task_directory(task_id)
        except TaskAttachmentStorageError:
            logger.warning(
                "⚠️ Не удалось очистить каталог файлов удалённой задачи id=%s.",
                task_id,
                exc_info=True,
            )

    @staticmethod
    def _validate_schedule(*, start_date, due_date) -> None:
        """Не допускает обратный плановый интервал задачи."""
        if start_date is not None and due_date is not None and start_date > due_date:
            raise TaskDateRangeError()

    async def _replace_participants(self, task_id: int, assignments: list[dict]) -> None:
        """Заменяет ролевые назначения задачи: удаление и вставка.

        Обе операции однозапросные и выполняются внутри уже открытой
        транзакции сценария: их последовательность — оркестрация, а
        значит принадлежит сервису.
        """
        await self.participants_repository.delete_for_task(task_id)
        await self.participants_repository.save_many(task_id, assignments)

    async def _ensure_wbs_node_in_project(self, node_id: int, project_id: int) -> None:
        """Проверяет, что раздел ИСР существует и принадлежит тому же проекту."""
        node = await self.wbs_nodes_repository.get_by_id(node_id=node_id)
        if node is None:
            raise WbsNodeNotFoundError(node_id=node_id)
        if node.project_id != project_id:
            raise WbsNodeForeignProjectError(node_id=node_id, project_id=project_id)


def build_task_key(project_key: str, number: int) -> str:
    """Возвращает отображаемый идентификатор задачи вида ``PROJ-142``."""
    return f"{project_key}-{number}"


def _baseline_value(start_date, due_date) -> str:
    """Возвращает компактное значение baseline для TaskActivity."""
    return f"{start_date or '—'}..{due_date or '—'}"


def to_task_schema(
    task: Task,
    project_key: str,
    participants: list[TaskParticipant] | None = None,
) -> TaskSchema:
    """Преобразует ORM-задачу в схему ответа с отображаемым идентификатором.

    Args:
        task: ORM-модель задачи.
        project_key: Короткий код проекта для формирования ключа задачи.

    Returns:
        Схема задачи без агрегатов комментариев и поиска.
    """
    return TaskSchema(
        id=task.id,
        revision=getattr(task, "revision", 1),
        kind=task_kind(task),
        project_id=task.project_id,
        stage_id=task.stage_id,
        wbs_node_id=getattr(task, "wbs_node_id", None),
        status=getattr(task, "status", None),
        number=task.number,
        key=build_task_key(project_key=project_key, number=task.number),
        title=task.title,
        description_md=task.description_md,
        checklist=getattr(task, "checklist", None),
        checklist_revision=getattr(task, "checklist_revision", None) or 0,
        priority=task.priority,
        role=task.role,
        assignee=task.assignee,
        participants=[
            to_task_participant_schema(participant)
            for participant in sorted(
                participants or [],
                key=lambda item: (_participant_role_order(item.role), item.id),
            )
        ],
        start_date=task.start_date,
        due_date=task.due_date,
        baseline_start_date=task.baseline_start_date,
        baseline_due_date=task.baseline_due_date,
        completed_at=task.completed_at,
        position=task.position,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _normalize_checklist(value: dict | None) -> dict | None:
    """Проверяет агрегат также для внутренних клиентов и переводит UUID в JSON."""
    if value is None:
        return None
    try:
        return TaskChecklistSchema.model_validate(value).model_dump(mode="json")
    except ValidationError as error:
        raise TaskChecklistValidationError(str(error)) from error


def to_task_participant_schema(participant: TaskParticipant) -> TaskParticipantSchema:
    """Преобразует назначение в безопасную карточку участника задачи."""
    return TaskParticipantSchema(
        id=participant.id,
        role=participant.role,
        user=to_user_summary(participant_user(participant)),
    )


def participant_user(participant: TaskParticipant):
    """Пользователь назначения: через команду проекта или напрямую у задачи вне проекта."""
    if participant.project_member is not None:
        return participant.project_member.user
    return participant.user


def task_kind(task: Task) -> TaskKind:
    """Вид задачи по тому, какая из ссылок задана."""
    if task.project_id is not None:
        return TaskKind.PROJECT
    return TaskKind.PERSONAL


def _participant_role_order(role: TaskParticipantRole) -> int:
    """Стабильно располагает основные роли перед наблюдателями."""
    return {
        TaskParticipantRole.EXECUTOR: 0,
        TaskParticipantRole.REPORTER: 1,
        TaskParticipantRole.OBSERVER: 2,
    }[role]


def _participant_user_ids(participants: list[TaskParticipant]) -> dict:
    """Возвращает текущие user id по трём поддерживаемым ролям."""
    executor_id = next(
        (
            participant_user(item).id
            for item in participants
            if item.role is TaskParticipantRole.EXECUTOR
        ),
        None,
    )
    reporter_id = next(
        (
            participant_user(item).id
            for item in participants
            if item.role is TaskParticipantRole.REPORTER
        ),
        None,
    )
    observer_ids = [
        participant_user(item).id
        for item in participants
        if item.role is TaskParticipantRole.OBSERVER
    ]
    return {
        "executor_id": executor_id,
        "reporter_id": reporter_id,
        "observer_ids": observer_ids,
    }


def _member_display_name(member: ProjectMember) -> str:
    """Возвращает стабильную подпись исполнителя для старых read-моделей."""
    user = member.user
    return " ".join(part for part in (user.last_name, user.first_name) if part) or user.username


def _task_context(
    task: Task, *, projects: dict
) -> tuple[TaskContextSchema | None, str]:
    """Контекст задачи для общего списка и префикс её номера."""
    if task.project_id is not None and task.project_id in projects:
        project = projects[task.project_id]
        return (
            TaskContextSchema(
                kind=TaskKind.PROJECT,
                id=project.id,
                key=project.key,
                name=project.name,
                color=project.color,
                icon=project.icon,
                icon_image_version=project.icon_image_version,
            ),
            project.key,
        )
    return None, PERSONAL_TASK_KEY


def task_state(task: Task, stages: list) -> tuple[TaskStatus, str]:
    """Состояние задачи для общего списка.

    У задачи вне проекта — её статус. У проектной — по стадии: первая
    стадия доски — «новая», завершающая — «готово», прочие — «в работе»;
    подпись — имя стадии, как на доске проекта.
    """
    if task.project_id is None:
        status = task.status or TaskStatus.NEW
        return status, STATUS_LABELS[status]
    stage = next((item for item in stages if item.id == task.stage_id), None)
    if stage is None:
        return TaskStatus.IN_PROGRESS, STATUS_LABELS[TaskStatus.IN_PROGRESS]
    if stage.is_done_stage:
        return TaskStatus.DONE, stage.name
    if stages and stages[0].id == stage.id:
        return TaskStatus.NEW, stage.name
    return TaskStatus.IN_PROGRESS, stage.name


def user_display_name(user) -> str:
    """Full local user name for immutable authorship snapshots."""
    return " ".join(part for part in (user.last_name, user.first_name, user.middle_name) if part) or user.username
