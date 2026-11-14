import logging
from datetime import date, timedelta
from time import perf_counter

from src.db.models.project_milestones import ProjectMilestoneStatus
from src.db.models.projects import ProjectStatus
from src.db.models.tasks import Task, TaskPriority
from src.exceptions.calendar import CalendarFilterError, CalendarRangeError, CalendarServiceError
from src.exceptions.milestones import MilestonesRepositoryError
from src.exceptions.project_stages import ProjectStagesRepositoryError
from src.exceptions.projects import ProjectNotFoundError, ProjectsRepositoryError
from src.exceptions.task_activity import TaskActivityRepositoryError
from src.exceptions.task_dependencies import TaskDependenciesRepositoryError
from src.exceptions.tasks import TasksRepositoryError
from src.exceptions.wbs_nodes import WbsNodesRepositoryError
from src.repositories.milestones import MilestonesRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_dependencies import TaskDependenciesRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.wbs_nodes import WbsNodesRepository
from src.schemas.calendar import (
    CalendarDateChangeSchema,
    CalendarDependencySchema,
    CalendarMilestoneSchema,
    CalendarProjectSchema,
    CalendarRangeSchema,
    CalendarResponseSchema,
    CalendarRiskReasonSchema,
    CalendarStageSchema,
    CalendarSummarySchema,
    CalendarTaskSchema,
    CalendarWbsNodeSchema,
    UnscheduledTasksPageSchema,
)
from src.services.critical_path import checklist_progress, critical_path
from src.services.tasks import build_task_key, executor_summary
from src.utils.deadlines import DUE_SOON_DAYS, is_task_due_soon, is_task_overdue

logger = logging.getLogger(__name__)

MAX_CALENDAR_RANGE_DAYS = 370


