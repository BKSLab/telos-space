"""Разрешение принципала запроса сервисом аутентификации.

Правила прав живут в сервисе, а не в Depends-слое, поэтому проверяются
здесь — один раз для обоих транспортов.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from src.db.models.api_tokens import ApiToken, ApiTokenScope
from src.db.models.users import User
from src.exceptions.api_tokens import ApiTokensRepositoryError
from src.exceptions.auth import (
    AuthServiceError,
    InactiveUserError,
    NotAuthenticatedError,
)
from src.exceptions.users import UsersRepositoryError
from src.repositories.api_tokens import ApiTokensRepository
from src.repositories.users import UsersRepository
from src.services.auth import AuthService, Principal
from src.utils.api_tokens import hash_token_secret
from src.utils.tokens import create_access_token

SECRET = "telos_token_valid"
INVITE = "код-приглашения"


def make_user(**overrides) -> User:
    """Пользователь с заполненными полями идентичности."""
    values = {
        "id": 1,
        "username": "tester",
        "password_hash": "hash",
        "last_name": "Тестов",
        "first_name": "Тест",
        "middle_name": None,
        "is_active": True,
        "created_at": datetime.now(UTC),
    }
    values.update(overrides)
    return User(**values)


def make_token(secret: str = SECRET, **overrides) -> ApiToken:
    """Действующий токен доступа, если не указано иное."""
    now = datetime.now(UTC)
    values = {
        "id": 1,
        "user_id": 1,
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


def build_service(
    *,
    user: User | None = None,
    token: ApiToken | None = None,
    users_error: Exception | None = None,
    tokens_error: Exception | None = None,
) -> tuple[AuthService, AsyncMock, AsyncMock]:
    """Собирает сервис на дублёрах репозиториев с фиксированными ответами."""
    users = AsyncMock(spec=UsersRepository)
    users.get_by_id.return_value = user
    if users_error is not None:
        users.get_by_id.side_effect = users_error

    tokens = AsyncMock(spec=ApiTokensRepository)
    tokens.get_active_by_hash.return_value = token
    if tokens_error is not None:
        tokens.get_active_by_hash.side_effect = tokens_error

    service = AuthService(
        users_repository=users,
        tokens_repository=tokens,
        invite_code=INVITE,
    )
    return service, users, tokens


async def test_token_authenticates_and_carries_its_scope() -> None:
    """Действующий токен пускает пользователя и приносит свои права.

    Право записи — часть принципала, а не отдельная проверка: скоуп
    токена и производный от него `can_write` обязаны совпадать.
    """
    service, _, tokens = build_service(user=make_user(), token=make_token())

    principal = await service.resolve_principal(session_token=None, bearer_secret=SECRET)

    assert principal.user_id == 1
    assert principal.username == "tester"
    assert principal.scope is ApiTokenScope.READ
    assert principal.via_api_token is True
    assert principal.can_write is False
    tokens.touch_last_used.assert_awaited_once()

    service, _, _ = build_service(
        user=make_user(),
        token=make_token(scope=ApiTokenScope.WRITE),
    )
    write_principal = await service.resolve_principal(
        session_token=None,
        bearer_secret=SECRET,
    )
    assert write_principal.can_write is True


async def test_unusable_token_or_user_is_rejected_indistinguishably() -> None:
    """Выдуманный, истёкший и отозванный токен неотличимы друг от друга.

    Репозиторий не отдаёт недействующий токен, поэтому все три случая
    приходят в сервис одинаково — как отсутствие токена. Отключённый
    пользователь отличается: он существует, и ему отвечают своим кодом.
    """
    service, _, _ = build_service(user=make_user(), token=None)
    with pytest.raises(NotAuthenticatedError):
        await service.resolve_principal(session_token=None, bearer_secret="telos_token_unknown")

    service, _, _ = build_service(user=None, token=make_token())
    with pytest.raises(NotAuthenticatedError):
        await service.resolve_principal(session_token=None, bearer_secret=SECRET)

    service, _, _ = build_service(user=make_user(is_active=False), token=make_token())
    with pytest.raises(InactiveUserError):
        await service.resolve_principal(session_token=None, bearer_secret=SECRET)


async def test_repository_failure_is_not_an_access_denial() -> None:
    """Сбой базы отличается от отказа в доступе.

    Иначе временная недоступность PostgreSQL молча выглядела бы как
    неверный токен, и причину искали бы не там. Правило одинаково для
    чтения токена и для чтения пользователя.
    """
    service, _, _ = build_service(
        user=make_user(),
        tokens_error=ApiTokensRepositoryError("сбой БД"),
    )
    with pytest.raises(AuthServiceError) as error:
        await service.resolve_principal(session_token=None, bearer_secret=SECRET)
    assert not isinstance(error.value, NotAuthenticatedError)
    assert error.value.status_code == 500

    service, _, _ = build_service(
        token=make_token(),
        users_error=UsersRepositoryError("сбой БД"),
    )
    with pytest.raises(AuthServiceError) as users_error:
        await service.resolve_principal(session_token=None, bearer_secret=SECRET)
    assert not isinstance(users_error.value, NotAuthenticatedError)


async def test_failed_touch_does_not_block_access() -> None:
    """Отметка использования — диагностика, а не условие доступа."""
    service, _, tokens = build_service(user=make_user(), token=make_token())
    tokens.touch_last_used.side_effect = ApiTokensRepositoryError("сбой БД")

    principal = await service.resolve_principal(session_token=None, bearer_secret=SECRET)

    assert principal.user_id == 1


async def test_cookie_gives_full_rights_and_bearer_wins_over_it() -> None:
    """Cookie даёт полные права, но токен в заголовке важнее.

    Скоуп введён для внешних клиентов: если бы cookie перебивала токен,
    ограничение на чтение обходилось бы одним лишним заголовком.
    Подделанная cookie и полное отсутствие учётных данных не пускают.
    """
    user = make_user()
    service, _, _ = build_service(user=user)
    principal = await service.resolve_principal(
        session_token=create_access_token(user.id),
        bearer_secret=None,
    )
    assert principal.scope is ApiTokenScope.WRITE
    assert principal.via_api_token is False
    assert principal.can_write is True

    service, _, _ = build_service(user=user, token=make_token())
    both = await service.resolve_principal(
        session_token=create_access_token(user.id),
        bearer_secret=SECRET,
    )
    assert both.scope is ApiTokenScope.READ
    assert both.via_api_token is True

    service, _, _ = build_service(user=make_user())
    with pytest.raises(NotAuthenticatedError):
        await service.resolve_principal(session_token=None, bearer_secret=None)
    with pytest.raises(NotAuthenticatedError):
        await service.resolve_principal(session_token="не.настоящий.токен", bearer_secret=None)


def test_principal_composes_display_names() -> None:
    """Полное и короткое имя различаются отчеством, без имени остаётся логин."""
    named = Principal(
        user_id=1,
        username="tester",
        last_name="Тестов",
        first_name="Тест",
        middle_name="Тестович",
        scope=ApiTokenScope.WRITE,
        via_api_token=False,
    )
    assert named.full_name == "Тестов Тест Тестович"
    assert named.short_name == "Тестов Тест"

    nameless = Principal(
        user_id=1,
        username="tester",
        last_name="",
        first_name="",
        middle_name=None,
        scope=ApiTokenScope.READ,
        via_api_token=True,
    )
    assert nameless.full_name == "tester"
    assert nameless.short_name == "tester"
