"""Настоящий PostgreSQL: Git ACL, FTS, immutable snapshots, restart/CAS и факты SHA."""

import asyncio
import base64
import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.clients.github import GitHubClient, GitRequestBudget
from src.db.models.api_keys import ApiKey
from src.db.models.api_tokens import ApiTokenScope
from src.db.models.git_sources import (
    GitChange,
    GitConnection,
    GitDelivery,
    GitFile,
    GitSyncJob,
)
from src.db.models.knowledge_index_jobs import KnowledgeIndexJob
from src.db.models.project_members import ProjectMember, ProjectRole
from src.db.models.project_stages import ProjectStage
from src.db.models.projects import Project
from src.db.models.tasks import Task
from src.db.models.users import User
from src.dependencies.git_sources import build_git_source_scope
from src.exceptions.git_sources import (
    GitSourceBudgetError,
    GitSourceConflictError,
    GitSourceNotAvailableError,
    GitSourceOwnerRequiredError,
    GitSourceScopeRequiredError,
    GitSourceSessionRequiredError,
    GitSourceValidationError,
)
from src.repositories.git_sources import GitSourcesRepository
from src.schemas.git_sources import (
    GitConnectionCreate,
    GitConnectionUpdate,
    GitSourceProof,
    GitTaskLinkCreate,
    GitWebhookSecretUpdate,
)
from src.services.auth import ApiKeyGrant, Principal
from src.services.git_credentials import GitCredentialCodec
from src.services.git_sources import GitSourceLimits, GitSourceService

A, B, C = (char * 40 for char in "abc")


def blob(content):
    return hashlib.sha1(
        f"blob {len(content)}\0".encode() + content, usedforsecurity=False
    ).hexdigest()


class Remote:
    """Синтетический provider transport; бизнес/SQL слои остаются настоящими."""

    def __init__(self):
        self.sha = A
        self.private = False
        self.denied = set()
        self.rate_limited_blobs = set()
        self.requests = []
        self.batches = []
        self.db_active = 0
        self.versions = {}
        self.contents = {}
        self.pr_head, self.pr_merge = B, None
        self.deployments = []
        self.statuses = {}
        self.check_forbidden = False
        self.add_version(A, {"README.md": b"Original project guidance\n"})

    def add_version(self, sha, files):
        entries = []
        for path, value in files.items():
            content, mode = value if isinstance(value, tuple) else (value, "100644")
            digest = blob(content)
            self.contents[digest] = content
            entries.append(
                {
                    "path": path,
                    "sha": digest,
                    "mode": mode,
                    "type": "commit" if mode == "160000" else "blob",
                    "size": len(content),
                }
            )
        self.versions[sha] = (
            hashlib.sha1(sha.encode(), usedforsecurity=False).hexdigest(),
            entries,
        )

    def response(self, request):
        assert self.db_active == 0, "HTTP must run after closing DB scope"
        assert (
            request.method == "GET"
            and request.url.scheme == "https"
            and request.url.host == "api.github.com"
        )
        assert request.headers["Authorization"].startswith("Bearer ")
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "example"})
        self.requests.append(path)
        parts = path.split("/")
        repo = parts[3]
        if repo in self.denied:
            return httpx.Response(403, json={"message": "untrusted provider detail"})
        tail = "/".join(parts[4:])
        repository_id = {"app": 101, "api": 102, "docs": 103}[repo]
        if not tail:
            data = {
                "id": repository_id,
                "owner": {"login": "example"},
                "name": repo,
                "private": self.private,
                "default_branch": "main",
            }
        elif tail.startswith("git/trees/"):
            tree_sha = tail.split("/")[-1]
            _, entries = next(value for value in self.versions.values() if value[0] == tree_sha)
            data = {"sha": tree_sha, "truncated": False, "tree": entries}
        elif tail.startswith("git/blobs/"):
            digest = tail.split("/")[-1]
            if digest in self.rate_limited_blobs:
                return httpx.Response(429, headers={"Retry-After": "120"}, json={})
            content = self.contents[digest]
            data = {
                "sha": digest,
                "size": len(content),
                "encoding": "base64",
                "content": base64.b64encode(content).decode(),
            }
        elif tail.endswith("/check-runs"):
            if self.check_forbidden:
                return httpx.Response(403, json={"message": "Checks permission revoked"})
            sha = parts[-2]
            data = {
                "total_count": 1,
                "check_runs": [
                    {
                        "id": 500,
                        "name": "tests",
                        "head_sha": sha,
                        "status": "completed",
                        "conclusion": "failure",
                        "completed_at": "2026-11-16T12:00:00Z",
                    }
                ],
            }
        elif tail.endswith("/status"):
            data = {"sha": parts[-2], "total_count": 0, "statuses": []}
        elif tail.startswith("commits/"):
            ref = tail[len("commits/") :]
            sha = ref if ref in self.versions else self.sha
            data = {
                "sha": sha,
                "commit": {"tree": {"sha": self.versions[sha][0]}, "message": "TEL-1 change"},
            }
        elif tail.startswith("pulls/"):
            data = {
                "number": int(parts[-1]),
                "title": "TEL-1 fix",
                "state": "closed" if self.pr_merge else "open",
                "merged": self.pr_merge is not None,
                "merge_commit_sha": self.pr_merge or C,
                "head": {"sha": self.pr_head, "ref": "TEL-1-fix"},
                "base": {"sha": A, "ref": "main", "repo": {"id": repository_id}},
            }
        elif tail == "deployments":
            data = self.deployments
        elif tail.startswith("deployments/") and tail.endswith("/statuses"):
            data = self.statuses[int(parts[-2])]
        elif tail == "pulls":
            data = []
        elif tail == "commits":
            data = []
        else:
            raise AssertionError("Unexpected synthetic GitHub route")
        return httpx.Response(200, json=data)

    @asynccontextmanager
    async def factory(self, credential):
        headers = {"Authorization": "Bearer " + credential.get_secret_value()} if credential else {}
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(self.response),
            trust_env=False,
            follow_redirects=False,
            headers=headers,
        ) as http:
            client = GitHubClient(http_client=http, max_requests=4)
            try:
                yield client
            finally:
                self.batches.append(client.requests_made)


