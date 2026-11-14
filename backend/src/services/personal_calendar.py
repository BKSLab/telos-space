"""Календарь проектных и разовых задач с доступом по локальным назначениям."""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from src.db.models.project_milestones import ProjectMilestone, ProjectMilestoneStatus
from src.db.models.projects import Project, ProjectStatus
from src.db.models.tasks import Task, TaskPriority
from src.exceptions.calendar import CalendarServiceError
from src.exceptions.milestones import MilestonesRepositoryError
from src.exceptions.project_stages import ProjectStagesRepositoryError
from src.exceptions.projects import ProjectsRepositoryError
from src.exceptions.tasks import TasksRepositoryError
from src.exceptions.users import UsersRepositoryError
from src.repositories.milestones import MilestonesRepository
from src.repositories.project_members import ProjectMembersRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_participants import TaskParticipantsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.users import UsersRepository
from src.schemas.calendar import CalendarRangeSchema
from src.schemas.enums import TaskKind, TaskStatus
from src.schemas.my_calendar import (
    CalendarProjectRefSchema,
    CalendarSourceSchema,
    HolidayDaySchema,
    HolidayKind,
    HolidaysResponseSchema,
    MyCalendarMilestoneSchema,
    MyCalendarResponseSchema,
    MyCalendarSummarySchema,
    MyCalendarTaskSchema,
    MyUnscheduledTasksPageSchema,
    PortfolioCalendarProjectSchema,
    PortfolioCalendarResponseSchema,
    PortfolioMilestoneSchema,
)
from src.services.calendar import to_calendar_task, validate_calendar_range
from src.services.tasks import PERSONAL_TASK_KEY, executor_summary, task_state
from src.utils.deadlines import DUE_SOON_DAYS
from src.utils.holidays_ru import SOURCES, holiday_days

logger = logging.getLogger(__name__)

MAX_MY_CALENDAR_TASKS = 2000
PROJECT_DEADLINE_TITLE = "Дедлайн проекта"

RepositoryErrors = (
    MilestonesRepositoryError,
    ProjectStagesRepositoryError,
    ProjectsRepositoryError,
    TasksRepositoryError,
    UsersRepositoryError,
)


@dataclass(frozen=True, slots=True)
class PersonScope:
    """Календарь проектных и разовых задач с доступом по локальным назначениям."""

    visible_project_ids: set[int] | None
    member_project_ids: set[int]


@dataclass(frozen=True, slots=True)
class FeedItems:
    """Задачи и вехи для ленты .ics."""

    tasks: list[MyCalendarTaskSchema]
    milestones: list[MyCalendarMilestoneSchema]


