import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Result, Row, and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.project_members import ProjectMember
from src.db.models.project_stages import ProjectStage
from src.db.models.projects import Project
from src.db.models.task_participants import TaskParticipant, TaskParticipantRole
from src.db.models.tasks import Task, TaskPriority
from src.exceptions.tasks import (
    TaskNumberAlreadyExistsRepositoryError,
    TasksRepositoryError,
)
from src.schemas.enums import TaskStatus
from src.utils.db_errors import get_integrity_constraint_name
from src.utils.deadlines import due_soon_sql, overdue_sql
from src.utils.fts import (
    EXCERPT_HEADLINE_OPTIONS,
    HIGHLIGHT_START,
    TITLE_HEADLINE_OPTIONS,
    build_ts_query,
)

logger = logging.getLogger(__name__)
CLOSED_STATUSES = (TaskStatus.DONE, TaskStatus.CANCELLED)
PERSONAL_NUMBERS_LOCK_KEY = 7_310_001

TASK_NUMBER_CONSTRAINTS = frozenset({"uq_tasks_project_number", "uq_tasks_personal_number"})


@dataclass(frozen=True, slots=True)
class ProjectTaskStatistics:
    """Ограниченный набор агрегатов задач для Арис."""

    total: int
    overdue: int
    by_stage: dict[int, int]
    by_priority: dict[str, int]
    by_assignee: dict[str, int]


@dataclass(frozen=True, slots=True)
class CalendarTaskCounts:
    """Счётчики задач для сводки календаря."""

    overdue: int
    due_soon: int
    unscheduled: int
    drifted: int