@asynccontextmanager
async def environment(engine, *, limits=None):
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    settings = SimpleNamespace(
        auth=SimpleNamespace(registration_invite_code=SecretStr("unused-test")),
        knowledge=SimpleNamespace(knowledge_enabled=False),
    )
    remote = Remote()
    async with sessions() as db:
        owner = User(
            username="git_owner_" + uuid4().hex,
            password_hash="!",
            first_name="Git",
            last_name="Owner",
        )
        member = User(
            username="git_member_" + uuid4().hex,
            password_hash="!",
            first_name="Git",
            last_name="Member",
        )
        db.add_all([owner, member])
        await db.flush()
        project = Project(
            owner_id=owner.id,
            key="T" + uuid4().hex[:7].upper(),
            name="Git sources",
            color="#334455",
        )
        other = Project(
            owner_id=owner.id,
            key="T" + uuid4().hex[:7].upper(),
            name="Other project",
            color="#334455",
        )
        db.add_all([project, other])
        await db.flush()
        db.add_all(
            [
                ProjectMember(project_id=project.id, user_id=owner.id, role=ProjectRole.OWNER),
                ProjectMember(project_id=project.id, user_id=member.id, role=ProjectRole.MEMBER),
                ProjectMember(project_id=other.id, user_id=owner.id, role=ProjectRole.OWNER),
            ]
        )
        stage = ProjectStage(project_id=project.id, name="Open", color="#334455")
        db.add(stage)
        await db.flush()
        tasks = [
            Task(project_id=project.id, stage_id=stage.id, number=i, title=f"Git task {i}")
            for i in (1, 2)
        ]
        db.add_all(tasks)
        await db.commit()
        owner_id, member_id, project_id, other_id = owner.id, member.id, project.id, other.id
        task_ids, stage_id = [task.id for task in tasks], stage.id
        principal = Principal(
            user_id=owner.id,
            username=owner.username,
            first_name=owner.first_name,
            last_name=owner.last_name,
            middle_name=None,
            scope=ApiTokenScope.WRITE,
            via_api_token=False,
        )
        member_principal = replace(principal, user_id=member.id, username=member.username)
    original_scope = build_git_source_scope(session_factory=sessions, settings=settings)

    @asynccontextmanager
    async def scope():
        remote.db_active += 1
        try:
            async with original_scope() as current:
                yield current
        finally:
            remote.db_active -= 1

    codec = GitCredentialCodec(SecretStr(Fernet.generate_key().decode()))
    service = GitSourceService(
        scope=scope, client_factory=remote.factory, credential_codec=codec, limits=limits
    )
    env = SimpleNamespace(
        sessions=sessions,
        service=service,
        scope=scope,
        remote=remote,
        codec=codec,
        principal=principal,
        member=member_principal,
        project=project_id,
        other_project=other_id,
        tasks=task_ids,
        stage=stage_id,
    )
    try:
        await service.set_github_profile(principal, SecretStr("synthetic-default-credential"))
        yield env
    finally:
        async with sessions() as db:
            await db.execute(delete(Project).where(Project.id.in_([project_id, other_id])))
            await db.execute(delete(ApiKey).where(ApiKey.owner_user_id.in_([owner_id, member_id])))
            await db.execute(delete(User).where(User.id.in_([owner_id, member_id])))
            # Deletion outbox deliberately outlives its project without an FK.
            # Remove only this fixture's jobs after all teardown triggers ran.
            await db.execute(
                delete(KnowledgeIndexJob).where(
                    KnowledgeIndexJob.project_id.in_([project_id, other_id])
                )
            )
            await db.commit()


