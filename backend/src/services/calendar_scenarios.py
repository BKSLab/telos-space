import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, timedelta
from time import perf_counter

from src.db.models.task_activity import TaskActivityEventType
from src.db.models.tasks import Task
from src.exceptions.calendar import (
    CalendarScenarioConflictError,
    CalendarScenarioVersionConflictError,
    CalendarServiceError,
)
from src.exceptions.milestones import MilestonesRepositoryError
from src.exceptions.projects import ProjectNotFoundError, ProjectsRepositoryError
from src.exceptions.task_activity import TaskActivityRepositoryError
from src.exceptions.task_dependencies import TaskDependenciesRepositoryError
from src.exceptions.tasks import (
    TaskDateChangeReasonError,
    TaskDateRangeError,
    TaskNotFoundError,
    TasksRepositoryError,
)
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.repositories.milestones import MilestonesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_dependencies import TaskDependenciesRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.calendar import CalendarRiskReasonSchema
from src.schemas.calendar_scenarios import (
    ScenarioApplyResponseSchema,
    ScenarioChangeSource,
    ScenarioConflictSchema,
    ScenarioNormalizedChangeSchema,
    ScenarioPreviewResponseSchema,
    ScenarioTaskDatesSchema,
)
from src.services.task_dates import task_dates_value, validate_date_change_reason
from src.services.tasks import build_task_key

logger = logging.getLogger(__name__)

RepositoryErrors = (
    MilestonesRepositoryError,
    ProjectsRepositoryError,
    TaskActivityRepositoryError,
    TaskDependenciesRepositoryError,
    TasksRepositoryError,
    UnitOfWorkRepositoryError,
)


@dataclass(slots=True)
class ProposedDates:
    """Внутреннее представление дат и причины их появления."""

    start_date: date | None
    due_date: date | None
    source: ScenarioChangeSource
    reasons: list[CalendarRiskReasonSchema]


