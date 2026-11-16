"""HTTP-контракт паспорта сохраняет идентичность автора и PATCH-семантику."""

from dataclasses import replace
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from main import app
from src.api.middleware.api_key_usage import ApiKeyUsageMiddleware
from src.db.models.api_tokens import ApiTokenScope
from src.dependencies.access import require_project_access
from src.dependencies.auth import get_principal
from src.dependencies.services import get_access_service, get_auth_service, get_projects_service
from src.schemas.projects import ProjectSchema
from src.services.access import AccessGrant, AccessService
from src.services.auth import ApiKeyGrant, AuthService
from src.services.projects import ProjectsService


async def test_project_patch_passes_authenticated_author_and_explicit_empty_date(api_client):
    service = AsyncMock(spec=ProjectsService)
    service.update_project.return_value = ProjectSchema(
        id=1,
        owner_id=1,
        key="PROJ",
        name="Проект",
        status="ACTIVE",
        color="#334455",
        order_index=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    app.dependency_overrides[get_projects_service] = lambda: service
    response = await api_client.patch(
        "/api/v1/projects/1",
        json={
            "due_date": None,
            "expected_due_date": "2026-10-01",
            "due_date_comment": "Пересмотр",
            "expected_context_revision": 7,
            "description_sections": {
                "audience": "Команда",
                "open_questions": "Кто принимает итог?",
            },
            "updated_by_user_id": 999,
            "context_revision": 999,
        },
    )
    assert response.status_code == 200
    service.update_project.assert_awaited_once_with(
        project_id=1,
        updated_by_user_id=1,
        data={
            "due_date": None,
            "expected_due_date": date(2026, 10, 1),
            "due_date_comment": "Пересмотр",
            "expected_context_revision": 7,
            "description_sections": {
                "audience": "Команда",
                "open_questions": "Кто принимает итог?",
            },
        },
    )


@pytest.mark.parametrize(
    "stages", [[], [{"name": " "}], [{"name": "Готово"}, {"name": " готово "}]]
)
async def test_project_creation_rejects_invalid_stage_sets(api_client, stages):
    service = AsyncMock(spec=ProjectsService)
    app.dependency_overrides[get_projects_service] = lambda: service
    response = await api_client.post(
        "/api/v1/projects", json={"key": "NEW", "name": "Проект", "stages": stages}
    )
    assert response.status_code == 422
    service.create_project.assert_not_awaited()


async def test_context_history_is_paginated_metadata_without_full_snapshots(api_client):
    service = AsyncMock(spec=ProjectsService)
    stamp = datetime.now(UTC)
    item = {
        "revision": 2,
        "changed_by_user_id": 1,
        "changed_by_name": "Тестов Тест",
        "created_at": stamp,
        "is_initial": False,
        "changed_fields": ["goal"],
        "description_md": "SYNTHETIC_FULL_SNAPSHOT_MUST_NOT_APPEAR",
        "description_sections": {"goal": "SYNTHETIC_FULL_SNAPSHOT_MUST_NOT_APPEAR"},
    }
    service.get_context_history.return_value = {
        "items": [item],
        "total": 3,
        "offset": 1,
        "limit": 1,
        "has_more": True,
        "next_offset": 2,
    }
    app.dependency_overrides[get_projects_service] = lambda: service
    response = await api_client.get("/api/v1/projects/1/context-history?limit=1&offset=1")
    assert response.status_code == 200
    page = response.json()
    assert {key: value for key, value in page.items() if key != "items"} == {
        "total": 3,
        "offset": 1,
        "limit": 1,
        "has_more": True,
        "next_offset": 2,
    }
    assert set(page["items"][0]) == {
        "revision",
        "changed_by_user_id",
        "changed_by_name",
        "created_at",
        "is_initial",
        "changed_fields",
    }
    assert page["items"][0]["revision"] == 2
    assert "SYNTHETIC_FULL_SNAPSHOT" not in response.text
    call = service.get_context_history.await_args
    assert call.kwargs["limit"] == 1 and call.kwargs["offset"] == 1
    service.get_context_history.reset_mock()
    for query in ("limit=0", "offset=-1"):
        assert (
            await api_client.get(f"/api/v1/projects/1/context-history?{query}")
        ).status_code == 422
    service.get_context_history.assert_not_awaited()


async def test_context_history_api_key_requires_projects_read(
    api_client, current_principal, monkeypatch
):
    """Настоящая auth dependency проверяет новый route, не дефолтную подмену."""
    # Фоновая телеметрия не должна открывать настроенную application DB в API-double.
    monkeypatch.setattr(ApiKeyUsageMiddleware, "_schedule", lambda *_: None)
    service = AsyncMock(spec=ProjectsService)
    service.get_context_history.return_value = {
        "items": [],
        "total": 0,
        "offset": 0,
        "limit": 20,
        "has_more": False,
        "next_offset": None,
    }
    auth = AsyncMock(spec=AuthService)
    access = AsyncMock(spec=AccessService)
    access.ensure_project_access.return_value = AccessGrant(
        project_id=1, resource_id=1, is_owner=True
    )
    app.dependency_overrides.pop(get_principal)
    app.dependency_overrides.pop(require_project_access)
    app.dependency_overrides.update(
        {
            get_auth_service: lambda: auth,
            get_access_service: lambda: access,
            get_projects_service: lambda: service,
        }
    )
    key = ApiKeyGrant(
        key_id=uuid4(),
        label="Synthetic context reader",
        key_prefix="fixture",
        owner_user_id=current_principal.user_id,
        scopes=frozenset({"projects:read"}),
    )
    for scopes, status in (({"projects:read"}, 200), ({"tasks:read"}, 403), (set(), 403)):
        service.get_context_history.reset_mock()
        auth.resolve_principal.return_value = replace(
            current_principal,
            scope=ApiTokenScope.READ,
            via_api_token=True,
            api_key=replace(key, scopes=frozenset(scopes)),
        )
        response = await api_client.get("/api/v1/projects/1/context-history")
        assert response.status_code == status
        if status == 200:
            assert response.json()["items"] == []
        else:
            service.get_context_history.assert_not_awaited()