async def connect(env, repo="app", **kwargs):
    return await env.service.create_connection(
        project_id=env.project,
        principal=env.principal,
        data=GitConnectionCreate(owner="example", repo=repo, name=repo, **kwargs),
    )


async def force_due(env):
    async with env.sessions() as db:
        await db.execute(
            update(GitConnection)
            .where(GitConnection.project_id == env.project)
            .values(next_poll_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()


async def converge(env, predicate, *, ticks=30):
    for _ in range(ticks):
        if await predicate():
            return
        await force_due(env)
        await env.service.tick()
    assert await predicate(), "Durable Git queue did not converge"


async def test_index_restart_three_repositories_search_exact_source_and_secret_exclusions(engine):
    async with environment(engine) as env:
        remote, service = env.remote, env.service
        allowed = {
            "src/main.py": "from src.api import run\nclass App:\n    pass\n# архитектура\n".encode(),
            "src/api.py": b"def run():\n    return 'architecture'\n",
            "README.md": "архитектура проекта\n".encode(),
        }
        remote.add_version(
            A,
            {
                **allowed,
                ".env": b"never read this",
                "safe.py": b"API_KEY = 'synthetic-password-value'\n",
                "external.md": (b"outside", "120000"),
                "module": (b"module", "160000"),
                "pointer.txt": b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\n",
            },
        )
        connections = [await connect(env, repo) for repo in ("app", "api", "docs")]

        async def ready():
            page = await service.list_connections(project_id=env.project, principal=env.principal)
            return all(item.indexed_sha == A for item in page.items)

        # Recreate the service between batches: all progress is in PostgreSQL.
        await service.tick()
        env.service = service = GitSourceService(
            scope=env.scope, client_factory=remote.factory, credential_codec=env.codec
        )
        await converge(env, ready)
        assert max(remote.batches) <= 4
        assert not any(
            path.endswith("/" + blob(b"never read this")) or path.endswith("/" + blob(b"outside"))
            for path in remote.requests
        )
        result = await service.search(
            project_id=env.project, principal=env.principal, query="архитектура"
        )
        assert {item.connection_id for item in result.items} == {item.id for item in connections}
        assert result.partial and all(item.commit_sha == A for item in result.items)
        proof = result.items[0]
        read = await service.read_file(
            project_id=env.project,
            principal=env.member,
            connection_id=proof.connection_id,
            path=proof.path,
            sha=A,
            offset=0,
            max_chars=8,
        )
        original = allowed[proof.path].decode()
        assert (
            read.text == original[:8]
            and read.next_offset == 8
            and read.total_chars == len(original)
        )
        assert read.text_hash == hashlib.sha256(original.encode()).hexdigest()
        await service.validate_sources(
            project_id=env.project,
            principal=env.member,
            sources=[
                GitSourceProof.model_validate(
                    read.model_dump(include=set(GitSourceProof.model_fields))
                )
            ],
        )
        map_result = await service.get_map(project_id=env.project, principal=env.member)
        assert len(map_result.manifest) == 3 and len(map_result.sources) == 9
        assert any(component.kind == "python_module" for component in map_result.components)
        assert all(
            relation["confidence"] in {"declared", "candidate"} for relation in map_result.relations
        )
        async with env.sessions() as db:
            stored = (
                (
                    await db.execute(
                        select(GitFile).where(GitFile.connection_id == connections[0].id)
                    )
                )
                .scalars()
                .all()
            )
            assert {file.path for file in stored if file.state == "ready"} == set(allowed)
            assert all(
                file.text is None and file.text_hash is None
                for file in stored
                if file.state != "ready"
            )
            assert (
                len(
                    (
                        await db.execute(
                            select(GitSyncJob).where(GitSyncJob.connection_id == connections[0].id)
                        )
                    )
                    .scalars()
                    .all()
                )
                == 1
            )


async def test_forcepush_rollback_generation_revocation_and_project_membership(engine):
    async with environment(engine) as env:
        source = await connect(env)
        service, remote = env.service, env.remote
        first = await service.read_file(
            project_id=env.project,
            principal=env.principal,
            connection_id=source.id,
            path="README.md",
        )
        proof = GitSourceProof.model_validate(
            first.model_dump(include=set(GitSourceProof.model_fields))
        )
        remote.add_version(B, {"renamed.md": b"new version"})
        remote.sha = B
        await service.get_tree(
            project_id=env.project, principal=env.principal, connection_id=source.id, sha=B
        )
        middle = await service.get_connection(
            project_id=env.project, principal=env.principal, connection_id=source.id
        )
        remote.sha = A
        await service.get_tree(
            project_id=env.project, principal=env.principal, connection_id=source.id, sha=A
        )
        rollback = await service.get_connection(
            project_id=env.project, principal=env.principal, connection_id=source.id
        )
        assert (
            source.generation < middle.generation < rollback.generation
            and rollback.observed_sha == A
        )
        with pytest.raises(GitSourceConflictError):
            await service.validate_sources(
                project_id=env.project, principal=env.principal, sources=[proof]
            )
        async with service.scope() as db:
            stale = await db.sources.get_connection(project_id=env.project, connection_id=source.id)
        stale["generation"] = middle.generation
        with pytest.raises(GitSourceConflictError):
            await service._checkpoint(
                stale,
                principal=env.principal,
                lease_id=uuid4(),
                job={"id": 1},
                cursor=9,
                byte_count=9,
            )
        remote.denied.add("app")
        with pytest.raises(GitSourceNotAvailableError):
            await service.read_file(
                project_id=env.project,
                principal=env.principal,
                connection_id=source.id,
                path="README.md",
            )
        denied = await service.get_connection(
            project_id=env.project, principal=env.principal, connection_id=source.id
        )
        assert denied.state == "access_lost" and denied.generation == rollback.generation
        assert denied.credential_revision > rollback.credential_revision
        remote.denied.clear()
        recovered = await service.read_file(
            project_id=env.project,
            principal=env.principal,
            connection_id=source.id,
            path="README.md",
        )
        assert recovered.text == first.text and recovered.generation == denied.generation
        count = len(remote.requests)
        with pytest.raises(GitSourceNotAvailableError):
            await service.read_file(
                project_id=env.other_project,
                principal=env.principal,
                connection_id=source.id,
                path="README.md",
            )
        assert len(remote.requests) == count
        async with env.sessions() as db:
            await db.execute(
                delete(ProjectMember).where(
                    ProjectMember.project_id == env.project,
                    ProjectMember.user_id == env.member.user_id,
                )
            )
            await db.commit()
        with pytest.raises(GitSourceNotAvailableError):
            await service.read_file(
                project_id=env.project,
                principal=env.member,
                connection_id=source.id,
                path="README.md",
            )
        assert len(remote.requests) == count


async def test_private_binding_owner_session_current_scopes_and_immediate_revoke(engine):
    async with environment(engine) as env:
        env.remote.private = True
        secret = SecretStr("synthetic-test-credential")
        await env.service.set_github_profile(env.principal, secret)
        source = await connect(env, share_private_with_project=True)
        assert source.has_credential and "synthetic-test-credential" not in source.model_dump_json()
        with pytest.raises(GitSourceOwnerRequiredError):
            await env.service.create_connection(
                project_id=env.project,
                principal=env.member,
                data=GitConnectionCreate(owner="example", repo="api", name="api"),
            )
        api_key = replace(
            env.principal,
            via_api_token=True,
            api_key=ApiKeyGrant(
                key_id=uuid4(),
                label="Git",
                key_prefix="test",
                scopes=frozenset({"projects:read", "projects:write", "knowledge:read"}),
                owner_user_id=env.principal.user_id,
            ),
        )
        async with env.sessions() as db:
            db.add(
                ApiKey(
                    id=api_key.api_key.key_id,
                    label="Git test",
                    key_hash="synthetic-hash",
                    key_prefix=uuid4().hex[:8],
                    owner_user_id=env.principal.user_id,
                    scopes=["projects:read", "projects:write", "knowledge:read", "agent:ask"],
                )
            )
            await db.commit()
        with pytest.raises(GitSourceSessionRequiredError):
            await env.service.set_github_profile(api_key, secret)
        for scopes in ({"projects:read", "agent:ask"}, {"knowledge:read", "agent:ask"}):
            restricted = replace(
                api_key, api_key=replace(api_key.api_key, scopes=frozenset(scopes))
            )
            with pytest.raises(GitSourceScopeRequiredError):
                await env.service.read_file(
                    project_id=env.project,
                    principal=restricted,
                    connection_id=source.id,
                    path="README.md",
                )
        await env.service.read_file(
            project_id=env.project, principal=env.member, connection_id=source.id, path="README.md"
        )
        async with env.sessions() as db:
            stored = await db.get(GitConnection, source.id)
            assert stored.credential_ciphertext is None
            profile = await GitSourcesRepository(db).github_profile(env.principal.user_id)
            ciphertext = profile["github_token_ciphertext"]
            assert ciphertext != secret.get_secret_value()
            assert env.codec.decrypt(ciphertext).get_secret_value() == secret.get_secret_value()
        revoked = await env.service.update_connection(
            project_id=env.project,
            principal=env.principal,
            connection_id=source.id,
            data=GitConnectionUpdate(
                expected_revision=source.revision, share_private_with_project=False
            ),
        )
        count = len(env.remote.requests)
        with pytest.raises(GitSourceNotAvailableError):
            await env.service.read_file(
                project_id=env.project,
                principal=env.member,
                connection_id=source.id,
                path="README.md",
            )
        assert (
            len(env.remote.requests) == count
            and revoked.credential_revision > source.credential_revision
        )


async def test_task_many_to_many_exact_sha_ci_deployment_rollback_and_hmac_dedup(engine):
    async with environment(engine) as env:
        service, remote = env.service, env.remote
        remote.add_version(B, {"README.md": b"head"})
        remote.add_version(C, {"README.md": b"merge"})
        remote.pr_merge = C
        remote.deployments = [
            {"id": 10, "sha": C, "environment": "production", "created_at": "2026-11-16T11:00:00Z"},
            {"id": 11, "sha": A, "environment": "production", "created_at": "2026-11-16T12:00:00Z"},
        ]
        remote.statuses = {
            10: [
                {
                    "id": 100,
                    "state": "inactive",
                    "environment": "production",
                    "created_at": "2026-11-16T12:00:00Z",
                }
            ],
            11: [
                {
                    "id": 101,
                    "state": "success",
                    "environment": "production",
                    "created_at": "2026-11-16T12:01:00Z",
                }
            ],
        }
        source = await connect(env)
        args = dict(
            project_id=env.project,
            principal=env.principal,
            data=GitTaskLinkCreate(connection_id=source.id, kind="pull_request", pull_number=7),
        )
        first = await service.link_task(task_id=env.tasks[0], **args)
        second = await service.link_task(task_id=env.tasks[1], **args)
        assert first.change_id == second.change_id and first.id != second.id
        assert first.commit_sha == C and first.head_sha == B

        async def ready():
            result = await service.get_task_links(
                project_id=env.project, principal=env.principal, task_id=env.tasks[0]
            )
            return bool(result.items) and not result.items[0].evidence_partial

        await converge(env, ready)
        links = await service.get_task_links(
            project_id=env.project, principal=env.principal, task_id=env.tasks[0]
        )
        facts = links.items[0].evidence
        assert next(item for item in facts if item.kind == "check").commit_sha == C
        assert next(item for item in facts if item.kind == "check").conclusion == "failure"
        deployments = [item for item in facts if item.kind == "deployment"]
        assert [(item.commit_sha, item.is_current) for item in deployments] == [
            (C, False),
            (A, True),
        ]
        await service.validate_sources(
            project_id=env.project, principal=env.principal, sources=links.sources
        )
        await service.unlink_task(
            project_id=env.project, principal=env.principal, task_id=env.tasks[0], link_id=first.id
        )
        with pytest.raises(GitSourceConflictError):
            await service.validate_sources(
                project_id=env.project, principal=env.principal, sources=links.sources
            )
        remaining = await service.get_task_links(
            project_id=env.project, principal=env.principal, task_id=env.tasks[1]
        )
        assert remaining.items[0].change_id == second.change_id
        async with env.sessions() as db:
            assert (await db.get(Task, env.tasks[0])).stage_id == env.stage
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(GitChange)
                    .where(GitChange.connection_id == source.id)
                )
                == 1
            )
        webhook_secret = SecretStr("synthetic-webhook-secret")
        await service.set_webhook_secret(
            project_id=env.project,
            principal=env.principal,
            connection_id=source.id,
            data=GitWebhookSecretUpdate(
                expected_revision=source.revision, webhook_secret=webhook_secret
            ),
        )
        rotated = await service.get_task_links(
            project_id=env.project, principal=env.principal, task_id=env.tasks[1]
        )
        assert rotated.items[0].evidence == [] and rotated.items[0].evidence_partial
        assert rotated.items[0].revision > remaining.items[0].revision
        body = json.dumps(
            {"repository": {"id": 101}, "ref": "refs/heads/main", "after": B}
        ).encode()
        signature = (
            "sha256="
            + hmac.new(webhook_secret.get_secret_value().encode(), body, hashlib.sha256).hexdigest()
        )
        event = dict(
            project_id=env.project,
            connection_id=source.id,
            body=body,
            signature=signature,
            delivery_id=str(uuid4()),
            event_type="push",
        )
        assert not (await service.ingest_event(**event)).duplicate
        assert (await service.ingest_event(**event)).duplicate
        with pytest.raises(GitSourceNotAvailableError):
            await service.ingest_event(**{**event, "signature": "sha256=не-ascii"})
        async with env.sessions() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(GitDelivery)
                    .where(GitDelivery.connection_id == source.id)
                )
                == 1
            )
            assert (
                await db.get(GitConnection, source.id)
            ).observed_sha == A  # webhook SHA is not authoritative.