class CalendarScenarioService:
    """Рассчитывает последствия и атомарно применяет подтверждённый сценарий."""

    def __init__(
        self,
        projects_repository: ProjectsRepository,
        tasks_repository: TasksRepository,
        dependencies_repository: TaskDependenciesRepository,
        milestones_repository: MilestonesRepository,
        activity_repository: TaskActivityRepository,
        unit_of_work: UnitOfWork,
    ) -> None:
        self.projects_repository = projects_repository
        self.tasks_repository = tasks_repository
        self.dependencies_repository = dependencies_repository
        self.milestones_repository = milestones_repository
        self.activity_repository = activity_repository
        self.unit_of_work = unit_of_work

    async def preview(self, project_id: int, changes: list[dict]) -> ScenarioPreviewResponseSchema:
        """Строит proposed state, не выполняя ни одной операции записи."""
        started = perf_counter()
        try:
            project = await self.projects_repository.get_by_id(project_id)
            if project is None:
                raise ProjectNotFoundError(project_id=project_id)
            tasks = await self.tasks_repository.get_by_project(project_id)
            tasks_by_id = {task.id: task for task in tasks}
            self._ensure_tasks_exist(tasks_by_id, changes)
            dependencies = await self.dependencies_repository.get_by_project(project_id)
            milestones = await self.milestones_repository.get_by_project(project_id)
            proposed = _build_proposed_dates(
                changes=changes,
                tasks_by_id=tasks_by_id,
                dependencies=dependencies,
                project_key=project.key,
            )
            conflicts = _scenario_conflicts(
                proposed=proposed,
                tasks_by_id=tasks_by_id,
                dependencies=dependencies,
                project_key=project.key,
                affected_only=True,
            )
            _append_milestone_risks(
                proposed=proposed,
                tasks_by_id=tasks_by_id,
                project_due_date=project.due_date,
                milestones=milestones,
            )
            normalized = [
                _to_normalized_change(
                    task=tasks_by_id[task_id],
                    proposed=item,
                    project_key=project.key,
                )
                for task_id, item in proposed.items()
                if _dates_changed(tasks_by_id[task_id], item)
            ]
            result = ScenarioPreviewResponseSchema(
                changes=normalized,
                conflicts=conflicts,
                consequences_count=sum(
                    item.source is ScenarioChangeSource.CASCADE for item in normalized
                ),
                can_apply=not conflicts and bool(normalized),
            )
            logger.info(
                "✅ Preview календарного сценария проекта id=%s рассчитан. "
                "changes_count=%s, conflicts_count=%s, duration_ms=%.3f.",
                project_id,
                len(normalized),
                len(conflicts),
                (perf_counter() - started) * 1000,
            )
            return result
        except RepositoryErrors as error:
            logger.error(
                "❌ Ошибка preview календарного сценария проекта id=%s.",
                project_id,
                exc_info=True,
            )
            raise CalendarServiceError(str(error)) from error

    async def apply(
        self, project_id: int, changes: list[dict], *, date_change_reason: str | None = None
    ) -> ScenarioApplyResponseSchema:
        """Повторно проверяет версии и применяет даты одной транзакцией."""
        started = perf_counter()
        try:
            project = await self.projects_repository.get_by_id(project_id)
            if project is None:
                raise ProjectNotFoundError(project_id=project_id)
            tasks = await self.tasks_repository.get_by_project(project_id, for_update=True)
            tasks_by_id = {task.id: task for task in tasks}
            self._ensure_tasks_exist(tasks_by_id, changes)
            if any(
                tasks_by_id[item["task_id"]].updated_at != item["expected_updated_at"]
                for item in changes
            ):
                raise CalendarScenarioVersionConflictError()
            proposed = {
                item["task_id"]: ProposedDates(
                    start_date=item["start_date"],
                    due_date=item["due_date"],
                    source=ScenarioChangeSource.DIRECT,
                    reasons=[],
                )
                for item in changes
            }
            dependencies = await self.dependencies_repository.get_by_project(project_id)
            conflicts = _scenario_conflicts(
                proposed=proposed,
                tasks_by_id=tasks_by_id,
                dependencies=dependencies,
                project_key=project.key,
                affected_only=True,
            )
            if conflicts:
                raise CalendarScenarioConflictError(conflicts[0].message)
            changed_ids = [
                task_id
                for task_id, item in proposed.items()
                if _dates_changed(tasks_by_id[task_id], item)
            ]
            reasons = {
                task_id: validate_date_change_reason(
                    previous_start=tasks_by_id[task_id].start_date,
                    previous_due=tasks_by_id[task_id].due_date,
                    next_start=proposed[task_id].start_date,
                    next_due=proposed[task_id].due_date,
                    reason=date_change_reason,
                )
                for task_id in changed_ids
            }
            for task_id in changed_ids:
                task = tasks_by_id[task_id]
                item = proposed[task_id]
                await self.activity_repository.save(
                    task_id=task.id,
                    event_type=TaskActivityEventType.DATES_CHANGED,
                    from_value=task_dates_value(task.start_date, task.due_date),
                    to_value=task_dates_value(item.start_date, item.due_date),
                    reason=reasons[task_id],
                )
                await self.tasks_repository.update(
                    task,
                    {"start_date": item.start_date, "due_date": item.due_date},
                )
            await self.unit_of_work.commit()
            logger.info(
                "✅ Календарный сценарий проекта id=%s применён. "
                "changes_count=%s, conflicts_count=0, duration_ms=%.3f.",
                project_id,
                len(changed_ids),
                (perf_counter() - started) * 1000,
            )
            return ScenarioApplyResponseSchema(
                applied_count=len(changed_ids),
                task_ids=changed_ids,
            )
        except RepositoryErrors as error:
            logger.error(
                "❌ Ошибка применения календарного сценария проекта id=%s.",
                project_id,
                exc_info=True,
            )
            await self.unit_of_work.rollback()
            raise CalendarServiceError(str(error)) from error

        except (
            TaskDateChangeReasonError,
            TaskDateRangeError,
            TaskNotFoundError,
            CalendarScenarioConflictError,
            CalendarScenarioVersionConflictError,
        ):
            await self.unit_of_work.rollback()
            raise

    @staticmethod
    def _ensure_tasks_exist(tasks_by_id: dict[int, Task], changes: list[dict]) -> None:
        for item in changes:
            if item["task_id"] not in tasks_by_id:
                raise TaskNotFoundError(item["task_id"])
            if (
                item["start_date"] is not None
                and item["due_date"] is not None
                and item["start_date"] > item["due_date"]
            ):
                raise TaskDateRangeError()


