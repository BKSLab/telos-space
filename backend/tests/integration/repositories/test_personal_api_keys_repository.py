"""Ключ локального владельца: секрет, права, срок и отзыв на PostgreSQL."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.exceptions.api_keys import ApiKeyInvalidError, ApiKeyNotFoundError, ApiKeyScopeError
from src.repositories.api_keys import ApiKeysRepository
from src.repositories.api_tokens import ApiTokensRepository
from src.repositories.users import UsersRepository
from src.schemas.api_keys import PersonalApiKeyCreateSchema, PersonalApiKeyUpdateSchema
from src.services.api_keys import ApiKeysService
from src.services.auth import AuthService
from src.utils.api_keys import key_prefix_of, verify_api_key
from src.utils.tokens import create_access_token


def services(session):
    users = UsersRepository(session)
    keys = ApiKeysRepository(session)
    return (
        ApiKeysService(keys, users),
        AuthService(users, tokens_repository=ApiTokensRepository(session),
            api_keys_repository=keys, invite_code="local-test"),
    )


@pytest.mark.parametrize("scopes", [["tasks:read"], ["tasks:write"]], ids=["read", "write"])
async def test_only_owner_can_list_or_change_key_and_secret_is_returned_once(db_session, user, scopes):
    service, auth = services(db_session)
    for kind in ("personal", "service"):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            PersonalApiKeyCreateSchema(label="Локальный клиент", kind=kind, scopes=scopes)
    created = await service.create_personal_key(
        PersonalApiKeyCreateSchema(label="Локальный клиент", scopes=scopes),
        owner_user_id=user.id,
    )
    stored = await ApiKeysRepository(db_session).get_by_id(created.id)
    parsed_prefix = key_prefix_of(created.raw_key)
    valid_secret = verify_api_key(created.raw_key, stored.key_hash)
    assert parsed_prefix == created.key_prefix == stored.key_prefix and len(parsed_prefix) == 8
    assert valid_secret and stored.kind == "personal" and stored.owner_user_id == user.id
    principal = await auth.resolve_principal(session_token=None, bearer_secret=created.raw_key)
    assert principal.user_id == user.id and principal.api_key.scopes == frozenset(scopes)
    assert principal.allows("tasks:read") is ("tasks:read" in scopes)
    assert principal.allows("tasks:write") is ("tasks:write" in scopes)
    listed = await service.list_personal_keys(user.id)
    assert [key.id for key in listed] == [created.id]
    if created.raw_key in str(listed[0].model_dump()):
        pytest.fail("Список ключей не должен раскрывать полный секрет.", pytrace=False)
    assert not {"key_hash", "raw_key", "kind"} & listed[0].model_dump().keys()
    assert await service.list_personal_keys(user.id + 1000) == []
    with pytest.raises(ApiKeyNotFoundError):
        await service.update_personal_key(created.id,
            PersonalApiKeyUpdateSchema(is_active=False),
            owner_user_id=user.id + 1000, owner_name="Другой пользователь")
    assert (await ApiKeysRepository(db_session).get_by_id(created.id)).is_active


async def test_scope_updates_expiry_and_revocation_apply_to_next_request(db_session, user):
    service, auth = services(db_session)
    created = await service.create_personal_key(
        PersonalApiKeyCreateSchema(label="Сверка", scopes=["tasks:read"]), owner_user_id=user.id)
    read = await auth.resolve_principal(session_token=None, bearer_secret=created.raw_key)
    assert read.user_id == user.id and read.allows("tasks:read")
    with pytest.raises(ApiKeyScopeError):
        read.require("tasks:write")
    await service.update_personal_key(created.id,
        PersonalApiKeyUpdateSchema(scopes=["tasks:write"]),
        owner_user_id=user.id, owner_name="Владелец")
    write = await auth.resolve_principal(session_token=None, bearer_secret=created.raw_key)
    assert write.allows("tasks:write") and not write.allows("tasks:read")
    await service.update_personal_key(created.id,
        PersonalApiKeyUpdateSchema(expires_at=datetime.now(UTC) - timedelta(seconds=1)),
        owner_user_id=user.id, owner_name="Владелец")
    with pytest.raises(ApiKeyInvalidError):
        await auth.resolve_principal(session_token=None, bearer_secret=created.raw_key)
    await service.update_personal_key(created.id,
        PersonalApiKeyUpdateSchema(expires_at=None, is_active=False),
        owner_user_id=user.id, owner_name="Владелец")
    with pytest.raises(ApiKeyInvalidError):
        await auth.resolve_principal(session_token=None, bearer_secret=created.raw_key)
    stored = await ApiKeysRepository(db_session).get_by_id(created.id)
    assert stored.revoked_at is not None and stored.revoked_by_user_id == user.id


async def test_key_owner_and_scope_win_over_cookie_from_another_local_user(db_session, user):
    service, auth = services(db_session)
    other = await UsersRepository(db_session).save(data={"username": "other-key-owner",
        "password_hash": "hash", "last_name": "Другой", "first_name": "Участник", "is_active": True})
    created = await service.create_personal_key(
        PersonalApiKeyCreateSchema(label="Чтение", scopes=["tasks:read"]), owner_user_id=user.id)
    principal = await auth.resolve_principal(
        session_token=create_access_token(other.id), bearer_secret=created.raw_key)
    assert principal.user_id == user.id and principal.api_key.owner_user_id == user.id
    assert principal.via_api_token and not principal.can_write
    with pytest.raises(ApiKeyInvalidError):
        await auth.resolve_principal(session_token=create_access_token(user.id),
            bearer_secret=created.raw_key + "invalid")