class TasksRepository:
    """Репозиторий задач проекта."""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def get_calendar_range(
        self,
        *,
        project_id: int,
        date_from: date,
        date_to: date,
        stage_id: int | None = None,
        priority: TaskPriority | None = None,
        assignee: str | None = None,
        wbs_node_id: int | None = None,
    ) -> list[Task]:
        """Возвращает задачи, чьи известные плановые интервалы пересекают диапазон.

        Для интервала с одной известной границей отсутствующая граница
        подменяется известной датой. Задача только с ``start_date`` поэтому
        показывается на временной карте и не входит в список без плана.
        """
        try:
            interval_start = func.coalesce(Task.start_date, Task.due_date)
            interval_end = func.coalesce(Task.due_date, Task.start_date)
            stmt = select(Task).where(
                Task.project_id == project_id,
                interval_start <= date_to,
                interval_end >= date_from,
            )
            stmt = _apply_calendar_filters(
                stmt,
                stage_id=stage_id,
                priority=priority,
                assignee=assignee,
                wbs_node_id=wbs_node_id,
            )
            result: Result = await self.db_session.execute(
                stmt.order_by(Task.due_date, Task.position, Task.id)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить диапазон календаря проекта id=%s.",
                project_id,
                exc_info=True,
            )
            raise TasksRepositoryError("Ошибка получения диапазона календаря.") from error

    async def get_unscheduled_page(
        self,
        *,
        project_id: int,
        cursor: int | None,
        limit: int,
        stage_id: int | None = None,
        priority: TaskPriority | None = None,
        assignee: str | None = None,
        wbs_node_id: int | None = None,
    ) -> list[Task]:
        """Возвращает страницу задач без обеих плановых дат с курсором по id.

        Задача с известным ``start_date`` относится только к временной карте,
        даже если её открытый конец ``due_date`` ещё не задан.
        """
        try:
            stmt = select(Task).where(
                Task.project_id == project_id,
                Task.start_date.is_(None),
                Task.due_date.is_(None),
            )
            if cursor is not None:
                stmt = stmt.where(Task.id > cursor)
            stmt = _apply_calendar_filters(
                stmt,
                stage_id=stage_id,
                priority=priority,
                assignee=assignee,
                wbs_node_id=wbs_node_id,
            )
            result: Result = await self.db_session.execute(stmt.order_by(Task.id).limit(limit + 1))
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить задачи без срока проекта id=%s.",
                project_id,
                exc_info=True,
            )
            raise TasksRepositoryError("Ошибка получения задач без срока.") from error

    async def get_calendar_counts(
        self,
        *,
        project_id: int,
        today: date,
        soon_until: date,
        stage_id: int | None = None,
        priority: TaskPriority | None = None,
        assignee: str | None = None,
        wbs_node_id: int | None = None,
    ) -> CalendarTaskCounts:
        """Считает сигналы срока по всему проекту с учётом фильтров."""
        try:
            stmt = (
                select(
                    func.count(Task.id)
                    .filter(
                        overdue_sql(
                            due_date_column=Task.due_date,
                            is_done_column=ProjectStage.is_done_stage,
                            today=today,
                        )
                    )
                    .label("overdue"),
                    func.count(Task.id)
                    .filter(
                        due_soon_sql(
                            due_date_column=Task.due_date,
                            is_done_column=ProjectStage.is_done_stage,
                            today=today,
                            soon_until=soon_until,
                        )
                    )
                    .label("due_soon"),
                    func.count(Task.id)
                    .filter(Task.start_date.is_(None), Task.due_date.is_(None))
                    .label("unscheduled"),
                    func.count(Task.id)
                    .filter(
                        Task.baseline_due_date.is_not(None),
                        Task.due_date.is_not(None),
                        Task.due_date != Task.baseline_due_date,
                    )
                    .label("drifted"),
                )
                .join(ProjectStage, ProjectStage.id == Task.stage_id)
                .where(Task.project_id == project_id)
            )
            stmt = _apply_calendar_filters(
                stmt,
                stage_id=stage_id,
                priority=priority,
                assignee=assignee,
                wbs_node_id=wbs_node_id,
            )
            row = (await self.db_session.execute(stmt)).one()
            return CalendarTaskCounts(
                overdue=int(row.overdue),
                due_soon=int(row.due_soon),
                unscheduled=int(row.unscheduled),
                drifted=int(row.drifted),
            )
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось посчитать календарь проекта id=%s.",
                project_id,
                exc_info=True,
            )
            raise TasksRepositoryError("Ошибка подсчёта календаря.") from error

    async def get_calendar_assignees(self, *, project_id: int) -> list[str]:
        """Возвращает уникальные непустые подписи исполнителей проекта."""
        try:
            result = await self.db_session.execute(
                select(Task.assignee)
                .where(
                    Task.project_id == project_id,
                    Task.assignee.is_not(None),
                    Task.assignee != "",
                )
                .distinct()
                .order_by(Task.assignee)
            )
            return [value for value in result.scalars().all() if value]
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить исполнителей проекта id=%s.",
                project_id,
                exc_info=True,
            )
            raise TasksRepositoryError("Ошибка получения исполнителей календаря.") from error

    async def get_by_project(
        self,
        project_id: int,
        stage_id: int | None = None,
        task_ids: set[int] | None = None,
    ) -> list[Task]:
        """Возвращает задачи проекта с опциональными фильтрами.

        Args:
            project_id: Идентификатор проекта.
            stage_id: Опциональный идентификатор стадии.
            task_ids: Опциональный набор идентификаторов задач.

        Returns:
            Задачи проекта в сохранённом порядке.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            stmt = (
                select(Task).where(Task.project_id == project_id).order_by(Task.position, Task.id)
            )
            if stage_id is not None:
                stmt = stmt.where(Task.stage_id == stage_id)
            if task_ids is not None:
                if not task_ids:
                    return []
                stmt = stmt.where(Task.id.in_(task_ids))
            result: Result = await self.db_session.execute(stmt)
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить задачи проекта id=%s.", project_id, exc_info=True)
            raise TasksRepositoryError(
                f"Ошибка получения задач проекта id={project_id}."
            ) from error

    async def get_by_id(self, task_id: int) -> Task | None:
        """Возвращает задачу по идентификатору.

        Args:
            task_id: Идентификатор задачи.

        Returns:
            Найденная задача или ``None``.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(select(Task).where(Task.id == task_id))
            return result.scalar_one_or_none()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить задачу id=%s.", task_id, exc_info=True)
            raise TasksRepositoryError(f"Ошибка получения задачи id={task_id}.") from error

    async def get_for_update(self, *, task_id: int) -> Task | None:
        """Читает актуальную задачу с блокировкой до конца транзакции.

        Args:
            task_id: Изменяемая задача.

        Returns:
            Задача либо None.

        Raises:
            TasksRepositoryError: Ошибка получения блокировки.
        """
        try:
            result = await self.db_session.execute(
                select(Task)
                .where(Task.id == task_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            return result.scalar_one_or_none()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось заблокировать задачу id=%s.", task_id, exc_info=True)
            raise TasksRepositoryError(f"Ошибка блокировки задачи id={task_id}.") from error

    async def get_by_project_number(self, project_id: int, number: int) -> Task | None:
        """Возвращает задачу по отображаемому номеру внутри проекта."""
        try:
            result: Result = await self.db_session.execute(
                select(Task).where(Task.project_id == project_id, Task.number == number)
            )
            return result.scalar_one_or_none()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить задачу номер=%s проекта id=%s.",
                number,
                project_id,
                exc_info=True,
            )
            raise TasksRepositoryError("Ошибка получения задачи по номеру.") from error

    async def get_by_ids(self, task_ids: set[int]) -> list[Task]:
        """Возвращает задачи по набору идентификаторов.

        Args:
            task_ids: Идентификаторы задач.

        Returns:
            Найденные задачи.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        if not task_ids:
            return []
        try:
            result: Result = await self.db_session.execute(
                select(Task).where(Task.id.in_(task_ids))
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить набор задач.", exc_info=True)
            raise TasksRepositoryError("Ошибка получения набора задач.") from error

    async def get_ids_by_wbs_nodes(self, node_ids: set[int]) -> list[int]:
        """Возвращает идентификаторы задач, назначенных указанным разделам ИСР."""
        if not node_ids:
            return []
        try:
            result: Result = await self.db_session.execute(
                select(Task.id).where(Task.wbs_node_id.in_(node_ids)).order_by(Task.id)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить задачи разделов ИСР.", exc_info=True)
            raise TasksRepositoryError("Ошибка получения задач разделов ИСР.") from error

    async def search_ranked(
        self,
        *,
        project_id: int,
        search: str,
        limit: int = 30,
    ) -> list[Task]:
        """Возвращает ограниченный список задач по убыванию FTS-релевантности."""
        if not search.strip() or limit < 1:
            return []
        try:
            ts_query = build_ts_query(search)
            rank = func.ts_rank_cd(Task.search_vector, ts_query)
            number = _extract_task_number(search)
            condition = Task.search_vector.op("@@")(ts_query)
            exact_number = case((Task.number == number, 1), else_=0) if number else 0
            if number is not None:
                condition = or_(condition, Task.number == number)
            result: Result = await self.db_session.execute(
                select(Task)
                .where(Task.project_id == project_id, condition)
                .order_by(exact_number.desc() if number else rank.desc(), rank.desc(), Task.id)
                .limit(limit)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось ранжировать задачи проекта id=%s.", project_id, exc_info=True
            )
            raise TasksRepositoryError("Ошибка ранжированного поиска задач.") from error

    async def get_project_statistics(
        self,
        *,
        project_id: int,
        today: date,
    ) -> ProjectTaskStatistics:
        """Возвращает агрегаты задач проекта без загрузки самих задач."""
        try:
            rows = (
                await self.db_session.execute(
                    select(
                        Task.stage_id,
                        Task.priority,
                        Task.assignee,
                        func.count(Task.id).label("tasks_count"),
                        func.count(Task.id)
                        .filter(
                            overdue_sql(
                                due_date_column=Task.due_date,
                                is_done_column=ProjectStage.is_done_stage,
                                today=today,
                            )
                        )
                        .label("overdue_count"),
                    )
                    .join(ProjectStage, ProjectStage.id == Task.stage_id)
                    .where(Task.project_id == project_id)
                    .group_by(Task.stage_id, Task.priority, Task.assignee)
                )
            ).all()
            by_stage: dict[int, int] = {}
            by_priority: dict[str, int] = {}
            by_assignee: dict[str, int] = {}
            total = 0
            overdue = 0
            for stage_id, priority, assignee, count, overdue_count in rows:
                count_value = int(count)
                total += count_value
                overdue += int(overdue_count)
                by_stage[stage_id] = by_stage.get(stage_id, 0) + count_value
                priority_key = priority.value
                by_priority[priority_key] = by_priority.get(priority_key, 0) + count_value
                assignee_key = assignee or "не назначен"
                by_assignee[assignee_key] = by_assignee.get(assignee_key, 0) + count_value
            return ProjectTaskStatistics(
                total=total,
                overdue=overdue,
                by_stage=by_stage,
                by_priority=by_priority,
                by_assignee=by_assignee,
            )
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить агрегаты проекта id=%s.", project_id, exc_info=True
            )
            raise TasksRepositoryError("Ошибка получения агрегатов задач.") from error

    async def get_by_stage_limited(
        self,
        *,
        project_id: int,
        stage_id: int,
        limit: int = 30,
    ) -> list[Task]:
        """Возвращает ограниченный список задач выбранной стадии проекта."""
        try:
            result: Result = await self.db_session.execute(
                select(Task)
                .where(Task.project_id == project_id, Task.stage_id == stage_id)
                .order_by(Task.position, Task.id)
                .limit(limit)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить задачи стадии id=%s.", stage_id, exc_info=True)
            raise TasksRepositoryError("Ошибка получения задач стадии.") from error

    async def get_overdue_limited(
        self,
        *,
        project_id: int,
        today: date,
        limit: int = 30,
    ) -> list[Task]:
        """Возвращает ограниченный список незавершённых просроченных задач."""
        try:
            result: Result = await self.db_session.execute(
                select(Task)
                .join(ProjectStage, ProjectStage.id == Task.stage_id)
                .where(
                    Task.project_id == project_id,
                    overdue_sql(
                        due_date_column=Task.due_date,
                        is_done_column=ProjectStage.is_done_stage,
                        today=today,
                    ),
                )
                .order_by(Task.due_date, Task.id)
                .limit(limit)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить просроченные задачи id=%s.", project_id, exc_info=True
            )
            raise TasksRepositoryError("Ошибка получения просроченных задач.") from error

    async def get_wbs_counts(self, project_id: int) -> dict[int, int]:
        """Возвращает число задач по узлам ИСР без загрузки задач."""
        try:
            rows = (
                await self.db_session.execute(
                    select(Task.wbs_node_id, func.count(Task.id))
                    .where(Task.project_id == project_id, Task.wbs_node_id.is_not(None))
                    .group_by(Task.wbs_node_id)
                )
            ).all()
            return {int(node_id): int(count) for node_id, count in rows if node_id is not None}
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить агрегаты ИСР id=%s.", project_id, exc_info=True)
            raise TasksRepositoryError("Ошибка получения агрегатов ИСР.") from error

    async def get_next_number(self, project_id: int) -> int:
        """Возвращает следующий свободный номер задачи в проекте.

        Args:
            project_id: Идентификатор проекта.

        Returns:
            Номер для новой задачи, начиная с ``1``.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(func.coalesce(func.max(Task.number), 0) + 1).where(
                    Task.project_id == project_id
                )
            )
            return int(result.scalar_one())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить номер задачи проекта id=%s.",
                project_id,
                exc_info=True,
            )
            raise TasksRepositoryError(
                f"Ошибка получения номера задачи проекта id={project_id}."
            ) from error

    async def get_count_by_stage(self, stage_id: int) -> int:
        """Возвращает количество задач в указанной стадии.

        Args:
            stage_id: Идентификатор стадии.

        Returns:
            Количество задач.

        Raises:
            TasksRepositoryError: Если подсчёт завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(func.count()).select_from(Task).where(Task.stage_id == stage_id)
            )
            return int(result.scalar_one())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось подсчитать задачи стадии id=%s.", stage_id, exc_info=True)
            raise TasksRepositoryError(f"Ошибка подсчёта задач стадии id={stage_id}.") from error

    async def get_max_position_by_stage(self, stage_id: int) -> float:
        """Возвращает наибольшую позицию задачи в указанной стадии.

        Args:
            stage_id: Идентификатор стадии.

        Returns:
            Наибольшая позиция или ``0.0`` для пустой стадии.

        Raises:
            TasksRepositoryError: Если запрос завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(func.coalesce(func.max(Task.position), 0.0)).where(Task.stage_id == stage_id)
            )
            return float(result.scalar_one())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить максимальную позицию стадии id=%s.",
                stage_id,
                exc_info=True,
            )
            raise TasksRepositoryError(
                f"Ошибка получения позиции задач стадии id={stage_id}."
            ) from error

    async def search_ids(self, project_id: int, search: str) -> set[int]:
        """Возвращает id задач проекта, совпавших по FTS или номеру.

        Args:
            project_id: Идентификатор проекта.
            search: Поисковый запрос.

        Returns:
            Идентификаторы найденных задач.

        Raises:
            TasksRepositoryError: Если поиск завершился ошибкой.
        """
        try:
            ts_query = build_ts_query(search)
            condition = Task.search_vector.op("@@")(ts_query)
            number = _extract_task_number(search)
            if number is not None:
                condition = or_(condition, Task.number == number)
            result: Result = await self.db_session.execute(
                select(Task.id).where(and_(Task.project_id == project_id, condition))
            )
            return set(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось выполнить поиск задач.", exc_info=True)
            raise TasksRepositoryError("Ошибка поиска задач.") from error

    async def get_search_highlights(
        self,
        task_ids: list[int],
        search: str,
    ) -> dict[int, dict[str, str]]:
        """Возвращает подсвеченные совпадения в заголовках и описаниях задач.

        Args:
            task_ids: Идентификаторы отображаемых задач.
            search: Полнотекстовый запрос.

        Returns:
            Поисковый контекст по идентификаторам задач.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        if not task_ids or not search.strip():
            return {}
        try:
            ts_query = build_ts_query(search)
            title_vector = func.to_tsvector("russian", func.coalesce(Task.title, ""))
            description_vector = func.to_tsvector("russian", func.coalesce(Task.description_md, ""))
            stmt = select(
                Task.id,
                title_vector.op("@@")(ts_query).label("title_matches"),
                description_vector.op("@@")(ts_query).label("description_matches"),
                func.ts_headline("russian", Task.title, ts_query, TITLE_HEADLINE_OPTIONS).label(
                    "title_headline"
                ),
                func.ts_headline(
                    "russian",
                    func.coalesce(Task.description_md, ""),
                    ts_query,
                    EXCERPT_HEADLINE_OPTIONS,
                ).label("description_headline"),
            ).where(Task.id.in_(task_ids))
            rows = (await self.db_session.execute(stmt)).mappings().all()

            highlights: dict[int, dict[str, str]] = {}
            for row in rows:
                data: dict[str, str] = {}
                if HIGHLIGHT_START in row["title_headline"]:
                    data["search_title"] = row["title_headline"]
                if row["title_matches"]:
                    data["search_match_source"] = "title"
                elif row["description_matches"]:
                    data["search_match_source"] = "description"
                    data["search_excerpt"] = row["description_headline"]
                if data:
                    highlights[row["id"]] = data
            return highlights
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось подготовить подсветку задач.", exc_info=True)
            raise TasksRepositoryError("Ошибка подсветки результатов задач.") from error

    async def get_stage_counts(self) -> list[Row]:
        """Возвращает количество задач по каждой стадии всех проектов.

        Args:
            Нет дополнительных аргументов.

        Returns:
            Строки ``(project_id, stage_id, tasks_count)``.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(
                    Task.project_id,
                    Task.stage_id,
                    func.count().label("tasks_count"),
                ).group_by(Task.project_id, Task.stage_id)
            )
            return list(result.all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось подсчитать задачи по стадиям.", exc_info=True)
            raise TasksRepositoryError("Ошибка подсчёта задач по стадиям.") from error

    async def get_portfolio_counters(self, today: date, soon_until: date) -> list[Row]:
        """Возвращает агрегаты задач по всем проектам одним запросом.

        Args:
            today: Текущая дата для определения просрочки.
            soon_until: Верхняя граница окна ближайших сроков.

        Returns:
            Строки ``(project_id, total, done, overdue, due_soon, next_due_date)``.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            is_open = ProjectStage.is_done_stage.is_(False)
            result: Result = await self.db_session.execute(
                select(
                    Task.project_id,
                    func.count().label("total"),
                    func.count().filter(ProjectStage.is_done_stage.is_(True)).label("done"),
                    func.count()
                    .filter(
                        overdue_sql(
                            due_date_column=Task.due_date,
                            is_done_column=ProjectStage.is_done_stage,
                            today=today,
                        )
                    )
                    .label("overdue"),
                    func.count()
                    .filter(
                        due_soon_sql(
                            due_date_column=Task.due_date,
                            is_done_column=ProjectStage.is_done_stage,
                            today=today,
                            soon_until=soon_until,
                        )
                    )
                    .label("due_soon"),
                    func.min(Task.due_date)
                    .filter(and_(is_open, Task.due_date >= today))
                    .label("next_due_date"),
                )
                .join(ProjectStage, ProjectStage.id == Task.stage_id)
                .group_by(Task.project_id)
            )
            return list(result.all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось подсчитать показатели проектов.", exc_info=True)
            raise TasksRepositoryError("Ошибка подсчёта показателей проектов.") from error

    async def get_attention_tasks(
        self,
        today: date,
        soon_until: date,
        limit: int,
        project_ids: set[int] | None = None,
    ) -> list[Task]:
        """Возвращает незавершённые задачи с просроченным или ближайшим сроком.

        Отбор по проектам выполняется в запросе, а не после него: иначе лимит
        мог бы целиком уйти на чужие задачи и вернуть пустую ленту.

        Args:
            today: Текущая дата.
            soon_until: Верхняя граница окна ближайших сроков.
            limit: Максимальное количество задач.
            project_ids: Проекты, доступные пользователю.

        Returns:
            Задачи, отсортированные по возрастанию срока.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        if project_ids is not None and not project_ids:
            return []
        try:
            stmt = (
                select(Task)
                .join(ProjectStage, ProjectStage.id == Task.stage_id)
                .where(
                    and_(
                        ProjectStage.is_done_stage.is_(False),
                        Task.due_date.is_not(None),
                        Task.due_date <= soon_until,
                    )
                )
                .order_by(
                    case(
                        (
                            overdue_sql(
                                due_date_column=Task.due_date,
                                is_done_column=ProjectStage.is_done_stage,
                                today=today,
                            ),
                            0,
                        ),
                        else_=1,
                    ),
                    Task.due_date,
                    Task.id,
                )
                .limit(limit)
            )
            if project_ids is not None:
                stmt = stmt.where(Task.project_id.in_(project_ids))
            result: Result = await self.db_session.execute(stmt)
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить задачи с ближайшими сроками.", exc_info=True)
            raise TasksRepositoryError("Ошибка получения задач с ближайшими сроками.") from error

    async def get_recent(
        self,
        limit: int,
        project_ids: set[int] | None = None,
    ) -> list[Task]:
        """Возвращает недавно изменённые задачи.

        Args:
            limit: Максимальное количество задач.
            project_ids: Проекты, доступные пользователю.

        Returns:
            Задачи, отсортированные по убыванию даты обновления.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        if project_ids is not None and not project_ids:
            return []
        try:
            stmt = select(Task).order_by(Task.updated_at.desc(), Task.id.desc()).limit(limit)
            if project_ids is not None:
                stmt = stmt.where(Task.project_id.in_(project_ids))
            result: Result = await self.db_session.execute(stmt)
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить недавние задачи.", exc_info=True)
            raise TasksRepositoryError("Ошибка получения недавних задач.") from error

    async def clear_wbs_node(self, node_ids: set[int]) -> int:
        """Снимает привязку задач к удаляемым разделам ИСР.

        Args:
            node_ids: Идентификаторы разделов ИСР.

        Синхронизирует identity map сессии, чтобы уже загруженные задачи не
        сохраняли устаревшую привязку к удалённому разделу.

        Returns:
            Количество затронутых задач.

        Raises:
            TasksRepositoryError: Если обновление завершилось ошибкой.
        """
        if not node_ids:
            return 0
        try:
            result = await self.db_session.execute(
                update(Task)
                .where(Task.wbs_node_id.in_(node_ids))
                .values(wbs_node_id=None, wbs_position=None, revision=Task.revision + 1)
                .returning(Task)
                .execution_options(populate_existing=True, synchronize_session="fetch")
            )
            changed = result.scalars().all()
            await self.db_session.flush()
            return len(changed)
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось снять привязку задач к разделам ИСР.", exc_info=True)
            raise TasksRepositoryError("Ошибка снятия привязки задач к разделам ИСР.") from error

    async def get_by_wbs_node(self, node_id: int) -> list[Task]:
        """Возвращает задачи раздела ИСР в порядке их размещения.

        Args:
            node_id: Идентификатор раздела ИСР.

        Returns:
            Задачи раздела, упорядоченные по позиции внутри раздела.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(Task)
                .where(Task.wbs_node_id == node_id)
                .order_by(Task.wbs_position.nulls_last(), Task.id)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить задачи раздела ИСР id=%s.", node_id, exc_info=True)
            raise TasksRepositoryError(
                f"Ошибка получения задач раздела ИСР id={node_id}."
            ) from error

    async def update_wbs_positions(self, positions: dict[int, float]) -> None:
        """Переприсваивает позиции задач внутри раздела одной транзакцией.

        Используется при уплотнении разреженных позиций, когда между
        соседними задачами не остаётся свободного промежутка.

        Args:
            positions: Соответствие идентификатора задачи новой позиции.

        Returns:
            ``None`` после успешного обновления.

        Raises:
            TasksRepositoryError: Если обновить позиции не удалось.
        """
        if not positions:
            return
        try:
            result = await self.db_session.execute(
                update(Task)
                .where(Task.id.in_(positions))
                .values(
                    wbs_position=case(positions, value=Task.id),
                    revision=Task.revision + 1,
                )
                .returning(Task)
                .execution_options(populate_existing=True, synchronize_session="fetch")
            )
            result.scalars().all()
            await self.db_session.flush()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось обновить позиции задач в разделе ИСР.", exc_info=True)
            raise TasksRepositoryError("Ошибка обновления позиций задач в разделе ИСР.") from error

    async def save(self, data: dict) -> Task:
        """Создаёт задачу и возвращает сохранённую модель.

        Args:
            data: Поля новой задачи.

        Returns:
            Сохранённая задача.

        Raises:
            TaskNumberAlreadyExistsRepositoryError: Если номер задачи уже занят.
            TasksRepositoryError: Если сохранить задачу не удалось.
        """
        try:
            task = Task(**data)
            self.db_session.add(task)
            await self.db_session.flush()
            return task
        except IntegrityError as error:
            await self.db_session.rollback()
            if get_integrity_constraint_name(error) in TASK_NUMBER_CONSTRAINTS:
                project_id = int(data.get("project_id") or 0)
                number = int(data.get("number", 0))
                logger.warning("⚠️ Номер %s уже занят в проекте id=%s.", number, project_id)
                raise TaskNumberAlreadyExistsRepositoryError(
                    project_id=project_id,
                    number=number,
                ) from error
            logger.error("❌ Ограничение БД не позволило сохранить задачу.", exc_info=True)
            raise TasksRepositoryError("Ошибка ограничения БД при сохранении задачи.") from error
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось сохранить задачу.", exc_info=True)
            raise TasksRepositoryError("Ошибка сохранения задачи.") from error

    async def update(self, task: Task, data: dict) -> Task:
        """Обновляет задачу и атомарно увеличивает её версию.

        Args:
            task: Изменяемая ORM-модель задачи.
            data: Новые значения полей.

        Returns:
            Обновлённая задача с актуальной версией из RETURNING.

        Raises:
            TasksRepositoryError: Если обновить задачу не удалось.
        """
        try:
            result = await self.db_session.execute(
                update(Task)
                .where(Task.id == task.id)
                .values(**{**data, "revision": Task.revision + 1})
                .returning(Task)
                .execution_options(populate_existing=True, synchronize_session="fetch")
            )
            return result.scalar_one()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось обновить задачу id=%s.", task.id, exc_info=True)
            raise TasksRepositoryError(f"Ошибка обновления задачи id={task.id}.") from error

    async def clear_assignees(self, task_ids: list[int]) -> None:
        """Очищает совместимую подпись исполнителя у набора задач."""
        if not task_ids:
            return
        try:
            result = await self.db_session.execute(
                update(Task)
                .where(Task.id.in_(task_ids))
                .values(assignee=None, revision=Task.revision + 1)
                .returning(Task)
                .execution_options(populate_existing=True, synchronize_session="fetch")
            )
            result.scalars().all()
            await self.db_session.flush()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось очистить исполнителей задач.", exc_info=True)
            raise TasksRepositoryError("Ошибка очистки исполнителей задач.") from error

    async def bump_revisions(self, *, task_ids: set[int]) -> None:
        """Повышает версии задач после изменения только ролевых назначений.

        Args:
            task_ids: Задачи с изменившимся составом участников.

        Raises:
            TasksRepositoryError: Если повысить версии не удалось.
        """
        if not task_ids:
            return
        try:
            result = await self.db_session.execute(
                update(Task)
                .where(Task.id.in_(task_ids))
                .values(revision=Task.revision + 1)
                .returning(Task)
                .execution_options(populate_existing=True, synchronize_session="fetch")
            )
            result.scalars().all()
            await self.db_session.flush()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось повысить версии задач.", exc_info=True)
            raise TasksRepositoryError("Ошибка обновления версий задач.") from error

    async def delete(self, task: Task) -> None:
        """Удаляет задачу.

        Args:
            task: Удаляемая ORM-модель задачи.

        Returns:
            ``None`` после успешного удаления.

        Raises:
            TasksRepositoryError: Если удалить задачу не удалось.
        """
        try:
            await self.db_session.delete(task)
            await self.db_session.flush()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось удалить задачу id=%s.", task.id, exc_info=True)
            raise TasksRepositoryError(f"Ошибка удаления задачи id={task.id}.") from error

    async def relocate(self, *, task_id: int, data: dict) -> None:
        """Календарь проектных и разовых задач с доступом по локальным назначениям."""
        try:
            async with self.db_session.begin_nested():
                result = await self.db_session.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(**{**data, "revision": Task.revision + 1})
                    .returning(Task)
                    .execution_options(populate_existing=True, synchronize_session="fetch")
                )
                result.scalars().all()
        except IntegrityError as error:
            if get_integrity_constraint_name(error) in TASK_NUMBER_CONSTRAINTS:
                logger.warning("⚠️ Номер %s в цели переноса занят.", data.get("number"))
                raise TaskNumberAlreadyExistsRepositoryError(
                    project_id=int(data.get("project_id") or 0),
                    number=int(data.get("number") or 0),
                ) from error
            await self.db_session.rollback()
            logger.error("❌ Ограничение БД не позволило перенести задачу id=%s.", task_id)
            raise TasksRepositoryError(
                f"Ошибка ограничения БД при переносе задачи id={task_id}."
            ) from error
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось перенести задачу id=%s.", task_id, exc_info=True)
            raise TasksRepositoryError(f"Ошибка переноса задачи id={task_id}.") from error

    async def lock_personal_numbers(self) -> None:
        """Календарь проектных и разовых задач с доступом по локальным назначениям."""
        try:
            await self.db_session.execute(
                select(func.pg_advisory_xact_lock(PERSONAL_NUMBERS_LOCK_KEY))
            )
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось заблокировать счётчик разовых задач.", exc_info=True)
            raise TasksRepositoryError("Ошибка блокировки счётчика разовых задач.") from error

    async def get_next_personal_number(self) -> int:
        """Возвращает следующий номер разовой задачи (общий счётчик).

        Returns:
            Номер для новой разовой задачи, начиная с ``1``.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(func.coalesce(func.max(Task.number), 0) + 1).where(Task.project_id.is_(None))
            )
            return int(result.scalar_one())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить номер разовой задачи.", exc_info=True)
            raise TasksRepositoryError("Ошибка получения номера разовой задачи.") from error

    async def get_for_person(
        self,
        *,
        user_id: int,
        roles: set[TaskParticipantRole],
        include_closed: bool,
        limit: int,
        outside_projects_only: bool = False,
        attention_task_ids: set[int] | None = None,
    ) -> list[Task]:
        """Календарь проектных и разовых задач с доступом по локальным назначениям."""
        if not roles:
            return []
        try:
            participation = (
                select(TaskParticipant.task_id)
                .outerjoin(ProjectMember, ProjectMember.id == TaskParticipant.project_member_id)
                .where(
                    func.coalesce(TaskParticipant.user_id, ProjectMember.user_id) == user_id,
                    TaskParticipant.role.in_(roles),
                )
            )
            stmt = (
                select(Task)
                .outerjoin(ProjectStage, ProjectStage.id == Task.stage_id)
                .where(Task.id.in_(participation))
                .order_by(Task.due_date.asc().nulls_last(), Task.id.desc())
                .limit(limit)
            )
            if attention_task_ids is not None:
                stmt = stmt.where(Task.id.in_(attention_task_ids))
            if outside_projects_only:
                stmt = stmt.where(Task.project_id.is_(None))
            if not include_closed:
                stmt = stmt.where(
                    or_(
                        and_(Task.project_id.is_not(None), ProjectStage.is_done_stage.is_(False)),
                        and_(Task.project_id.is_(None), Task.status.not_in(CLOSED_STATUSES)),
                    )
                )
            result: Result = await self.db_session.execute(stmt)
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить задачи пользователя id=%s.", user_id, exc_info=True
            )
            raise TasksRepositoryError("Ошибка получения задач пользователя.") from error

    async def get_latest_open_end_date(self, *, project_id: int) -> date | None:
        """Возвращает самый поздний конец открытой задачи проекта — цель критического пути."""
        try:
            result = await self.db_session.execute(
                select(func.max(func.coalesce(Task.due_date, Task.start_date)))
                .join(ProjectStage, ProjectStage.id == Task.stage_id)
                .where(Task.project_id == project_id, ProjectStage.is_done_stage.is_(False))
            )
            return result.scalar_one_or_none()
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить последний срок проекта id=%s.",
                project_id,
                exc_info=True,
            )
            raise TasksRepositoryError("Ошибка получения последнего срока проекта.") from error

    async def personal_access(self, task_id: int, user_id: int) -> tuple[bool, bool]:
        """Return participation and ownership of a personal task."""
        try:
            result = await self.db_session.execute(
                select(TaskParticipant.role)
                .join(Task, Task.id == TaskParticipant.task_id)
                .where(
                    Task.id == task_id,
                    Task.project_id.is_(None),
                    TaskParticipant.user_id == user_id,
                )
            )
            roles = set(result.scalars())
            return bool(roles), TaskParticipantRole.REPORTER in roles
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise TasksRepositoryError("Cannot read personal task access.") from error

    async def get_person_calendar_range(
        self,
        *,
        user_id: int,
        date_from: date,
        date_to: date,
        visible_project_ids: set[int] | None,
        limit: int,
        project_id: int | None = None,
        priority: TaskPriority | None = None,
        include_cancelled: bool = False,
        require_due_date: bool = False,
    ) -> list[Task]:
        """Возвращает задачи любого вида, где пользователь исполнитель, в диапазоне дат.

        Интервал задачи считается так же, как в календаре проекта:
        отсутствующая граница подменяется известной.

        Args:
            user_id: Исполнитель.
            date_from: Первый день диапазона включительно.
            date_to: Последний день диапазона включительно.
            visible_project_ids: Проекты, видимые пользователю; ``None`` — все.
            limit: Максимальное число задач.
            project_id: Только задачи этого проекта.
            priority: Только задачи этого приоритета.
            include_cancelled: Возвращать ли отменённые задачи вне проекта.
            require_due_date: Только задачи с ``due_date``.

        Returns:
            Задачи по сроку, затем по id.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            interval_start = func.coalesce(Task.start_date, Task.due_date)
            interval_end = func.coalesce(Task.due_date, Task.start_date)
            stmt = _person_executor_scope(
                select(Task),
                user_id=user_id,
                visible_project_ids=visible_project_ids,
            ).where(interval_start <= date_to, interval_end >= date_from)
            if project_id is not None:
                stmt = stmt.where(Task.project_id == project_id)
            if priority is not None:
                stmt = stmt.where(Task.priority == priority)
            if not include_cancelled:
                stmt = stmt.where(
                    or_(Task.project_id.is_not(None), Task.status != TaskStatus.CANCELLED)
                )
            if require_due_date:
                stmt = stmt.where(Task.due_date.is_not(None))
            result: Result = await self.db_session.execute(
                stmt.order_by(Task.due_date.asc().nulls_last(), Task.id).limit(limit)
            )
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить календарь пользователя id=%s.", user_id, exc_info=True
            )
            raise TasksRepositoryError("Ошибка получения календаря пользователя.") from error

    async def get_person_unscheduled_page(
        self,
        *,
        user_id: int,
        visible_project_ids: set[int] | None,
        cursor: int | None,
        limit: int,
    ) -> list[Task]:
        """Возвращает страницу незавершённых задач исполнителя без обеих дат.

        Args:
            user_id: Исполнитель.
            visible_project_ids: Проекты, видимые пользователю; ``None`` — все.
            cursor: Последний id прошлой страницы.
            limit: Размер страницы; возвращается до ``limit + 1`` строк.

        Returns:
            Задачи по возрастанию id.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            stmt = _person_executor_scope(
                select(Task).outerjoin(ProjectStage, ProjectStage.id == Task.stage_id),
                user_id=user_id,
                visible_project_ids=visible_project_ids,
            ).where(Task.start_date.is_(None), Task.due_date.is_(None), _open_task_sql())
            if cursor is not None:
                stmt = stmt.where(Task.id > cursor)
            result: Result = await self.db_session.execute(stmt.order_by(Task.id).limit(limit + 1))
            return list(result.scalars().all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить задачи без срока пользователя id=%s.",
                user_id,
                exc_info=True,
            )
            raise TasksRepositoryError("Ошибка получения задач без срока.") from error

    async def get_person_calendar_counts(
        self,
        *,
        user_id: int,
        visible_project_ids: set[int] | None,
        today: date,
        soon_until: date,
    ) -> CalendarTaskCounts:
        """Считает просроченные, близкие к сроку и бессрочные незавершённые задачи исполнителя.

        Args:
            user_id: Исполнитель.
            visible_project_ids: Проекты, видимые пользователю; ``None`` — все.
            today: Текущая дата клиента.
            soon_until: Верхняя граница окна «скоро срок».

        Returns:
            Счётчики; ``drifted`` не считается и равен нулю.

        Raises:
            TasksRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            stmt = _person_executor_scope(
                select(
                    func.count().filter(Task.due_date < today).label("overdue"),
                    func.count()
                    .filter(Task.due_date >= today, Task.due_date <= soon_until)
                    .label("due_soon"),
                    func.count()
                    .filter(Task.start_date.is_(None), Task.due_date.is_(None))
                    .label("unscheduled"),
                )
                .select_from(Task)
                .outerjoin(ProjectStage, ProjectStage.id == Task.stage_id),
                user_id=user_id,
                visible_project_ids=visible_project_ids,
            ).where(_open_task_sql())
            row = (await self.db_session.execute(stmt)).one()
            return CalendarTaskCounts(
                overdue=int(row.overdue or 0),
                due_soon=int(row.due_soon or 0),
                unscheduled=int(row.unscheduled or 0),
                drifted=0,
            )
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось посчитать сводку календаря пользователя id=%s.",
                user_id,
                exc_info=True,
            )
            raise TasksRepositoryError("Ошибка подсчёта сводки календаря.") from error

    async def get_workload_rows(
        self,
        *,
        user_ids: set[int],
        date_from: date,
        date_to: date,
        limit: int,
    ) -> list[Row]:
        """Календарь проектных и разовых задач с доступом по локальным назначениям."""
        if not user_ids:
            return []
        try:
            executor = func.coalesce(TaskParticipant.user_id, ProjectMember.user_id)
            interval_start = func.coalesce(Task.start_date, Task.due_date)
            interval_end = func.coalesce(Task.due_date, Task.start_date)
            stmt = (
                select(
                    executor.label("user_id"),
                    Task.id.label("task_id"),
                    Task.project_id,
                    Task.number,
                    Task.title,
                    Task.start_date,
                    Task.due_date,
                    Project.key.label("project_key"),
                    Project.name.label("project_name"),
                )
                .select_from(TaskParticipant)
                .join(Task, Task.id == TaskParticipant.task_id)
                .outerjoin(ProjectMember, ProjectMember.id == TaskParticipant.project_member_id)
                .outerjoin(ProjectStage, ProjectStage.id == Task.stage_id)
                .outerjoin(Project, Project.id == Task.project_id)
                .where(
                    TaskParticipant.role == TaskParticipantRole.EXECUTOR,
                    executor.in_(user_ids),
                    interval_start <= date_to,
                    interval_end >= date_from,
                    _open_task_sql(),
                )
                .order_by(executor, interval_start, Task.id)
                .limit(limit)
            )
            result: Result = await self.db_session.execute(stmt)
            return list(result.all())
        except (SQLAlchemyError, Exception) as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить задачи для загрузки команды.", exc_info=True)
            raise TasksRepositoryError("Ошибка получения загрузки команды.") from error


