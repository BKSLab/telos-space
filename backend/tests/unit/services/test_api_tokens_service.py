"""Проверки сервиса токенов доступа."""

from datetime import UTC, datetime, timedelta

import pytest

from src.db.models.api_tokens import ApiToken, ApiTokenScope
from src.exceptions.api_tokens import (
    ApiTokenLimitExceededError,
    ApiTokenNotFoundError,
    ApiTokensRepositoryError,
    ApiTokensServiceError,
)
from src.schemas.api_tokens import ApiTokenCreateSchema
from src.services.api_tokens import ApiTokensService
from src.utils.api_tokens import hash_token_secret

# Предел активных токенов сервис получает значением, а не читает из настроек.
MAX_ACTIVE_TOKENS = 10


class FakeTokensRepository:
    """Репозиторий токенов в памяти."""

    def __init__(self, *, active_count: int = 0, tokens: list[ApiToken] | None = None):
        self.active_count = active_count
        self.tokens = tokens or []
        self.created: dict | None = None
        self.revoked: tuple[int, int] | None = None
        self.touched: list[ApiToken] = []

    async def count_active_by_user(self, user_id: int) -> int:
        return self.active_count

    async def create(self, *, commit: bool = True, **kwargs) -> ApiToken:
        self.created = kwargs
        self.created_with_commit = commit
        return ApiToken(
            id=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            **kwargs,
        )

    async def get_by_user(self, user_id: int) -> list[ApiToken]:
        return self.tokens

    async def revoke(self, *, token_id: int, user_id: int, commit: bool = True) -> bool:
        self.revoked = (token_id, user_id)
        return any(token.id == token_id for token in self.tokens)

    async def get_active_by_hash(self, token_hash: str) -> ApiToken | None:
        return next(
            (token for token in self.tokens if token.token_hash == token_hash),
            None,
        )

    async def touch_last_used(self, token: ApiToken, *, commit: bool = True) -> None:
        self.touched.append(token)


def _token(**overrides) -> ApiToken:
    values = {
        "id": 1,
        "user_id": 1,
        "name": "Ноутбук",
        "token_hash": "hash",
        "prefix": "Ab123456",
        "scope": ApiTokenScope.READ,
        "expires_at": None,
        "revoked_at": None,
        "last_used_at": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    values.update(overrides)
    return ApiToken(**values)


async def test_issue_token_returns_secret_once_and_stores_only_hash() -> None:
    """Секрет возвращается пользователю, а в репозиторий уходит только хеш."""
    repository = FakeTokensRepository()
    service = ApiTokensService(tokens_repository=repository, max_active_tokens=MAX_ACTIVE_TOKENS)

    result = await service.issue_token(
        user_id=1,
        data=ApiTokenCreateSchema(name="Ноутбук", scope="WRITE", ttl_days=30),
    )

    assert result.secret.startswith("telos_token_")
    assert repository.created["token_hash"] == hash_token_secret(result.secret)
    assert repository.created["token_hash"] != result.secret
    assert repository.created["scope"] is ApiTokenScope.WRITE
    assert result.token.prefix == result.secret[len("telos_token_"):][:8]


async def test_issue_token_sets_lifetime_and_respects_the_limit() -> None:
    """Срок жизни токена: бессрочный, с TTL и отказ при исчерпании лимита."""
    # Отсутствие срока означает бессрочный токен.
    repository = FakeTokensRepository()
    service = ApiTokensService(tokens_repository=repository, max_active_tokens=MAX_ACTIVE_TOKENS)

    await service.issue_token(
        user_id=1,
        data=ApiTokenCreateSchema(name="Бессрочный", scope="READ", ttl_days=None),
    )

    assert repository.created["expires_at"] is None
    # Срок жизни отсчитывается от момента выпуска.
    repository = FakeTokensRepository()
    service = ApiTokensService(tokens_repository=repository, max_active_tokens=MAX_ACTIVE_TOKENS)

    await service.issue_token(
        user_id=1,
        data=ApiTokenCreateSchema(name="Срочный", scope="READ", ttl_days=10),
    )

    expires_at = repository.created["expires_at"]
    assert expires_at is not None
    assert timedelta(days=9) < expires_at - datetime.now(UTC) <= timedelta(days=10)
    # Достигнутый предел действующих токенов запрещает выпуск.
    repository = FakeTokensRepository(active_count=10)
    service = ApiTokensService(tokens_repository=repository, max_active_tokens=MAX_ACTIVE_TOKENS)

    with pytest.raises(ApiTokenLimitExceededError):
        await service.issue_token(
            user_id=1,
            data=ApiTokenCreateSchema(name="Лишний", scope="READ", ttl_days=None),
        )

    assert repository.created is None


async def test_token_list_and_revocation_keep_the_secret_safe() -> None:
    """Список не отдаёт хеш, отзыв неизвестного токена — 404, отзыв идёт с владельцем."""
    # Список токенов не содержит ни секрета, ни его хеша.
    repository = FakeTokensRepository(tokens=[_token(token_hash="секретный-хеш")])
    service = ApiTokensService(tokens_repository=repository, max_active_tokens=MAX_ACTIVE_TOKENS)

    tokens = await service.list_tokens(1)

    assert len(tokens) == 1
    assert "token_hash" not in tokens[0].model_dump()
    assert "секретный-хеш" not in str(tokens[0].model_dump())
    # Отзыв чужого или несуществующего токена не молчит.
    repository = FakeTokensRepository(tokens=[])
    service = ApiTokensService(tokens_repository=repository, max_active_tokens=MAX_ACTIVE_TOKENS)

    with pytest.raises(ApiTokenNotFoundError):
        await service.revoke_token(token_id=99, user_id=1)
    # Отзыв всегда ограничен владельцем токена.
    repository = FakeTokensRepository(tokens=[_token(id=5)])
    service = ApiTokensService(tokens_repository=repository, max_active_tokens=MAX_ACTIVE_TOKENS)

    await service.revoke_token(token_id=5, user_id=1)

    assert repository.revoked == (5, 1)


async def test_secret_is_resolved_by_hash_and_failures_are_wrapped() -> None:
    """Поиск идёт по хешу, сбой репозитория становится ошибкой сервиса."""
    # Поиск токена идёт по хешу, а не по самому секрету.
    secret = "telos_token_test-secret"
    repository = FakeTokensRepository(tokens=[_token(token_hash=hash_token_secret(secret))])
    service = ApiTokensService(tokens_repository=repository, max_active_tokens=MAX_ACTIVE_TOKENS)

    assert await service.resolve_secret(secret) is not None
    assert await service.resolve_secret("telos_token_другой") is None
    # Ошибка репозитория не утекает наружу в исходном виде.
    class BrokenRepository(FakeTokensRepository):
        async def get_by_user(self, user_id: int) -> list[ApiToken]:
            raise ApiTokensRepositoryError("сбой БД")

    service = ApiTokensService(
        tokens_repository=BrokenRepository(),
        max_active_tokens=MAX_ACTIVE_TOKENS,
    )

    with pytest.raises(ApiTokensServiceError):
        await service.list_tokens(1)