class CalendarService:
    """Сервис read model календаря проекта."""

    def __init__(
        self,
        projects_repository: ProjectsRepository,
        tasks_repository: TasksRepository,
        stages_repository: ProjectStagesRepository,
        wbs_nodes_repository: WbsNodesRepository,
        activity_repository: TaskActivityRepository,
        milestones_repository: MilestonesRepository,
        dependencies_repository: TaskDependenciesRepository,
        participants_repository: TaskParticipantsRepository,
    ):
        self.projects_repository = projects_repository
        self.tasks_repository = tasks_repository
        self.stages_repository = stages_repository
        self.wbs_nodes_repository = wbs_nodes_repository
        self.activity_repository = activity_repository
        self.milestones_repository = milestones_repository
        self.dependencies_repository = dependencies_repository
        self.participants_repository = participants_repository

    async def get_range(
        self,
        *,
        project_id: int,
        date_from: date,
        date_to: date,
        today: date,
        stage_id: int | None = None,
        priority: TaskPriority | None = None,
        assignee: str | None = None,
        wbs_node_id: int | None = None,
    ) -> CalendarResponseSchema:
        """Возвращает календарные факты и справочники выбранного проекта."""
        started = perf_counter()
        self._validate_range(date_from=date_from, date_to=date_to)
        try:
            project = await self.projects_repository.get_by_id(project_id=project_id)
            if project is None:
                raise ProjectNotFoundError(project_id=project_id)
            stages = await self.stages_repository.get_by_project(project_id=project_id)
            nodes = await self.wbs_nodes_repository.get_by_project(project_id=project_id)
            self._validate_filters(
                stage_id=stage_id,
                wbs_node_id=wbs_node_id,
                stage_ids={stage.id for stage in stages},
                node_ids={node.id for node in nodes},
            )
            tasks = await self.tasks_repository.get_calendar_range(
                project_id=project_id,
                date_from=date_from,
                date_to=date_to,
                stage_id=stage_id,
                priority=priority,
                assignee=assignee,
                wbs_node_id=wbs_node_id,
            )
            counts = await self.tasks_repository.get_calendar_counts(
                project_id=project_id,
                today=today,
                soon_until=today + timedelta(days=DUE_SOON_DAYS),
                stage_id=stage_id,
                priority=priority,
                assignee=assignee,
                wbs_node_id=wbs_node_id,
            )
            assignees = await self.tasks_repository.get_calendar_assignees(project_id=project_id)
            milestones = await self.milestones_repository.get_range(
                project_id=project_id,
                date_from=date_from,
                date_to=date_to,
            )
            dependencies = await self.dependencies_repository.get_by_project(project_id)
            related_task_ids = {
                task_id
                for dependency in dependencies
                for task_id in (
                    dependency.predecessor_task_id,
                    dependency.successor_task_id,
                )
            }
            related_tasks = {
                task.id: task for task in await self.tasks_repository.get_by_ids(related_task_ids)
            }
            changes = await self.activity_repository.get_recent_due_date_changes(
                project_id=project_id,
                limit=12,
            )
            changed_tasks = {
                task.id: task
                for task in await self.tasks_repository.get_by_ids(
                    {change.task_id for change in changes}
                )
            }
            done_stage_ids = {stage.id for stage in stages if stage.is_done_stage}
            dependency_risks = _dependency_risks(
                dependencies=dependencies,
                tasks=related_tasks,
                done_stage_ids=done_stage_ids,
                project_key=project.key,
                project_due_date=project.due_date,
                milestones=milestones,
            )
            latest_open_end = await self.tasks_repository.get_latest_open_end_date(
                project_id=project_id
            )
            critical = critical_path(
                tasks={**related_tasks, **{task.id: task for task in tasks}},
                dependencies=dependencies,
                done_stage_ids=done_stage_ids,
                project_due_date=project.due_date,
                latest_open_due_date=latest_open_end,
            )
            participants = await self.participants_repository.get_by_task_ids(
                [task.id for task in tasks]
            )
            result = CalendarResponseSchema(
                range=CalendarRangeSchema(date_from=date_from, date_to=date_to, today=today),
                project=CalendarProjectSchema(
                    start_date=project.start_date,
                    due_date=project.due_date,
                ),
                tasks=[
                    _to_calendar_task(
                        task=task,
                        project_key=project.key,
                        is_done=task.stage_id in done_stage_ids,
                        today=today,
                        extra_risks=dependency_risks.get(task.id, []),
                        is_critical=task.id in critical.task_ids,
                        executor=executor_summary(participants.get(task.id, [])),
                    )
                    for task in tasks
                ],
                stages=[
                    CalendarStageSchema.model_validate(stage, from_attributes=True)
                    for stage in stages
                ],
                wbs_nodes=[
                    CalendarWbsNodeSchema.model_validate(node, from_attributes=True)
                    for node in nodes
                ],
                assignees=assignees,
                summary=CalendarSummarySchema(
                    overdue=counts.overdue,
                    due_soon=counts.due_soon,
                    unscheduled=counts.unscheduled,
                    drifted=counts.drifted,
                    dependency_risks=sum(bool(dependency_risks.get(task.id)) for task in tasks),
                ),
                recent_changes=[
                    CalendarDateChangeSchema(
                        id=change.id,
                        task_id=change.task_id,
                        task_key=build_task_key(
                            project_key=project.key,
                            number=changed_tasks[change.task_id].number,
                        ),
                        task_title=changed_tasks[change.task_id].title,
                        from_date=_parse_activity_date(change.from_value),
                        to_date=_parse_activity_date(change.to_value),
                        changed_at=change.created_at,
                    )
                    for change in changes
                    if change.task_id in changed_tasks
                ],
                milestones=[
                    CalendarMilestoneSchema(
                        id=milestone.id,
                        title=milestone.title,
                        due_date=milestone.due_date,
                        status=milestone.status,
                        wbs_node_id=milestone.wbs_node_id,
                        description_md=milestone.description_md,
                        is_system=False,
                    )
                    for milestone in milestones
                ]
                + (
                    [
                        CalendarMilestoneSchema(
                            id=None,
                            title="Дедлайн проекта",
                            due_date=project.due_date,
                            status=(
                                ProjectMilestoneStatus.ACHIEVED
                                if getattr(project, "status", None) is ProjectStatus.COMPLETED
                                else ProjectMilestoneStatus.PLANNED
                            ),
                            wbs_node_id=None,
                            description_md=None,
                            is_system=True,
                        )
                    ]
                    if project.due_date is not None and date_from <= project.due_date <= date_to
                    else []
                ),
                dependencies=[
                    CalendarDependencySchema(
                        id=dependency.id,
                        predecessor_task_id=dependency.predecessor_task_id,
                        successor_task_id=dependency.successor_task_id,
                        dependency_type=dependency.dependency_type,
                        lag_days=dependency.lag_days,
                        is_critical=dependency.id in critical.dependency_ids,
                    )
                    for dependency in dependencies
                ],
            )
            logger.info(
                "✅ Календарь проекта id=%s собран. date_from=%s, date_to=%s, "
                "tasks_count=%s, duration_ms=%.3f.",
                project_id,
                date_from,
                date_to,
                len(tasks),
                (perf_counter() - started) * 1000,
            )
            return result
        except (
            ProjectsRepositoryError,
            MilestonesRepositoryError,
            ProjectStagesRepositoryError,
            TaskActivityRepositoryError,
            TaskDependenciesRepositoryError,
            TasksRepositoryError,
            WbsNodesRepositoryError,
        ) as error:
            logger.error(
                "❌ Не удалось собрать календарь проекта id=%s.", project_id, exc_info=True
            )
            raise CalendarServiceError(str(error)) from error

    async def get_unscheduled(
        self,
        *,
        project_id: int,
        today: date,
        cursor: int | None,
        limit: int,
        stage_id: int | None = None,
        priority: TaskPriority | None = None,
        assignee: str | None = None,
        wbs_node_id: int | None = None,
    ) -> UnscheduledTasksPageSchema:
        """Возвращает курсорную страницу задач без обеих плановых дат."""
        try:
            project = await self.projects_repository.get_by_id(project_id=project_id)
            if project is None:
                raise ProjectNotFoundError(project_id=project_id)
            stages = await self.stages_repository.get_by_project(project_id=project_id)
            nodes = await self.wbs_nodes_repository.get_by_project(project_id=project_id)
            self._validate_filters(
                stage_id=stage_id,
                wbs_node_id=wbs_node_id,
                stage_ids={stage.id for stage in stages},
                node_ids={node.id for node in nodes},
            )
            tasks = await self.tasks_repository.get_unscheduled_page(
                project_id=project_id,
                cursor=cursor,
                limit=limit,
                stage_id=stage_id,
                priority=priority,
                assignee=assignee,
                wbs_node_id=wbs_node_id,
            )
            has_more = len(tasks) > limit
            page = tasks[:limit]
            participants = await self.participants_repository.get_by_task_ids(
                [task.id for task in page]
            )
            done_stage_ids = {stage.id for stage in stages if stage.is_done_stage}
            return UnscheduledTasksPageSchema(
                items=[
                    _to_calendar_task(
                        task=task,
                        project_key=project.key,
                        is_done=task.stage_id in done_stage_ids,
                        today=today,
                        executor=executor_summary(participants.get(task.id, [])),
                    )
                    for task in page
                ],
                next_cursor=page[-1].id if has_more and page else None,
            )
        except (
            ProjectsRepositoryError,
            ProjectStagesRepositoryError,
            TaskActivityRepositoryError,
            TasksRepositoryError,
            WbsNodesRepositoryError,
        ) as error:
            logger.error(
                "❌ Не удалось получить задачи без срока проекта id=%s.",
                project_id,
                exc_info=True,
            )
            raise CalendarServiceError(str(error)) from error

    @staticmethod
    def _validate_range(*, date_from: date, date_to: date) -> None:
        """Проверяет порядок и максимальную длину диапазона."""
        if date_to < date_from:
            raise CalendarRangeError("Конец диапазона не может быть раньше начала.")
        if (date_to - date_from).days > MAX_CALENDAR_RANGE_DAYS:
            raise CalendarRangeError(
                f"Диапазон календаря не может превышать {MAX_CALENDAR_RANGE_DAYS + 1} день."
            )

    @staticmethod
    def _validate_filters(
        *,
        stage_id: int | None,
        wbs_node_id: int | None,
        stage_ids: set[int],
        node_ids: set[int],
    ) -> None:
        """Проверяет принадлежность ссылочных фильтров проекту."""
        if stage_id is not None and stage_id not in stage_ids:
            raise CalendarFilterError("stage_id")
        if wbs_node_id is not None and wbs_node_id not in node_ids:
            raise CalendarFilterError("wbs_node_id")


