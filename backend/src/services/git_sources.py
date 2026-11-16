"""Общий доступ к Git, неизменяемые snapshots и восстанавливаемая синхронизация."""

import asyncio
import hashlib
import hmac
import json
import re
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import UUID, uuid4

from pydantic import SecretStr

from src.clients.github import GitHubClient, GitRequestBudget
from src.db.models.api_tokens import ApiTokenScope
from src.exceptions.access import AccessServiceError
from src.exceptions.auth import AuthServiceError
from src.exceptions.base import RepositoryError
from src.exceptions.git_sources import (
    GitCredentialUnavailableError,
    GitHubAuthenticationError,
    GitHubBudgetExceededError,
    GitHubClientError,
    GitHubForbiddenError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitSourceBudgetError,
    GitSourceConflictError,
    GitSourceIncompleteError,
    GitSourceNotAvailableError,
    GitSourceOwnerRequiredError,
    GitSourceScopeRequiredError,
    GitSourceServiceError,
    GitSourceSessionRequiredError,
    GitSourceValidationError,
)
from src.exceptions.knowledge import KnowledgeEventsServiceError
from src.repositories.projects import ProjectsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.git_sources import (
    GitChangeProof,
    GitComponent,
    GitConnection,
    GitConnectionCreate,
    GitConnectionPage,
    GitConnectionUpdate,
    GitCredentialUpdate,
    GitEventAccepted,
    GitEvidence,
    GitFileRead,
    GitProjectMap,
    GitQueued,
    GitSearchHit,
    GitSearchResult,
    GitSourceProof,
    GitTaskCandidate,
    GitTaskCandidates,
    GitTaskLink,
    GitTaskLinkCreate,
    GitTaskLinks,
    GitTreeEntry,
    GitTreePage,
    validate_git_path,
    validate_git_sha,
)
from src.services.access import AccessService
from src.services.auth import AuthService, Principal
from src.services.git_credentials import GitCredentialCodec
from src.services.git_index import (
    analyze_file,
    exclusion_reason,
    has_secret,
    policy_hash,
    prepare_text,
    safe_title,
    text_fragments,
)
from src.services.knowledge_events import KnowledgeEvents

if TYPE_CHECKING:
    from src.repositories.git_sources import GitSourcesRepository


@dataclass(frozen=True, slots=True)
class GitSourceScope:
    """Короткая DB-область; внешний API всегда вызывается после её закрытия."""

    sources: "GitSourcesRepository"
    access: AccessService
    auth: AuthService
    projects: ProjectsRepository
    tasks: TasksRepository
    knowledge_events: KnowledgeEvents
    unit_of_work: UnitOfWork


GitSourceScopeFactory = Callable[[], AbstractAsyncContextManager[GitSourceScope]]
GitHubClientFactory = Callable[[SecretStr | None], AbstractAsyncContextManager[GitHubClient]]
_READ_BUDGET: ContextVar[GitRequestBudget | None] = ContextVar("git_read_budget", default=None)


def _bounded_read(method):
    """Reuse an enclosing ARIS budget or create one bounded foreground operation."""

    @wraps(method)
    async def call(self, *args, **kwargs):
        async with self.read_budget():
            try:
                return await method(self, *args, **kwargs)
            except GitHubBudgetExceededError:
                raise GitSourceBudgetError() from None

    return call


@dataclass(frozen=True, slots=True)
class GitSourceLimits:
    """Явные пределы очереди и чтения, не измерения производительности."""

    poll_interval_seconds: float = 60.0
    batch_files: int = 20
    batch_deadline_seconds: float = 45.0
    lease_seconds: int = 120
    max_connections_per_tick: int = 4
    max_index_files: int = 10_000
    max_snapshot_bytes: int = 20 * 1024 * 1024
    max_file_bytes: int = 1024 * 1024
    max_fragment_chars: int = 8000
    max_http_requests_per_batch: int = 4
    max_foreground_requests: int = 32