async def test_skip_locked_leases_resume_and_reject_stale_generation(engine):
    async with environment(engine) as env:
        source = await connect(env)
        first_lease, second_lease = uuid4(), uuid4()
        async with env.sessions() as first, env.sessions() as second:
            claimed = await GitSourcesRepository(first).claim_connections(
                lease_id=first_lease, lease_seconds=120, limit=1
            )
            assert [row["id"] for row in claimed] == [source.id]
            assert (
                await GitSourcesRepository(second).claim_connections(
                    lease_id=second_lease, lease_seconds=120, limit=1
                )
                == []
            )
            await first.commit()
            await second.commit()
        await env.service.update_connection(
            project_id=env.project,
            principal=env.principal,
            connection_id=source.id,
            data=GitConnectionUpdate(expected_revision=source.revision, ref="heads/next"),
        )
        with pytest.raises(GitSourceConflictError):
            await env.service._checkpoint(
                claimed[0],
                principal=env.principal,
                lease_id=first_lease,
                job={"id": 1},
                cursor=99,
                byte_count=99,
            )
        async with env.sessions() as db:
            job = await db.scalar(
                select(GitSyncJob).where(
                    GitSyncJob.connection_id == source.id,
                    GitSyncJob.generation == source.generation,
                )
            )
            assert job.cursor == 0 and job.state == "superseded"