def _to_calendar_task(
    *,
    task: Task,
    project_key: str,
    is_done: bool,
    today: date,
    extra_risks: list[CalendarRiskReasonSchema] | None = None,
    schema: type[CalendarTaskSchema] = CalendarTaskSchema,
    **extra_fields,
) -> CalendarTaskSchema:
    """Преобразует задачу в компактный календарный контракт.

    ``schema`` и ``extra_fields`` нужны личному календарю: его задача —
    наследник календарной с источником и состоянием.
    """
    due_date = task.due_date
    risk_level, risk_reasons = _task_risk(
        due_date=due_date,
        assignee=task.assignee,
        is_done=is_done,
        today=today,
    )
    extra_risks = extra_risks or []
    risk_reasons.extend(extra_risks)
    progress = checklist_progress(task, is_done=is_done)
    if any(reason.code in {"NEGATIVE_SLACK", "MILESTONE_AT_RISK"} for reason in extra_risks):
        risk_level = "high"
    elif extra_risks and risk_level not in {"high", "medium"}:
        risk_level = "medium"
    if due_date is None:
        return schema(
            id=task.id,
            key=build_task_key(project_key=project_key, number=task.number),
            title=task.title,
            start_date=task.start_date,
            due_date=None,
            baseline_start_date=task.baseline_start_date,
            baseline_due_date=task.baseline_due_date,
            drift_days=None,
            stage_id=task.stage_id,
            wbs_node_id=task.wbs_node_id,
            priority=task.priority,
            assignee=task.assignee,
            is_done=is_done,
            is_overdue=False,
            is_due_soon=False,
            risk_level=risk_level,
            risk_reasons=risk_reasons,
            updated_at=task.updated_at,
            progress=progress,
            **extra_fields,
        )
    return schema(
        id=task.id,
        key=build_task_key(project_key=project_key, number=task.number),
        title=task.title,
        start_date=task.start_date,
        due_date=due_date,
        baseline_start_date=task.baseline_start_date,
        baseline_due_date=task.baseline_due_date,
        drift_days=(
            (due_date - task.baseline_due_date).days if task.baseline_due_date is not None else None
        ),
        stage_id=task.stage_id,
        wbs_node_id=task.wbs_node_id,
        priority=task.priority,
        assignee=task.assignee,
        is_done=is_done,
        is_overdue=is_task_overdue(due_date=due_date, is_done=is_done, today=today),
        is_due_soon=is_task_due_soon(
            due_date=due_date,
            is_done=is_done,
            today=today,
            soon_until=today + timedelta(days=DUE_SOON_DAYS),
        ),
        risk_level=risk_level,
        risk_reasons=risk_reasons,
        updated_at=task.updated_at,
        progress=progress,
        **extra_fields,
    )