class PersonalCalendarService:
    """Календарь проектных и разовых задач с доступом по локальным назначениям."""

    def __init__(
        self,
        *,
        users_repository: UsersRepository,
        members_repository: ProjectMembersRepository,
        projects_repository: ProjectsRepository,
        stages_repository: ProjectStagesRepository,
        tasks_repository: TasksRepository,
        milestones_repository: MilestonesRepository,
        participants_repository: TaskParticipantsRepository,
    ):
        self.users_repository = users_repository
        self.members_repository = members_repository
        self.projects_repository = projects_repository
        self.stages_repository = stages_repository
        self.tasks_repository = tasks_repository
        self.milestones_repository = milestones_repository
        self.participants_repository = participants_repository

    async def get_my_range(
        self,
        *,
        user_id: int,
        date_from: date,
        date_to: date,
        today: date,
        status: TaskStatus | None = None,
        priority: TaskPriority | None = None,
        project_id: int | None = None,
    ) -> MyCalendarResponseSchema:
        """Собирает мой календарь: задачи, где я исполнитель, и вехи моих проектов.

        Args:
            user_id: Пользователь запроса.
            date_from: Первый день диапазона включительно.
            date_to: Последний день диапазона включительно.
            today: Текущая дата клиента.
            status: Фильтр по общему состоянию задачи.
            priority: Фильтр по приоритету.
            project_id: Только задачи и вехи этого проекта.

        Returns:
            Задачи, вехи и сводка.

        Raises:
            CalendarRangeError: Если диапазон некорректен.
            CalendarServiceError: Если собрать календарь не удалось.
        """
        validate_calendar_range(date_from=date_from, date_to=date_to)
        try:
            scope = await self._scope(user_id)
            tasks = await self.tasks_repository.get_person_calendar_range(
                user_id=user_id,
                date_from=date_from,
                date_to=date_to,
                visible_project_ids=scope.visible_project_ids,
                limit=MAX_MY_CALENDAR_TASKS + 1,
                project_id=project_id,
                priority=priority,
                include_cancelled=status is TaskStatus.CANCELLED,
            )
            truncated = len(tasks) > MAX_MY_CALENDAR_TASKS
            items = await self._to_items(tasks[:MAX_MY_CALENDAR_TASKS], today=today)
            if status is not None:
                items = [item for item in items if item.state is status]
            milestone_projects = scope.member_project_ids
            if project_id is not None:
                milestone_projects = milestone_projects & {project_id}
            milestones = await self._member_milestones(
                project_ids=milestone_projects, date_from=date_from, date_to=date_to
            )
            counts = await self.tasks_repository.get_person_calendar_counts(
                user_id=user_id,
                visible_project_ids=scope.visible_project_ids,
                today=today,
                soon_until=today + timedelta(days=DUE_SOON_DAYS),
            )
            logger.info(
                "✅ Мой календарь пользователя id=%s собран: задач %s, вех %s.",
                user_id,
                len(items),
                len(milestones),
            )
            return MyCalendarResponseSchema(
                range=CalendarRangeSchema(date_from=date_from, date_to=date_to, today=today),
                tasks=items,
                milestones=milestones,
                summary=MyCalendarSummarySchema(
                    overdue=counts.overdue,
                    due_soon=counts.due_soon,
                    unscheduled=counts.unscheduled,
                ),
                truncated=truncated,
            )
        except RepositoryErrors as error:
            logger.error("❌ Не удалось собрать мой календарь id=%s.", user_id, exc_info=True)
            raise CalendarServiceError(str(error)) from error

    async def get_my_unscheduled(
        self, *, user_id: int, today: date, cursor: int | None, limit: int
    ) -> MyUnscheduledTasksPageSchema:
        """Возвращает страницу моих незавершённых задач без дат.

        Args:
            user_id: Пользователь запроса.
            today: Текущая дата клиента.
            cursor: Последний id прошлой страницы.
            limit: Размер страницы.

        Returns:
            Страница задач и курсор следующей.

        Raises:
            CalendarServiceError: Если получить страницу не удалось.
        """
        try:
            scope = await self._scope(user_id)
            tasks = await self.tasks_repository.get_person_unscheduled_page(
                user_id=user_id,
                visible_project_ids=scope.visible_project_ids,
                cursor=cursor,
                limit=limit,
            )
            page = tasks[:limit]
            return MyUnscheduledTasksPageSchema(
                items=await self._to_items(page, today=today),
                next_cursor=page[-1].id if len(tasks) > limit and page else None,
            )
        except RepositoryErrors as error:
            logger.error("❌ Мои задачи без срока id=%s не получены.", user_id, exc_info=True)
            raise CalendarServiceError(str(error)) from error

    async def get_feed_items(
        self, *, user_id: int, date_from: date, date_to: date, today: date
    ) -> FeedItems:
        """Задачи со сроком и вехи для ленты .ics — с правами пользователя сейчас.

        Args:
            user_id: Владелец ленты.
            date_from: Начало окна ленты.
            date_to: Конец окна ленты.
            today: Текущая дата сервера.

        Returns:
            Задачи и вехи окна.

        Raises:
            CalendarServiceError: Если собрать ленту не удалось.
        """
        try:
            scope = await self._scope(user_id)
            tasks = await self.tasks_repository.get_person_calendar_range(
                user_id=user_id,
                date_from=date_from,
                date_to=date_to,
                visible_project_ids=scope.visible_project_ids,
                limit=MAX_MY_CALENDAR_TASKS,
                require_due_date=True,
            )
            return FeedItems(
                tasks=await self._to_items(tasks, today=today),
                milestones=await self._member_milestones(
                    project_ids=scope.member_project_ids, date_from=date_from, date_to=date_to
                ),
            )
        except RepositoryErrors as error:
            logger.error("❌ Лента календаря id=%s не собрана.", user_id, exc_info=True)
            raise CalendarServiceError(str(error)) from error

    async def get_portfolio(
        self, *, user_id: int, date_from: date, date_to: date, today: date
    ) -> PortfolioCalendarResponseSchema:
        """Календарь проектных и разовых задач с доступом по локальным назначениям."""
        validate_calendar_range(date_from=date_from, date_to=date_to)
        try:
            scope = await self._scope(user_id)
            projects = [
                project
                for project in await self.projects_repository.get_all()
                if project.status is not ProjectStatus.ARCHIVED
                and (scope.visible_project_ids is None or project.id in scope.visible_project_ids)
            ]
            dated = [
                project
                for project in projects
                if _project_start(project) is not None
                and _project_start(project) <= date_to
                and _project_end(project) >= date_from
            ]
            dated.sort(key=lambda project: (_project_start(project), project.key))
            unscheduled = sorted(
                (project for project in projects if _project_start(project) is None),
                key=lambda project: project.key,
            )
            shown = [*dated, *unscheduled]
            milestones = await self.milestones_repository.get_range_for_projects(
                project_ids={project.id for project in shown},
                date_from=date_from,
                date_to=date_to,
            )
            by_project: dict[int, list[ProjectMilestone]] = {}
            for milestone in milestones:
                by_project.setdefault(milestone.project_id, []).append(milestone)
            counters = {
                row.project_id: row
                for row in await self.tasks_repository.get_portfolio_counters(
                    today=today, soon_until=today + timedelta(days=DUE_SOON_DAYS)
                )
            }
            return PortfolioCalendarResponseSchema(
                range=CalendarRangeSchema(date_from=date_from, date_to=date_to, today=today),
                projects=[
                    _portfolio_project(
                        project,
                        counters=counters.get(project.id),
                        milestones=by_project.get(project.id, []),
                        date_from=date_from,
                        date_to=date_to,
                    )
                    for project in shown
                ],
            )
        except RepositoryErrors as error:
            logger.error("❌ Календарь портфеля id=%s не собран.", user_id, exc_info=True)
            raise CalendarServiceError(str(error)) from error

    @staticmethod
    def get_holidays(year: int) -> HolidaysResponseSchema:
        """Возвращает производственный календарь РФ на год.

        Args:
            year: Календарный год.

        Returns:
            Отклонения от пятидневки; ``known=false``, если данных на год нет.
        """
        days = holiday_days(year)
        if days is None:
            return HolidaysResponseSchema(year=year, known=False, source=None, days=[])
        return HolidaysResponseSchema(
            year=year,
            known=True,
            source=SOURCES[year],
            days=[
                HolidayDaySchema(date=day, kind=HolidayKind(kind), title=title)
                for day, kind, title in days
            ],
        )

    async def _scope(self, user_id: int) -> PersonScope:
        """Projects visible to the local user follow their current memberships."""
        project_ids = await self.members_repository.get_project_ids_for_user(user_id=user_id)
        return PersonScope(
            visible_project_ids=set(project_ids), member_project_ids=set(project_ids)
        )

    async def _to_items(self, tasks: list[Task], *, today: date) -> list[MyCalendarTaskSchema]:
        """Добавляет к задачам источник и состояние, как в «Моих задачах»."""
        project_ids = {task.project_id for task in tasks if task.project_id is not None}
        projects = {
            item.id: item for item in await self.projects_repository.get_by_ids(project_ids)
        }
        stages_by_project: dict[int, list] = {}
        for stage in await self.stages_repository.get_by_project_ids(project_ids):
            stages_by_project.setdefault(stage.project_id, []).append(stage)
        participants = await self.participants_repository.get_by_task_ids(
            [task.id for task in tasks]
        )
        items: list[MyCalendarTaskSchema] = []
        for task in tasks:
            source, key_prefix = _task_source(task, projects=projects)
            state, label = task_state(task, stages_by_project.get(task.project_id, []))
            items.append(
                to_calendar_task(
                    task=task,
                    project_key=key_prefix,
                    is_done=state in (TaskStatus.DONE, TaskStatus.CANCELLED),
                    today=today,
                    schema=MyCalendarTaskSchema,
                    source=source,
                    state=state,
                    state_label=label,
                    executor=executor_summary(participants.get(task.id, [])),
                )
            )
        return items

    async def _member_milestones(
        self, *, project_ids: set[int], date_from: date, date_to: date
    ) -> list[MyCalendarMilestoneSchema]:
        """Вехи неархивных проектов в диапазоне плюс системный «Дедлайн проекта»."""
        if not project_ids:
            return []
        # Вехи архивного проекта — уже история, в календаре они только шумят.
        projects = {
            project.id: project
            for project in await self.projects_repository.get_by_ids(project_ids)
            if project.status is not ProjectStatus.ARCHIVED
        }
        milestones = await self.milestones_repository.get_range_for_projects(
            project_ids=set(projects), date_from=date_from, date_to=date_to
        )
        result = [
            MyCalendarMilestoneSchema(
                id=milestone.id,
                title=milestone.title,
                due_date=milestone.due_date,
                status=milestone.status,
                description_md=milestone.description_md,
                wbs_node_id=milestone.wbs_node_id,
                is_system=False,
                project=_project_ref(projects[milestone.project_id]),
            )
            for milestone in milestones
            if milestone.project_id in projects
        ]
        result += [
            MyCalendarMilestoneSchema(
                id=None,
                title=PROJECT_DEADLINE_TITLE,
                due_date=project.due_date,
                status=_deadline_status(project),
                description_md=None,
                wbs_node_id=None,
                is_system=True,
                project=_project_ref(project),
            )
            for project in projects.values()
            if project.due_date is not None and date_from <= project.due_date <= date_to
        ]
        result.sort(key=lambda item: (item.due_date, item.project.key, item.id or 0))
        return result


