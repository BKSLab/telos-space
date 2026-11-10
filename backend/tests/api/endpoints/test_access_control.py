import pytest
from httpx import AsyncClient

from main import app

# Единственные адреса, доступные без входа: всё остальное закрыто.
PUBLIC_PATHS = {"/api/v1/auth/register", "/api/v1/auth/login", "/api/v1/auth/logout"}

PROTECTED_REQUESTS = [
    ("get", "/api/v1/auth/me"),
    ("get", "/api/v1/dashboard"),
    ("get", "/api/v1/dashboard/analytics"),
    ("post", "/api/v1/dashboard/analytics"),
    ("get", "/api/v1/projects"),
    ("post", "/api/v1/projects"),
    ("get", "/api/v1/projects/1"),
    ("get", "/api/v1/projects/1/stats"),
    ("get", "/api/v1/projects/1/members"),
    ("post", "/api/v1/projects/1/members"),
    ("delete", "/api/v1/projects/1/members/2"),
    ("get", "/api/v1/projects/1/members/2/avatar"),
    ("get", "/api/v1/projects/1/boards/3/stickers"),
    ("post", "/api/v1/projects/1/boards/3/stickers"),
    ("patch", "/api/v1/projects/1/boards/3/stickers/1"),
    ("delete", "/api/v1/projects/1/boards/3/stickers/1?revision=1"),
    ("get", "/api/v1/projects/1/stages"),
    ("get", "/api/v1/projects/1/tasks"),
    ("get", "/api/v1/projects/1/wbs"),
    ("post", "/api/v1/projects/1/wbs/tasks/1/placement"),
    ("post", "/api/v1/projects/1/wbs/suggestion"),
    ("post", "/api/v1/projects/1/wbs/suggestion/apply"),
    ("get", "/api/v1/projects/1/documents"),
    ("get", "/api/v1/projects/1/knowledge/status"),
    ("post", "/api/v1/projects/1/knowledge/ask"),
    ("post", "/api/v1/projects/1/knowledge/reindex"),
    ("get", "/api/v1/tasks/1"),
    ("get", "/api/v1/tasks/1/comments"),
    ("get", "/api/v1/tasks/1/activity"),
    ("get", "/api/v1/tasks/1/attachments"),
    ("get", "/api/v1/documents/1"),
    ("patch", "/api/v1/stages/1"),
    ("post", "/api/v1/document-links"),
    ("patch", "/api/v1/users/me"),
    ("get", "/api/v1/users/me/avatar"),
]


@pytest.fixture
def anonymous() -> None:
    """Снимает подмену сессии: тесты этого модуля проверяют саму стену входа."""
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_protected_endpoints_require_login(
    api_client: AsyncClient,
    anonymous: None,
) -> None:
    """Ни один защищённый маршрут не отвечает без входа.

    Список проходится одним тестом: сообщение показывает сразу все
    открывшиеся маршруты. Это одно правило, а не тридцать пять разных.
    """
    offenders: list[str] = []
    for method, path in PROTECTED_REQUESTS:
        request_kwargs = {"json": {}} if method in {"post", "put", "patch"} else {}
        response = await api_client.request(method.upper(), path, **request_kwargs)
        if response.status_code != 401:
            offenders.append(f"{method.upper()} {path} -> {response.status_code}")

    assert not offenders, "Доступны без входа:\n  " + "\n  ".join(offenders)


@pytest.mark.asyncio
async def test_invalid_session_cookie_is_rejected(
    api_client: AsyncClient,
    anonymous: None,
) -> None:
    response = await api_client.get(
        "/api/v1/auth/me",
        cookies={"telos_session": "not.a.valid.token"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_public_paths_are_declared_explicitly() -> None:
    """Страхует от случайного открытия эндпоинта: список публичных путей фиксирован."""
    documented = {
        route.path for route in app.routes if getattr(route, "path", "").startswith("/api/v1/auth/")
    }

    assert documented - {"/api/v1/auth/me"} == PUBLIC_PATHS
