"""Общий HTTP/MCP/ARIS read-контракт с текущими ACL и source generations."""

from collections.abc import Sequence
from contextlib import asynccontextmanager
from uuid import UUID

from src.exceptions.access import AccessServiceError
from src.exceptions.auth import AuthServiceError
from src.exceptions.base import RepositoryError
from src.exceptions.project_knowledge import (
    ProjectKnowledgeAccessError,
    ProjectKnowledgeNotFoundError,
    ProjectKnowledgeServiceError,
    ProjectKnowledgeStaleError,
    ProjectKnowledgeValidationError,
)
from src.knowledge.catalog import build_catalog
from src.schemas.project_knowledge import (
    ProjectKnowledgeClaimSchema,
    ProjectKnowledgeCoverageSchema,
    ProjectKnowledgeMapSchema,
    ProjectKnowledgeObservationSchema,
    ProjectKnowledgeSourceObservationSchema,
    ProjectKnowledgeStatusSchema,
)
from src.services.auth import Principal
from src.services.project_knowledge_scope import ProjectKnowledgeScope, ProjectKnowledgeScopeFactory


def version_is_current(version: dict, heads: dict[str, dict]) -> bool:
    """Проверяет собственную версию и каждый контекст применимости."""
    head = heads.get(version["source_id"])
    return bool(
        head
        and not head["is_deleted"]
        and head["generation"] == version["generation"]
        and head["current_version_id"] == version["id"]
        and all(
            source_id in heads
            and not heads[source_id]["is_deleted"]
            and heads[source_id]["generation"] == generation
            for source_id, generation in version["dependencies"].items()
        )
    )


