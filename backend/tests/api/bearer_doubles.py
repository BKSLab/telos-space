"""Дублёры аутентификации по Bearer-токену для API-тестов.

Тесты прав должны проходить настоящую цепочку `AuthService` → `AccessService`
→ guard, а не подменять её результат. Поэтому подменяются репозитории под
сервисами, а не сами сервисы.
"""

from datetime import UTC, datetime

from main import app
from src.db.models.api_tokens import ApiToken, ApiTokenScope
from src.db.models.project_members import ProjectMember, ProjectRole
from src.db.models.users import User
from src.dependencies.repositories import (
    get_api_tokens_repository,
    get_project_members_repository,
    get_users_repository,
)
from src.schemas.users import UserSchema
from src.utils.api_tokens import hash_token_secret

SECRET = "telos_token_test_token"
USER_ID = 1


def user() -> User:
    """Активный владелец токена."""
    return User(
        id=USER_ID,
        username="reader",
        password_hash="hash",
        last_name="Читателев",
        first_name="Чтец",
        is_active=True,
        created_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
    )


def user_schema() -> UserSchema:
    """Карточка того же пользователя для подменённого сервиса профиля."""
    return UserSchema(
        id=USER_ID,
        username="reader",
        last_name="Читателев",
        first_name="Чтец",
        middle_name=None,
        email=None,
        phone=None,
        telegram=None,
        has_avatar=False,
        created_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
    )


def token(scope: ApiTokenScope) -> ApiToken:
    """Действующий токен с заданными правами."""
    now = datetime.now(UTC)
    return ApiToken(
        id=1,
        user_id=USER_ID,
        name="Внешний клиент",
        token_hash=hash_token_secret(SECRET),
        prefix=SECRET.removeprefix("telos_token_")[:8],
        scope=scope,
        expires_at=None,
        revoked_at=None,
        last_used_at=None,
        created_at=now,
        updated_at=now,
    )


class FakeUsersRepository:
    """Репозиторий пользователей в памяти."""

    async def get_by_id(self, user_id: int) -> User | None:
        return user() if user_id == USER_ID else None


class FakeTokensRepository:
    """Репозиторий токенов, отдающий один действующий токен."""

    def __init__(self, scope: ApiTokenScope) -> None:
        self.scope = scope

    async def get_active_by_hash(self, token_hash: str) -> ApiToken | None:
        return token(self.scope) if token_hash == hash_token_secret(SECRET) else None

    async def touch_last_used(self, stored: ApiToken, *, commit: bool = True) -> None:
        return None


class FakeMembersRepository:
    """Пользователь состоит во всех проектах: тест проверяет права, не доступ."""

    async def get(self, project_id: int, user_id: int) -> ProjectMember:
        return ProjectMember(project_id=project_id, user_id=user_id, role=ProjectRole.OWNER)


def install_bearer_auth(scope: ApiTokenScope) -> None:
    """Ставит в граф аутентификацию по токену с выбранными правами."""
    app.dependency_overrides.update(
        {
            get_users_repository: lambda: FakeUsersRepository(),
            get_api_tokens_repository: lambda: FakeTokensRepository(scope),
            get_project_members_repository: lambda: FakeMembersRepository(),
        }
    )


def auth_header() -> dict[str, str]:
    """Заголовок предъявления токена."""
    return {"Authorization": f"Bearer {SECRET}"}
