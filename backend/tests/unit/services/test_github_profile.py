"""Проверка кандидата GitHub изолирована от сохранённых credentials и очереди."""

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr

from src.exceptions.auth import NotAuthenticatedError
from src.exceptions.git_sources import (
    GitHubAuthenticationError,
    GitHubClientError,
    GitHubRateLimitError,
    GitSourceIncompleteError,
    GitSourceRateLimitedError,
    GitSourceSessionRequiredError,
    GitSourceValidationError,
)
from src.schemas.git_sources import GitAccountSnapshot
from src.services.git_sources import GitSourceService


def profile_service(principal):
    state = SimpleNamespace(db_active=False, received_tokens=[])
    db = SimpleNamespace(
        auth=SimpleNamespace(refresh_principal=AsyncMock(return_value=principal)),
        sources=AsyncMock(),
        knowledge_events=AsyncMock(),
        unit_of_work=AsyncMock(),
    )
    codec = Mock()
    remote = AsyncMock()
    remote.get_authenticated_account.return_value = GitAccountSnapshot(id=42, login="candidate")

    @asynccontextmanager
    async def scope():
        assert not state.db_active
        state.db_active = True
        try:
            yield db
        finally:
            state.db_active = False

    @asynccontextmanager
    async def client_factory(token):
        assert not state.db_active, "GitHub requests must not hold a DB scope"
        state.received_tokens.append(token)
        yield remote

    service = GitSourceService(scope=scope, client_factory=client_factory, credential_codec=codec)
    return service, db, codec, remote, state


def assert_no_profile_mutation(db, codec):
    assert db.sources.mock_calls == []
    assert db.knowledge_events.mock_calls == []
    assert db.unit_of_work.mock_calls == []
    assert codec.mock_calls == []


async def test_candidate_check_returns_only_account_without_touching_saved_profile(current_principal):
    service, db, codec, remote, state = profile_service(current_principal)
    token = SecretStr("synthetic-candidate-secret")
    result = await service.check_github_profile(current_principal, token)
    assert result.model_dump() == {"account": {"id": 42, "login": "candidate"}}
    assert token.get_secret_value() not in result.model_dump_json()
    assert state.received_tokens == [token]
    remote.get_authenticated_account.assert_awaited_once()
    assert db.auth.refresh_principal.await_count == 2
    assert_no_profile_mutation(db, codec)


async def test_candidate_check_requires_current_identity_before_and_after_network(current_principal):
    for revoked_after_network in (False, True):
        service, db, codec, remote, _ = profile_service(current_principal)
        db.auth.refresh_principal.side_effect = (
            [current_principal, NotAuthenticatedError()]
            if revoked_after_network else NotAuthenticatedError()
        )
        with pytest.raises(NotAuthenticatedError):
            await service.check_github_profile(current_principal, SecretStr("candidate"))
        assert remote.get_authenticated_account.await_count == int(revoked_after_network)
        assert_no_profile_mutation(db, codec)

    service, db, codec, remote, _ = profile_service(current_principal)
    with pytest.raises(GitSourceSessionRequiredError):
        await service.check_github_profile(
            replace(current_principal, via_api_token=True), SecretStr("candidate")
        )
    remote.get_authenticated_account.assert_not_awaited()
    assert_no_profile_mutation(db, codec)


async def test_candidate_failures_are_safe_and_never_mutate_profile(current_principal):
    for remote_error, service_error in (
        (GitHubAuthenticationError(), GitSourceValidationError),
        (GitHubRateLimitError(retry_after=120), GitSourceRateLimitedError),
        (GitHubClientError(), GitSourceIncompleteError),
    ):
        service, db, codec, remote, _ = profile_service(current_principal)
        remote.get_authenticated_account.side_effect = remote_error
        with pytest.raises(service_error) as error:
            await service.check_github_profile(current_principal, SecretStr("candidate-secret"))
        assert "candidate-secret" not in str(error.value)
        if isinstance(error.value, GitSourceRateLimitedError):
            assert error.value.retry_after == 120
        assert_no_profile_mutation(db, codec)

    service, db, codec, remote, _ = profile_service(current_principal)
    with pytest.raises(GitSourceValidationError):
        await service.check_github_profile(current_principal, SecretStr("bad\ntoken"))
    remote.get_authenticated_account.assert_not_awaited()
    assert_no_profile_mutation(db, codec)


async def test_save_rechecks_token_and_does_not_trust_previous_check(current_principal):
    service, db, codec, remote, _ = profile_service(current_principal)
    token = SecretStr("candidate-secret")
    await service.check_github_profile(current_principal, token)
    remote.get_authenticated_account.side_effect = GitHubAuthenticationError()
    with pytest.raises(GitSourceValidationError):
        await service.set_github_profile(current_principal, token)
    assert remote.get_authenticated_account.await_count == 2
    assert_no_profile_mutation(db, codec)


async def test_save_does_not_persist_when_identity_is_revoked_after_network(current_principal):
    service, db, codec, remote, _ = profile_service(current_principal)
    db.auth.refresh_principal.side_effect = [current_principal, NotAuthenticatedError()]
    with pytest.raises(NotAuthenticatedError):
        await service.set_github_profile(current_principal, SecretStr("candidate-secret"))
    remote.get_authenticated_account.assert_awaited_once()
    assert_no_profile_mutation(db, codec)