class ProjectKnowledgeService:
    """Читает сохранённую карту без LLM, embeddings и доверия к model handles."""

    def __init__(self, *, scope: ProjectKnowledgeScopeFactory) -> None:
        @asynccontextmanager
        async def safe_scope():
            try:
                async with scope() as db:
                    yield db
            except RepositoryError:
                raise ProjectKnowledgeServiceError(ProjectKnowledgeServiceError.detail) from None

        self.scope = safe_scope

    @staticmethod
    async def _authorize(
        db: ProjectKnowledgeScope, *, project_id: int, principal: Principal, write: bool = False
    ) -> Principal:
        try:
            current = await db.auth.refresh_principal(principal)
            current.require("knowledge:read")
            current.require("projects:read")
            if write:
                current.require("projects:write")
                await db.access.ensure_project_ownership(
                    project_id=project_id, user_id=current.user_id
                )
            else:
                await db.access.ensure_project_access(
                    project_id=project_id, user_id=current.user_id
                )
            return current
        except (AuthServiceError, AccessServiceError) as error:
            raise ProjectKnowledgeAccessError(
                status_code=error.status_code, detail=error.detail
            ) from None

    @staticmethod
    def _pagination(limit: int, offset: int) -> None:
        if (
            isinstance(limit, bool)
            or isinstance(offset, bool)
            or not 1 <= limit <= 50
            or offset < 0
        ):
            raise ProjectKnowledgeValidationError()

    @staticmethod
    async def _status(
        db: ProjectKnowledgeScope, *, project_id: int, state: dict | None
    ) -> ProjectKnowledgeStatusSchema:
        heads = await db.knowledge.get_sources(project_id)
        versions = await db.knowledge.get_versions(
            project_id, [h["current_version_id"] for h in heads.values() if h["current_version_id"]]
        )
        visible = [head for head in heads.values() if not head["is_deleted"]]
        current = [
            head
            for head in visible
            if head["processed_generation"] == head["generation"]
            and head["current_version_id"] in versions
            and version_is_current(versions[head["current_version_id"]], heads)
        ]
        ready = [head for head in current if head["status"] == "ready"]
        skipped = [head for head in current if head["status"] == "skipped"]
        counts = await db.knowledge.get_claim_counts(project_id)
        same = bool(state and state["built_generation"] == state["generation"])
        coverage = dict(state["coverage"] if state and same else {})
        reasons = list(coverage.get("reasons", []))
        if "semantic_verification_not_performed" not in reasons:
            reasons.append("semantic_verification_not_performed")
        if not same:
            reasons.append("map_not_built_for_current_generation")
        coverage.update(
            sources_total=len(visible),
            sources_current=len(ready),
            sources_skipped=len(skipped),
            sources_pending=max(0, len(visible) - len(ready) - len(skipped)),
            sources_failed=sum(head["status"] == "failed" for head in visible),
            chars_total=sum(head["total_chars"] for head in visible),
            chars_processed=sum(head["processed_chars"] for head in current),
            reasons=list(dict.fromkeys(reasons)),
            complete=bool(
                same and coverage.get("complete") and len(ready) + len(skipped) == len(visible)
            ),
            **counts,
        )
        status = state["status"] if state else "empty"
        if state and not same and status not in {"failed", "updating"}:
            status = "pending"
        return ProjectKnowledgeStatusSchema(
            project_id=project_id,
            generation=state["generation"] if state else 0,
            built_generation=state["built_generation"] if state else None,
            status=status,
            updated_at=state["updated_at"] if state else None,
            coverage=ProjectKnowledgeCoverageSchema(**coverage),
            metrics=state["metrics"] if state else {},
            last_error=state["last_error"] if state else None,
        )

    @classmethod
    async def _finish_read(
        cls, db: ProjectKnowledgeScope, *, project_id: int, principal: Principal, generation: int
    ) -> None:
        # Повторная авторизация после materialization защищает от revoke в ходе чтения.
        await cls._authorize(db, project_id=project_id, principal=principal)
        latest = await db.knowledge.get_state(project_id)
        if (latest["generation"] if latest else 0) != generation:
            raise ProjectKnowledgeStaleError()

    async def get_status(
        self, *, project_id: int, principal: Principal
    ) -> ProjectKnowledgeStatusSchema:
        async with self.scope() as db:
            await self._authorize(db, project_id=project_id, principal=principal)
            state = await db.knowledge.get_state(project_id)
            result = await self._status(db, project_id=project_id, state=state)
            await self._finish_read(
                db, project_id=project_id, principal=principal, generation=result.generation
            )
            return result

    async def read_map(
        self,
        *,
        project_id: int,
        principal: Principal,
        limit: int = 20,
        offset: int = 0,
        category: str | None = None,
    ) -> ProjectKnowledgeMapSchema:
        self._pagination(limit, offset)
        async with self.scope() as db:
            await self._authorize(db, project_id=project_id, principal=principal)
            state = await db.knowledge.get_state(project_id)
            status = await self._status(db, project_id=project_id, state=state)
            page = await db.knowledge.get_claim_page(
                project_id=project_id, limit=limit, offset=offset, category=category
            )
            items = [ProjectKnowledgeClaimSchema(**item) for item in page["items"]]
            observations = {}
            for item in items:
                for evidence in item.evidence:
                    observations[evidence.source_id] = ProjectKnowledgeSourceObservationSchema(
                        source_id=evidence.source_id,
                        generation=evidence.source_generation,
                        source_version_id=evidence.source_version_id,
                        source_hash=evidence.source_hash,
                        content_hash=evidence.content_hash,
                    )
            observation = ProjectKnowledgeObservationSchema(
                project_id=project_id,
                generation=status.generation,
                sources=list(observations.values()),
            )
            gaps = (
                state["gaps"]
                if state and state["built_generation"] == state["generation"]
                else [
                    {
                        "code": "map_pending",
                        "category": "processing",
                        "message": "Текущий набор ещё не обработан; старые выводы скрыты по версиям доказательств.",
                    }
                ]
            )
            if len(gaps) > 31:
                total_gaps = len(gaps)
                gaps = gaps[:31] + [
                    {
                        "code": "gaps_truncated",
                        "category": "coverage",
                        "message": f"Показаны 31 из {total_gaps} пробелов; ещё {total_gaps - 31} не включены в эту краткую карту. Полный охват отражён в coverage.",
                    }
                ]
            await self._finish_read(
                db, project_id=project_id, principal=principal, generation=status.generation
            )
            next_offset = offset + len(items)
            return ProjectKnowledgeMapSchema(
                **status.model_dump(),
                items=items,
                gaps=gaps,
                observation=observation,
                total=page["total"],
                offset=offset,
                limit=limit,
                has_more=next_offset < page["total"],
                next_offset=next_offset if next_offset < page["total"] else None,
            )

    async def get_claim(
        self, *, project_id: int, claim_id: UUID, principal: Principal
    ) -> ProjectKnowledgeClaimSchema:
        async with self.scope() as db:
            await self._authorize(db, project_id=project_id, principal=principal)
            state = await db.knowledge.get_state(project_id)
            page = await db.knowledge.get_claim_page(
                project_id=project_id, claim_id=claim_id, limit=1, offset=0
            )
            if not page["items"]:
                raise ProjectKnowledgeNotFoundError()
            result = ProjectKnowledgeClaimSchema(**page["items"][0])
            await self._finish_read(
                db,
                project_id=project_id,
                principal=principal,
                generation=state["generation"] if state else 0,
            )
            return result

    async def request_refresh(
        self, *, project_id: int, principal: Principal
    ) -> ProjectKnowledgeStatusSchema:
        async with self.scope() as db:
            await self._authorize(db, project_id=project_id, principal=principal, write=True)
            if await db.knowledge.lock_project(project_id) is None:
                raise ProjectKnowledgeNotFoundError()
            # Проверяем owner после блокировки, а не по старому grant.
            await self._authorize(db, project_id=project_id, principal=principal, write=True)
            state = await db.knowledge.ensure_state(project_id)
            await db.knowledge.enqueue_refresh(project_id)
            await db.knowledge.publish(
                project_id=project_id,
                generation=state["generation"],
                values={"status": "pending", "last_error": None},
            )
            await db.unit_of_work.commit()
        return await self.get_status(project_id=project_id, principal=principal)

    async def capture_observation(
        self, *, project_id: int, principal: Principal, source_ids: Sequence[str]
    ) -> ProjectKnowledgeObservationSchema:
        ids = sorted(set(source_ids))
        if len(ids) > 256 or any(not isinstance(value, str) or len(value) > 256 for value in ids):
            raise ProjectKnowledgeValidationError()
        async with self.scope() as db:
            await self._authorize(db, project_id=project_id, principal=principal)
            state = await db.knowledge.get_state(project_id)
            heads = await db.knowledge.get_sources(project_id)
            missing = [source_id for source_id in ids if source_id not in heads]
            if missing:
                catalog = build_catalog(project_id, await db.sources.get_project_rows(project_id))
                if any(source_id not in catalog.sources for source_id in missing):
                    raise ProjectKnowledgeNotFoundError()
            versions = await db.knowledge.get_versions(
                project_id,
                [
                    heads[value]["current_version_id"]
                    for value in ids
                    if value in heads and heads[value]["current_version_id"]
                ],
            )
            observations = []
            for source_id in ids:
                head = heads.get(source_id)
                if head and head["is_deleted"]:
                    raise ProjectKnowledgeStaleError()
                version = versions.get(head["current_version_id"]) if head else None
                # Прямое чтение может опередить worker. Его защищает project epoch,
                # а не притворная старая saved version текущего исходного текста.
                if version and not version_is_current(version, heads):
                    version = None
                observations.append(
                    ProjectKnowledgeSourceObservationSchema(
                        source_id=source_id,
                        generation=head["generation"] if head else 0,
                        source_version_id=version["id"] if version else None,
                        source_hash=version["source_hash"] if version else None,
                        content_hash=version["content_hash"] if version else None,
                    )
                )
            generation = state["generation"] if state else 0
            await self._finish_read(
                db, project_id=project_id, principal=principal, generation=generation
            )
            return ProjectKnowledgeObservationSchema(
                project_id=project_id, generation=generation, sources=observations
            )

    async def validate_observation(
        self,
        *,
        project_id: int,
        principal: Principal,
        observation: ProjectKnowledgeObservationSchema,
    ) -> None:
        if observation.project_id != project_id:
            raise ProjectKnowledgeStaleError()
        async with self.scope() as db:
            await self._authorize(db, project_id=project_id, principal=principal)
            state = await db.knowledge.get_state(project_id)
            if (state["generation"] if state else 0) != observation.generation:
                raise ProjectKnowledgeStaleError()
            heads = await db.knowledge.get_sources(project_id)
            versions = await db.knowledge.get_versions(
                project_id,
                [
                    source.source_version_id
                    for source in observation.sources
                    if source.source_version_id
                ],
            )
            for source in observation.sources:
                head = heads.get(source.source_id)
                if (
                    head
                    and head["is_deleted"]
                    or (head["generation"] if head else 0) != source.generation
                ):
                    raise ProjectKnowledgeStaleError()
                if source.source_version_id:
                    version = versions.get(source.source_version_id)
                    if (
                        not version
                        or version["source_id"] != source.source_id
                        or not version_is_current(version, heads)
                        or version["source_hash"] != source.source_hash
                        or version["content_hash"] != source.content_hash
                    ):
                        raise ProjectKnowledgeStaleError()
            await self._finish_read(
                db, project_id=project_id, principal=principal, generation=observation.generation
            )