async def test_rate_limit_and_profile_reconnect_preserve_index_checkpoint(engine):
    async with environment(engine) as env:
        first, second = b"first file", b"second file"
        env.remote.add_version(A, {"a.md": first, "b.md": second})
        source = await connect(env)
        env.remote.rate_limited_blobs.add(blob(second))
        await env.service.tick()
        await force_due(env)
        await env.service.tick()
        paused = await env.service.get_connection(
            project_id=env.project, connection_id=source.id, principal=env.principal
        )
        assert paused.last_error_code == "rate_limited"
        assert paused.next_poll_at > datetime.now(UTC) + timedelta(seconds=90)
        assert (paused.coverage.indexed_files, paused.coverage.total_files) == (1, 2)
        await env.service.sync_connection(
            project_id=env.project, connection_id=source.id, principal=env.principal
        )
        assert await env.service.tick() == 0

        env.remote.denied.add("app")
        await force_due(env)
        await env.service.tick()
        unavailable = await env.service.get_connection(
            project_id=env.project, connection_id=source.id, principal=env.principal
        )
        assert unavailable.last_error_code == "remote_forbidden"
        assert unavailable.generation == source.generation
        env.remote.denied.clear()
        with pytest.raises(GitSourceValidationError):
            await env.service.set_github_profile(env.principal, SecretStr("токен"))
        await env.service.set_github_profile(env.principal, None)
        assert await env.service.tick() == 0
        with pytest.raises(GitSourceNotAvailableError):
            await env.service.read_file(
                project_id=env.project, connection_id=source.id, principal=env.member, path="a.md"
            )
        async with env.sessions() as db:
            job = await GitSourcesRepository(db).get_job(
                connection_id=source.id, generation=source.generation
            )
            assert job["cursor"] == 1
        env.remote.rate_limited_blobs.clear()
        await env.service.set_github_profile(env.principal, SecretStr("synthetic-replacement"))
        await env.service.tick()
        finished = await env.service.get_connection(
            project_id=env.project, connection_id=source.id, principal=env.principal
        )
        assert finished.indexed_sha == A
        assert finished.generation == source.generation
        assert sum(path.endswith(blob(first)) for path in env.remote.requests) == 1


