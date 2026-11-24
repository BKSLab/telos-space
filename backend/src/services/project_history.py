from datetime import date, timedelta
from zoneinfo import ZoneInfo

from src.repositories.project_history import ProjectHistoryRepository
from src.schemas.project_history import (
    ProjectHistoryPage,
    ProjectHistoryQuery,
    ProjectHistoryVersion,
)
from src.utils.document_refs import ref_from_source_id


class ProjectHistoryService:
    def __init__(self, repository: ProjectHistoryRepository) -> None:
        self.repository = repository

    async def version(self, project_id: int) -> ProjectHistoryVersion:
        return ProjectHistoryVersion(revision=str(await self.repository.version(project_id)))

    async def read(self, project_id: int, filters: ProjectHistoryQuery) -> ProjectHistoryPage:
        # Capture a lower bound before the data read. A later commit may cause an
        # extra refresh, but can never label old contents with a newer revision.
        revision = str(await self.repository.version(project_id))
        items = await self.repository.read(project_id, filters)
        page = items[: filters.limit]
        for item in page:
            if item.document_ref and not item.document_ref.startswith("d1_"):
                item.document_ref = ref_from_source_id(item.document_ref)
            elif item.kind == "document_created" and item.source_available:
                item.document_ref = ref_from_source_id(item.id)
        previous_day = None
        if (
            filters.include_previous
            and filters.offset == 0
            and not filters.event_id
            and filters.since
            and filters.since > date.min
        ):
            previous = await self.repository.read(
                project_id,
                filters.model_copy(
                    update={
                        "since": None,
                        "until": filters.since - timedelta(days=1),
                        "limit": 1,
                        "offset": 0,
                        "include_previous": False,
                    }
                ),
            )
            if previous:
                previous_day = previous[0].created_at.astimezone(ZoneInfo(filters.timezone)).date()
        return ProjectHistoryPage(
            items=page,
            next_offset=filters.offset + filters.limit if len(items) > filters.limit else None,
            previous_day=previous_day,
            revision=revision,
        )
