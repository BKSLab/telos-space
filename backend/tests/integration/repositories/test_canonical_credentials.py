"""Канонические учётные данные: хранение, владелец, точные права и отзыв на PostgreSQL."""

import json
import re

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.api_keys import ApiKey
from src.db.models.api_tokens import ApiToken, ApiTokenScope
from src.exceptions.api_keys import ApiKeyInvalidError, ApiKeyScopeError
from src.exceptions.auth import NotAuthenticatedError
from src.repositories.api_keys import ApiKeysRepository
from src.repositories.api_tokens import ApiTokensRepository
from src.repositories.users import UsersRepository
from src.schemas.api_keys import PersonalApiKeyCreateSchema, PersonalApiKeyUpdateSchema
from src.schemas.api_tokens import ApiTokenCreateSchema
from src.services.api_keys import ApiKeysService
from src.services.api_tokens import ApiTokensService
from src.services.auth import AuthService
from src.utils.api_keys import hash_api_key
from src.utils.api_tokens import hash_token_secret
from src.utils.tokens import create_access_token


def auth_service(session: AsyncSession) -> AuthService:
    """Собирает новый сервис с реальными репозиториями сохранённых учётных данных."""
    return AuthService(
        UsersRepository(session),
        tokens_repository=ApiTokensRepository(session),
        api_keys_repository=ApiKeysRepository(session),
        invite_code="credentials-test",
    )


async def test_issued_credentials_preserve_secrets_owner_and_exact_permissions_until_revoked(
    db_session, user
):
    owner_id = user.id
    users = UsersRepository(db_session)
    keys = ApiKeysRepository(db_session)
    tokens = ApiTokensRepository(db_session)
    key_service = ApiKeysService(keys, users)
    token_service = ApiTokensService(tokens, max_active_tokens=5)
    other = await users.save(
        data={
            "username": "credential-cookie-owner",
            "password_hash": "unused",
            "last_name": "Другой",
            "first_name": "Пользователь",
            "is_active": True,
        }
    )
    other_id = other.id
    cookie = create_access_token(other_id)
    created = await key_service.create_personal_key(
        PersonalApiKeyCreateSchema(label="Внешний клиент", scopes=["tasks:read", "agent:ask"]),
        owner_user_id=owner_id,
    )
    issued = [
        await token_service.issue_token(
            user_id=owner_id, data=ApiTokenCreateSchema(name=f"Клиент {scope}", scope=scope)
        )
        for scope in ("READ", "WRITE")
    ]
    if re.fullmatch(r"telos_key_[0-9a-f]{8}_[0-9a-f]{32}", created.raw_key) is None:
        pytest.fail("Выпущенный ключ должен иметь канонический формат.", pytrace=False)
    assert len(created.key_prefix) == 8
    expected_key_hash = hash_api_key(created.raw_key)
    key_row = (
        (await db_session.execute(select(ApiKey.__table__).where(ApiKey.id == created.id)))
        .mappings()
        .one()
    )
    assert key_row["key_prefix"] == created.key_prefix
    assert key_row["key_hash"] == expected_key_hash and key_row["kind"] == "personal"
    stored_rows = [dict(key_row)]
    for token in issued:
        if re.fullmatch(r"telos_token_[A-Za-z0-9_-]{32,}", token.secret) is None:
            pytest.fail("Выпущенный токен должен иметь канонический формат.", pytrace=False)
        token_row = (
            (
                await db_session.execute(
                    select(ApiToken.__table__).where(ApiToken.id == token.token.id)
                )
            )
            .mappings()
            .one()
        )
        expected_token_hash = hash_token_secret(token.secret)
        assert token_row["token_hash"] == expected_token_hash
        assert token_row["prefix"] == token.token.prefix
        if token.token.prefix != token.secret.removeprefix("telos_token_")[:8]:
            pytest.fail(
                "Публичный префикс токена должен содержать восемь случайных символов.",
                pytrace=False,
            )
        assert len(token_row["prefix"]) == 8
        stored_rows.append(dict(token_row))
    lengths = dict(
        (
            await db_session.execute(
                text(
                    "SELECT table_name, character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND "
                    "((table_name = 'api_keys' AND column_name = 'key_prefix') OR "
                    "(table_name = 'api_tokens' AND column_name = 'prefix'))"
                )
            )
        ).all()
    )
    assert lengths == {"api_keys": 16, "api_tokens": 8}
    stored_text = json.dumps(stored_rows, default=str)
    if any(
        secret in stored_text for secret in [created.raw_key, *[item.secret for item in issued]]
    ):
        pytest.fail("В PostgreSQL должен сохраняться только хеш полного секрета.", pytrace=False)
    key_card = (await ApiKeysService(keys, users).list_personal_keys(owner_id))[0].model_dump()
    token_cards = [
        item.model_dump()
        for item in await ApiTokensService(tokens, max_active_tokens=5).list_tokens(owner_id)
    ]
    assert not {"kind", "key_hash", "raw_key"} & key_card.keys()
    assert all(not {"token_hash", "secret"} & item.keys() for item in token_cards)
    for schema in (PersonalApiKeyCreateSchema, PersonalApiKeyUpdateSchema):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            schema.model_validate({"label": "Клиент", "kind": "service"})

    db_session.expunge_all()
    auth = auth_service(db_session)
    read_key = await auth.resolve_principal(session_token=cookie, bearer_secret=created.raw_key)
    assert read_key.user_id == read_key.api_key.owner_user_id == owner_id
    assert read_key.api_key.key_id == created.id
    assert read_key.api_key.scopes == frozenset({"tasks:read", "agent:ask"})
    assert read_key.via_api_token and not read_key.can_write
    assert read_key.allows("tasks:read") and read_key.allows("agent:ask")
    assert not read_key.allows("projects:read") and not read_key.allows("unknown:read")
    with pytest.raises(ApiKeyScopeError):
        read_key.require("tasks:write")
    for token, can_write in zip(issued, (False, True), strict=True):
        principal = await auth.resolve_principal(session_token=cookie, bearer_secret=token.secret)
        assert principal.user_id == owner_id and principal.api_key is None
        assert principal.via_api_token and principal.can_write is can_write
        assert principal.allows("tasks:read") and principal.allows("projects:read")
        assert principal.allows("tasks:write") is can_write
        assert principal.allows("tasks:delete") is can_write
        assert not principal.allows("unknown:read")
    key_service = ApiKeysService(ApiKeysRepository(db_session), UsersRepository(db_session))
    await key_service.update_personal_key(
        created.id,
        PersonalApiKeyUpdateSchema(scopes=["tasks:write"]),
        owner_user_id=owner_id,
        owner_name="Владелец",
    )
    write_key = await auth_service(db_session).resolve_principal(
        session_token=cookie, bearer_secret=created.raw_key
    )
    assert write_key.can_write and write_key.allows("tasks:write")
    assert not write_key.allows("tasks:read") and not write_key.allows("agent:ask")
    await key_service.update_personal_key(
        created.id,
        PersonalApiKeyUpdateSchema(is_active=False),
        owner_user_id=owner_id,
        owner_name="Владелец",
    )
    for token in issued:
        await token_service.revoke_token(token_id=token.token.id, user_id=owner_id)
    db_session.expunge_all()
    auth = auth_service(db_session)
    with pytest.raises(ApiKeyInvalidError):
        await auth.resolve_principal(session_token=cookie, bearer_secret=created.raw_key)
    for token in issued:
        with pytest.raises(NotAuthenticatedError):
            await auth.resolve_principal(session_token=cookie, bearer_secret=token.secret)
    saved_key = await keys.get_by_id(created.id)
    assert saved_key.revoked_at is not None and saved_key.revoked_by_user_id == owner_id
    assert saved_key.last_used_at is not None
    assert all(item.revoked_at is not None for item in await tokens.get_by_user(owner_id))
    session_principal = await auth.resolve_principal(session_token=cookie, bearer_secret=None)
    assert session_principal.user_id == other_id and not session_principal.via_api_token


