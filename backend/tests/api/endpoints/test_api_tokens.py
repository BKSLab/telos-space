"""Проверки HTTP-контракта управления токенами доступа."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from main import app
from src.db.models.api_tokens import ApiToken, ApiTokenScope
from src.dependencies.auth import get_principal, require_session
from src.dependencies.services import get_api_tokens_service
from src.exceptions.api_tokens import ApiTokenLimitExceededError, ApiTokenNotFoundError
from src.schemas.api_tokens import ApiTokenCreatedSchema, ApiTokenSchema
from src.services.auth import Principal

TOKENS_URL = "/api/v1/users/me/tokens"


class FakeTokensService:
    """Сервис токенов, подменяющий обращения к базе."""

    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.issued: dict | None = None
        self.revoked: tuple[int, int] | None = None

    async def list_tokens(self, user_id: int) -> list[ApiTokenSchema]:
        return [
            ApiTokenSchema(
                id=1,
                name="Ноутбук",
                prefix="Ab123456",
                scope="READ",
                created_at=datetime.now(UTC),
                expires_at=None,
                revoked_at=None,
                last_used_at=None,
            )
        ]

    async def issue_token(self, *, user_id: int, data) -> ApiTokenCreatedSchema:
        if self.error is not None:
            raise self.error
        self.issued = {"user_id": user_id, "name": data.name, "scope": data.scope}
        return ApiTokenCreatedSchema(
            token=ApiTokenSchema(
                id=2,
                name=data.name,
                prefix="Cd123456",
                scope=data.scope,
                created_at=datetime.now(UTC),
                expires_at=None,
                revoked_at=None,
                last_used_at=None,
            ),
            secret="telos_token_secret-value",
        )

    async def revoke_token(self, *, token_id: int, user_id: int) -> None:
        if self.error is not None:
            raise self.error
        self.revoked = (token_id, user_id)


@pytest.fixture
def session_user(current_principal: Principal):
    """Пускает запросы как из интерфейса и подменяет сервис токенов."""
    service = FakeTokensService()
    app.dependency_overrides[require_session] = lambda: current_principal
    app.dependency_overrides[get_api_tokens_service] = lambda: service
    yield service
    app.dependency_overrides.pop(require_session, None)
    app.dependency_overrides.pop(get_api_tokens_service, None)


async def test_list_tokens_never_returns_secret(
    api_client: AsyncClient,
    session_user: FakeTokensService,
) -> None:
    """Список токенов не содержит ни секрета, ни хеша."""
    response = await api_client.get(TOKENS_URL)

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["prefix"] == "Ab123456"
    assert "secret" not in payload[0]
    assert "token_hash" not in payload[0]


async def test_create_token_returns_secret_once(
    api_client: AsyncClient,
    session_user: FakeTokensService,
) -> None:
    """Выпуск возвращает секрет вместе с карточкой токена."""
    response = await api_client.post(
        TOKENS_URL,
        json={"name": "Ноутбук", "scope": "WRITE", "ttl_days": 30},
    )

    assert response.status_code == 201
    assert response.json()["secret"] == "telos_token_secret-value"
    assert session_user.issued["scope"] == "WRITE"


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", TOKENS_URL),
        ("post", TOKENS_URL),
        ("delete", f"{TOKENS_URL}/1"),
    ],
)
async def test_api_token_cannot_manage_tokens(
    api_client: AsyncClient,
    current_principal: Principal,
    method: str,
    url: str,
) -> None:
    """Скомпрометированный токен не может выпустить себе замену или отозвать чужие."""
    principal = replace(current_principal, scope=ApiTokenScope.WRITE, via_api_token=True)
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_api_tokens_service] = lambda: FakeTokensService()
    try:
        response = await getattr(api_client, method)(
            url,
            **({"json": {"name": "Ноутбук"}} if method == "post" else {}),
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_api_tokens_service, None)

    assert response.status_code == 403


def test_api_token_model_never_serialises_hash() -> None:
    """Схема ответа не знает о поле с хешем, даже если модель его несёт."""
    token = ApiToken(
        id=1,
        user_id=1,
        name="Ноутбук",
        token_hash="секрет",
        prefix="Ab123456",
        scope=ApiTokenScope.READ,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    dumped = ApiTokenSchema.model_validate(token).model_dump()

    assert "token_hash" not in dumped
    assert "секрет" not in str(dumped)


async def test_create_token_validates_name_scope_and_limit(api_client: AsyncClient, session_user: FakeTokensService) -> None:
    """Пустое имя, неизвестный скоуп и исчерпанный лимит отклоняются своими кодами."""
    # Имя из пробелов не проходит валидацию.
    response = await api_client.post(TOKENS_URL, json={"name": "   ", "scope": "READ"})

    assert response.status_code == 422
    # Произвольные права не принимаются.
    response = await api_client.post(TOKENS_URL, json={"name": "Ноутбук", "scope": "ADMIN"})

    assert response.status_code == 422
    # Превышение лимита отдаётся как конфликт, а не как ошибка сервера.
    session_user.error = ApiTokenLimitExceededError(10)

    response = await api_client.post(TOKENS_URL, json={"name": "Лишний", "scope": "READ"})

    assert response.status_code == 409


async def test_revoke_token_and_unknown_token_answers(api_client: AsyncClient, session_user: FakeTokensService, current_principal: Principal) -> None:
    """Отзыв проходит, неизвестный токен отвечает 404."""
    # Отзыв выполняется от имени владельца.
    response = await api_client.delete(f"{TOKENS_URL}/5")

    assert response.status_code == 204
    assert session_user.revoked == (5, current_principal.user_id)
    # Чужой или несуществующий токен даёт 404.
    session_user.error = ApiTokenNotFoundError(99)

    response = await api_client.delete(f"{TOKENS_URL}/99")

    assert response.status_code == 404
