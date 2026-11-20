"""Хранение полных версий без бизнес-логики."""

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.task_reports import TaskReport
from src.exceptions.task_reports import TaskReportsRepositoryError


class TaskReportsRepository:
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def latest(self, task_id: int) -> TaskReport | None:
        """Возвращает последнюю версию; уникальный индекс покрывает обратный проход."""
        try:
            result = await self.db_session.execute(
                select(TaskReport)
                .where(TaskReport.task_id == task_id)
                .order_by(TaskReport.revision.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise TaskReportsRepositoryError("Ошибка чтения отчёта.") from error

    async def history(
        self,
        task_id: int,
        limit: int = 50,
        *,
        report_id: int | None = None,
        revision: int | None = None,
    ) -> list[TaskReport]:
        """Возвращает ограниченную историю от новых версий к старым."""
        try:
            query = select(TaskReport).where(TaskReport.task_id == task_id)
            if report_id is not None:
                query = query.where(TaskReport.id == report_id)
            if revision is not None:
                query = query.where(TaskReport.revision == revision)
            result = await self.db_session.execute(
                query.order_by(TaskReport.revision.desc()).limit(limit)
            )
            return list(result.scalars())
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise TaskReportsRepositoryError("Ошибка чтения истории отчёта.") from error

    async def append(self, data: dict) -> TaskReport:
        """Добавляет версию; транзакцией владеет сервис."""
        try:
            report = TaskReport(**data)
            self.db_session.add(report)
            await self.db_session.flush()
            return report
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise TaskReportsRepositoryError("Ошибка публикации отчёта.") from error

    async def latest_for_tasks(self, task_ids: list[int]) -> dict[int, TaskReport]:
        """Читает одну актуальную версию каждой задачи без N+1 запросов."""
        if not task_ids:
            return {}
        try:
            result = await self.db_session.execute(
                select(TaskReport)
                .where(TaskReport.task_id.in_(task_ids))
                .distinct(TaskReport.task_id)
                .order_by(TaskReport.task_id, TaskReport.revision.desc())
            )
            return {row.task_id: row for row in result.scalars()}
        except SQLAlchemyError as error:
            raise TaskReportsRepositoryError("Ошибка чтения отчётов задач.") from error