async def test_noncanonical_or_wrong_bearer_never_uses_cookie_even_when_its_hash_is_stored(
    db_session, user
):
    owner_id = user.id
    keys = ApiKeysRepository(db_session)
    tokens = ApiTokensRepository(db_session)
    old_key_secret = "ttk_a1b2c3d4_0123456789abcdef0123456789abcdef"
    malformed_key_secret = "telos_key_ABCDEF12_0123456789abcdef0123456789abcdef"
    key_rows = []
    for prefix, secret in (("ttk_a1b2c3d4", old_key_secret), ("ABCDEF12", malformed_key_secret)):
        key_rows.append(
            await keys.create(
                data={
                    "label": "Сохранённый непринимаемый формат",
                    "key_prefix": prefix,
                    "key_hash": hash_api_key(secret),
                    "owner_user_id": owner_id,
                    "kind": "personal",
                    "scopes": ["tasks:write"],
                    "is_active": True,
                }
            )
        )
    old_token_secret = "tt_older-token-with-valid-stored-hash"
    unknown_secret = "external-bearer-with-valid-stored-hash"
    for prefix, secret in (("oldtoken", old_token_secret), ("external", unknown_secret)):
        await tokens.create(
            user_id=owner_id,
            name="Сохранённый непринимаемый формат",
            token_hash=hash_token_secret(secret),
            prefix=prefix,
            scope=ApiTokenScope.WRITE,
            expires_at=None,
        )
    created = await ApiKeysService(keys, UsersRepository(db_session)).create_personal_key(
        PersonalApiKeyCreateSchema(label="Проверка полного секрета", scopes=["tasks:write"]),
        owner_user_id=owner_id,
    )
    cookie = create_access_token(owner_id)
    wrong_key_secret = created.raw_key[:-1] + ("0" if created.raw_key[-1] != "0" else "1")
    db_session.expunge_all()
    auth = auth_service(db_session)
    cookie_principal = await auth.resolve_principal(session_token=cookie, bearer_secret=None)
    assert cookie_principal.user_id == owner_id and cookie_principal.can_write
    for secret in (old_key_secret, old_token_secret, unknown_secret, "", "telos_token_not-issued"):
        with pytest.raises(NotAuthenticatedError):
            await auth.resolve_principal(session_token=cookie, bearer_secret=secret)
    for secret in (malformed_key_secret, wrong_key_secret, "telos_key_76543210_" + "0" * 32):
        with pytest.raises(ApiKeyInvalidError):
            await auth.resolve_principal(session_token=cookie, bearer_secret=secret)
    # Отказ задан форматом учётных данных, а не исчезновением прежних записей.
    for row in key_rows:
        assert (await keys.get_by_id(row.id)).is_active
    assert await keys.get_active_by_prefix("ttk_a1b2c3d4") is not None
    assert await keys.get_active_by_prefix("ABCDEF12") is not None
    assert await tokens.get_active_by_hash(hash_token_secret(old_token_secret)) is not None
    assert await tokens.get_active_by_hash(hash_token_secret(unknown_secret)) is not None