def _extract_task_number(search: str) -> int | None:
    """Возвращает номер задачи из запроса вида ``PROJ-142`` или ``142``."""
    candidate = search.strip().rsplit("-", maxsplit=1)[-1]
    return int(candidate) if candidate.isdigit() else None


def _apply_calendar_filters(
    stmt,
    *,
    stage_id: int | None,
    priority: TaskPriority | None,
    assignee: str | None,
    wbs_node_id: int | None,
):
    """Добавляет к запросу единый набор календарных фильтров."""
    if stage_id is not None:
        stmt = stmt.where(Task.stage_id == stage_id)
    if priority is not None:
        stmt = stmt.where(Task.priority == priority)
    if assignee is not None:
        stmt = stmt.where(Task.assignee == assignee)
    if wbs_node_id is not None:
        stmt = stmt.where(Task.wbs_node_id == wbs_node_id)
    return stmt


def _person_executor_scope(stmt, *, user_id: int, visible_project_ids: set[int] | None):
    """Сужает запрос до задач, где пользователь исполнитель и которые ему видны.

    Исполнитель определяется так же, как во вкладке «Делаю» «Моих задач»:
    участник с ролью EXECUTOR напрямую или через участника проекта.
    Проектная задача дополнительно ограничена видимыми проектами — задача
    вне проекта видна своему исполнителю по правилам доступа.
    """
    participation = (
        select(TaskParticipant.task_id)
        .outerjoin(ProjectMember, ProjectMember.id == TaskParticipant.project_member_id)
        .where(
            func.coalesce(TaskParticipant.user_id, ProjectMember.user_id) == user_id,
            TaskParticipant.role == TaskParticipantRole.EXECUTOR,
        )
    )
    stmt = stmt.where(Task.id.in_(participation))
    if visible_project_ids is not None:
        stmt = stmt.where(or_(Task.project_id.is_(None), Task.project_id.in_(visible_project_ids)))
    return stmt


def _open_task_sql():
    """Условие «задача не завершена»; запрос обязан присоединить ProjectStage."""
    return or_(
        and_(Task.project_id.is_not(None), ProjectStage.is_done_stage.is_(False)),
        and_(Task.project_id.is_(None), Task.status.not_in(CLOSED_STATUSES)),
    )
