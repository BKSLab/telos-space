"""Канонический HTTP список отдаёт превью, адресная карточка — полный текст."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from main import app
from src.dependencies.services import get_tasks_service
from src.schemas.tasks import TaskDetailSchema, TaskListSchema
from src.services.tasks import TasksService


async def test_task_list_contract_excludes_detail_fields_and_preserves_search(api_client):
    now = datetime.now(UTC)
    metadata = dict(
        id=12,
        project_id=1,
        stage_id=1,
        number=42,
        key="PROJ-42",
        title="Задача",
        priority="HIGH",
        position=1000,
        created_at=now,
        updated_at=now,
    )
    text = "Длинное описание 🔎 " * 1000
    service = AsyncMock(spec=TasksService)
    service.get_task_list.return_value = [
        TaskListSchema(
            **metadata,
            description_preview=text[:320],
            comments_count=2,
            last_comment="Последний",
            search_match_source="description",
            search_title="Задача",
            search_excerpt="__FTS_START__Длинное__FTS_END__ описание",
        )
    ]
    service.get_task.return_value = TaskDetailSchema(**metadata, description_md=text)
    app.dependency_overrides[get_tasks_service] = lambda: service
    response = await api_client.get("/api/v1/projects/1/tasks?search=Длинное&priority=HIGH")
    assert response.status_code == 200
    row = response.json()[0]
    assert row["description_preview"] == text[:320]
    assert row["comments_count"] == 2
    assert row["search_excerpt"] == "__FTS_START__Длинное__FTS_END__ описание"
    assert not {"description_md", "checklist", "checklist_revision", "report"} & row.keys()
    detail = await api_client.get("/api/v1/tasks/12")
    assert detail.status_code == 200
    assert detail.json()["description_md"] == text
    assert "description_preview" not in detail.json()
