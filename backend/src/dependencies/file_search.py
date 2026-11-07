"""Сборка read-сервиса полнотекстового поиска файлов."""

from typing import Annotated

from fastapi import Depends

from src.dependencies.db_session import DbSessionDep
from src.dependencies.services import AccessServiceDep
from src.repositories.file_search import FileSearchRepository
from src.services.file_search import FileSearchService


def get_file_search_repository(session: DbSessionDep) -> FileSearchRepository:
    """Создаёт репозиторий поискового snapshot запроса."""
    return FileSearchRepository(session)


FileSearchRepositoryDep = Annotated[FileSearchRepository, Depends(get_file_search_repository)]


def get_file_search_service(
    repository: FileSearchRepositoryDep, access: AccessServiceDep
) -> FileSearchService:
    """Создаёт единый сценарий поиска с общим сервисом доступа."""
    return FileSearchService(repository=repository, access=access)


FileSearchServiceDep = Annotated[FileSearchService, Depends(get_file_search_service)]
