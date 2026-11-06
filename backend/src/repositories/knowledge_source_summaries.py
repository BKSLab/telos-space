"""Сохранение производных описаний отдельно от первичного текста."""

from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.knowledge_source_summaries import KnowledgeSourceSummary
from src.db.models.project_files import ProjectFile
from src.exceptions.knowledge import KnowledgeIndexJobsRepositoryError


class KnowledgeSourceSummariesRepository:
    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def save(self, data: dict) -> None:
        """Сохраняет версию: читатель отбрасывает запоздавшее саммари.

        Args:
            data: Описание, хэш текста и ссылки на исходную сущность и проект.
        Raises:
            KnowledgeIndexJobsRepositoryError: Сохранение не удалось.
        """
        statement = insert(KnowledgeSourceSummary).values(**data)
        if data.get("project_file_id") is not None:
            statement = insert(KnowledgeSourceSummary).from_select(
                list(data),
                select(*(literal(data[key]) for key in data))
                .select_from(ProjectFile)
                .where(
                    ProjectFile.id == data["project_file_id"],
                    ProjectFile.project_id == data["project_id"],
                    ProjectFile.deleted_at.is_(None),
                ),
                include_defaults=False,
            )
        try:
            await self.db_session.execute(
                statement.on_conflict_do_update(
                    index_elements=["project_id", "source_id"],
                    set_={**data, "updated_at": func.now()},
                )
            )
        except SQLAlchemyError as error:
            raise KnowledgeIndexJobsRepositoryError(
                "Не удалось сохранить описание источника."
            ) from error