async def test_new_project_owner_can_rebind_using_their_existing_profile(engine):
    async with environment(engine) as env:
        source = await connect(env)
        await env.service.set_github_profile(env.member, SecretStr("synthetic-new-owner"))
        async with env.sessions() as db:
            await db.execute(
                update(Project).where(Project.id == env.project).values(owner_id=env.member.user_id)
            )
            await db.execute(
                update(ProjectMember)
                .where(ProjectMember.project_id == env.project)
                .values(role=ProjectRole.MEMBER)
            )
            await db.execute(
                update(ProjectMember)
                .where(ProjectMember.project_id == env.project, ProjectMember.user_id == env.member.user_id)
                .values(role=ProjectRole.OWNER)
            )
            await db.commit()
        await env.service.tick()
        before = await env.service.get_connection(
            project_id=env.project, connection_id=source.id, principal=env.member
        )
        assert before.last_error_code == "github_profile_required"
        pending = await env.service.sync_connection(
            project_id=env.project, connection_id=source.id, principal=env.member
        )
        assert pending.last_error_code == "github_profile_pending"
        assert pending.generation == source.generation

        async def indexed():
            current = await env.service.get_connection(
                project_id=env.project, connection_id=source.id, principal=env.member
            )
            return current.indexed_sha == A

        await converge(env, indexed)


