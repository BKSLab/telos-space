"""Запросы метаданных файлов проекта и их производного состояния."""

from sqlalchemy import Result, Select, delete, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable

from src.db.models.knowledge_project_file_texts import KnowledgeProjectFileText
from src.db.models.knowledge_source_summaries import KnowledgeSourceSummary
from src.db.models.project_files import ProjectFile
from src.exceptions.project_files import ProjectFilesRepositoryError


def _metadata_query() -> Select:
    cached = KnowledgeProjectFileText
    return (
        select(
            *ProjectFile.__table__.c,
            cached.status.label("extraction_status"),
            cached.detail.label("extraction_detail"),
            cached.index_status,
            cached.index_detail,
            cached.indexed_at,
            KnowledgeSourceSummary.summary,
        )
        .outerjoin(cached, cached.project_file_id == ProjectFile.id)
        .outerjoin(KnowledgeSourceSummary, KnowledgeSourceSummary.project_file_id == ProjectFile.id)
    )


class ProjectFilesRepository:
    """Все выборки файла ограничены проектом, включая запрос по ID."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_for_project(
        self, *, project_id: int, ids: list[int] | None, limit: int, offset: int
    ) -> list[dict]:
        """Возвращает активные файлы и состояния без полного текста.

        Args:
            project_id: Проект, ограничивающий все строки.
            ids: Результаты FTS либо None для полного списка.
            limit: Размер страницы.
            offset: Смещение страницы.
        Returns:
            Строки метаданных и состояний обработки.
        Raises:
            ProjectFilesRepositoryError: Запрос завершился ошибкой.
        """
        statement = _metadata_query().where(
            ProjectFile.project_id == project_id, ProjectFile.deleted_at.is_(None)
        )
        if ids is not None:
            statement = statement.where(ProjectFile.id.in_(ids))
        result = await self._execute(
            statement.order_by(ProjectFile.created_at.desc(), ProjectFile.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [dict(row) for row in result.mappings()]

    async def get_by_id(
        self, *, project_id: int, file_id: int, offset: int = 0, max_chars: int = 8000
    ) -> dict | None:
        """Читает ограниченный фрагмент текста активного файла.

        Args:
            project_id: Проект, ограничивающий поиск по ID.
            file_id: Идентификатор файла.
            offset: Смещение в тексте.
            max_chars: Максимум символов, ноль для одних метаданных.
        Returns:
            Метаданные и фрагмент либо None, включая удалённый файл.
        Raises:
            ProjectFilesRepositoryError: Запрос завершился ошибкой.
        """
        result = await self._execute(
            _metadata_query()
            .add_columns(
                func.coalesce(
                    func.substring(KnowledgeProjectFileText.text, offset + 1, max_chars), ""
                ).label("text"),
                func.coalesce(func.length(KnowledgeProjectFileText.text), 0).label(
                    "text_total_chars"
                ),
            )
            .where(
                ProjectFile.id == file_id,
                ProjectFile.project_id == project_id,
                ProjectFile.deleted_at.is_(None),
            )
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def save(self, data: dict) -> dict:
        """Записывает оригинал без промежуточного commit.

        Args:
            data: Проверенные метаданные оригинала.
        Returns:
            Сохранённую строку вместе с серверными значениями.
        Raises:
            ProjectFilesRepositoryError: INSERT завершился ошибкой.
        """
        result = await self._execute(
            insert(ProjectFile).values(**data).returning(*ProjectFile.__table__.c)
        )
        return dict(result.mappings().one())

    async def mark_deleted(self, *, project_id: int, file_id: int) -> None:
        """Скрывает источник до удаления оригинала worker-ом.

        Args:
            project_id: Проект-владелец файла.
            file_id: Идентификатор файла.
        Raises:
            ProjectFilesRepositoryError: UPDATE завершился ошибкой.
        """
        await self._execute(
            update(ProjectFile)
            .where(
                ProjectFile.project_id == project_id,
                ProjectFile.id == file_id,
                ProjectFile.deleted_at.is_(None),
            )
            .values(deleted_at=func.now())
        )

    async def request_retry(self, *, project_id: int, file_id: int) -> None:
        """Изменяет источник, атомарно создавая задание outbox.

        Args:
            project_id: Проект-владелец файла.
            file_id: Идентификатор файла.
        Raises:
            ProjectFilesRepositoryError: UPDATE завершился ошибкой.
        """
        await self._execute(
            update(ProjectFile)
            .where(
                ProjectFile.project_id == project_id,
                ProjectFile.id == file_id,
                ProjectFile.deleted_at.is_(None),
            )
            .values(retry_requested_at=func.now())
        )

    async def purge_deleted(self, *, project_id: int, file_ids: list[int]) -> None:
        """Удаляет tombstones с уже очищенными физическими файлами.

        Args:
            project_id: Проект-владелец удалённых оригиналов.
            file_ids: Файлы, физическая очистка которых завершена.
        Raises:
            ProjectFilesRepositoryError: DELETE завершился ошибкой.
        """
        await self._execute(
            delete(ProjectFile).where(
                ProjectFile.project_id == project_id,
                ProjectFile.id.in_(file_ids),
                ProjectFile.deleted_at.is_not(None),
            )
        )

    async def _execute(self, statement: Executable) -> Result:
        try:
            return await self.db_session.execute(statement)
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise ProjectFilesRepositoryError("Ошибка запроса файлов проекта.") from error
