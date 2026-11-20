from src.repositories.project_history import ProjectHistoryRepository
from src.schemas.project_history import ProjectHistoryPage, ProjectHistoryQuery
from src.utils.document_refs import ref_from_source_id


class ProjectHistoryService:
    def __init__(self, repository: ProjectHistoryRepository) -> None:
        self.repository = repository

    async def read(self, project_id: int, filters: ProjectHistoryQuery) -> ProjectHistoryPage:
        items = await self.repository.read(project_id, filters)
        page = items[: filters.limit]
        for item in page:
            if item.kind == "document_created":
                item.document_ref = ref_from_source_id(item.id)
        return ProjectHistoryPage(
            items=page,
            next_offset=filters.offset + filters.limit if len(items) > filters.limit else None,
        )
