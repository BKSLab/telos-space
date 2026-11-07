"""Общий read-сценарий поиска файлов с доменным доступом."""

from src.exceptions.access import ResourceNotAvailableError
from src.exceptions.file_processing import FileSearchRepositoryError, FileSearchServiceError
from src.repositories.file_search import FileSearchRepository
from src.schemas.file_search import FileSearchItemSchema, FileSearchPageSchema, FileSearchScope
from src.services.access import AccessService
from src.utils.file_previews import is_raster_previewable


class FileSearchService:
    def __init__(self, *, repository: FileSearchRepository, access: AccessService) -> None:
        self.repository = repository
        self.access = access

    async def search_project(
        self,
        *,
        project_id: int,
        user_id: int,
        query: str,
        scope: FileSearchScope,
        task_id: int | None,
        limit: int,
        offset: int,
    ) -> FileSearchPageSchema:
        """Ищет только в текущем проекте и проверяет принадлежность фильтра задачи."""
        await self.access.ensure_project_access(project_id=project_id, user_id=user_id)
        if task_id is not None:
            grant = await self.access.ensure_task_access(task_id=task_id, user_id=user_id)
            if grant.project_id != project_id:
                raise ResourceNotAvailableError(resource="Задача", resource_id=task_id)
        return await self._search(
            project_id=project_id,
            task_id=task_id,
            user_id=user_id,
            query=query,
            scope=scope,
            limit=limit,
            offset=offset,
        )

    async def search_task(
        self, *, task_id: int, user_id: int, query: str, limit: int, offset: int
    ) -> FileSearchPageSchema:
        """Ищет вложения и допустимые связанные документы, включая личные задачи."""
        grant = await self.access.ensure_task_access(task_id=task_id, user_id=user_id)
        return await self._search(
            project_id=grant.project_id,
            task_id=task_id,
            user_id=user_id,
            query=query,
            scope="all",
            limit=limit,
            offset=offset,
        )

    async def _search(
        self,
        *,
        project_id: int | None,
        task_id: int | None,
        user_id: int,
        query: str,
        scope: FileSearchScope,
        limit: int,
        offset: int,
    ) -> FileSearchPageSchema:
        try:
            result = await self.repository.search(
                query=query.strip(),
                user_id=user_id,
                project_id=project_id,
                task_id=task_id,
                scope=scope,
                limit=limit,
                offset=offset,
            )
        except FileSearchRepositoryError as error:
            raise FileSearchServiceError(str(error)) from error
        items = []
        for row in result["items"]:
            preview_url = None
            if row["content_type"] and is_raster_previewable(row["title"], row["content_type"]):
                if row["source_type"] == "project_file":
                    preview_url = (
                        f"/api/v1/projects/{row['project_id']}/files/{row['source_id']}/preview"
                    )
                elif row["source_type"] == "task_attachment":
                    preview_url = (
                        f"/api/v1/tasks/{row['task_id']}/attachments/{row['source_id']}/preview"
                    )
            items.append(FileSearchItemSchema(**row, preview_url=preview_url))
        return FileSearchPageSchema(items=items, total=result["total"], limit=limit, offset=offset)
