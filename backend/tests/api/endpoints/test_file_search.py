"""Read-контракт поиска файлов, фильтры и личные задачи."""

from unittest.mock import AsyncMock

import pytest

from src.dependencies.file_search import get_file_search_service
from src.schemas.file_search import FileSearchItemSchema, FileSearchPageSchema
from src.services.file_search import FileSearchService


@pytest.fixture
def service():
    return AsyncMock(spec=FileSearchService)


async def test_project_search_passes_filters_and_returns_source_context(api_client, service):
    from main import app

    service.search_project.return_value = FileSearchPageSchema(
        items=[
            FileSearchItemSchema(
                source_type="document",
                source_id=9,
                project_id=2,
                task_id=7,
                task_key="TEST-7",
                task_title="Поставка",
                document_slug="spec",
                title="Спецификация",
                caption=None,
                content_type=None,
                preview_url=None,
                excerpt="Поставка __FTS_START__кварца__FTS_END__",
                rank=0.2,
            )
        ],
        total=1,
        limit=10,
        offset=0,
    )
    app.dependency_overrides[get_file_search_service] = lambda: service
    response = await api_client.get(
        "/api/v1/projects/2/files/search",
        params={"q": "кварц", "scope": "documents", "task_id": 7, "limit": 10},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["document_slug"] == "spec"
    assert response.json()["items"][0]["task_key"] == "TEST-7"
    service.search_project.assert_awaited_once_with(
        project_id=2, user_id=1, query="кварц", scope="documents", task_id=7, limit=10, offset=0
    )


async def test_task_search_supports_nullable_project(api_client, service):
    from main import app

    service.search_task.return_value = FileSearchPageSchema(items=[], total=0, limit=30, offset=0)
    app.dependency_overrides[get_file_search_service] = lambda: service
    response = await api_client.get("/api/v1/tasks/7/files/search", params={"q": "файл"})
    assert response.status_code == 200 and response.json() == {
        "items": [],
        "total": 0,
        "limit": 30,
        "offset": 0,
    }
    service.search_task.assert_awaited_once_with(
        task_id=7, user_id=1, query="файл", limit=30, offset=0
    )


@pytest.mark.parametrize(
    "params",
    [{"q": ""}, {"q": "x", "scope": "unknown"}, {"q": "x", "limit": 101}, {"q": "x", "task_id": 0}],
)
async def test_project_search_validates_bounded_filters(api_client, service, params):
    from main import app

    app.dependency_overrides[get_file_search_service] = lambda: service
    assert (
        await api_client.get("/api/v1/projects/2/files/search", params=params)
    ).status_code == 422
    service.search_project.assert_not_awaited()