def _task_risk(
    *,
    due_date: date | None,
    assignee: str | None,
    is_done: bool,
    today: date,
) -> tuple[str | None, list[CalendarRiskReasonSchema]]:
    """Возвращает уровень и детерминированные причины риска задачи."""
    if is_done:
        return None, []
    reasons: list[CalendarRiskReasonSchema] = []
    level: str | None = None
    if due_date is None:
        level = "medium"
        reasons.append(
            CalendarRiskReasonSchema(code="NO_DUE_DATE", message="У задачи не задан срок.")
        )
    elif is_task_overdue(due_date=due_date, is_done=False, today=today):
        days = (today - due_date).days
        level = "high"
        reasons.append(
            CalendarRiskReasonSchema(
                code="OVERDUE",
                message=f"Срок просрочен на {days} дн.",
                days=days,
            )
        )
    elif is_task_due_soon(
        due_date=due_date,
        is_done=False,
        today=today,
        soon_until=today + timedelta(days=DUE_SOON_DAYS),
    ):
        days = (due_date - today).days
        level = "medium"
        reasons.append(
            CalendarRiskReasonSchema(
                code="DUE_SOON",
                message="Срок сегодня." if days == 0 else f"До срока {days} дн.",
                days=days,
            )
        )
    if not assignee:
        level = level or "low"
        reasons.append(
            CalendarRiskReasonSchema(
                code="NO_ASSIGNEE",
                message="У задачи нет исполнителя.",
            )
        )
    return level, reasons