def _build_proposed_dates(*, changes, tasks_by_id, dependencies, project_key):
    proposed = {
        item["task_id"]: ProposedDates(
            start_date=item["start_date"],
            due_date=item["due_date"],
            source=ScenarioChangeSource.DIRECT,
            reasons=[],
        )
        for item in changes
    }
    successors = defaultdict(list)
    for dependency in dependencies:
        successors[dependency.predecessor_task_id].append(dependency)
    queue = deque(proposed)
    while queue:
        predecessor_id = queue.popleft()
        predecessor_dates = proposed.get(predecessor_id)
        predecessor = tasks_by_id[predecessor_id]
        predecessor_due = (
            predecessor_dates.due_date if predecessor_dates is not None else predecessor.due_date
        )
        if predecessor_due is None:
            continue
        for dependency in successors[predecessor_id]:
            successor = tasks_by_id.get(dependency.successor_task_id)
            if successor is None:
                continue
            current = proposed.get(successor.id)
            start_date = current.start_date if current is not None else successor.start_date
            due_date = current.due_date if current is not None else successor.due_date
            effective_start = start_date or due_date
            required_start = predecessor_due + timedelta(days=dependency.lag_days)
            if effective_start is not None and effective_start >= required_start:
                continue
            delta = 0 if effective_start is None else (required_start - effective_start).days
            if effective_start is None:
                next_start = None
                next_due = required_start
            else:
                next_start = start_date + timedelta(days=delta) if start_date is not None else None
                next_due = (
                    due_date + timedelta(days=delta) if due_date is not None else required_start
                )
            predecessor_key = build_task_key(project_key=project_key, number=predecessor.number)
            reasons = list(current.reasons) if current is not None else []
            reasons.append(
                CalendarRiskReasonSchema(
                    code="SUCCESSOR_SHIFTED",
                    message=f"Сдвиг вслед за {predecessor_key}.",
                    days=max(delta, 0),
                    task_key=predecessor_key,
                )
            )
            proposed[successor.id] = ProposedDates(
                start_date=next_start,
                due_date=next_due,
                source=(current.source if current is not None else ScenarioChangeSource.CASCADE),
                reasons=reasons,
            )
            queue.append(successor.id)
    return proposed


def _scenario_conflicts(
    *,
    proposed,
    tasks_by_id,
    dependencies,
    project_key,
    affected_only=False,
):
    conflicts = []
    for dependency in dependencies:
        if affected_only and not {
            dependency.predecessor_task_id,
            dependency.successor_task_id,
        }.intersection(proposed):
            continue
        predecessor = tasks_by_id.get(dependency.predecessor_task_id)
        successor = tasks_by_id.get(dependency.successor_task_id)
        if predecessor is None or successor is None:
            continue
        predecessor_dates = proposed.get(predecessor.id)
        successor_dates = proposed.get(successor.id)
        predecessor_due = (
            predecessor_dates.due_date if predecessor_dates is not None else predecessor.due_date
        )
        successor_start = (
            successor_dates.start_date or successor_dates.due_date
            if successor_dates is not None
            else successor.start_date or successor.due_date
        )
        predecessor_key = build_task_key(project_key=project_key, number=predecessor.number)
        successor_key = build_task_key(project_key=project_key, number=successor.number)
        if predecessor_due is None:
            conflicts.append(
                ScenarioConflictSchema(
                    code="UNSCHEDULED_PREDECESSOR",
                    message=f"У predecessor {predecessor_key} нет даты завершения.",
                    task_id=predecessor.id,
                    task_key=predecessor_key,
                )
            )
        elif successor_start is not None:
            required_start = predecessor_due + timedelta(days=dependency.lag_days)
            if successor_start < required_start:
                conflicts.append(
                    ScenarioConflictSchema(
                        code="FINISH_TO_START_CONFLICT",
                        message=f"{successor_key} начинается раньше завершения {predecessor_key}.",
                        task_id=successor.id,
                        task_key=successor_key,
                    )
                )
    return conflicts


def _append_milestone_risks(*, proposed, tasks_by_id, project_due_date, milestones):
    for task_id, item in proposed.items():
        task = tasks_by_id[task_id]
        if item.due_date is None:
            continue
        deadlines = [("Дедлайн проекта", project_due_date)] + [
            (milestone.title, milestone.due_date)
            for milestone in milestones
            if milestone.wbs_node_id in {None, task.wbs_node_id}
        ]
        missed = [(title, due) for title, due in deadlines if due and item.due_date > due]
        if not missed:
            continue
        title, due = min(missed, key=lambda value: value[1])
        days = (item.due_date - due).days
        item.reasons.append(
            CalendarRiskReasonSchema(
                code="MILESTONE_AT_RISK",
                message=f"Предложенный срок позже вехи «{title}» на {days} дн.",
                days=days,
                milestone_title=title,
            )
        )


def _to_normalized_change(*, task, proposed, project_key):
    return ScenarioNormalizedChangeSchema(
        task_id=task.id,
        task_key=build_task_key(project_key=project_key, number=task.number),
        task_title=task.title,
        current=ScenarioTaskDatesSchema(start_date=task.start_date, due_date=task.due_date),
        proposed=ScenarioTaskDatesSchema(
            start_date=proposed.start_date,
            due_date=proposed.due_date,
        ),
        expected_updated_at=task.updated_at,
        source=proposed.source,
        reasons=proposed.reasons,
    )


def _dates_changed(task, proposed):
    return task.start_date != proposed.start_date or task.due_date != proposed.due_date