async def test_persisted_loss_closes_an_inflight_cached_read_and_recovers_by_new_probe(engine):
    async with environment(engine) as env:
        source = await connect(env)
        await env.service.read_file(
            project_id=env.project,
            principal=env.principal,
            connection_id=source.id,
            path="README.md",
        )
        probed, release = asyncio.Event(), asyncio.Event()
        snapshot = env.service._snapshot

        async def pause(*args, **kwargs):
            probed.set()
            await release.wait()
            return await snapshot(*args, **kwargs)

        with patch.object(env.service, "_snapshot", side_effect=pause):
            first = asyncio.create_task(
                env.service.read_file(
                    project_id=env.project,
                    principal=env.principal,
                    connection_id=source.id,
                    path="README.md",
                )
            )
            await asyncio.wait_for(probed.wait(), timeout=2)
            env.remote.denied.add("app")
            try:
                with pytest.raises(GitSourceNotAvailableError):
                    await env.service.read_file(
                        project_id=env.project,
                        principal=env.principal,
                        connection_id=source.id,
                        path="README.md",
                    )
            finally:
                release.set()
            with pytest.raises((GitSourceNotAvailableError, GitSourceConflictError)):
                await asyncio.wait_for(first, timeout=2)
        env.remote.denied.clear()
        assert (
            await env.service.read_file(
                project_id=env.project,
                principal=env.principal,
                connection_id=source.id,
                path="README.md",
            )
        ).text


async def test_bounded_map_partial_empty_search_and_snapshot_byte_coverage(engine):
    async with environment(engine, limits=GitSourceLimits(max_snapshot_bytes=5)) as env:
        env.remote.add_version(A, {"a.md": b"one!", "b.md": b"longer than limit"})
        source = await connect(env)
        empty = await env.service.search(
            project_id=env.project, principal=env.principal, query="absent"
        )
        assert empty.items == [] and empty.partial

        async def finished():
            async with env.sessions() as db:
                job = await db.scalar(
                    select(GitSyncJob).where(GitSyncJob.connection_id == source.id)
                )
                return job.state == "partial"

        await converge(env, finished)
        state = await env.service.get_connection(
            project_id=env.project, principal=env.principal, connection_id=source.id
        )
        assert (
            state.indexed_sha is None
            and state.coverage.truncated
            and "snapshot_byte_limit" in state.coverage.reasons
        )
        assert (
            await env.service.search(
                project_id=env.project, principal=env.principal, query="absent"
            )
        ).partial
    async with environment(engine) as env:
        # Identical blobs are reused; component and symbol projections remain bounded.
        env.remote.add_version(A, {f"docs/f{i}.md": b"same source" for i in range(105)})
        source = await connect(env)

        async def indexed():
            return (
                await env.service.get_connection(
                    project_id=env.project, principal=env.principal, connection_id=source.id
                )
            ).indexed_sha == A

        await converge(env, indexed)
        result = await env.service.get_map(project_id=env.project, principal=env.principal)
        assert len(result.components) == len(result.sources) == 100
        assert result.total_components == 105 and result.components_truncated and result.partial
        assert sum("/git/blobs/" in path for path in env.remote.requests) == 1


