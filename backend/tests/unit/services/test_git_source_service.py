"""Публикация Git-кэша, повторный proof и доказательства текущей выкладки."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.exceptions.git_sources import (
    GitHubForbiddenError,
    GitHubRateLimitError,
    GitSourceConflictError,
    GitSourceNotAvailableError,
)
from src.schemas.git_sources import GitDeploymentStatusPage, GitDeploymentStatusSnapshot
from src.services.access import AccessGrant
from src.services.git_credentials import GitCredentialCodec
from src.services.git_sources import GitSourceService


def connection():
    return {
        "id": 1,
        "project_id": 1,
        "repository_id": 101,
        "owner": "example",
        "repo": "app",
        "name": "app",
        "purpose": "",
        "ref": "heads/main",
        "include_paths": [""],
        "exclude_patterns": [],
        "enabled": True,
        "is_private": False,
        "share_private_with_project": False,
        "credential_ciphertext": None,
        "credential_owner_id": None,
        "credential_revision": 0,
        "revision": 1,
        "generation": 1,
        "observation_revision": 0,
        "state": "ready",
        "observed_sha": "a" * 40,
        "indexed_sha": "a" * 40,
        "coverage": {},
        "scope_hash": "",
        "lease_id": uuid4(),
        "lease_until": None,
        "work_phase": "evidence",
        "last_error_code": None,
    }


def harness(principal, row=None, client=None):
    row = row if row is not None else connection()
    sources = SimpleNamespace(
        get_connection=AsyncMock(side_effect=lambda **_: dict(row)),
        update_connection=AsyncMock(),
        supersede_jobs=AsyncMock(),
        get_snapshot=AsyncMock(return_value={"id": 1}),
        insert_job=AsyncMock(),
        due_changes=AsyncMock(),
        request_evidence_refresh=AsyncMock(),
    )

    async def update_connection(*, data, **_):
        row.update(data)
        return dict(row)

    sources.update_connection.side_effect = update_connection
    db = SimpleNamespace(
        sources=sources,
        auth=SimpleNamespace(refresh_principal=AsyncMock(return_value=principal)),
        access=SimpleNamespace(
            ensure_project_access=AsyncMock(
                return_value=AccessGrant(project_id=1, resource_id=1, is_owner=True)
            )
        ),
        projects=SimpleNamespace(
            get_by_id=AsyncMock(return_value=SimpleNamespace(owner_id=principal.user_id))
        ),
        unit_of_work=SimpleNamespace(commit=AsyncMock()),
        knowledge_events=SimpleNamespace(reindex_project=AsyncMock()),
    )

    @asynccontextmanager
    async def scope():
        yield db

    @asynccontextmanager
    async def client_factory(_):
        assert client is not None
        yield client

    service = GitSourceService(
        scope=scope, client_factory=client_factory, credential_codec=GitCredentialCodec(None)
    )
    row["scope_hash"] = service._scope_hash(row)
    return service, row, db


async def test_confirmed_remote_loss_blocks_inflight_cached_publication(current_principal):
    service, row, _ = harness(current_principal)
    successful_probe = dict(row)
    await service._remote_failure(dict(row), GitHubForbiddenError(upstream_status=403))
    assert row["state"] == "access_lost"
    with pytest.raises((GitSourceNotAvailableError, GitSourceConflictError)):
        await service._current_rows([successful_probe], project_id=1, principal=current_principal)


async def test_newer_success_is_not_overwritten_by_an_older_failed_probe(current_principal):
    service, row, _ = harness(current_principal)
    old_probe = dict(row)
    row["observation_revision"] += 1
    await service._remote_failure(old_probe, GitHubForbiddenError(upstream_status=403))
    assert row["state"] == "ready"


async def test_successful_live_probe_recovers_access_without_reviving_old_proofs(current_principal):
    client = SimpleNamespace(
        max_requests=4,
        get_repository=AsyncMock(return_value=SimpleNamespace(id=101, private=False)),
        resolve_ref=AsyncMock(return_value=SimpleNamespace(sha="a" * 40, tree_sha="b" * 40)),
    )
    service, row, _ = harness(current_principal, client=client)
    previous = dict(row)
    await service._remote_failure(dict(row), GitHubForbiddenError(upstream_status=403))
    async with service._open(project_id=1, connection_id=1, principal=current_principal) as (
        recovered,
        _,
    ):
        assert recovered["state"] == "ready"
    with pytest.raises((GitSourceNotAvailableError, GitSourceConflictError)):
        await service._current_rows([previous], project_id=1, principal=current_principal)


def deployment(provider_id, *, status="success", upstream_at="2026-11-16T10:00:00Z"):
    return {
        "kind": "deployment",
        "provider_type": "deployment",
        "provider_id": str(provider_id),
        "connection_id": 1,
        "repository_id": 101,
        "commit_sha": "a" * 40,
        "status": status,
        "conclusion": None,
        "environment": "production",
        "source_url": f"https://github.com/example/app/deployments/{provider_id}",
        "observed_at": "2026-11-16T11:00:00Z",
        "upstream_at": upstream_at,
        "is_current": False if status == "inactive" else None,
    }


async def run_evidence(principal, state, client=None):
    client = client or SimpleNamespace(requests_made=0, max_requests=4)
    service, row, db = harness(principal)
    state = {
        "generation": row["generation"],
        "credential_revision": row["credential_revision"],
        "checks": [],
        "deploy_evidence": [],
        "unavailable": [],
        "checks_complete": True,
        "statuses_complete": True,
        "deployments_complete": True,
        **state,
    }
    change = {
        "id": 1,
        "revision": 1,
        "kind": "commit",
        "provider_id": "a" * 40,
        "refresh_state": state,
    }
    db.sources.due_changes.return_value = [change]
    saved = []

    async def save(_row, current, evidence_state, **kwargs):
        saved.append((deepcopy(evidence_state), kwargs.get("complete", False)))
        return current

    with (
        patch.object(service, "_save_evidence_state", side_effect=save),
        patch.object(service, "_release", new=AsyncMock()),
    ):
        assert await service._evidence_batch(row, client, principal, row["lease_id"])
    return saved[-1]


async def test_two_successes_do_not_prove_current_deployment_by_timestamp(current_principal):
    state, complete = await run_evidence(
        current_principal,
        {
            "phase": "verify",
            "deploy_evidence": [deployment(1), deployment(2, upstream_at="2026-11-16T10:30:00Z")],
        },
    )
    assert complete
    assert all(item["is_current"] is None for item in state["deploy_evidence"])
    assert "deployment_ambiguous" in state["unavailable"]


async def test_incomplete_status_history_cannot_prove_current_deployment(current_principal):
    async def statuses(*_):
        client.requests_made += 1
        return GitDeploymentStatusPage(
            items=(
                GitDeploymentStatusSnapshot(
                    id=5,
                    state="success",
                    environment="production",
                    created_at=datetime(2026, 11, 16, tzinfo=UTC),
                ),
            ),
            complete=False,
        )

    client = SimpleNamespace(requests_made=0, max_requests=4, list_deployment_statuses=statuses)
    state, complete = await run_evidence(
        current_principal,
        {
            "phase": "deployment_statuses",
            "deployments": [{"id": 1, "sha": "a" * 40, "environment": "production"}],
            "deployment_cursor": 0,
        },
        client,
    )
    assert complete
    assert state["deploy_evidence"][0]["is_current"] is None
    assert "deployment_statuses_incomplete" in state["unavailable"]


async def test_single_active_success_requires_complete_proof_and_inactive_is_preserved(
    current_principal,
):
    state, complete = await run_evidence(
        current_principal,
        {
            "phase": "verify",
            "deploy_evidence": [deployment(1, status="inactive"), deployment(2)],
        },
    )
    assert complete
    assert [item["is_current"] for item in state["deploy_evidence"]] == [False, True]
    state, _ = await run_evidence(
        current_principal,
        {
            "phase": "verify",
            "deployments_complete": False,
            "deploy_evidence": [deployment(2)],
        },
    )
    assert state["deploy_evidence"][0]["is_current"] is None


async def test_worker_deadline_cancels_network_work_and_releases_lease(current_principal):
    service, row, db = harness(current_principal)
    service.limits = replace(service.limits, batch_deadline_seconds=0.01)
    db.sources.claim_connections = AsyncMock(return_value=[dict(row)])
    db.sources.worker_identity = AsyncMock(
        return_value={
            "id": current_principal.user_id,
            "username": current_principal.username,
            "first_name": "Git",
            "last_name": "Test",
            "middle_name": None,
        }
    )
    cancelled = asyncio.Event()

    @asynccontextmanager
    async def blocked_open(**_):
        try:
            await asyncio.Event().wait()
            yield
        finally:
            cancelled.set()

    with (
        patch.object(service, "_open", side_effect=blocked_open),
        patch.object(service, "_release", new=AsyncMock()) as release,
    ):
        assert await asyncio.wait_for(service.tick(), timeout=1) == 1
    assert cancelled.is_set() and release.await_count == 1 and release.await_args.kwargs["soon"]


async def test_foreground_ledgers_are_context_local_and_can_be_reactivated(current_principal):
    service, _, _ = harness(current_principal)
    ready = asyncio.Event()
    entered = 0

    async def operation():
        nonlocal entered
        async with service.read_budget() as budget:
            entered += 1
            if entered == 2:
                ready.set()
            await ready.wait()
            budget.consume()
            async with service.read_budget() as nested:
                assert nested is budget
            return budget

    first, second = await asyncio.gather(operation(), operation())
    assert first is not second and first.requests_made == second.requests_made == 1
    async with service.read_budget(first) as resumed:
        resumed.consume()
    assert first.requests_made == 2 and second.requests_made == 1


async def test_rate_limit_preserves_phase_and_never_issues_another_provider_call(current_principal):
    async def limited(*_):
        client.requests_made += 1
        raise GitHubRateLimitError(upstream_status=429, retry_after=120)

    client = SimpleNamespace(requests_made=0, max_requests=4, list_check_runs=limited)
    state, complete = await run_evidence(
        current_principal, {"phase": "checks", "target_sha": "a" * 40}, client
    )
    assert not complete and state["phase"] == "checks"
    assert state["unavailable"] == ["checks_rate_limited"] and client.requests_made == 1