class GitSourceService:
    """Единственные Git-сценарии для HTTP, MCP, Ариса и фоновой сверки."""

    def __init__(
        self,
        *,
        scope: GitSourceScopeFactory,
        client_factory: GitHubClientFactory,
        credential_codec: GitCredentialCodec,
        limits: GitSourceLimits | None = None,
    ) -> None:
        @asynccontextmanager
        async def safe_scope():
            try:
                async with scope() as db:
                    yield db
            except (RepositoryError, KnowledgeEventsServiceError):
                raise GitSourceServiceError() from None

        self.scope = safe_scope
        self.client_factory = client_factory
        self.credential_codec = credential_codec
        self.limits = limits or GitSourceLimits()

    @asynccontextmanager
    async def read_budget(
        self, existing_budget: GitRequestBudget | None = None
    ) -> AsyncIterator[GitRequestBudget]:
        """Activate a server-owned ledger; reactivation never replenishes attempts.

        ARIS may retain the yielded object for its queued final publication gate.
        No DTO or model tool can supply or reset this budget.
        """
        budget = existing_budget if existing_budget is not None else _READ_BUDGET.get()
        if budget is None:
            budget = GitRequestBudget(self.limits.max_foreground_requests)
        token = _READ_BUDGET.set(budget)
        try:
            yield budget
        finally:
            _READ_BUDGET.reset(token)

    @asynccontextmanager
    async def _client(self, credential: SecretStr | None) -> AsyncIterator[GitHubClient]:
        async with self.client_factory(credential) as client:
            if (budget := _READ_BUDGET.get()) is not None:
                client.request_budget = budget
            yield client

    @staticmethod
    def _budget_reasons() -> list[str]:
        budget = _READ_BUDGET.get()
        return ["request_budget"] if budget is not None and budget.exhausted else []

    async def resolve_principal(
        self, *, session_token: str | None, bearer_secret: str | None
    ) -> Principal:
        """Аутентифицирует HTTP в короткой DB-области без сохранения raw credentials."""
        async with self.scope() as db:
            return await db.auth.resolve_principal(
                session_token=session_token, bearer_secret=bearer_secret
            )

    async def _authorize(
        self,
        db: GitSourceScope,
        *,
        project_id: int,
        principal: Principal,
        write: bool = False,
        owner: bool = False,
        session: bool = False,
    ):
        try:
            current = await db.auth.refresh_principal(principal)
            grant = await db.access.ensure_project_access(
                project_id=project_id, user_id=current.user_id
            )
        except (AuthServiceError, AccessServiceError):
            raise GitSourceNotAvailableError() from None
        if not current.allows("projects:read") or not current.allows("knowledge:read"):
            raise GitSourceScopeRequiredError()
        if write and not current.allows("projects:write"):
            raise GitSourceOwnerRequiredError()
        project = await db.projects.get_by_id(project_id)
        if project is None:
            raise GitSourceNotAvailableError()
        if owner and (not grant.is_owner or project.owner_id != current.user_id):
            raise GitSourceOwnerRequiredError()
        if session and current.via_api_token:
            raise GitSourceSessionRequiredError()
        return current, project, grant

    @staticmethod
    def _connection_visible(
        row: dict, *, read: bool = True, allow_unverified: bool = False
    ) -> None:
        if read and (
            not row["enabled"]
            or (row["is_private"] and not row["share_private_with_project"])
            or (not allow_unverified and row["state"] in {"access_lost", "error"})
        ):
            raise GitSourceNotAvailableError()

    async def _load(
        self,
        db,
        *,
        project_id,
        connection_id,
        principal,
        write=False,
        owner=False,
        session=False,
        for_update=False,
        read=True,
        allow_unverified=False,
    ):
        current, project, grant = await self._authorize(
            db,
            project_id=project_id,
            principal=principal,
            write=write,
            owner=owner,
            session=session,
        )
        row = await db.sources.get_connection(
            project_id=project_id, connection_id=connection_id, for_update=for_update
        )
        if row is None:
            raise GitSourceNotAvailableError()
        self._connection_visible(row, read=read, allow_unverified=allow_unverified)
        return row, current, project, grant

    def _scope_hash(self, row: dict) -> str:
        base = policy_hash(
            row["include_paths"],
            row["exclude_patterns"],
            max_file_bytes=self.limits.max_file_bytes,
            ref=row["ref"],
        )
        return hashlib.sha256(
            f"{base}:{self.limits.max_snapshot_bytes}:{self.limits.max_index_files}".encode()
        ).hexdigest()

    @staticmethod
    def _dto(row: dict) -> GitConnection:
        fields = {name: row[name] for name in GitConnection.model_fields if name in row}
        return GitConnection(
            **fields,
            has_credential=bool(row.get("credential_ciphertext")),
            has_webhook_secret=bool(row.get("webhook_secret_ciphertext")),
        )

    def _credential(self, row: dict, project_owner_id: int | None) -> SecretStr | None:
        if row.get("credential_ciphertext"):
            if row.get("credential_owner_id") != project_owner_id:
                raise GitSourceNotAvailableError()
            return self.credential_codec.decrypt(row["credential_ciphertext"])
        if row["is_private"]:
            raise GitSourceNotAvailableError()
        return None

    @staticmethod
    def _error_code(error: GitHubClientError) -> str:
        if isinstance(error, GitHubAuthenticationError):
            return "authentication_failed"
        if isinstance(error, GitHubForbiddenError):
            return "remote_forbidden"
        if isinstance(error, GitHubNotFoundError):
            return "repository_or_ref_unavailable"
        if isinstance(error, GitHubRateLimitError):
            return "rate_limited"
        if isinstance(error, GitHubBudgetExceededError):
            return "request_budget"
        return "remote_read_failed"

    async def _remote_failure(self, row: dict, error: GitHubClientError) -> None:
        retry_at = self._retry_at(error)
        async with self.scope() as db:
            current = await db.sources.get_connection(
                project_id=row["project_id"], connection_id=row["id"], for_update=True
            )
            if current is None or any(
                current[name] != row[name]
                for name in (
                    "revision",
                    "credential_revision",
                    "generation",
                    "observation_revision",
                )
            ):
                return
            unavailable = isinstance(
                error, (GitHubAuthenticationError, GitHubForbiddenError, GitHubNotFoundError)
            )
            await db.sources.update_connection(
                project_id=row["project_id"],
                connection_id=row["id"],
                data={
                    "state": "access_lost" if unavailable else "error",
                    "generation": current["generation"] + int(unavailable),
                    "observation_revision": current["observation_revision"] + 1,
                    "last_error_code": self._error_code(error),
                    "next_poll_at": retry_at,
                    "lease_id": None,
                    "lease_until": None,
                },
                expected_revision=current["revision"],
            )
            if unavailable:
                await db.sources.request_evidence_refresh(connection_id=row["id"], invalidate=True)
                await db.knowledge_events.reindex_project(row["project_id"])
            await db.unit_of_work.commit()

    @staticmethod
    def _retry_at(error: GitHubClientError) -> datetime:
        now = datetime.now(UTC)
        delay = max(60, error.retry_after or 0)
        if error.rate_limit_reset is not None:
            delay = max(delay, error.rate_limit_reset - int(now.timestamp()))
        return now + timedelta(seconds=min(delay, 86400))

    async def _observe(
        self, row: dict, commit, metadata, *, principal: Principal, lease_id: UUID | None = None
    ) -> dict:
        async with self.scope() as db:
            current, _, _, _ = await self._load(
                db,
                project_id=row["project_id"],
                connection_id=row["id"],
                principal=principal,
                for_update=True,
                allow_unverified=True,
            )
            if (
                current["revision"] != row["revision"]
                or current["credential_revision"] != row["credential_revision"]
                or current["generation"] != row["generation"]
                or current["observation_revision"] != row["observation_revision"]
                or (lease_id is not None and current["lease_id"] != lease_id)
            ):
                raise GitSourceConflictError()
            if metadata.id != current["repository_id"] or (
                metadata.private and not current["share_private_with_project"]
            ):
                await db.sources.update_connection(
                    project_id=current["project_id"],
                    connection_id=current["id"],
                    expected_revision=current["revision"],
                    data={
                        "state": "access_lost",
                        "last_error_code": "repository_binding_unavailable",
                        "generation": current["generation"] + 1,
                        "observation_revision": current["observation_revision"] + 1,
                        "lease_id": None,
                        "lease_until": None,
                    },
                )
                await db.sources.request_evidence_refresh(
                    connection_id=current["id"], invalidate=True
                )
                await db.unit_of_work.commit()
                raise GitSourceNotAvailableError()
            changed = current["observed_sha"] != commit.sha
            changes = {
                "checked_at": datetime.now(UTC),
                "is_private": metadata.private,
                "last_error_code": None,
                "observation_revision": current["observation_revision"] + 1,
            }
            expected_hash = self._scope_hash(current)
            if changed or expected_hash != current["scope_hash"]:
                changes.update(
                    observed_sha=commit.sha,
                    observed_at=datetime.now(UTC),
                    generation=current["generation"] + 1,
                    state="pending",
                    scope_hash=expected_hash,
                    next_poll_at=datetime.now(UTC),
                )
                if lease_id is None:
                    changes.update(lease_id=None, lease_until=None)
                await db.sources.supersede_jobs(
                    connection_id=current["id"], generation=changes["generation"]
                )
            elif current["state"] in {"error", "access_lost"}:
                changes["state"] = (
                    "pending"
                    if current["indexed_sha"] != commit.sha
                    else ("partial" if current["coverage"].get("reasons") else "ready")
                )
            updated = await db.sources.update_connection(
                project_id=current["project_id"],
                connection_id=current["id"],
                data=changes,
                expected_revision=current["revision"],
                lease_id=lease_id,
            )
            if updated is None:
                raise GitSourceConflictError()
            snapshot = await db.sources.get_snapshot(
                connection_id=current["id"], sha=commit.sha, scope_hash=updated["scope_hash"]
            )
            if snapshot is None:
                snapshot = await db.sources.insert_snapshot(
                    data={
                        "connection_id": current["id"],
                        "commit_sha": commit.sha,
                        "tree_sha": commit.tree_sha,
                        "scope_hash": updated["scope_hash"],
                    }
                )
            await db.sources.insert_job(
                data={
                    "connection_id": current["id"],
                    "snapshot_id": snapshot["id"],
                    "generation": updated["generation"],
                    "credential_revision": updated["credential_revision"],
                }
            )
            if changed:
                await db.knowledge_events.reindex_project(current["project_id"])
            await db.unit_of_work.commit()
            return updated

    @asynccontextmanager
    async def _open(
        self,
        *,
        project_id: int,
        connection_id: int,
        principal: Principal,
        lease_id: UUID | None = None,
    ) -> AsyncIterator[tuple[dict, GitHubClient]]:
        async with self.scope() as db:
            # Only a fresh remote proof may recover a previously failed binding.
            row, current, project, _ = await self._load(
                db,
                project_id=project_id,
                connection_id=connection_id,
                principal=principal,
                for_update=True,
                allow_unverified=True,
            )
            credential_error = None
            try:
                credential = self._credential(row, project.owner_id)
            except (GitCredentialUnavailableError, GitSourceNotAvailableError) as error:
                credential_error = error
            if credential_error is not None:
                await db.sources.update_connection(
                    project_id=project_id,
                    connection_id=connection_id,
                    expected_revision=row["revision"],
                    data={
                        "state": "access_lost",
                        "last_error_code": "credential_binding_unavailable",
                        "generation": row["generation"] + 1,
                        "observation_revision": row["observation_revision"] + 1,
                        "lease_id": None,
                        "lease_until": None,
                    },
                )
                await db.sources.request_evidence_refresh(
                    connection_id=connection_id, invalidate=True
                )
                await db.unit_of_work.commit()
                raise credential_error
        async with self._client(credential) as client:
            client.max_requests = min(
                client.max_requests or self.limits.max_http_requests_per_batch,
                self.limits.max_http_requests_per_batch,
            )
            failure = None
            try:
                metadata = await client.get_repository(row["owner"], row["repo"])
                commit = await client.resolve_ref(row["owner"], row["repo"], row["ref"])
            except GitHubClientError as error:
                failure = error
            if failure is not None:
                if isinstance(failure, GitHubBudgetExceededError):
                    raise GitSourceBudgetError()
                await self._remote_failure(row, failure)
                raise GitSourceNotAvailableError()
            updated = await self._observe(
                row, commit, metadata, principal=current, lease_id=lease_id
            )
            yield updated, client

    async def list_connections(
        self, *, project_id: int, principal: Principal, limit: int = 50, offset: int = 0
    ) -> GitConnectionPage:
        """Возвращает bounded метаданные доступных project connections, без secret fields."""
        if not 1 <= limit <= 50 or offset < 0:
            raise GitSourceValidationError()
        async with self.scope() as db:
            _, _, grant = await self._authorize(db, project_id=project_id, principal=principal)
            rows = await db.sources.list_connections(
                project_id=project_id, is_owner=grant.is_owner, limit=limit, offset=offset
            )
            total = await db.sources.count_connections(
                project_id=project_id, is_owner=grant.is_owner
            )
        return GitConnectionPage(
            items=[self._dto(row) for row in rows],
            total=total,
            offset=offset,
            limit=limit,
            next_offset=offset + len(rows) if offset + len(rows) < total else None,
        )

    async def get_connection(
        self, *, project_id: int, connection_id: int, principal: Principal
    ) -> GitConnection:
        """Метаданные конкретной привязки; disabled не выдаёт её корпус."""
        async with self.scope() as db:
            row, _, _, grant = await self._load(
                db,
                project_id=project_id,
                connection_id=connection_id,
                principal=principal,
                read=False,
            )
            if row["is_private"] and not row["share_private_with_project"] and not grant.is_owner:
                raise GitSourceNotAvailableError()
        return self._dto(row)

    async def create_connection(
        self, *, project_id: int, principal: Principal, data: GitConnectionCreate
    ) -> GitConnection:
        """Владелец создаёт проверенную project binding; private требует явного согласия."""
        async with self.scope() as db:
            current, _, _ = await self._authorize(
                db,
                project_id=project_id,
                principal=principal,
                write=True,
                owner=True,
                session=data.credential is not None,
            )
        cipher = (
            self.credential_codec.encrypt(data.credential) if data.credential is not None else None
        )
        if not data.name.strip() or (
            data.credential is not None and not data.credential.get_secret_value()
        ):
            raise GitSourceValidationError()
        async with self._client(data.credential) as client:
            try:
                metadata = await client.get_repository(data.owner, data.repo)
                if metadata.private and not data.share_private_with_project:
                    raise GitSourceValidationError()
                ref = data.ref or (
                    f"heads/{metadata.default_branch}" if metadata.default_branch else None
                )
                if ref is None:
                    raise GitSourceValidationError()
                commit = await client.resolve_ref(metadata.owner, metadata.name, ref)
            except GitHubClientError:
                raise GitSourceNotAvailableError() from None
        now = datetime.now(UTC)
        values = {
            "project_id": project_id,
            "repository_id": metadata.id,
            "owner": metadata.owner,
            "repo": metadata.name,
            "name": data.name.strip(),
            "purpose": data.purpose,
            "ref": ref,
            "include_paths": data.include_paths,
            "exclude_patterns": data.exclude_patterns,
            "share_private_with_project": data.share_private_with_project,
            "is_private": metadata.private,
            "credential_ciphertext": cipher,
            "credential_owner_id": current.user_id if cipher else None,
            "credential_revision": 1 if cipher else 0,
            "observed_sha": commit.sha,
            "observed_at": now,
            "checked_at": now,
        }
        values["scope_hash"] = self._scope_hash(values)
        async with self.scope() as db:
            await self._authorize(
                db,
                project_id=project_id,
                principal=principal,
                write=True,
                owner=True,
                session=data.credential is not None,
            )
            row = await db.sources.insert_connection(data=values)
            snapshot = await db.sources.insert_snapshot(
                data={
                    "connection_id": row["id"],
                    "commit_sha": commit.sha,
                    "tree_sha": commit.tree_sha,
                    "scope_hash": row["scope_hash"],
                }
            )
            await db.sources.insert_job(
                data={
                    "connection_id": row["id"],
                    "snapshot_id": snapshot["id"],
                    "generation": row["generation"],
                    "credential_revision": row["credential_revision"],
                }
            )
            await db.knowledge_events.reindex_project(project_id)
            await db.unit_of_work.commit()
        return self._dto(row)

    async def update_connection(
        self,
        *,
        project_id: int,
        connection_id: int,
        principal: Principal,
        data: GitConnectionUpdate,
    ) -> GitConnection:
        """Меняет только выбранный binding; generation защищает ref/filter A→B→A."""
        changes = data.model_dump(exclude_unset=True, exclude={"expected_revision"})
        if "ref" in changes and changes["ref"] is None:
            async with self.scope() as db:
                previous, _, project, _ = await self._load(
                    db,
                    project_id=project_id,
                    connection_id=connection_id,
                    principal=principal,
                    write=True,
                    owner=True,
                    read=False,
                )
                credential = self._credential(previous, project.owner_id)
            async with self._client(credential) as client:
                try:
                    metadata = await client.get_repository(previous["owner"], previous["repo"])
                except GitHubClientError:
                    raise GitSourceNotAvailableError() from None
            if metadata.id != previous["repository_id"] or metadata.default_branch is None:
                raise GitSourceNotAvailableError()
            changes["ref"] = f"heads/{metadata.default_branch}"
        async with self.scope() as db:
            row, _, _, _ = await self._load(
                db,
                project_id=project_id,
                connection_id=connection_id,
                principal=principal,
                write=True,
                owner=True,
                for_update=True,
                read=False,
            )
            if row["revision"] != data.expected_revision:
                raise GitSourceConflictError()
            if "ref" in changes and changes["ref"] is None:
                raise GitSourceValidationError()
            combined = {**row, **changes}
            changes.update(
                revision=row["revision"] + 1,
                generation=row["generation"] + 1,
                scope_hash=self._scope_hash(combined),
                state="pending" if combined["enabled"] else "disabled",
                lease_id=None,
                lease_until=None,
                next_poll_at=datetime.now(UTC),
                last_error_code=None,
            )
            if not combined["enabled"] or (
                row["share_private_with_project"] and not combined["share_private_with_project"]
            ):
                changes.update(credential_revision=row["credential_revision"] + 1)
            updated = await db.sources.update_connection(
                project_id=project_id,
                connection_id=connection_id,
                data=changes,
                expected_revision=data.expected_revision,
            )
            await db.sources.supersede_jobs(
                connection_id=connection_id, generation=updated["generation"]
            )
            if updated["credential_revision"] != row["credential_revision"]:
                await db.sources.request_evidence_refresh(
                    connection_id=connection_id, invalidate=True
                )
            await db.knowledge_events.reindex_project(project_id)
            await db.unit_of_work.commit()
        return self._dto(updated)

    async def delete_connection(
        self, *, project_id: int, connection_id: int, principal: Principal
    ) -> None:
        """Отключает единственный источник, уничтожая credential binding, но сохраняя историю."""
        async with self.scope() as db:
            row, _, _, _ = await self._load(
                db,
                project_id=project_id,
                connection_id=connection_id,
                principal=principal,
                write=True,
                owner=True,
                for_update=True,
                read=False,
            )
            await db.sources.update_connection(
                project_id=project_id,
                connection_id=connection_id,
                expected_revision=row["revision"],
                data={
                    "enabled": False,
                    "state": "disabled",
                    "credential_ciphertext": None,
                    "webhook_secret_ciphertext": None,
                    "credential_owner_id": None,
                    "credential_revision": row["credential_revision"] + 1,
                    "revision": row["revision"] + 1,
                    "generation": row["generation"] + 1,
                    "lease_id": None,
                    "lease_until": None,
                },
            )
            await db.sources.supersede_jobs(
                connection_id=connection_id, generation=row["generation"] + 1
            )
            await db.sources.request_evidence_refresh(connection_id=connection_id, invalidate=True)
            await db.knowledge_events.reindex_project(project_id)
            await db.unit_of_work.commit()

    async def set_credential(
        self,
        *,
        project_id: int,
        connection_id: int,
        principal: Principal,
        data: GitCredentialUpdate,
    ) -> GitConnection:
        """Сессия владельца ротирует/отзывает encrypted secret; модель его не получает."""
        if not data.model_fields_set - {"expected_revision"}:
            raise GitSourceValidationError()
        async with self.scope() as db:
            row, current, _, _ = await self._load(
                db,
                project_id=project_id,
                connection_id=connection_id,
                principal=principal,
                write=True,
                owner=True,
                session=True,
                for_update=True,
                read=False,
            )
            if row["revision"] != data.expected_revision:
                raise GitSourceConflictError()
            changes = {
                "revision": row["revision"] + 1,
                "generation": row["generation"] + 1,
                "credential_revision": row["credential_revision"] + 1,
                "next_poll_at": datetime.now(UTC),
                "state": "pending" if row["enabled"] else "disabled",
                "lease_id": None,
                "lease_until": None,
            }
            if "credential" in data.model_fields_set:
                if data.credential is not None and not data.credential.get_secret_value():
                    raise GitSourceValidationError()
                changes["credential_owner_id"] = (
                    current.user_id if data.credential is not None else None
                )
            for name in ("credential", "webhook_secret"):
                if name in data.model_fields_set:
                    secret = getattr(data, name)
                    changes[name + "_ciphertext"] = (
                        self.credential_codec.encrypt(secret) if secret is not None else None
                    )
            updated = await db.sources.update_connection(
                project_id=project_id,
                connection_id=connection_id,
                expected_revision=data.expected_revision,
                data=changes,
            )
            await db.sources.supersede_jobs(
                connection_id=connection_id, generation=updated["generation"]
            )
            await db.sources.request_evidence_refresh(connection_id=connection_id, invalidate=True)
            await db.knowledge_events.reindex_project(project_id)
            await db.unit_of_work.commit()
        return self._dto(updated)

    async def sync_connection(
        self, *, project_id: int, connection_id: int, principal: Principal
    ) -> GitConnection:
        """Ставит текущую connection на authoritative reconciliation без синхронного обхода."""
        async with self.scope() as db:
            row, _, _, _ = await self._load(
                db,
                project_id=project_id,
                connection_id=connection_id,
                principal=principal,
                write=True,
                owner=True,
                for_update=True,
                read=False,
            )
            if not row["enabled"]:
                raise GitSourceNotAvailableError()
            updated = await db.sources.update_connection(
                project_id=project_id,
                connection_id=connection_id,
                data={"next_poll_at": datetime.now(UTC)},
            )
            await db.unit_of_work.commit()
        return self._dto(updated)

    def _allowed(self, row: dict, entry: dict) -> str | None:
        return exclusion_reason(
            entry["path"],
            row["include_paths"],
            row["exclude_patterns"],
            mode=entry.get("mode", "100644"),
            size=entry.get("size") or 0,
            max_file_bytes=self.limits.max_file_bytes,
        )

    async def _snapshot(self, row: dict, *, sha: str | None = None) -> dict:
        selected = sha or row["indexed_sha"] or row["observed_sha"]
        if selected is None:
            raise GitSourceIncompleteError()
        try:
            selected = validate_git_sha(selected)
        except ValueError:
            raise GitSourceValidationError() from None
        async with self.scope() as db:
            snapshot = await db.sources.get_snapshot(
                connection_id=row["id"], sha=selected, scope_hash=row["scope_hash"]
            )
            if snapshot is None and sha is None and row["observed_sha"] != selected:
                snapshot = await db.sources.get_snapshot(
                    connection_id=row["id"], sha=row["observed_sha"], scope_hash=row["scope_hash"]
                )
        if snapshot is None:
            raise GitSourceNotAvailableError()
        return snapshot

    async def _tree(
        self, row: dict, snapshot: dict, client: GitHubClient, principal: Principal
    ) -> dict:
        if snapshot["tree_loaded"]:
            return snapshot
        try:
            tree = await client.get_tree(row["owner"], row["repo"], snapshot["tree_sha"])
        except GitHubClientError as error:
            if isinstance(error, GitHubBudgetExceededError):
                raise error
            await self._remote_failure(row, error)
            raise GitSourceIncompleteError() from None
        entries = [entry.model_dump() for entry in tree.entries if entry.type != "tree"]
        async with self.scope() as db:
            current, _, _, _ = await self._load(
                db,
                project_id=row["project_id"],
                connection_id=row["id"],
                principal=principal,
                for_update=True,
            )
            if (
                current["generation"] != row["generation"]
                or current["credential_revision"] != row["credential_revision"]
            ):
                raise GitSourceConflictError()
            updated = await db.sources.update_snapshot(
                snapshot_id=snapshot["id"],
                data={
                    "manifest": entries[: self.limits.max_index_files],
                    "tree_loaded": True,
                    "tree_truncated": tree.truncated or len(entries) > self.limits.max_index_files,
                },
            )
            await db.unit_of_work.commit()
        return updated or snapshot

    @_bounded_read
    async def get_tree(
        self,
        *,
        project_id: int,
        connection_id: int,
        principal: Principal,
        sha: str | None = None,
        prefix: str = "",
        offset: int = 0,
        limit: int = 200,
    ) -> GitTreePage:
        """Читает только разрешённое дерево recorded snapshot, явно сохраняя неполноту."""
        if not 1 <= limit <= 200 or offset < 0:
            raise GitSourceValidationError()
        if prefix:
            try:
                validate_git_path(prefix)
            except ValueError:
                raise GitSourceValidationError() from None
        async with self._open(
            project_id=project_id, connection_id=connection_id, principal=principal
        ) as (row, client):
            snapshot = await self._tree(row, await self._snapshot(row, sha=sha), client, principal)
            allowed = [
                entry
                for entry in snapshot["manifest"]
                if not self._allowed(row, entry)
                and (
                    not prefix or entry["path"] == prefix or entry["path"].startswith(prefix + "/")
                )
            ]
            entries = allowed[offset : offset + limit]
            await self._current_rows([row], project_id=project_id, principal=principal)
            return GitTreePage(
                connection_id=connection_id,
                repository_id=row["repository_id"],
                commit_sha=snapshot["commit_sha"],
                items=[GitTreeEntry.model_validate(entry) for entry in entries],
                offset=offset,
                limit=limit,
                next_offset=offset + len(entries) if offset + len(entries) < len(allowed) else None,
                truncated=snapshot["tree_truncated"],
                stale=snapshot["commit_sha"] != row["observed_sha"],
                excluded_files=len(snapshot["manifest"]) - len(allowed),
            )

    @staticmethod
    def _proof(row: dict, file: dict) -> GitSourceProof:
        return GitSourceProof(
            source_id=f"git_file:{file['id']}",
            entity_id=file["id"],
            file_id=file["id"],
            project_id=row["project_id"],
            connection_id=row["id"],
            repository_id=row["repository_id"],
            ref=row["ref"],
            commit_sha=file["commit_sha"],
            path=file["path"],
            blob_sha=file["blob_sha"],
            text_hash=file["text_hash"],
            generation=row["generation"],
            credential_revision=row["credential_revision"],
        )

    @staticmethod
    def _file_url(row: dict, file: dict) -> str:
        return f"https://github.com/{row['owner']}/{row['repo']}/blob/{file['commit_sha']}/{quote(file['path'], safe='/')}"

    async def _store_file(
        self,
        row: dict,
        snapshot: dict,
        entry: dict,
        client: GitHubClient,
        principal: Principal,
        *,
        forced_reason: str | None = None,
    ) -> dict:
        reason = forced_reason or self._allowed(row, entry)
        text, analysis = None, {}
        async with self.scope() as db:
            ready = await db.sources.get_snapshot_file(
                snapshot_id=snapshot["id"], path=entry["path"]
            )
            reusable = (
                await db.sources.reusable_file(
                    connection_id=row["id"], blob_sha=entry["sha"], scope_hash=row["scope_hash"]
                )
                if ready is None and reason is None
                else None
            )
        if ready is not None:
            return {**ready, "commit_sha": snapshot["commit_sha"]}
        if reason is None and reusable is not None:
            text, reason = reusable["text"], reusable["reason"]
            if text is not None:
                analysis = analyze_file(entry["path"], text)
        elif reason is None:
            try:
                blob = await client.read_blob(row["owner"], row["repo"], entry["sha"])
            except (GitHubAuthenticationError, GitHubForbiddenError, GitHubNotFoundError) as error:
                await self._remote_failure(row, error)
                raise
            if blob.size != entry["size"]:
                raise GitSourceConflictError()
            text, reason = prepare_text(blob.content)
            if text is not None:
                analysis = analyze_file(entry["path"], text)
        values = {
            "snapshot_id": snapshot["id"],
            "connection_id": row["id"],
            "path": entry["path"],
            "blob_sha": entry["sha"],
            "size": entry.get("size") or 0,
            "state": "excluded" if reason else "ready",
            "reason": reason,
            "text": text,
            "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()
            if text is not None
            else None,
            "analysis": analysis,
        }
        async with self.scope() as db:
            current, _, _, _ = await self._load(
                db,
                project_id=row["project_id"],
                connection_id=row["id"],
                principal=principal,
                for_update=True,
            )
            if (
                current["generation"] != row["generation"]
                or current["credential_revision"] != row["credential_revision"]
            ):
                raise GitSourceConflictError()
            coverage = await db.sources.snapshot_coverage(snapshot_id=snapshot["id"])
            if (
                text is not None
                and coverage["bytes_processed"] + values["size"] > self.limits.max_snapshot_bytes
            ):
                text = None
                values.update(
                    state="excluded",
                    reason="snapshot_byte_limit",
                    text=None,
                    text_hash=None,
                    analysis={},
                )
            file = await db.sources.insert_file(data=values)
            if file is None:
                file = await db.sources.get_snapshot_file(
                    snapshot_id=snapshot["id"], path=entry["path"]
                )
            elif text is not None:
                await db.sources.insert_fragments(
                    file_id=file["id"],
                    fragments=text_fragments(text, max_chars=self.limits.max_fragment_chars),
                )
            await db.unit_of_work.commit()
        return {**file, "commit_sha": snapshot["commit_sha"]}

    @_bounded_read
    async def read_file(
        self,
        *,
        project_id: int,
        principal: Principal,
        connection_id: int,
        sha: str | None = None,
        path: str,
        offset: int = 0,
        max_chars: int = 8000,
    ) -> GitFileRead:
        """Проверяет current ACL/remote access и читает bounded страницу точного Git source."""
        if offset < 0 or not 1 <= max_chars <= self.limits.max_fragment_chars:
            raise GitSourceValidationError()
        try:
            validate_git_path(path)
        except ValueError:
            raise GitSourceValidationError() from None
        async with self._open(
            project_id=project_id, connection_id=connection_id, principal=principal
        ) as (row, client):
            snapshot = await self._snapshot(row, sha=sha)
            async with self.scope() as db:
                file = await db.sources.get_file(
                    project_id=project_id,
                    connection_id=connection_id,
                    sha=snapshot["commit_sha"],
                    path=path,
                )
            if file is None:
                snapshot = await self._tree(row, snapshot, client, principal)
                entry = next((item for item in snapshot["manifest"] if item["path"] == path), None)
                if entry is None:
                    if snapshot["tree_truncated"]:
                        raise GitSourceIncompleteError()
                    raise GitSourceNotAvailableError()
                try:
                    file = await self._store_file(row, snapshot, entry, client, principal)
                except GitHubClientError:
                    raise GitSourceIncompleteError() from None
            if file["state"] != "ready" or file["text"] is None:
                raise GitSourceNotAvailableError()
            if offset > len(file["text"]):
                raise GitSourceValidationError()
            async with self.scope() as db:
                current, _, _, _ = await self._load(
                    db, project_id=project_id, connection_id=connection_id, principal=principal
                )
                if (
                    current["generation"] != row["generation"]
                    or current["credential_revision"] != row["credential_revision"]
                ):
                    raise GitSourceConflictError()
            text = file["text"][offset : offset + max_chars]
            proof = self._proof(row, file)
            return GitFileRead(
                **proof.model_dump(),
                title=path,
                source_url=self._file_url(row, file),
                text=text,
                offset=offset,
                next_offset=offset + len(text) if offset + len(text) < len(file["text"]) else None,
                total_chars=len(file["text"]),
                line_start=file["text"][:offset].count("\n") + 1,
                line_end=file["text"][: offset + len(text)].count("\n") + 1,
                stale=snapshot["commit_sha"] != row["observed_sha"],
                partial=not snapshot["complete"]
                or snapshot["tree_truncated"]
                or bool(snapshot["coverage"].get("excluded_files")),
            )

    async def _readable_connections(
        self, *, project_id: int, principal: Principal, connection_ids: tuple[int, ...] = ()
    ) -> tuple[list[dict], list[int], bool]:
        if len(connection_ids) > 10:
            raise GitSourceValidationError()
        async with self.scope() as db:
            _, _, grant = await self._authorize(db, project_id=project_id, principal=principal)
            if connection_ids:
                rows = []
                for identifier in dict.fromkeys(connection_ids):
                    row = await db.sources.get_connection(
                        project_id=project_id, connection_id=identifier
                    )
                    if row is None:
                        raise GitSourceNotAvailableError()
                    rows.append(row)
                partial = False
            else:
                rows = await db.sources.list_connections(
                    project_id=project_id, is_owner=grant.is_owner, limit=10
                )
                partial = await db.sources.count_connections(
                    project_id=project_id, is_owner=grant.is_owner
                ) > len(rows)
        readable, inaccessible = [], []
        for row in rows:
            if not row["enabled"]:
                inaccessible.append(row["id"])
                continue
            try:
                async with self._open(
                    project_id=project_id, connection_id=row["id"], principal=principal
                ) as (current, _):
                    readable.append(current)
            except (
                GitSourceNotAvailableError,
                GitSourceIncompleteError,
                GitCredentialUnavailableError,
            ):
                inaccessible.append(row["id"])
        return readable, inaccessible, partial

    async def _current_rows(
        self, rows: list[dict], *, project_id: int, principal: Principal
    ) -> None:
        async with self.scope() as db:
            await self._authorize(db, project_id=project_id, principal=principal)
            for row in rows:
                current, _, _, _ = await self._load(
                    db, project_id=project_id, connection_id=row["id"], principal=principal
                )
                if (
                    current["generation"] != row["generation"]
                    or current["credential_revision"] != row["credential_revision"]
                ):
                    raise GitSourceConflictError()

    @_bounded_read
    async def search(
        self,
        *,
        project_id: int,
        principal: Principal,
        query: str,
        connection_ids: tuple[int, ...] = (),
        limit: int = 10,
    ) -> GitSearchResult:
        """FTS даёт кандидатов; текст возвращается лишь после live remote proof/current ACL."""
        if not query.strip() or len(query) > 2000 or not 1 <= limit <= 30:
            raise GitSourceValidationError()
        rows, inaccessible, capped = await self._readable_connections(
            project_id=project_id, principal=principal, connection_ids=connection_ids
        )
        by_id = {row["id"]: row for row in rows}
        async with self.scope() as db:
            await self._authorize(db, project_id=project_id, principal=principal)
            hits = (
                await db.sources.search(
                    project_id=project_id, connection_ids=tuple(by_id), query=query, limit=limit
                )
                if rows
                else []
            )
        await self._current_rows(rows, project_id=project_id, principal=principal)
        items = []
        for hit in hits:
            row = by_id[hit["connection_id"]]
            items.append(
                GitSearchHit(
                    **self._proof(row, hit).model_dump(),
                    title=hit["path"],
                    source_url=self._file_url(row, hit),
                    excerpt=hit["excerpt"][:2000],
                    offset=hit["fragment_offset"],
                    line_start=hit["line_start"],
                    line_end=hit["line_start"] + hit["excerpt"][:2000].count("\n"),
                    stale=hit["commit_sha"] != row["observed_sha"],
                    partial=not hit["snapshot_complete"]
                    or hit["tree_truncated"]
                    or bool(row["coverage"].get("excluded_files")),
                )
            )
        return GitSearchResult(
            items=items,
            partial=capped
            or bool(inaccessible)
            or any(
                row["indexed_sha"] != row["observed_sha"] or row["state"] != "ready" for row in rows
            )
            or any(item.partial or item.stale for item in items),
            inaccessible_connection_ids=inaccessible,
            incomplete_reasons=self._budget_reasons(),
        )

    @_bounded_read
    async def get_map(self, *, project_id: int, principal: Principal) -> GitProjectMap:
        """Возвращает bounded статическую карту с manifest и server-only proofs каждого файла."""
        rows, inaccessible, capped = await self._readable_connections(
            project_id=project_id, principal=principal
        )
        by_id = {row["id"]: row for row in rows}
        async with self.scope() as db:
            await self._authorize(db, project_id=project_id, principal=principal)
            files = (
                await db.sources.map_files(
                    project_id=project_id, connection_ids=tuple(by_id), limit=100
                )
                if rows
                else []
            )
            total = (
                await db.sources.count_map_files(project_id=project_id, connection_ids=tuple(by_id))
                if rows
                else 0
            )
        await self._current_rows(rows, project_id=project_id, principal=principal)
        components = [
            GitComponent(
                connection_id=file["connection_id"],
                repository_id=file["repository_id"],
                commit_sha=file["commit_sha"],
                file_id=file["id"],
                path=file["path"],
                kind=file["analysis"].get("kind", "text"),
                symbols=file["analysis"].get("symbols", [])[:20],
                imports=file["analysis"].get("imports", [])[:20],
            )
            for file in files
        ]
        relations = []
        for component in components:
            for imported in component.imports:
                name = imported["name"]
                suffix = name.replace(".", "/").lstrip("/")
                candidates = [
                    other
                    for other in components
                    if other.file_id != component.file_id
                    and suffix
                    and (
                        other.path.rsplit(".", 1)[0].endswith("/" + suffix)
                        or other.path.rsplit(".", 1)[0] == suffix
                    )
                ]
                target = candidates[0] if len(candidates) == 1 else None
                relations.append(
                    {
                        "kind": "declared_import",
                        "source_file_id": component.file_id,
                        "target_file_id": target.file_id if target else None,
                        "source_connection_id": component.connection_id,
                        "target_connection_id": target.connection_id if target else None,
                        "import_name": name,
                        "line": imported["line"],
                        "confidence": "candidate"
                        if target or imported.get("confidence") == "candidate"
                        else "declared",
                    }
                )
        partial = (
            capped
            or bool(inaccessible)
            or total > len(files)
            or len(relations) > 500
            or any(
                file["analysis"].get("partial")
                or len(file["analysis"].get("symbols", [])) > 20
                or len(file["analysis"].get("imports", [])) > 20
                for file in files
            )
            or any(
                row["indexed_sha"] != row["observed_sha"] or row["state"] != "ready" for row in rows
            )
        )
        manifest = (await self.list_connections(project_id=project_id, principal=principal)).items
        return GitProjectMap(
            project_id=project_id,
            manifest=manifest,
            components=components,
            relations=relations[:500],
            sources=[self._proof(by_id[file["connection_id"]], file) for file in files],
            total_components=total,
            components_truncated=total > len(files),
            partial=partial,
            incomplete_reasons=self._budget_reasons(),
        )

    @_bounded_read
    async def compare(
        self,
        *,
        project_id: int,
        connection_id: int,
        principal: Principal,
        base_sha: str,
        head_sha: str,
    ):
        """Сравнивает только recorded SHA данной connection/scope, не произвольную сеть forks."""
        async with self._open(
            project_id=project_id, connection_id=connection_id, principal=principal
        ) as (row, client):
            await self._snapshot(row, sha=base_sha)
            await self._snapshot(row, sha=head_sha)
            try:
                result = await client.compare_commits(row["owner"], row["repo"], base_sha, head_sha)
            except GitHubClientError:
                raise GitSourceIncompleteError() from None
            await self._current_rows([row], project_id=project_id, principal=principal)
            return result

    @staticmethod
    def _evidence_hash(evidence: list[dict]) -> str:
        stable = [
            {key: value for key, value in item.items() if key != "observed_at"} for item in evidence
        ]
        return hashlib.sha256(
            json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    @_bounded_read
    async def validate_sources(
        self,
        *,
        project_id: int,
        principal: Principal,
        sources: Sequence[GitSourceProof | GitChangeProof],
    ) -> None:
        """Повторно проверяет все прочитанные источники, включая не выбранные моделью handles."""
        if len(sources) > 200 or any(source.project_id != project_id for source in sources):
            raise GitSourceNotAvailableError()
        grouped = {}
        for source in sources:
            grouped.setdefault(source.connection_id, []).append(source)
        for connection_id, proofs in grouped.items():
            async with self._open(
                project_id=project_id, connection_id=connection_id, principal=principal
            ) as (row, _):
                if any(
                    proof.repository_id != row["repository_id"]
                    or proof.generation != row["generation"]
                    or proof.credential_revision != row["credential_revision"]
                    for proof in proofs
                ):
                    raise GitSourceConflictError()
                file_proofs = [proof for proof in proofs if isinstance(proof, GitSourceProof)]
                async with self.scope() as db:
                    actor, _, _ = await self._authorize(
                        db, project_id=project_id, principal=principal
                    )
                    files = (
                        await db.sources.files_by_ids(
                            project_id=project_id,
                            connection_id=connection_id,
                            file_ids={proof.file_id for proof in file_proofs},
                        )
                        if file_proofs
                        else []
                    )
                    found = {file["id"]: file for file in files}
                    for proof in file_proofs:
                        file = found.get(proof.file_id)
                        if (
                            file is None
                            or file["state"] != "ready"
                            or proof.source_id != f"git_file:{file['id']}"
                            or proof.entity_id != file["id"]
                            or proof.ref != row["ref"]
                            or any(
                                getattr(proof, name) != file[name]
                                for name in ("path", "blob_sha", "text_hash", "commit_sha")
                            )
                            or hashlib.sha256(file["text"].encode("utf-8")).hexdigest()
                            != proof.text_hash
                        ):
                            raise GitSourceConflictError()
                    change_proofs = [item for item in proofs if isinstance(item, GitChangeProof)]
                    if change_proofs and not actor.allows("tasks:read"):
                        raise GitSourceScopeRequiredError()
                    links = (
                        {
                            item["id"]: item
                            for item in await db.sources.links_by_ids(
                                project_id=project_id,
                                link_ids={item.link_id for item in change_proofs},
                            )
                        }
                        if change_proofs
                        else {}
                    )
                    for proof in change_proofs:
                        link = links.get(proof.link_id)
                        change = await db.sources.get_change(
                            connection_id=connection_id, change_id=proof.change_id
                        )
                        if (
                            link is None
                            or link["task_id"] != proof.task_id
                            or link["change_id"] != proof.change_id
                            or change is None
                            or change["revision"] != proof.revision
                            or (change["merge_sha"] or change["head_sha"]) != proof.commit_sha
                            or self._evidence_hash(change["evidence"]) != proof.evidence_hash
                        ):
                            raise GitSourceConflictError()
                await self._current_rows([row], project_id=project_id, principal=principal)

    async def _task(
        self, db, *, project_id: int, task_id: int, principal: Principal, write: bool = False
    ):
        actor, _, _ = await self._authorize(db, project_id=project_id, principal=principal)
        if not actor.allows("tasks:write" if write else "tasks:read"):
            raise GitSourceScopeRequiredError()
        task = (
            await db.tasks.get_for_update(task_id=task_id)
            if write
            else await db.tasks.get_by_id(task_id)
        )
        if task is None or task.project_id != project_id:
            raise GitSourceNotAvailableError()
        return actor, task

    @staticmethod
    def _change_url(row: dict, change: dict) -> str:
        suffix = (
            f"pull/{change['provider_id']}"
            if change["kind"] == "pull_request"
            else f"commit/{change['head_sha']}"
        )
        return f"https://github.com/{row['owner']}/{row['repo']}/{suffix}"

    def _link_dto(self, row: dict, change: dict, *, link_id: int, task_id: int) -> GitTaskLink:
        return GitTaskLink(
            id=link_id,
            task_id=task_id,
            change_id=change["id"],
            connection_id=row["id"],
            repository_id=row["repository_id"],
            kind=change["kind"],
            title=change["title"],
            source_url=self._change_url(row, change),
            commit_sha=change["merge_sha"] or change["head_sha"],
            head_sha=change["head_sha"],
            merge_sha=change["merge_sha"],
            pull_number=int(change["provider_id"]) if change["kind"] == "pull_request" else None,
            status=change["status"],
            revision=change["revision"],
            checked_at=change["checked_at"],
            evidence=[GitEvidence.model_validate(item) for item in change["evidence"]],
            evidence_partial=change["evidence_partial"],
            last_error_code=change["last_error_code"],
        )

    def _change_proof(
        self, row: dict, change: dict, *, link_id: int, task_id: int
    ) -> GitChangeProof:
        return GitChangeProof(
            project_id=row["project_id"],
            connection_id=row["id"],
            change_id=change["id"],
            link_id=link_id,
            task_id=task_id,
            repository_id=row["repository_id"],
            generation=row["generation"],
            credential_revision=row["credential_revision"],
            revision=change["revision"],
            commit_sha=change["merge_sha"] or change["head_sha"],
            evidence_hash=self._evidence_hash(change["evidence"]),
        )

    @_bounded_read
    async def get_task_links(
        self, *, project_id: int, principal: Principal, task_id: int
    ) -> GitTaskLinks:
        """Читает N:N связи/факты без смешения repository, SHA и environment."""
        async with self.scope() as db:
            await self._task(db, project_id=project_id, task_id=task_id, principal=principal)
            links = await db.sources.list_task_links(project_id=project_id, task_id=task_id)
        identifiers = tuple(dict.fromkeys(link["connection_id"] for link in links))
        rows, inaccessible, capped = (
            await self._readable_connections(
                project_id=project_id, principal=principal, connection_ids=identifiers[:10]
            )
            if identifiers
            else ([], [], False)
        )
        by_id = {row["id"]: row for row in rows}
        await self._current_rows(rows, project_id=project_id, principal=principal)
        async with self.scope() as db:
            await self._task(db, project_id=project_id, task_id=task_id, principal=principal)
            current_links = await db.sources.list_task_links(project_id=project_id, task_id=task_id)
        items, proofs = [], []
        for link in current_links[:100]:
            row = by_id.get(link["connection_id"])
            if row is not None:
                items.append(self._link_dto(row, link, link_id=link["link_id"], task_id=task_id))
                proofs.append(
                    self._change_proof(row, link, link_id=link["link_id"], task_id=task_id)
                )
        await self._current_rows(rows, project_id=project_id, principal=principal)
        return GitTaskLinks(
            project_id=project_id,
            task_id=task_id,
            items=items,
            sources=proofs,
            partial=capped
            or len(identifiers) > 10
            or len(current_links) > 100
            or bool(inaccessible)
            or any(item.evidence_partial for item in items),
            incomplete_reasons=self._budget_reasons(),
        )

    async def _change_identity(
        self, row: dict, client: GitHubClient, *, kind: str, provider_id: str
    ) -> dict:
        if kind == "pull_request":
            pr = await client.get_pull_request(row["owner"], row["repo"], int(provider_id))
            if pr.base_repository_id != row["repository_id"]:
                raise GitSourceNotAvailableError()
            return {
                "kind": kind,
                "provider_id": provider_id,
                "title": safe_title(pr.title),
                "head_sha": pr.head_sha,
                "merge_sha": pr.merge_sha,
                "status": "merged" if pr.merged else pr.state,
            }
        commit = await client.get_commit_details(row["owner"], row["repo"], provider_id)
        return {
            "kind": "commit",
            "provider_id": provider_id,
            "title": safe_title(commit.message.splitlines()[0] if commit.message else "Commit"),
            "head_sha": commit.sha,
            "merge_sha": None,
            "status": "committed",
        }

    async def link_task(
        self, *, project_id: int, principal: Principal, task_id: int, data: GitTaskLinkCreate
    ) -> GitTaskLink:
        """Явно связывает задачу и проверенную immutable PR/commit identity; стадии не меняет."""
        async with self.scope() as db:
            await self._task(
                db, project_id=project_id, task_id=task_id, principal=principal, write=True
            )
        async with self._open(
            project_id=project_id, connection_id=data.connection_id, principal=principal
        ) as (row, client):
            try:
                values = await self._change_identity(
                    row,
                    client,
                    kind=data.kind,
                    provider_id=str(data.pull_number)
                    if data.kind == "pull_request"
                    else data.commit_sha,
                )
            except GitHubClientError:
                raise GitSourceNotAvailableError() from None
            async with self.scope() as db:
                actor, _ = await self._task(
                    db, project_id=project_id, task_id=task_id, principal=principal, write=True
                )
                current, _, _, _ = await self._load(
                    db,
                    project_id=project_id,
                    connection_id=row["id"],
                    principal=principal,
                    for_update=True,
                )
                if (
                    current["generation"] != row["generation"]
                    or current["credential_revision"] != row["credential_revision"]
                ):
                    raise GitSourceConflictError()
                change = await db.sources.find_change(
                    connection_id=row["id"], kind=data.kind, provider_id=values["provider_id"]
                )
                if change is None:
                    change = await db.sources.insert_change(
                        data={"connection_id": row["id"], **values, "checked_at": datetime.now(UTC)}
                    )
                link = await db.sources.insert_task_link(
                    task_id=task_id, change_id=change["id"], user_id=actor.user_id
                )
                if link is None:
                    previous = await db.sources.list_task_links(
                        project_id=project_id, task_id=task_id
                    )
                    link = next(
                        {"id": item["link_id"]} for item in previous if item["id"] == change["id"]
                    )
                await db.sources.update_connection(
                    project_id=project_id,
                    connection_id=row["id"],
                    data={"next_poll_at": datetime.now(UTC), "work_phase": "evidence"},
                )
                await db.knowledge_events.reindex_project(project_id)
                await db.unit_of_work.commit()
            return self._link_dto(row, change, link_id=link["id"], task_id=task_id)

    async def unlink_task(
        self, *, project_id: int, principal: Principal, task_id: int, link_id: int
    ) -> None:
        """Удаляет одну task relationship, сохраняя остальные ссылки и GitChange."""
        async with self.scope() as db:
            await self._task(
                db, project_id=project_id, task_id=task_id, principal=principal, write=True
            )
            if not await db.sources.delete_task_link(
                project_id=project_id, task_id=task_id, link_id=link_id
            ):
                raise GitSourceNotAvailableError()
            await db.knowledge_events.reindex_project(project_id)
            await db.unit_of_work.commit()

    async def refresh_link(
        self, *, project_id: int, principal: Principal, task_id: int, link_id: int
    ) -> GitQueued:
        """Ставит bounded фактическое обновление; queued не является успешным CI/deploy."""
        async with self.scope() as db:
            await self._task(
                db, project_id=project_id, task_id=task_id, principal=principal, write=True
            )
            links = await db.sources.list_task_links(project_id=project_id, task_id=task_id)
            link = next((item for item in links if item["link_id"] == link_id), None)
            if link is None:
                raise GitSourceNotAvailableError()
            row, _, _, _ = await self._load(
                db,
                project_id=project_id,
                connection_id=link["connection_id"],
                principal=principal,
                for_update=True,
            )
            await db.sources.update_change(
                connection_id=row["id"],
                change_id=link["id"],
                data={"next_refresh_at": datetime.now(UTC), "evidence_partial": True},
            )
            await db.sources.update_connection(
                project_id=project_id,
                connection_id=row["id"],
                data={"next_poll_at": datetime.now(UTC), "work_phase": "evidence"},
            )
            await db.unit_of_work.commit()
        return GitQueued()

    @_bounded_read
    async def get_task_candidates(
        self, *, project_id: int, principal: Principal, task_id: int
    ) -> GitTaskCandidates:
        """Предлагает точные task-key matches, не превращая неоднозначность в autolink."""
        async with self.scope() as db:
            await self._task(db, project_id=project_id, task_id=task_id, principal=principal)
            task = await db.sources.get_task_key(project_id=project_id, task_id=task_id)
            _, _, grant = await self._authorize(db, project_id=project_id, principal=principal)
            connections = await db.sources.list_connections(
                project_id=project_id, is_owner=grant.is_owner, limit=10
            )
        key = f"{task['key']}-{task['number']}"
        items, partial = [], False
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(key)}(?![A-Za-z0-9])", re.IGNORECASE)
        for connection in connections:
            candidates = []
            try:
                async with self._open(
                    project_id=project_id, connection_id=connection["id"], principal=principal
                ) as (row, client):
                    for kind in ("pull_request", "commit"):
                        page = await client.list_change_candidates(
                            row["owner"], row["repo"], sha=row["observed_sha"], kind=kind
                        )
                        partial |= not page.complete
                        for candidate in page.items:
                            content = candidate.title + " " + (candidate.branch or "")
                            if not pattern.search(content) or has_secret(content):
                                continue
                            keys = {
                                match.upper()
                                for match in re.findall(
                                    r"\b[A-Za-z][A-Za-z0-9]{1,9}-[0-9]+\b", content
                                )
                            }
                            candidates.append(
                                GitTaskCandidate(
                                    connection_id=row["id"],
                                    repository_id=row["repository_id"],
                                    kind=kind,
                                    commit_sha=candidate.sha,
                                    pull_number=candidate.pull_number,
                                    title=safe_title(candidate.title),
                                    source_url=f"https://github.com/{row['owner']}/{row['repo']}/"
                                    + (
                                        f"pull/{candidate.pull_number}"
                                        if kind == "pull_request"
                                        else f"commit/{candidate.sha}"
                                    ),
                                    ambiguous=keys != {key.upper()},
                                )
                            )
                    await self._current_rows([row], project_id=project_id, principal=principal)
                    items.extend(candidates)
            except (
                GitSourceNotAvailableError,
                GitSourceIncompleteError,
                GitHubClientError,
                GitCredentialUnavailableError,
            ):
                partial = True
        async with self.scope() as db:
            await self._task(db, project_id=project_id, task_id=task_id, principal=principal)
        return GitTaskCandidates(
            items=items[:50],
            partial=partial or len(items) > 50,
            incomplete_reasons=self._budget_reasons(),
        )

    async def ingest_event(
        self,
        *,
        project_id: int,
        connection_id: int,
        body: bytes,
        signature: str,
        delivery_id: str,
        event_type: str,
    ) -> GitEventAccepted:
        """Проверяет HMAC/repository binding и дедуплицирует событие, не доверяя его SHA."""
        if (
            len(body) > 256 * 1024
            or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", delivery_id)
            or not re.fullmatch(r"[a-z_]{1,40}", event_type)
        ):
            raise GitSourceValidationError()
        if not re.fullmatch(r"sha256=[0-9a-f]{64}", signature):
            raise GitSourceNotAvailableError()
        async with self.scope() as db:
            row = await db.sources.get_connection(
                project_id=project_id, connection_id=connection_id
            )
        if row is None or not row["enabled"] or not row["webhook_secret_ciphertext"]:
            raise GitSourceNotAvailableError()
        secret = self.credential_codec.decrypt(row["webhook_secret_ciphertext"])
        expected = (
            "sha256="
            + hmac.new(secret.get_secret_value().encode("utf-8"), body, hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(expected, signature):
            raise GitSourceNotAvailableError()
        payload = None
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, RecursionError):
            pass
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("repository"), dict)
            or type(payload["repository"].get("id")) is not int
            or payload["repository"]["id"] != row["repository_id"]
        ):
            raise GitSourceNotAvailableError()
        digest = hashlib.sha256(body).hexdigest()
        async with self.scope() as db:
            current = await db.sources.get_connection(
                project_id=project_id, connection_id=connection_id, for_update=True
            )
            if (
                current is None
                or not current["enabled"]
                or current["revision"] != row["revision"]
                or current["credential_revision"] != row["credential_revision"]
            ):
                raise GitSourceConflictError()
            previous = await db.sources.get_delivery(
                connection_id=connection_id, delivery_id=delivery_id
            )
            if previous is not None:
                if previous["payload_hash"] != digest:
                    raise GitSourceConflictError()
                return GitEventAccepted(duplicate=True)
            await db.sources.insert_delivery(
                data={
                    "connection_id": connection_id,
                    "delivery_id": delivery_id,
                    "payload_hash": digest,
                    "event_type": event_type,
                }
            )
            accepted = event_type in {
                "push",
                "pull_request",
                "check_run",
                "check_suite",
                "status",
                "deployment",
                "deployment_status",
            }
            if event_type == "push":
                accepted = payload.get("ref") == (
                    "refs/" + row["ref"]
                    if row["ref"].startswith(("heads/", "tags/"))
                    else row["ref"]
                )
            if accepted:
                await db.sources.update_connection(
                    project_id=project_id,
                    connection_id=connection_id,
                    data={"next_poll_at": datetime.now(UTC)},
                )
                await db.sources.request_evidence_refresh(connection_id=connection_id)
            await db.unit_of_work.commit()
        return GitEventAccepted()

    def _check_evidence(self, row: dict, check) -> dict | None:
        if (
            has_secret(check.name)
            or has_secret(check.status)
            or (check.conclusion and has_secret(check.conclusion))
        ):
            return None
        return GitEvidence(
            kind="check",
            provider_type=check.provider_type,
            provider_id=str(check.id),
            connection_id=row["id"],
            repository_id=row["repository_id"],
            commit_sha=check.sha,
            status=check.status,
            conclusion=check.conclusion,
            source_url=f"https://github.com/{row['owner']}/{row['repo']}/commit/{check.sha}/checks",
            observed_at=datetime.now(UTC),
            upstream_at=check.updated_at,
        ).model_dump(mode="json")

    @staticmethod
    def _current_deployments(state: dict) -> None:
        """Timestamps do not prove replacement of an active deployment.

        A current success is established only by an exhaustive provider list
        and complete statuses, with exactly one active success per environment.
        """
        evidence = state.get("deploy_evidence", [])
        for item in evidence:
            item["is_current"] = False if item["status"] == "inactive" else None
        if not state.get("deployments_complete") or any(
            code.startswith("deployment") for code in state["unavailable"]
        ):
            return
        successes = {}
        for item in evidence:
            if item["status"] == "success":
                successes.setdefault(item["environment"], []).append(item)
        for active in successes.values():
            if len(active) == 1:
                active[0]["is_current"] = True
            elif "deployment_ambiguous" not in state["unavailable"]:
                state["unavailable"].append("deployment_ambiguous")

    async def _save_evidence_state(
        self,
        row: dict,
        change: dict,
        state: dict,
        *,
        principal: Principal,
        lease_id: UUID,
        complete: bool = False,
    ) -> dict:
        identity = state.get("identity", {})
        async with self.scope() as db:
            current, _, _, _ = await self._load(
                db,
                project_id=row["project_id"],
                connection_id=row["id"],
                principal=principal,
                for_update=True,
            )
            if (
                current["lease_id"] != lease_id
                or current["generation"] != row["generation"]
                or current["credential_revision"] != row["credential_revision"]
            ):
                raise GitSourceConflictError()
            latest = await db.sources.get_change(connection_id=row["id"], change_id=change["id"])
            if latest is None or latest["revision"] != change["revision"]:
                raise GitSourceConflictError()
            changed_identity = bool(identity) and any(
                latest[name] != identity[name]
                for name in ("head_sha", "merge_sha", "status", "title")
            )
            data = {
                "refresh_state": {} if complete else state,
                "next_refresh_at": datetime.now(UTC)
                + timedelta(seconds=self.limits.poll_interval_seconds if complete else 0),
            }
            semantic_change = changed_identity
            if identity:
                data.update(identity)
            if changed_identity:
                data.update(evidence=[], evidence_partial=True, last_error_code=None)
            if not complete and state.get("unavailable"):
                state["unavailable"] = list(dict.fromkeys(state["unavailable"]))[:64]
                error_code = state["unavailable"][0]
                semantic_change |= (
                    not latest["evidence_partial"] or latest["last_error_code"] != error_code
                )
                data.update(evidence_partial=True, last_error_code=error_code)
            if complete:
                evidence = state.get("checks", []) + state.get("deploy_evidence", [])
                partial = (
                    bool(state.get("unavailable"))
                    or not state.get("checks_complete", False)
                    or not state.get("statuses_complete", False)
                    or not state.get("deployments_complete", False)
                )
                semantic_change |= (
                    self._evidence_hash(latest["evidence"]) != self._evidence_hash(evidence)
                    or latest["evidence_partial"] != partial
                )
                data.update(
                    evidence=evidence,
                    evidence_partial=partial,
                    checked_at=datetime.now(UTC),
                    last_error_code=state.get("unavailable", [None])[0]
                    if state.get("unavailable")
                    else None,
                )
            if semantic_change:
                data["revision"] = latest["revision"] + 1
                await db.knowledge_events.reindex_project(row["project_id"])
            updated = await db.sources.update_change(
                connection_id=row["id"],
                change_id=change["id"],
                expected_revision=latest["revision"],
                data=data,
            )
            if updated is None:
                raise GitSourceConflictError()
            await db.unit_of_work.commit()
            return updated

    async def _evidence_batch(
        self, row: dict, client: GitHubClient, principal: Principal, lease_id: UUID
    ) -> bool:
        async with self.scope() as db:
            due = await db.sources.due_changes(connection_id=row["id"], limit=1)
        if not due:
            return False
        change = due[0]
        state = dict(change["refresh_state"])
        if (
            state.get("generation") != row["generation"]
            or state.get("credential_revision") != row["credential_revision"]
        ):
            state = {
                "phase": "identity",
                "generation": row["generation"],
                "credential_revision": row["credential_revision"],
                "checks": [],
                "deploy_evidence": [],
                "unavailable": [],
            }
        complete = False
        rate_error = None
        while client.requests_made < (
            client.max_requests or self.limits.max_http_requests_per_batch
        ):
            phase = state["phase"]
            try:
                if phase == "identity":
                    identity = await self._change_identity(
                        row, client, kind=change["kind"], provider_id=change["provider_id"]
                    )
                    state.update(
                        identity=identity,
                        target_sha=identity["merge_sha"] or identity["head_sha"],
                        phase="checks",
                    )
                elif phase == "checks":
                    checks = await client.list_check_runs(
                        row["owner"], row["repo"], state["target_sha"]
                    )
                    for check in checks.items:
                        item = self._check_evidence(row, check)
                        if item is None:
                            state["unavailable"].append("check_secret_metadata")
                        else:
                            state["checks"].append(item)
                    state.update(checks_complete=checks.complete, phase="statuses")
                elif phase == "statuses":
                    statuses = await client.get_commit_statuses(
                        row["owner"], row["repo"], state["target_sha"]
                    )
                    for check in statuses.items:
                        item = self._check_evidence(row, check)
                        if item is None:
                            state["unavailable"].append("status_secret_metadata")
                        else:
                            state["checks"].append(item)
                    state.update(statuses_complete=statuses.complete, phase="deployments")
                elif phase == "deployments":
                    deployments = await client.list_deployments(row["owner"], row["repo"], limit=10)
                    state.update(
                        deployments=[item.model_dump(mode="json") for item in deployments.items],
                        deployments_complete=deployments.complete,
                        deployment_cursor=0,
                        phase="deployment_statuses",
                    )
                elif phase == "deployment_statuses":
                    cursor = state.get("deployment_cursor", 0)
                    deployments = state.get("deployments", [])
                    if cursor >= len(deployments):
                        state["phase"] = "verify"
                        continue
                    deployment = deployments[cursor]
                    statuses = await client.list_deployment_statuses(
                        row["owner"], row["repo"], deployment["id"]
                    )
                    if not statuses.complete:
                        state["unavailable"].append("deployment_statuses_incomplete")
                    if statuses.items:
                        latest = max(
                            statuses.items, key=lambda status: (status.created_at, status.id)
                        )
                        environment = latest.environment or deployment["environment"]
                        if has_secret(environment):
                            state["unavailable"].append("deployment_secret_metadata")
                        else:
                            state["deploy_evidence"].append(
                                GitEvidence(
                                    kind="deployment",
                                    provider_type="deployment",
                                    provider_id=str(deployment["id"]),
                                    connection_id=row["id"],
                                    repository_id=row["repository_id"],
                                    commit_sha=deployment["sha"],
                                    status=latest.state,
                                    environment=environment,
                                    source_url=f"https://github.com/{row['owner']}/{row['repo']}/deployments/{deployment['id']}",
                                    observed_at=datetime.now(UTC),
                                    upstream_at=latest.created_at,
                                    is_current=False if latest.state == "inactive" else None,
                                ).model_dump(mode="json")
                            )
                    else:
                        state["unavailable"].append("deployment_status_unknown")
                    state["deployment_cursor"] = cursor + 1
                elif phase == "verify":
                    if change["kind"] == "pull_request":
                        identity = await self._change_identity(
                            row, client, kind=change["kind"], provider_id=change["provider_id"]
                        )
                        if identity != state["identity"]:
                            state.update(
                                identity=identity,
                                target_sha=identity["merge_sha"] or identity["head_sha"],
                                phase="checks",
                                checks=[],
                                deploy_evidence=[],
                                unavailable=[],
                            )
                            change = await self._save_evidence_state(
                                row, change, state, principal=principal, lease_id=lease_id
                            )
                            continue
                    complete = True
                    break
                else:
                    raise GitSourceConflictError()
            except GitHubBudgetExceededError:
                break
            except GitHubClientError as error:
                state["unavailable"].append(f"{phase}_{self._error_code(error)}")
                if isinstance(error, GitHubRateLimitError):
                    rate_error = error
                    break
                next_phase = {
                    "identity": "identity",
                    "checks": "statuses",
                    "statuses": "deployments",
                    "deployments": "verify",
                    "deployment_statuses": "deployment_statuses",
                    "verify": "verify",
                }[phase]
                if phase == "identity" or phase == "verify":
                    break
                if phase == "deployment_statuses":
                    state["deployment_cursor"] = state.get("deployment_cursor", 0) + 1
                state["phase"] = next_phase
            change = await self._save_evidence_state(
                row, change, state, principal=principal, lease_id=lease_id
            )
        # Переход verify для commit и пустого deployment списка не требует дополнительного API.
        if state["phase"] == "deployment_statuses" and state.get("deployment_cursor", 0) >= len(
            state.get("deployments", [])
        ):
            state["phase"] = "verify"
        if state["phase"] == "verify" and change["kind"] == "commit":
            complete = True
        if complete:
            self._current_deployments(state)
        change = await self._save_evidence_state(
            row, change, state, principal=principal, lease_id=lease_id, complete=complete
        )
        async with self.scope() as db:
            current, _, _, _ = await self._load(
                db,
                project_id=row["project_id"],
                connection_id=row["id"],
                principal=principal,
                for_update=True,
            )
            if current["lease_id"] != lease_id or current["generation"] != row["generation"]:
                raise GitSourceConflictError()
            await db.sources.update_connection(
                project_id=row["project_id"],
                connection_id=row["id"],
                lease_id=lease_id,
                expected_generation=row["generation"],
                data={"work_phase": "index"},
            )
            more_due = bool(await db.sources.due_changes(connection_id=row["id"], limit=1))
            await db.unit_of_work.commit()
        await self._release(
            row,
            lease_id=lease_id,
            soon=rate_error is None
            and (not complete or more_due or row["indexed_sha"] != row["observed_sha"]),
            data={"next_poll_at": self._retry_at(rate_error)} if rate_error is not None else None,
        )
        return True

    async def _checkpoint(
        self,
        row: dict,
        *,
        principal: Principal,
        lease_id: UUID,
        job: dict,
        cursor: int,
        byte_count: int,
        error_code: str | None = None,
    ) -> None:
        async with self.scope() as db:
            current, _, _, _ = await self._load(
                db,
                project_id=row["project_id"],
                connection_id=row["id"],
                principal=principal,
                for_update=True,
            )
            if (
                current["generation"] != row["generation"]
                or current["credential_revision"] != row["credential_revision"]
                or current["lease_id"] != lease_id
            ):
                raise GitSourceConflictError()
            await db.sources.update_job(
                job_id=job["id"],
                data={
                    "cursor": cursor,
                    "bytes_processed": byte_count,
                    "last_error_code": error_code,
                },
            )
            await db.unit_of_work.commit()

    async def _release(
        self, row: dict, *, lease_id: UUID, data: dict | None = None, soon: bool = False
    ) -> None:
        async with self.scope() as db:
            await db.sources.update_connection(
                project_id=row["project_id"],
                connection_id=row["id"],
                lease_id=lease_id,
                expected_generation=row["generation"],
                data={
                    "lease_id": None,
                    "lease_until": None,
                    "next_poll_at": datetime.now(UTC)
                    + timedelta(seconds=1 if soon else self.limits.poll_interval_seconds),
                    **(data or {}),
                },
            )
            await db.unit_of_work.commit()

    async def _index_batch(
        self, row: dict, client: GitHubClient, principal: Principal, lease_id: UUID
    ) -> bool:
        snapshot = await self._snapshot(row, sha=row["observed_sha"])
        async with self.scope() as db:
            job = await db.sources.get_job(connection_id=row["id"], generation=row["generation"])
        if snapshot["complete"]:
            await self._publish(row, snapshot, job, principal=principal, lease_id=lease_id)
            return False
        snapshot = await self._tree(row, snapshot, client, principal)
        cursor, byte_count = job["cursor"], job["bytes_processed"]
        processed, started, error_code = 0, time.monotonic(), None
        while (
            cursor < len(snapshot["manifest"])
            and processed < self.limits.batch_files
            and time.monotonic() - started < self.limits.batch_deadline_seconds
        ):
            entry = snapshot["manifest"][cursor]
            try:
                file = await self._store_file(row, snapshot, entry, client, principal)
            except GitHubBudgetExceededError:
                break
            except GitHubClientError as error:
                if isinstance(error, GitHubRateLimitError):
                    await self._remote_failure(row, error)
                    raise GitSourceIncompleteError() from None
                error_code = self._error_code(error)
                break
            byte_count += file["size"] if file["state"] == "ready" else 0
            cursor += 1
            processed += 1
            await self._checkpoint(
                row,
                principal=principal,
                lease_id=lease_id,
                job=job,
                cursor=cursor,
                byte_count=byte_count,
            )
        if error_code is not None:
            await self._checkpoint(
                row,
                principal=principal,
                lease_id=lease_id,
                job=job,
                cursor=cursor,
                byte_count=byte_count,
                error_code=error_code,
            )
        async with self.scope() as db:
            current, _, _, _ = await self._load(
                db,
                project_id=row["project_id"],
                connection_id=row["id"],
                principal=principal,
                for_update=True,
            )
            if current["generation"] != row["generation"] or current["lease_id"] != lease_id:
                raise GitSourceConflictError()
            counts = await db.sources.snapshot_coverage(snapshot_id=snapshot["id"])
            finished = cursor >= len(snapshot["manifest"])
            truncated = snapshot["tree_truncated"] or counts["budget_excluded"] > 0
            coverage = {
                "total_files": len(snapshot["manifest"]),
                "indexed_files": counts["indexed_files"],
                "excluded_files": counts["excluded_files"],
                "failed_files": counts["failed_files"] + (1 if error_code else 0),
                "truncated": truncated,
                "reasons": [
                    reason
                    for reason, present in (
                        ("tree_limit", snapshot["tree_truncated"]),
                        ("snapshot_byte_limit", counts["budget_excluded"] > 0),
                        ("filtered_files", counts["excluded_files"] > 0),
                        ("read_error", error_code is not None),
                        ("preparing", not finished),
                    )
                    if present
                ],
                "commit_sha": snapshot["commit_sha"],
                "generation": row["generation"],
            }
            complete = finished and not truncated and error_code is None
            await db.sources.update_snapshot(
                snapshot_id=snapshot["id"], data={"complete": complete, "coverage": coverage}
            )
            await db.sources.update_job(
                job_id=job["id"],
                data={
                    "state": "complete" if complete else "partial" if finished else "pending",
                    "last_error_code": error_code,
                },
            )
            if complete:
                values = {
                    "indexed_sha": snapshot["commit_sha"],
                    "indexed_at": datetime.now(UTC),
                    "state": "partial" if counts["excluded_files"] else "ready",
                    "coverage": coverage,
                    "last_error_code": None,
                    "work_phase": "evidence",
                }
                await db.knowledge_events.reindex_project(row["project_id"])
            else:
                values = {
                    "state": "error" if error_code else "partial",
                    "coverage": coverage,
                    "last_error_code": error_code,
                    "work_phase": "evidence",
                }
            await db.sources.update_connection(
                project_id=row["project_id"],
                connection_id=row["id"],
                expected_generation=row["generation"],
                lease_id=lease_id,
                data=values,
            )
            more_due = bool(await db.sources.due_changes(connection_id=row["id"], limit=1))
            await db.unit_of_work.commit()
        await self._release(
            row, lease_id=lease_id, soon=error_code is None and (not finished or more_due)
        )
        return not finished

    async def _publish(
        self, row: dict, snapshot: dict, job: dict, *, principal: Principal, lease_id: UUID
    ) -> None:
        async with self.scope() as db:
            current, _, _, _ = await self._load(
                db,
                project_id=row["project_id"],
                connection_id=row["id"],
                principal=principal,
                for_update=True,
            )
            if (
                current["generation"] != row["generation"]
                or current["credential_revision"] != row["credential_revision"]
                or current["observed_sha"] != snapshot["commit_sha"]
                or current["lease_id"] != lease_id
            ):
                raise GitSourceConflictError()
            await db.sources.update_job(job_id=job["id"], data={"state": "complete"})
            await db.sources.update_connection(
                project_id=row["project_id"],
                connection_id=row["id"],
                expected_generation=row["generation"],
                lease_id=lease_id,
                data={
                    "indexed_sha": snapshot["commit_sha"],
                    "indexed_at": current["indexed_at"]
                    if current["indexed_sha"] == snapshot["commit_sha"]
                    else datetime.now(UTC),
                    "coverage": snapshot["coverage"],
                    "state": "partial" if snapshot["coverage"].get("excluded_files") else "ready",
                    "last_error_code": None,
                    "work_phase": "evidence",
                },
            )
            if current["indexed_sha"] != snapshot["commit_sha"]:
                await db.knowledge_events.reindex_project(row["project_id"])
            more_due = bool(await db.sources.due_changes(connection_id=row["id"], limit=1))
            await db.unit_of_work.commit()
        await self._release(row, lease_id=lease_id, soon=more_due)

    async def tick(self) -> int:
        """Обрабатывает bounded очередь; каждый lease имеет отдельные 4 HTTP attempts/45 секунд."""
        lease_id = uuid4()
        async with self.scope() as db:
            rows = await db.sources.claim_connections(
                lease_id=lease_id,
                lease_seconds=self.limits.lease_seconds,
                limit=self.limits.max_connections_per_tick,
            )
            await db.unit_of_work.commit()
        for claimed in rows:
            row = claimed
            try:
                async with self.scope() as db:
                    identity = await db.sources.worker_identity(connection_id=row["id"])
                if identity is None:
                    await self._release(
                        row,
                        lease_id=lease_id,
                        data={"state": "access_lost", "last_error_code": "owner_unavailable"},
                    )
                    continue
                # Persistent owner-approved connection policy, not an agent:ask scope grant.
                principal = Principal(
                    user_id=identity["id"],
                    username=identity["username"],
                    first_name=identity["first_name"],
                    last_name=identity["last_name"],
                    middle_name=identity["middle_name"],
                    scope=ApiTokenScope.WRITE,
                    via_api_token=False,
                )
                async with asyncio.timeout(self.limits.batch_deadline_seconds):
                    async with self._open(
                        project_id=row["project_id"],
                        connection_id=row["id"],
                        principal=principal,
                        lease_id=lease_id,
                    ) as (current, client):
                        row = current
                        if row["work_phase"] == "evidence" and await self._evidence_batch(
                            row, client, principal, lease_id
                        ):
                            continue
                        await self._index_batch(row, client, principal, lease_id)
            except (
                GitSourceConflictError,
                GitSourceNotAvailableError,
                GitCredentialUnavailableError,
            ):
                await self._release(row, lease_id=lease_id)
            except (TimeoutError, GitSourceIncompleteError, GitHubBudgetExceededError):
                await self._release(row, lease_id=lease_id, soon=True)
            except GitSourceServiceError:
                await self._release(
                    row,
                    lease_id=lease_id,
                    data={"state": "error", "last_error_code": "processing_failed"},
                )
        return len(rows)

    async def run(self, stop: asyncio.Event) -> None:
        """Lifecycle worker: polling/очередь восстанавливаются из PostgreSQL после рестарта."""
        while not stop.is_set():
            try:
                processed = await self.tick()
            except GitSourceServiceError:
                processed = 0
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=1.0 if processed else min(5.0, self.limits.poll_interval_seconds),
                )
            except TimeoutError:
                pass