def _parse_activity_date(value: str | None) -> date | None:
    """Безопасно разбирает date-only значение существующей истории."""
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _dependency_risks(
    *,
    dependencies,
    tasks: dict[int, Task],
    done_stage_ids: set[int],
    project_key: str,
    project_due_date: date | None,
    milestones,
) -> dict[int, list[CalendarRiskReasonSchema]]:
    """Строит объяснимые сигналы из графа Finish-to-Start."""
    result: dict[int, list[CalendarRiskReasonSchema]] = {}

    def add(task_id: int, reason: CalendarRiskReasonSchema) -> None:
        result.setdefault(task_id, []).append(reason)

    for dependency in dependencies:
        predecessor = tasks.get(dependency.predecessor_task_id)
        successor = tasks.get(dependency.successor_task_id)
        if predecessor is None or successor is None:
            continue
        predecessor_done = predecessor.stage_id in done_stage_ids
        successor_done = successor.stage_id in done_stage_ids
        predecessor_key = build_task_key(project_key=project_key, number=predecessor.number)
        successor_key = build_task_key(project_key=project_key, number=successor.number)
        if not predecessor_done and not successor_done:
            add(
                predecessor.id,
                CalendarRiskReasonSchema(
                    code="BLOCKED_SUCCESSOR",
                    message=f"Незавершённая задача блокирует {successor_key}.",
                    task_key=successor_key,
                ),
            )
        successor_start = successor.start_date or successor.due_date
        if predecessor.due_date is not None and successor_start is not None and not successor_done:
            required_start = predecessor.due_date + timedelta(days=dependency.lag_days)
            if successor_start < required_start:
                days = (required_start - successor_start).days
                add(
                    successor.id,
                    CalendarRiskReasonSchema(
                        code="NEGATIVE_SLACK",
                        message=(
                            f"Начало на {days} дн. раньше допустимого после {predecessor_key}."
                        ),
                        days=days,
                        task_key=predecessor_key,
                    ),
                )
        if successor.due_date is None or successor_done:
            continue
        deadlines = [
            ("Дедлайн проекта", project_due_date),
            *[
                (milestone.title, milestone.due_date)
                for milestone in milestones
                if milestone.wbs_node_id in {None, successor.wbs_node_id}
            ],
        ]
        missed = [
            (title, due) for title, due in deadlines if due is not None and successor.due_date > due
        ]
        if missed:
            title, due = min(missed, key=lambda item: item[1])
            days = (successor.due_date - due).days
            add(
                successor.id,
                CalendarRiskReasonSchema(
                    code="MILESTONE_AT_RISK",
                    message=f"Завершение позже вехи «{title}» на {days} дн.",
                    days=days,
                    milestone_title=title,
                ),
            )
    return result


def validate_calendar_range(*, date_from: date, date_to: date) -> None:
    """Проверяет порядок и максимальную длину диапазона любого календаря.

    Raises:
        CalendarRangeError: Если конец раньше начала или диапазон длиннее предела.
    """
    if date_to < date_from:
        raise CalendarRangeError("Конец диапазона не может быть раньше начала.")
    if (date_to - date_from).days > MAX_CALENDAR_RANGE_DAYS:
        raise CalendarRangeError(
            f"Диапазон календаря не может превышать {MAX_CALENDAR_RANGE_DAYS + 1} день."
        )


to_calendar_task = _to_calendar_task
