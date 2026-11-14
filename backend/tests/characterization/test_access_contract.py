"""Контракт доступа: Bearer-токен и одинаковый 404 для чужого объекта.

Этап 2 переносит аутентификацию и проверку доступа из Depends-слоя в сервисы,
поэтому здесь зафиксирован именно внешний результат: какой код и какое тело
получает клиент. Реализация под этим слоем свободна.
"""

from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from main import app
from src.db.models.api_tokens import ApiToken, ApiTokenScope
from src.db.models.documents import Document
from src.db.models.project_members import ProjectMember, ProjectRole
from src.db.models.projects import Project
from src.db.models.tasks import Task
from src.db.models.users import User
from src.dependencies.auth import get_principal
from src.dependencies.document_catalog import (
    get_document_catalog_principal,
    get_document_catalog_service,
)
from src.dependencies.repositories import (
    get_api_tokens_repository,
    get_documents_repository,
    get_project_members_repository,
    get_projects_repository,
    get_tasks_repository,
    get_users_repository,
)
from src.services.auth import AuthService, Principal
from src.services.document_catalog import DocumentCatalogService
from src.utils.api_tokens import hash_token_secret

OWNER_ID = 1
OTHER_PROJECT_ID = 42


def _user(**overrides) -> User:
    """Активный пользователь запроса."""
    values = {
        "id": OWNER_ID,
        "username": "characterization",
        "password_hash": "hash",
        "last_name": "Тестов",
        "first_name": "Тест",
        "is_active": True,
        "created_at": datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return User(**values)


def _token(secret: str, **overrides) -> ApiToken:
    """Токен доступа с настраиваемым сроком и признаком отзыва."""
    now = datetime.now(UTC)
    values = {
        "id": 1,
        "user_id": OWNER_ID,
        "name": "Ноутбук",
        "token_hash": hash_token_secret(secret),
        "prefix": secret[:8],
        "scope": ApiTokenScope.READ,
        "expires_at": None,
        "revoked_at": None,
        "last_used_at": None,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return ApiToken(**values)


class FakeUsersRepository:
    """Репозиторий пользователей в памяти."""

    def __init__(self, user: User | None) -> None:
        self.user = user

    async def get_by_id(self, user_id: int) -> User | None:
        if self.user is None or self.user.id != user_id:
            return None
        return self.user


class FakeTokensRepository:
    """Репозиторий токенов, отдающий только действующие токены.

    Правило «действующий» повторяет продовый SQL: непросроченный и не
    отозванный. Тест проверяет, что все недействующие варианты клиент видит
    одинаково.
    """

    def __init__(self, token: ApiToken | None) -> None:
        self.token = token

    async def get_active_by_hash(self, token_hash: str) -> ApiToken | None:
        token = self.token
        if token is None or token.token_hash != token_hash:
            return None
        if token.revoked_at is not None:
            return None
        if token.expires_at is not None and token.expires_at <= datetime.now(UTC):
            return None
        return token

    async def touch_last_used(self, token: ApiToken, *, commit: bool = True) -> None:
        token.last_used_at = datetime.now(UTC)


class FakeMembersRepository:
    """Участие в проекте: пользователь состоит только в своих проектах."""

    def __init__(self, member_project_ids: set[int]) -> None:
        self.member_project_ids = member_project_ids

    async def get(self, project_id: int, user_id: int) -> ProjectMember | None:
        if project_id not in self.member_project_ids:
            return None
        return ProjectMember(project_id=project_id, user_id=user_id, role=ProjectRole.OWNER)


class FakeProjectsRepository:
    """Репозиторий проектов, знающий и чужой проект тоже."""

    async def get_by_id(self, project_id: int) -> Project | None:
        return Project(
            id=project_id,
            owner_id=99,
            key="OTHER",
            name="Чужой проект",
            color="#58a6ff",
        )


class FakeTasksRepository:
    """Репозиторий задач: задача существует и принадлежит чужому проекту."""

    async def get_by_id(self, task_id: int) -> Task | None:
        return Task(id=task_id, project_id=OTHER_PROJECT_ID)


class FakeDocumentsRepository:
    """Репозиторий документов: документ существует в чужом проекте."""

    async def get_by_id(self, document_id: int) -> Document | None:
        return Document(id=document_id, project_id=OTHER_PROJECT_ID)


@pytest.fixture
def bearer_graph(anonymous: None) -> Callable[[ApiToken | None, User | None], None]:
    """Ставит в граф репозитории аутентификации под управлением теста."""

    def install(token: ApiToken | None, user: User | None = None) -> None:
        app.dependency_overrides[get_api_tokens_repository] = lambda: FakeTokensRepository(token)
        app.dependency_overrides[get_users_repository] = lambda: FakeUsersRepository(
            user if user is not None else _user()
        )

    return install


async def test_unusable_tokens_are_indistinguishable(
    raw_client: AsyncClient,
    bearer_graph: Callable[..., None],
) -> None:
    """Выдуманный, истёкший и отозванный токен дают один и тот же ответ.

    Иначе перебором выяснялось бы, какой именно токен когда-то
    существовал. Все три случая проверяются вместе: расхождение любого из
    них — это и есть утечка.
    """
    secret = "telos_token_characterization"
    past = datetime.now(UTC) - timedelta(days=1)
    stored = {
        "выдуманный": _token("telos_token_other"),
        "истёкший": _token(secret, expires_at=past),
        "отозванный": _token(secret, revoked_at=past),
    }

    answers: dict[str, tuple[int, dict]] = {}
    for case, token in stored.items():
        bearer_graph(token)
        response = await raw_client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {secret}"},
        )
        answers[case] = (response.status_code, response.json())

    expected = (401, {"detail": "Требуется вход в систему."})
    unexpected = {case: answer for case, answer in answers.items() if answer != expected}

    assert not unexpected, f"Непригодные токены отвечают по-разному: {unexpected}"


async def test_missing_authorization_matches_unusable_token(
    raw_client: AsyncClient,
    bearer_graph: Callable[..., None],
) -> None:
    """Отсутствие токена отвечает так же, как недействующий токен."""
    bearer_graph(None)

    response = await raw_client.get("/api/v1/projects")

    assert response.status_code == 401
    assert response.json() == {"detail": "Требуется вход в систему."}


async def test_token_of_disabled_user_is_rejected(
    raw_client: AsyncClient,
    bearer_graph: Callable[..., None],
) -> None:
    """Отключённый пользователь не проходит даже с действующим токеном."""
    secret = "telos_token_characterization"
    bearer_graph(_token(secret), _user(is_active=False))

    response = await raw_client.get(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {secret}"},
    )

    assert response.status_code in (401, 403)


async def test_read_token_reaches_read_endpoint(
    raw_client: AsyncClient,
    bearer_graph: Callable[..., None],
) -> None:
    """Действующий READ-токен проходит аутентификацию на чтении."""
    secret = "telos_token_characterization"
    bearer_graph(_token(secret, scope=ApiTokenScope.READ))

    response = await raw_client.get(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {secret}"},
    )

    assert response.status_code != 401


async def test_token_cannot_manage_tokens(
    raw_client: AsyncClient,
    bearer_graph: Callable[..., None],
) -> None:
    """Управление токенами закрыто для самих токенов."""
    secret = "telos_token_characterization"
    bearer_graph(_token(secret, scope=ApiTokenScope.WRITE))

    response = await raw_client.get(
        "/api/v1/users/me/tokens",
        headers={"Authorization": f"Bearer {secret}"},
    )

    assert response.status_code == 403


def _principal() -> Principal:
    """Принципал запроса с полными правами сессии интерфейса."""
    return Principal(
        user_id=OWNER_ID,
        username="characterization",
        last_name="Тестов",
        first_name="Тест",
        middle_name=None,
        scope=ApiTokenScope.WRITE,
        via_api_token=False,
    )


@pytest.fixture
async def foreign_objects(anonymous: None) -> None:
    """Все объекты существуют, но принадлежат проекту без участия пользователя."""
    catalog_repo = AsyncMock()
    catalog_repo.get.return_value = None
    auth = AsyncMock(spec=AuthService)
    auth.resolve_principal.return_value = _principal()

    @asynccontextmanager
    async def scope():
        yield SimpleNamespace(catalog=catalog_repo, auth=auth)

    catalog = DocumentCatalogService(
        scope=scope,
        storage=None,
        max_file_size=10000,
        knowledge_enabled=False,
        embedding_signature="synthetic",
    )
    await catalog.resolve_principal(session_token="synthetic-session", bearer_secret=None)
    app.dependency_overrides.update(
        {
            get_document_catalog_service: lambda: catalog,
            get_document_catalog_principal: lambda: _principal(),
            get_principal: lambda: _principal(),
            get_project_members_repository: lambda: FakeMembersRepository(set()),
            get_projects_repository: lambda: FakeProjectsRepository(),
            get_tasks_repository: lambda: FakeTasksRepository(),
            get_documents_repository: lambda: FakeDocumentsRepository(),
        }
    )


FOREIGN_PATHS = (
    f"/api/v1/projects/{OTHER_PROJECT_ID}",
    "/api/v1/tasks/7",
    "/api/v1/documents/d1_ZG9jdW1lbnQ6Nw",
)


async def test_foreign_object_is_not_found(
    raw_client: AsyncClient,
    foreign_objects: None,
) -> None:
    """Существующий чужой объект отвечает 404, а не 403.

    Одинаковый ответ для чужого и несуществующего объекта — продуктовое
    правило: клиент не должен узнавать чужие идентификаторы перебором.
    Правило проверяется сразу на всех типах объектов.
    """
    answers = {}
    for path in FOREIGN_PATHS:
        response = await raw_client.get(path)
        answers[path] = (response.status_code, response.json())

    expected = (404, {"detail": "Объект не найден."})
    unexpected = {
        path: answer
        for path, answer in answers.items()
        if answer
        != (
            (404, {"detail": "Документ недоступен или не найден."})
            if "/documents/" in path
            else expected
        )
    }

    assert not unexpected, f"Чужой объект отличим от несуществующего: {unexpected}"