def _task_source(task: Task, *, projects: dict) -> tuple[CalendarSourceSchema, str]:
    """Источник задачи для календаря и префикс её номера."""
    if task.project_id is not None and task.project_id in projects:
        project = projects[task.project_id]
        return (
            CalendarSourceSchema(
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
    return CalendarSourceSchema(kind=TaskKind.PERSONAL), PERSONAL_TASK_KEY


def _project_ref(project: Project) -> CalendarProjectRefSchema:
    """Короткая ссылка на проект."""
    return CalendarProjectRefSchema(
        id=project.id, key=project.key, name=project.name, color=project.color
    )


def _deadline_status(project: Project) -> ProjectMilestoneStatus:
    """Статус системной вехи «Дедлайн проекта»."""
    if project.status is ProjectStatus.COMPLETED:
        return ProjectMilestoneStatus.ACHIEVED
    return ProjectMilestoneStatus.PLANNED


def _project_start(project: Project) -> date | None:
    """Начало интервала проекта; без начала — срок."""
    return project.start_date or project.due_date


def _project_end(project: Project) -> date | None:
    """Конец интервала проекта; без срока — начало."""
    return project.due_date or project.start_date


def _portfolio_project(
    project: Project,
    *,
    counters,
    milestones: list[ProjectMilestone],
    date_from: date,
    date_to: date,
) -> PortfolioCalendarProjectSchema:
    """Строит проект шкалы портфеля с показателями как на дашборде."""
    total = int(getattr(counters, "total", 0) or 0)
    done = int(getattr(counters, "done", 0) or 0)
    items = [
        PortfolioMilestoneSchema(
            id=milestone.id,
            title=milestone.title,
            due_date=milestone.due_date,
            status=milestone.status,
        )
        for milestone in milestones
    ]
    if project.due_date is not None and date_from <= project.due_date <= date_to:
        items.append(
            PortfolioMilestoneSchema(
                id=None,
                title=PROJECT_DEADLINE_TITLE,
                due_date=project.due_date,
                status=_deadline_status(project),
                is_system=True,
            )
        )
    items.sort(key=lambda item: (item.due_date, item.id or 0))
    return PortfolioCalendarProjectSchema(
        id=project.id,
        key=project.key,
        name=project.name,
        color=project.color,
        icon=project.icon,
        icon_image_version=project.icon_image_version,
        status=project.status,
        start_date=project.start_date,
        due_date=project.due_date,
        is_unscheduled=_project_start(project) is None,
        completion_rate=(done / total) if total else 0.0,
        total_tasks=total,
        overdue_tasks=int(getattr(counters, "overdue", 0) or 0),
        milestones=items,
    )
