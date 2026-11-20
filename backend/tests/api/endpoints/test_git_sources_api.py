"""HTTP-only Git контракт; реальные ACL/HMAC/SQL проверяются интеграционным сервисным тестом."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

from src.api.v1.endpoints.git_sources import profile_router, router
from src.dependencies.git_sources import get_git_source_principal, get_git_source_service
from src.exceptions.auth import NotAuthenticatedError
from src.exceptions.git_sources import (
    GitCredentialUnavailableError,
    GitSourceConflictError,
    GitSourceNotAvailableError,
    GitSourceOwnerRequiredError,
    GitSourceRateLimitedError,
    GitSourceSessionRequiredError,
    GitSourceValidationError,
)
from src.schemas.git_sources import (
    GitAccountSnapshot,
    GitConnection,
    GitConnectionPage,
    GitEventAccepted,
    GitHubProfile,
    GitQueued,
    GitRepositoryPage,
    GitSearchResult,
    GitTaskLinks,
)
from src.services.git_sources import GitSourceService


def transport(principal):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.include_router(profile_router, prefix="/api/v1")
    service = AsyncMock(spec=GitSourceService)
    app.dependency_overrides[get_git_source_service] = lambda: service
    app.dependency_overrides[get_git_source_principal] = lambda: principal
    return app, service


def source():
    now = datetime.now(UTC)
    return GitConnection(
        id=1,
        project_id=7,
        repository_id=101,
        owner="example",
        repo="app",
        name="App",
        purpose="",
        ref="heads/main",
        include_paths=[""],
        exclude_patterns=[],
        enabled=True,
        is_private=False,
        share_private_with_project=False,
        has_credential=False,
        revision=1,
        generation=1,
        credential_revision=0,
        state="pending",
        observed_sha="a" * 40,
        indexed_sha=None,
        observed_at=now,
        indexed_at=None,
        checked_at=now,
        last_error_code=None,
        created_at=now,
        updated_at=now,
    )


async def test_git_metadata_read_bounds_config_queue_and_typed_errors(current_principal):
    app, service = transport(current_principal)
    service.list_connections.return_value = GitConnectionPage(
        items=[source()], total=1, offset=0, limit=50, next_offset=None
    )
    service.create_connection.return_value = source()
    service.update_connection.return_value = source()
    service.sync_connection.return_value = source()
    service.get_connection.return_value = source()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = "/api/v1/projects/7/git/connections"
        response = await client.get(base)
        assert response.status_code == 200 and response.json()["items"][0]["repository_id"] == 101
        for query in ("limit=51", "offset=-1", "limit=0"):
            assert (await client.get(base + "?" + query)).status_code == 422
        assert service.list_connections.await_count == 1
        response = await client.post(base, json={"owner": "example", "repo": "app", "name": "App"})
        assert response.status_code == 201 and "credential" not in response.json()
        assert (
            await client.patch(base + "/1", json={"expected_revision": 1, "ref": None})
        ).status_code == 200
        assert service.update_connection.await_args.kwargs["data"].model_fields_set == {
            "expected_revision",
            "ref",
        }
        assert (await client.post(base + "/1/sync")).status_code == 202
        for error, status in (
            (GitSourceConflictError(), 409),
            (GitSourceNotAvailableError(), 404),
            (GitSourceOwnerRequiredError(), 403),
            (GitCredentialUnavailableError(), 503),
        ):
            service.get_connection.side_effect = error
            response = await client.get(base + "/1")
            assert response.status_code == status and response.json() == {"detail": error.detail}
        assert (await client.delete(base + "/1")).status_code == 204


async def test_profile_candidate_check_contract_and_safe_errors(current_principal):
    app, service = transport(current_principal)
    service.check_github_profile.return_value = GitHubProfile(
        account=GitAccountSnapshot(id=42, login="candidate")
    )
    marker = "synthetic-candidate-secret"
    path = "/api/v1/users/me/github/check"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(path, json={"token": marker})
        assert response.status_code == 200
        assert response.json() == {"account": {"id": 42, "login": "candidate"}}
        assert marker not in response.text
        token = service.check_github_profile.await_args.args[1]
        assert token.get_secret_value() == marker and marker not in repr(token)
        for payload in ({}, {"token": {marker: marker}}, {"token": marker, marker: marker}):
            response = await client.post(path, json=payload)
            assert response.status_code == 422 and marker not in response.text
        assert service.check_github_profile.await_count == 1
        for error, status in (
            (GitSourceValidationError(), 422),
            (GitSourceSessionRequiredError(), 403),
            (NotAuthenticatedError(), 401),
            (GitSourceRateLimitedError(120), 429),
        ):
            service.check_github_profile.side_effect = error
            response = await client.post(path, json={"token": marker})
            assert response.status_code == status and response.json() == {"detail": error.detail}
            assert marker not in response.text
            if status == 429:
                assert response.headers["Retry-After"] == "120"
        service.set_github_profile.assert_not_awaited()


async def test_profile_candidate_check_uses_cookie_authentication(current_principal):
    app, service = transport(current_principal)
    del app.dependency_overrides[get_git_source_principal]
    service.resolve_principal.side_effect = NotAuthenticatedError()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/users/me/github/check", json={"token": "candidate"})
        assert response.status_code == 401
        service.check_github_profile.assert_not_awaited()
        service.resolve_principal.side_effect = None
        service.resolve_principal.return_value = current_principal
        service.check_github_profile.return_value = GitHubProfile(
            account=GitAccountSnapshot(id=42, login="candidate")
        )
        client.cookies.set("telos_session", "synthetic-session")
        response = await client.post("/api/v1/users/me/github/check", json={"token": "candidate"})
        assert response.status_code == 200
        service.resolve_principal.assert_awaited_with(
            session_token="synthetic-session", bearer_secret=None
        )


async def test_secret_input_is_never_reflected_in_validation_or_response(current_principal):
    app, service = transport(current_principal)
    marker = "synthetic-secret-input-marker"
    service.set_github_profile.return_value = GitHubProfile(account=None)
    service.github_profile.return_value = GitHubProfile(account=None)
    service.list_github_repositories.return_value = GitRepositoryPage(
        repositories=(), next_page=None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = "/api/v1/projects/7/git/connections"
        for payload in (
            {"owner": "example", "repo": "app", "name": "App", "credential": {marker: marker}},
            {"owner": marker + "/bad", "repo": "app", "name": "App", "credential": marker},
            {"owner": "example", "repo": "app", "name": "App", marker: marker},
        ):
            response = await client.post(base, json=payload)
            assert response.status_code == 422 and marker not in response.text
        service.create_connection.assert_not_awaited()
        profile = "/api/v1/users/me/github"
        assert (await client.get(profile)).json() == {"account": None}
        invalid = await client.put(profile, json={"token": {marker: marker}})
        assert invalid.status_code == 422 and marker not in invalid.text
        response = await client.put(profile, json={"token": marker})
        assert response.status_code == 200 and marker not in response.text
        token = service.set_github_profile.await_args.args[1]
        assert token.get_secret_value() == marker and marker not in repr(token)
        assert (await client.get(profile + "/repositories?page=2")).json() == {
            "repositories": [], "next_page": None,
        }
        service.list_github_repositories.assert_awaited_once_with(current_principal, 2)
        service.list_github_repositories.side_effect = GitSourceRateLimitedError(120)
        limited = await client.get(profile + "/repositories")
        assert limited.status_code == 429 and limited.headers["Retry-After"] == "120"
        assert (await client.delete(profile)).status_code == 204
        service.set_github_profile.assert_awaited_with(current_principal, None)


async def test_search_task_and_evidence_refresh_are_separate_bounded_operations(current_principal):
    app, service = transport(current_principal)
    service.search.return_value = GitSearchResult(
        items=[], partial=True, inaccessible_connection_ids=[2]
    )
    service.get_task_links.return_value = GitTaskLinks(
        project_id=7, task_id=8, items=[], partial=True
    )
    service.refresh_link.return_value = GitQueued()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        base = "/api/v1/projects/7/git"
        response = await client.get(base + "/search?q=module&connection_id=1&limit=30")
        assert response.status_code == 200 and response.json()["partial"]
        assert service.search.await_args.kwargs["connection_ids"] == (1,)
        assert (await client.get(base + "/search?q=module&limit=31")).status_code == 422
        assert (
            await client.get(base + "/connections/1/file?path=README.md&max_chars=8001")
        ).status_code == 422
        assert (await client.get(base + "/connections/1/tree?limit=201")).status_code == 422
        assert (await client.get(base + "/tasks/8/links")).status_code == 200
        response = await client.post(base + "/tasks/8/links/9/refresh")
        assert response.status_code == 202 and response.json() == {"queued": True}
        assert (
            await client.post(
                base + "/tasks/8/links",
                json={"connection_id": 1, "kind": "commit", "commit_sha": "heads/main"},
            )
        ).status_code == 422
        service.link_task.assert_not_awaited()


async def test_event_stream_is_bounded_and_does_not_use_session_auth(current_principal):
    app, service = transport(current_principal)

    def forbidden_auth():
        raise AssertionError("Webhook uses HMAC, not cookie/API key auth")

    app.dependency_overrides[get_git_source_principal] = forbidden_auth
    service.ingest_event.return_value = GitEventAccepted(duplicate=True)
    headers = {
        "X-Hub-Signature-256": "sha256=" + "a" * 64,
        "X-GitHub-Delivery": "delivery-1",
        "X-GitHub-Event": "push",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        url = "/api/v1/projects/7/git/connections/1/events"
        response = await client.post(url, content=b"{}", headers=headers)
        assert response.status_code == 202 and response.json() == {
            "accepted": True,
            "duplicate": True,
        }

        async def chunks():
            yield b"a" * (128 * 1024)
            yield b"b" * (128 * 1024)
            yield b"c"

        assert (await client.post(url, content=chunks(), headers=headers)).status_code == 413
        assert service.ingest_event.await_count == 1
