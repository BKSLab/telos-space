"""Real PostgreSQL: hourly idle polling, durable/coalesced signals and stage opt-in."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import update

from src.clients.github import GitHubClient
from src.db.models.git_sources import GitChange, GitConnection, GitTaskChangeLink
from src.db.models.project_stages import ProjectStage
from src.db.models.tasks import Task
from src.exceptions.git_sources import GitSourceOwnerRequiredError, GitSourceValidationError
from src.repositories.git_sources import GitSourcesRepository
from src.schemas.git_sources import GitConnectionUpdate, GitWebhookSecretUpdate
from tests.integration.repositories.test_git_sources import (
    A,
    connect,
    converge,
    environment,
    force_due,
)


async def row(env, connection_id):
    async with env.sessions() as db:
        return await GitSourcesRepository(db).get_connection(
            project_id=env.project, connection_id=connection_id
        )


async def ready(env, connection_id):
    async def complete():
        item = await row(env, connection_id)
        return (
            item["indexed_sha"] == A
            and item["sync_requested_revision"] == item["sync_completed_revision"]
        )

    await converge(env, complete)


async def configure(env, source, stages, principal=None):
    current = await row(env, source.id)
    return await env.service.update_connection(
        project_id=env.project,
        connection_id=source.id,
        principal=principal or env.principal,
        data=GitConnectionUpdate(
            expected_revision=current["revision"],
            sync_stage_ids=stages,
            name=current["name"],
            purpose=current["purpose"],
            ref=current["ref"],
            share_private_with_project=current["share_private_with_project"],
        ),
    )


@pytest.mark.parametrize(
    "already_indexed, rate_limited", [(True, False), (False, False), (True, True)]
)
async def test_metadata_conflict_retries_pending_work_without_waiting_for_hour(
    engine, already_indexed, rate_limited
):
    async with environment(engine) as env:
        source = await connect(env)
        if already_indexed:
            await ready(env, source.id)
            await env.service.sync_connection(
                project_id=env.project, connection_id=source.id, principal=env.principal
            )
        before = await row(env, source.id)
        # Host and container clocks can differ slightly; make this lease eligible explicitly.
        await force_due(env)
        resolve_ref = GitHubClient.resolve_ref
        until = datetime.now(UTC) + timedelta(minutes=7)

        async def resolve_and_rename(client, owner, repo, ref):
            result = await resolve_ref(client, owner, repo, ref)
            await env.service.update_connection(
                project_id=env.project,
                connection_id=source.id,
                principal=env.principal,
                data=GitConnectionUpdate(expected_revision=before["revision"], name="Renamed"),
            )
            if rate_limited:
                async with env.sessions() as db:
                    await db.execute(
                        update(GitConnection)
                        .where(GitConnection.id == source.id)
                        .values(last_error_code="rate_limited", next_poll_at=until)
                    )
                    await db.commit()
            return result

        with patch.object(GitHubClient, "resolve_ref", resolve_and_rename):
            assert await env.service.tick() == 1
        interrupted = await row(env, source.id)
        assert interrupted["name"] == "Renamed"
        assert interrupted["generation"] == before["generation"]
        assert interrupted["lease_id"] is None
        assert interrupted["sync_requested_revision"] > interrupted["sync_completed_revision"]
        if rate_limited:
            assert interrupted["next_poll_at"] == until
            assert await env.service.tick() == 0
        else:
            assert 0 <= (interrupted["next_poll_at"] - datetime.now(UTC)).total_seconds() <= 2
            await ready(env, source.id)
            assert (await row(env, source.id))["indexed_sha"] == A


async def test_hourly_idle_polling_manual_wakeup_and_metadata_counts(engine):
    async with environment(engine) as env:
        env.remote.add_version(
            A, {"README.md": b"public", ".env": b"never read", "logo.png": b"binary\0"}
        )
        source = await connect(env)
        await ready(env, source.id)
        done = await row(env, source.id)
        assert 3590 <= (done["next_poll_at"] - datetime.now(UTC)).total_seconds() <= 3600
        assert done["coverage"]["metadata_counts"] == {"binary_file": 1}
        attempts = len(env.remote.requests)
        assert await env.service.tick() == 0
        assert len(env.remote.requests) == attempts
        queued = await env.service.sync_connection(
            project_id=env.project, connection_id=source.id, principal=env.principal
        )
        assert queued.sync_pending and queued.last_sync_reason == "manual"
        assert queued.poll_interval_seconds == 3600
        assert queued.next_poll_at <= datetime.now(UTC)
        await ready(env, source.id)
        assert len(env.remote.requests) > attempts


async def test_stage_rule_owner_scope_rollback_repeat_and_linked_repository_selection(engine):
    async with environment(engine) as env:
        first, second = await connect(env), await connect(env, "api")
        async with env.sessions() as db:
            testing = ProjectStage(project_id=env.project, name="Testing", color="#334455")
            foreign = ProjectStage(project_id=env.other_project, name="Foreign", color="#334455")
            db.add_all([testing, foreign])
            await db.commit()
            testing_id, foreign_id = testing.id, foreign.id
        with pytest.raises(GitSourceOwnerRequiredError):
            await configure(env, first, [testing_id], env.member)
        with pytest.raises(GitSourceValidationError):
            await configure(env, first, [foreign_id])
        for source in (first, second):
            configured = await configure(env, source, [testing_id])
            assert configured.generation == source.generation
            assert configured.indexed_sha == source.indexed_sha
        before = await row(env, first.id)
        async with env.sessions() as db:
            await db.execute(
                update(Task).where(Task.id == env.tasks[0]).values(stage_id=testing_id)
            )
            assert (
                await GitSourcesRepository(db).get_connection(
                    project_id=env.project, connection_id=first.id
                )
            )["last_sync_reason"] == "task_stage"
            await db.rollback()
        assert (await row(env, first.id))["sync_requested_revision"] == before[
            "sync_requested_revision"
        ]
        async with env.sessions() as db:
            await db.execute(
                update(ProjectStage).where(ProjectStage.id == testing_id).values(name="Renamed")
            )
            await db.execute(
                update(Task).where(Task.id == env.tasks[0]).values(stage_id=testing_id)
            )
            await db.commit()
        bumped = await row(env, first.id)
        assert bumped["sync_requested_revision"] == before["sync_requested_revision"] + 1
        assert (await row(env, second.id))["last_sync_reason"] == "task_stage"
        async with env.sessions() as db:
            await db.execute(
                update(Task)
                .where(Task.id == env.tasks[0])
                .values(stage_id=testing_id, position=123)
            )
            await db.commit()
        assert (await row(env, first.id))["sync_requested_revision"] == bumped[
            "sync_requested_revision"
        ]
        # Explicit link confines future signals to its connection.
        async with env.sessions() as db:
            change = GitChange(
                connection_id=first.id,
                kind="commit",
                provider_id=A,
                head_sha=A,
                title="Change",
                status="committed",
            )
            db.add(change)
            await db.flush()
            db.add(GitTaskChangeLink(task_id=env.tasks[1], change_id=change.id))
            await db.execute(
                update(Task).where(Task.id == env.tasks[1]).values(stage_id=testing_id)
            )
            await db.commit()
        assert (await row(env, first.id))["sync_requested_revision"] == bumped[
            "sync_requested_revision"
        ] + 1
        assert (await row(env, second.id))["sync_requested_revision"] == bumped[
            "sync_requested_revision"
        ]


async def test_webhook_during_lease_is_not_lost_or_confirmed_by_old_worker_and_respects_backoff(
    engine,
):
    async with environment(engine) as env:
        source = await connect(env)
        await ready(env, source.id)
        current = await row(env, source.id)
        secret = SecretStr("synthetic-stage-trigger-secret")
        await env.service.set_webhook_secret(
            project_id=env.project,
            connection_id=source.id,
            principal=env.principal,
            data=GitWebhookSecretUpdate(
                expected_revision=current["revision"], webhook_secret=secret
            ),
        )

        async def event(kind="push", ref="refs/heads/main", delivery=None):
            body = json.dumps({"repository": {"id": 101}, "ref": ref}).encode()
            return await env.service.ingest_event(
                project_id=env.project,
                connection_id=source.id,
                body=body,
                signature="sha256="
                + hmac.new(secret.get_secret_value().encode(), body, hashlib.sha256).hexdigest(),
                delivery_id=delivery or str(uuid4()),
                event_type=kind,
            )

        await event("ping")
        ping = await row(env, source.id)
        assert ping["last_webhook_event"] == "ping" and ping["last_webhook_received_at"]
        assert ping["last_webhook_processed_at"] is None
        assert ping["sync_requested_revision"] == current["sync_requested_revision"]
        await event(ref="refs/heads/other")
        assert (await row(env, source.id))["sync_requested_revision"] == current[
            "sync_requested_revision"
        ]
        delivery = str(uuid4())
        await event(delivery=delivery)
        assert (await event(delivery=delivery)).duplicate
        lease = uuid4()
        async with env.sessions() as db:
            claimed = (
                await GitSourcesRepository(db).claim_connections(
                    lease_id=lease, lease_seconds=120, limit=1
                )
            )[0]
            await db.commit()
        await event()  # A new accepted event during the old lease.
        await env.service._release(claimed, lease_id=lease, complete=True)
        later = await row(env, source.id)
        assert later["next_poll_at"] <= datetime.now(UTC) + timedelta(seconds=2)
        assert later["last_webhook_processed_at"] is None
        assert later["sync_requested_revision"] > later["sync_completed_revision"]
        await ready(env, source.id)
        processed_at = (await row(env, source.id))["last_webhook_processed_at"]
        assert processed_at is not None
        await force_due(env)
        await env.service.tick()
        assert (await row(env, source.id))["last_webhook_processed_at"] == processed_at
        until = datetime.now(UTC) + timedelta(minutes=7)
        async with env.sessions() as db:
            await db.execute(
                update(GitConnection)
                .where(GitConnection.id == source.id)
                .values(last_error_code="rate_limited", next_poll_at=until)
            )
            await db.commit()
        await event()
        await env.service.sync_connection(
            project_id=env.project, connection_id=source.id, principal=env.principal
        )
        assert (await row(env, source.id))["next_poll_at"] == until
        assert await env.service.tick() == 0