async def test_failed_automatic_ci_refresh_is_partial_without_hiding_link_or_code(engine):
    async with environment(engine) as env:
        source = await connect(env)
        link = await env.service.link_task(
            project_id=env.project,
            principal=env.principal,
            task_id=env.tasks[0],
            data=GitTaskLinkCreate(connection_id=source.id, kind="commit", commit_sha=A),
        )

        async def ready():
            facts = await env.service.get_task_links(
                project_id=env.project, principal=env.principal, task_id=env.tasks[0]
            )
            return not facts.items[0].evidence_partial

        await converge(env, ready)
        before = await env.service.get_task_links(
            project_id=env.project, principal=env.principal, task_id=env.tasks[0]
        )
        env.remote.check_forbidden = True
        async with env.sessions() as db:
            await db.execute(
                update(GitChange)
                .where(GitChange.id == link.change_id)
                .values(next_refresh_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await db.execute(
                update(GitConnection)
                .where(GitConnection.id == source.id)
                .values(work_phase="evidence")
            )
            await db.commit()
        await force_due(env)
        await env.service.tick()
        partial = await env.service.get_task_links(
            project_id=env.project, principal=env.principal, task_id=env.tasks[0]
        )
        assert partial.partial and partial.items[0].evidence_partial
        assert partial.items[0].revision > before.items[0].revision
        assert partial.items[0].last_error_code == "checks_remote_forbidden"
        assert (
            await env.service.read_file(
                project_id=env.project,
                principal=env.principal,
                connection_id=source.id,
                path="README.md",
            )
        ).text


async def test_foreground_global_attempt_budget_is_shared_with_final_proof_without_access_loss(
    engine,
):
    async with environment(engine) as env:
        connections = [await connect(env, repo) for repo in ("app", "api", "docs")]
        read = await env.service.read_file(
            project_id=env.project,
            principal=env.principal,
            connection_id=connections[0].id,
            path="README.md",
        )
        proof = GitSourceProof.model_validate(
            read.model_dump(include=set(GitSourceProof.model_fields))
        )
        ledger = GitRequestBudget(max_requests=4)
        count = len(env.remote.requests)
        async with env.service.read_budget(ledger):
            partial = await env.service.get_map(project_id=env.project, principal=env.principal)
        assert len(env.remote.requests) - count == ledger.requests_made == 4
        assert partial.partial and partial.incomplete_reasons == ["request_budget"]
        async with env.service.read_budget(ledger):
            with pytest.raises(GitSourceBudgetError):
                await env.service.validate_sources(
                    project_id=env.project, principal=env.principal, sources=[proof]
                )
        async with env.sessions() as db:
            rows = (
                (
                    await db.execute(
                        select(GitConnection).where(GitConnection.project_id == env.project)
                    )
                )
                .scalars()
                .all()
            )
            assert all(
                row.state not in {"access_lost", "error"} and row.last_error_code is None
                for row in rows
            )
        await env.service.validate_sources(
            project_id=env.project, principal=env.principal, sources=[proof]
        )


@pytest.mark.parametrize("change", ["disable", "sharing", "policy", "credential_owner"])
async def test_final_source_gate_rechecks_earlier_connections_after_last_remote_probe(engine, change):
    async with environment(engine) as env:
        env.remote.private = True
        connections = {
            repo: await connect(
                env,
                repo,
                share_private_with_project=True,
            )
            for repo in ("app", "api", "docs")
        }

        async def indexed():
            page = await env.service.list_connections(
                project_id=env.project, principal=env.principal
            )
            return len(page.items) == 3 and all(item.indexed_sha == A for item in page.items)

        await converge(env, indexed)
        proofs = []
        for source in connections.values():
            page = await env.service.read_file(
                project_id=env.project,
                principal=env.member,
                connection_id=source.id,
                path="README.md",
            )
            assert page.text == "Original project guidance\n"
            proofs.append(
                GitSourceProof.model_validate(
                    page.model_dump(include=set(GitSourceProof.model_fields))
                )
            )
        async with env.service.read_budget() as budget:
            await env.service.validate_sources(
                project_id=env.project, principal=env.member, sources=proofs
            )
        assert budget.max_requests == 32 and budget.requests_made == 6

        original = env.remote.response
        committed = []

        async def change_earlier_binding(request):
            if request.url.path == f"/repos/example/docs/commits/{connections['docs'].ref}":
                async with env.sessions() as db:
                    if change == "credential_owner":
                        await db.execute(
                            update(Project)
                            .where(Project.id == env.project)
                            .values(owner_id=env.member.user_id)
                        )
                        await db.execute(
                            update(ProjectMember)
                            .where(ProjectMember.project_id == env.project)
                            .values(role=ProjectRole.MEMBER)
                        )
                        await db.execute(
                            update(ProjectMember)
                            .where(
                                ProjectMember.project_id == env.project,
                                ProjectMember.user_id == env.member.user_id,
                            )
                            .values(role=ProjectRole.OWNER)
                        )
                    else:
                        values = {
                            "generation": GitConnection.generation + 1,
                            "revision": GitConnection.revision + 1,
                        }
                        if change == "disable":
                            values.update(enabled=False, state="disabled")
                        elif change == "sharing":
                            values.update(share_private_with_project=False)
                        else:
                            values.update(include_paths=["src"], scope_hash="d" * 64)
                        await db.execute(
                            update(GitConnection)
                            .where(GitConnection.id == connections["app"].id)
                            .values(**values)
                        )
                    await db.commit()
                committed.append(change)
            return original(request)

        count = len(env.remote.requests)
        with patch.object(env.remote, "response", change_earlier_binding):
            async with env.service.read_budget() as budget:
                try:
                    await env.service.validate_sources(
                        project_id=env.project, principal=env.member, sources=proofs
                    )
                except (GitSourceConflictError, GitSourceNotAvailableError):
                    rejected = True
                else:
                    rejected = False
        assert committed == [change]
        assert rejected, "Earlier source was accepted after a committed cohort change"
        assert len(env.remote.requests) - count == budget.requests_made == 6
