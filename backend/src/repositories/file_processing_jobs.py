"""Захват и lease независимой очереди обработки оригиналов."""

from datetime import timedelta

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.file_processing_jobs import FileProcessingJob
from src.db.models.project_files import ProjectFile
from src.db.models.task_attachments import TaskAttachment
from src.exceptions.file_processing import FileProcessingRepositoryError


class FileProcessingJobsRepository:
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def request_retry(
        self, *, attachment_id: int | None = None, project_file_id: int | None = None
    ) -> bool:
        """Коалесцирует явный повтор, сохраняя активный lease и готовый общий кеш.

        Транзакцию фиксирует вызывающий сервис. Возвращает False для удалённого
        оригинала; ровно один идентификатор обязателен.
        """
        if (attachment_id is None) == (project_file_id is None):
            raise ValueError("Нужен ровно один оригинал для повтора.")
        jobs = FileProcessingJob
        source = TaskAttachment if attachment_id is not None else ProjectFile
        key = "attachment_id" if attachment_id is not None else "project_file_id"
        source_id = attachment_id if attachment_id is not None else project_file_id
        conditions = [source.id == source_id]
        if source is ProjectFile:
            conditions.append(source.deleted_at.is_(None))
        statement = insert(jobs).from_select(
            [key], select(source.id).where(*conditions), include_defaults=False
        )
        try:
            result = await self.db_session.execute(
                statement.on_conflict_do_update(
                    index_elements=[getattr(jobs, key)],
                    index_where=getattr(jobs, key).is_not(None),
                    set_={
                        "requested_version": jobs.requested_version + 1,
                        "status": case(
                            (jobs.status == "processing", "processing"), else_="pending"
                        ),
                        "attempts": case((jobs.status == "processing", jobs.attempts), else_=0),
                        "available_at": func.now(),
                        "detail": None,
                        "updated_at": func.now(),
                    },
                ).returning(jobs.id)
            )
            return result.scalar_one_or_none() is not None
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileProcessingRepositoryError(
                "Не удалось повторить обработку оригинала."
            ) from error

    async def claim_next(
        self, *, lease_token: str, lease_seconds: float
    ) -> FileProcessingJob | None:
        """Атомарно захватывает доступную строку без ожидания чужих блокировок."""
        jobs = FileProcessingJob
        candidate = (
            select(jobs.id)
            .where(
                or_(
                    and_(jobs.status == "pending", jobs.available_at <= func.now()),
                    and_(jobs.status == "processing", jobs.lease_until < func.now()),
                )
            )
            .order_by(jobs.available_at, jobs.id)
            .with_for_update(skip_locked=True)
            .limit(1)
            .cte("claimable_file")
        )
        try:
            result = await self.db_session.execute(
                update(jobs)
                .where(jobs.id == candidate.c.id)
                .values(
                    status="processing",
                    lease_token=lease_token,
                    lease_until=func.now() + timedelta(seconds=lease_seconds),
                    claimed_version=jobs.requested_version,
                    attempts=jobs.attempts + 1,
                    updated_at=func.now(),
                )
                .returning(jobs)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileProcessingRepositoryError("Не удалось захватить обработку файла.") from error

    async def renew(self, *, job_id: int, lease_token: str, lease_seconds: float) -> bool:
        """Продлевает только действующий lease его текущему владельцу."""
        jobs = FileProcessingJob
        try:
            result = await self.db_session.execute(
                update(jobs)
                .where(
                    jobs.id == job_id,
                    jobs.status == "processing",
                    jobs.lease_token == lease_token,
                    jobs.lease_until > func.now(),
                )
                .values(
                    lease_until=func.now() + timedelta(seconds=lease_seconds), updated_at=func.now()
                )
                .returning(jobs.id)
            )
            return result.scalar_one_or_none() is not None
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileProcessingRepositoryError("Не удалось продлить обработку файла.") from error

    async def lock_owned(self, *, job_id: int, lease_token: str) -> FileProcessingJob | None:
        """Повторно проверяет квитанцию и срок lease перед фиксацией результата."""
        jobs = FileProcessingJob
        try:
            result = await self.db_session.execute(
                select(jobs)
                .where(
                    jobs.id == job_id,
                    jobs.status == "processing",
                    jobs.lease_token == lease_token,
                    jobs.lease_until > func.now(),
                )
                .with_for_update()
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileProcessingRepositoryError(
                "Не удалось проверить владельца обработки."
            ) from error

    async def finish(
        self,
        *,
        job_id: int,
        lease_token: str,
        succeeded: bool,
        detail: str | None,
        max_attempts: int,
        retry_seconds: float,
    ) -> None:
        """Фиксирует результат, сохраняя новый запрос, пришедший во время lease."""
        jobs = FileProcessingJob
        changed = jobs.requested_version != jobs.claimed_version
        retry = (jobs.attempts < max_attempts) & (not succeeded)
        try:
            await self.db_session.execute(
                update(jobs)
                .where(
                    jobs.id == job_id, jobs.lease_token == lease_token, jobs.status == "processing"
                )
                .values(
                    status=case(
                        (changed, "pending"),
                        (succeeded, "ready"),
                        (retry, "pending"),
                        else_="failed",
                    ),
                    attempts=case((changed, 0), else_=jobs.attempts),
                    available_at=case(
                        (changed, func.now()), else_=func.now() + timedelta(seconds=retry_seconds)
                    ),
                    lease_token=None,
                    lease_until=None,
                    claimed_version=None,
                    detail=case((changed, None), else_=detail),
                    updated_at=func.now(),
                )
            )
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileProcessingRepositoryError("Не удалось завершить обработку файла.") from error
