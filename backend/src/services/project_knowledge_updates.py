"""Инкрементальное построение карты: короткий snapshot → CPU → короткий CAS.

Внешних запросов, моделей и платных повторов этот этап не выполняет. Снимок
текущего каталога остаётся единственным первичным представлением. Сохраняются
только metadata версий, точные evidence, история утверждений и checkpoints.
"""

import asyncio
import hashlib
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from src.exceptions.base import RepositoryError
from src.exceptions.project_knowledge import (
    ProjectKnowledgeServiceError,
    ProjectKnowledgeStaleError,
)
from src.knowledge.catalog import SourceType, build_catalog
from src.knowledge.project_map import (
    MAP_DEADLINE_SECONDS,
    MAX_BATCH_CHARS,
    MAX_BATCH_CLAIMS,
    MAX_BATCH_SOURCES,
    MAX_FRAGMENT_CHARS,
    ParsedSource,
    SourceProjection,
    build_projections,
    parse_projection,
)
from src.services.project_knowledge_scope import ProjectKnowledgeUpdateScopeFactory


@dataclass(frozen=True, slots=True)
class MapSnapshot:
    project_id: int
    generation: int
    rows: dict[str, list[dict]]
    heads: dict[str, dict]
    gaps: list[dict]


@dataclass(slots=True)
class MapPlan:
    snapshot: MapSnapshot
    versions: dict[str, dict]
    projections: dict[str, SourceProjection]
    parsed: list[ParsedSource]
    metrics: dict
    gaps: list[dict]
    continuation: bool


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


class ProjectKnowledgeUpdatesService:
    """Worker-сценарий с явной зависимостью и возобновляемым ограниченным batch."""

    def __init__(self, *, scope: ProjectKnowledgeUpdateScopeFactory) -> None:
        @asynccontextmanager
        async def safe_scope():
            try:
                async with scope() as db:
                    yield db
            except RepositoryError:
                raise ProjectKnowledgeServiceError(ProjectKnowledgeServiceError.detail) from None

        self.scope = safe_scope

    async def enqueue_missing_projects(self) -> int:
        async with self.scope() as db:
            count = await db.knowledge.enqueue_missing_projects(limit=20)
            await db.unit_of_work.commit()
            return count

    async def _snapshot(self, project_id: int) -> MapSnapshot | None:
        async with self.scope() as db:
            if await db.knowledge.lock_project(project_id) is None:
                return None
            state = await db.knowledge.ensure_state(project_id)
            rows = await db.sources.get_project_rows(project_id)
            heads = await db.knowledge.get_sources(project_id)
            await db.knowledge.publish(
                project_id=project_id,
                generation=state["generation"],
                values={"status": "updating", "last_error": None},
            )
            await db.unit_of_work.commit()
            return MapSnapshot(project_id, state["generation"], rows, heads, state["gaps"])

    @staticmethod
    def _plan(snapshot: MapSnapshot, *, started_at: float) -> MapPlan:
        # Этот метод вызывается только после закрытия DB-области. CPU-парсер
        # имеет такой же общий deadline, проверяемый и между источниками.
        catalog = build_catalog(snapshot.project_id, snapshot.rows)
        projections = build_projections(catalog)
        versions = {}
        for source_id, projection in projections.items():
            head = snapshot.heads.get(source_id, {})
            dependencies = {
                value: snapshot.heads.get(value, {}).get("generation", 0)
                for value in projection.dependencies
            }
            generation = head.get("generation", 0)
            identity = _digest(
                [
                    projection.content_hash,
                    projection.representation,
                    projection.native_revision,
                    projection.source.title,
                    projection.supported,
                    projection.skip_reason,
                    projection.report_current,
                    projection.task_done,
                    dependencies,
                ]
            )
            versions[source_id] = {
                "id": uuid5(
                    NAMESPACE_URL,
                    f"telos-project-version:{snapshot.project_id}:{source_id}:{generation}:{identity}",
                ),
                "project_id": snapshot.project_id,
                "source_id": source_id,
                "generation": generation,
                "identity_hash": identity,
                "native_revision": projection.native_revision,
                "source_hash": projection.source.source_hash,
                "content_hash": projection.content_hash,
                "representation": projection.representation,
                "title": projection.source.title,
                "source_type": projection.source.kind.value,
                "document_ref": projection.document_ref,
                "task_id": projection.source.entity_id
                if projection.source.kind is SourceType.TASK
                else projection.source.properties.get("task_id")
                or projection.source.properties.get("owner_task_id"),
                "dependencies": dependencies,
            }
        parsed = []
        reused = 0
        remaining = MAX_BATCH_CHARS
        remaining_claims = MAX_BATCH_CLAIMS
        continuation = False
        processed = 0
        # Паспорт полезен и в проекте без задач; далее документы и опубликованные
        # результаты, затем структурированные состояния и metadata.
        priority = {
            SourceType.PROJECT: 0,
            SourceType.DOCUMENT: 1,
            SourceType.PROJECT_FILE: 1,
            SourceType.ATTACHMENT: 1,
            SourceType.TASK_REPORT: 2,
            SourceType.TASK: 3,
        }
        for source_id, projection in sorted(
            projections.items(), key=lambda pair: (priority.get(pair[1].source.kind, 4), pair[0])
        ):
            if time.monotonic() - started_at >= MAP_DEADLINE_SECONDS:
                continuation = True
                break
            head = snapshot.heads.get(source_id, {})
            version = versions[source_id]
            same = (
                head.get("projection_hash") == version["identity_hash"]
                and head.get("processed_generation") == version["generation"]
            )
            if same and head.get("status") in {"ready", "skipped"}:
                reused += 1
                continue
            if len(parsed) >= MAX_BATCH_SOURCES or projection.supported and remaining_claims < 17:
                continuation = True
                continue
            offset = head.get("processed_chars", 0) if same else 0
            needs_text = projection.supported and projection.source.kind is not SourceType.TASK
            if needs_text and remaining <= 0:
                continuation = True
                continue
            result = parse_projection(
                projection,
                offset=offset,
                max_chars=min(remaining, MAX_FRAGMENT_CHARS),
                max_claims=min(256, remaining_claims),
            )
            parsed.append(result)
            consumed = result.end - result.offset
            remaining -= consumed
            processed += consumed
            remaining_claims -= len(result.drafts)
            if not result.complete:
                continuation = True
        changed_ids = {result.source_id for result in parsed if result.offset == 0}
        # Неприсланные source gaps не теряются между checkpoints. Старый gap о
        # partial заменяется текущим; удалённые sources не раскрываются.
        gaps = [
            gap
            for gap in snapshot.gaps
            if gap.get("source_id") in projections
            and gap.get("source_id") not in changed_ids
            and gap.get("code") != "source_partial"
        ]
        for result in parsed:
            gaps.extend(result.gaps)
        if continuation:
            gaps.append(
                {
                    "code": "map_batch_limited",
                    "category": "processing",
                    "message": "Попытка ограничена 128 000 символов, 64 источниками и 512 утверждениями; оставшийся текст продолжится из checkpoint.",
                }
            )
        gaps = list(
            {json.dumps(gap, sort_keys=True, ensure_ascii=False): gap for gap in gaps}.values()
        )
        metrics = {
            "source_reads": sum(result.end > result.offset for result in parsed),
            "sources_reused": reused,
            "chars_processed": processed,
            "catalog_chars_read": sum(len(projection.text) for projection in projections.values()),
            "duration_ms": 0,
            "model_requests": 0,
            "http_attempts": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": None,
            "cost_note": "no_model_requests",
        }
        return MapPlan(snapshot, versions, projections, parsed, metrics, gaps, continuation)

    async def _publish(self, plan: MapPlan, *, started_at: float) -> None:
        project_id = plan.snapshot.project_id
        async with self.scope() as db:
            if await db.knowledge.lock_project(project_id) is None:
                raise ProjectKnowledgeStaleError()
            current = await db.knowledge.get_state(project_id, for_update=True)
            if not current or current["generation"] != plan.snapshot.generation:
                raise ProjectKnowledgeStaleError()
            # Два worker одного поколения не откатывают уже сохранённый cursor.
            heads = await db.knowledge.get_sources(project_id)
            for result in plan.parsed:
                before = plan.snapshot.heads.get(result.source_id, {})
                now = heads.get(result.source_id, {})
                if (
                    before.get("projection_hash"),
                    before.get("processed_chars", 0),
                    before.get("processed_generation"),
                ) != (
                    now.get("projection_hash"),
                    now.get("processed_chars", 0),
                    now.get("processed_generation"),
                ):
                    raise ProjectKnowledgeStaleError()
            missing_ids = [source_id for source_id in plan.projections if source_id not in heads]
            for offset in range(0, len(missing_ids), 512):
                await db.knowledge.seed_sources(project_id, missing_ids[offset : offset + 512])
            await db.knowledge.mark_absent_sources(
                project_id=project_id, current_source_ids=list(plan.projections)
            )
            # Metadata зависимостей сохраняется в той же транзакции, что claims.
            needed_ids = {result.source_id for result in plan.parsed}
            needed_ids.update(
                item.source_id
                for result in plan.parsed
                for draft in result.drafts
                for item in draft.evidence
            )
            needed_versions = [plan.versions[source_id] for source_id in sorted(needed_ids)]
            for offset in range(0, len(needed_versions), 512):
                await db.knowledge.save_versions(needed_versions[offset : offset + 512])
            claim_heads = await db.knowledge.get_claim_heads(
                project_id, [result.source_id for result in plan.parsed]
            )
            claims, revisions, evidence = [], [], []
            now = datetime.now(UTC)
            for result in plan.parsed:
                projection = plan.projections[result.source_id]
                version = plan.versions[result.source_id]
                before = heads.get(result.source_id, {})
                changed = (
                    before.get("projection_hash") != version["identity_hash"]
                    or before.get("processed_generation") != version["generation"]
                )
                if changed:
                    await db.knowledge.invalidate_source_claims(
                        project_id=project_id, source_id=result.source_id
                    )
                for draft in result.drafts:
                    claim_id = draft.identifier(project_id)
                    proof_ids = [
                        str(plan.versions[item.source_id]["id"]) for item in draft.evidence
                    ]
                    fingerprint = _digest(
                        [
                            draft.text,
                            draft.kind,
                            draft.acceptance,
                            draft.verification,
                            draft.provenance,
                            proof_ids,
                            [
                                (
                                    item.quote,
                                    item.start,
                                    item.end,
                                    item.field_path,
                                    item.field_value,
                                )
                                for item in draft.evidence
                            ],
                        ]
                    )
                    old = claim_heads.get(claim_id)
                    if old and old["fingerprint"] == fingerprint and not changed:
                        continue
                    revision = old["revision"] + 1 if old else 1
                    claims.append(
                        {
                            "id": claim_id,
                            "project_id": project_id,
                            "source_id": result.source_id,
                            "stable_key": draft.stable_key,
                            "revision": revision,
                            "category": draft.category,
                            "state": "current",
                            "fingerprint": fingerprint,
                            "updated_at": now,
                        }
                    )
                    revisions.append(
                        {
                            "claim_id": claim_id,
                            "revision": revision,
                            "project_id": project_id,
                            "text": draft.text,
                            "kind": draft.kind,
                            "acceptance": draft.acceptance,
                            "verification": draft.verification,
                            "provenance": draft.provenance,
                            "subject": draft.subject,
                            "value_hash": draft.value_hash,
                            "conflicts_with": [],
                            "replaced_by": None,
                            "reason": None,
                        }
                    )
                    for item in draft.evidence:
                        evidence.append(
                            {
                                "id": uuid4(),
                                "claim_id": claim_id,
                                "claim_revision": revision,
                                "project_id": project_id,
                                "source_version_id": plan.versions[item.source_id]["id"],
                                "quote": item.quote,
                                "start": item.start,
                                "end": item.end,
                                "field_path": item.field_path,
                                "field_value": plan.versions[item.source_id]["generation"]
                                if item.field_path == "source_generation"
                                else item.field_value,
                                "checked_at": now,
                            }
                        )
                await db.knowledge.save_source_checkpoint(
                    project_id=project_id,
                    source_id=result.source_id,
                    generation=version["generation"],
                    values={
                        "processed_generation": version["generation"],
                        "current_version_id": version["id"],
                        "projection_hash": version["identity_hash"],
                        "processed_chars": result.end,
                        "total_chars": result.total,
                        "status": "skipped"
                        if not projection.supported
                        else "ready"
                        if result.complete
                        else "partial",
                    },
                )
            # Даже необработанный хвост не выдаётся за актуальный: version pointer
            # для его dependencies обновляется, но processed_generation не меняется.
            parsed_ids = {result.source_id for result in plan.parsed}
            for source_id in needed_ids:
                version = plan.versions[source_id]
                if source_id not in parsed_ids:
                    await db.knowledge.save_source_checkpoint(
                        project_id=project_id,
                        source_id=source_id,
                        generation=version["generation"],
                        values={
                            "current_version_id": version["id"],
                            "total_chars": len(plan.projections[source_id].text),
                        },
                    )
            await db.knowledge.save_claims(claims)
            await db.knowledge.append_revisions(revisions)
            await db.knowledge.append_evidence(evidence)
            manifest_id = uuid4()
            plan.metrics["duration_ms"] = max(0, (time.monotonic() - started_at) * 1000)
            blocked = any(
                gap["code"]
                in {
                    "text_not_ready",
                    "task_report_not_current",
                    "task_report_empty",
                    "relation_limit",
                    "passport_projection_missing",
                }
                for gap in plan.gaps
            )
            complete = not plan.continuation and not blocked
            coverage = {
                "complete": complete,
                "reasons": ["semantic_verification_not_performed"]
                + (["batch_checkpoint_pending"] if plan.continuation else [])
                + (["source_text_or_report_not_current"] if blocked else []),
            }
            await db.knowledge.save_manifest(
                {
                    "id": manifest_id,
                    "project_id": project_id,
                    "generation": current["generation"],
                    "sources": {
                        source_id: {
                            "generation": version["generation"],
                            "version_id": str(version["id"])
                            if source_id in needed_ids
                            or heads.get(source_id, {}).get("current_version_id") == version["id"]
                            else None,
                        }
                        for source_id, version in plan.versions.items()
                    },
                    "complete": complete,
                    "metrics": plan.metrics,
                }
            )
            if not await db.knowledge.publish(
                project_id=project_id,
                generation=current["generation"],
                values={
                    "built_generation": current["generation"],
                    "status": "ready" if complete else "partial",
                    "manifest_id": manifest_id,
                    "coverage": coverage,
                    "gaps": plan.gaps,
                    "metrics": plan.metrics,
                    "last_error": None,
                },
            ):
                raise ProjectKnowledgeStaleError()
            if plan.continuation:
                await db.knowledge.enqueue_refresh(project_id)
            await db.unit_of_work.commit()

    async def process_project(self, project_id: int) -> None:
        """Одна ограниченная попытка; CAS/retry не публикует прежние выводы."""
        started_at = time.monotonic()
        snapshot = None
        plan = None
        try:
            async with asyncio.timeout(MAP_DEADLINE_SECONDS):
                snapshot = await self._snapshot(project_id)
                if snapshot is None:
                    return
                # Снимок не содержит сессии. Парсинг не должен задерживать HTTP
                # в общем event loop; поток только вычисляет план, CAS остаётся
                # в корутине и не выполняется после отмены/deadline.
                plan = await asyncio.to_thread(self._plan, snapshot, started_at=started_at)
                # Даём cancellation/deadline пройти до короткой публикации.
                await asyncio.sleep(0)
                await self._publish(plan, started_at=started_at)
        except asyncio.CancelledError:
            raise
        except ProjectKnowledgeStaleError:
            # Событие новой транзакции уже в outbox. Для concurrent same-epoch
            # batch тоже оставляем continuation в существующей очереди.
            async with self.scope() as db:
                if await db.knowledge.lock_project(project_id) is not None:
                    await db.knowledge.enqueue_refresh(project_id)
                    await db.unit_of_work.commit()
            raise
        except Exception:
            if snapshot is not None:
                async with self.scope() as db:
                    if await db.knowledge.lock_project(project_id) is not None:
                        await db.knowledge.publish(
                            project_id=project_id,
                            generation=snapshot.generation,
                            values={
                                "status": "failed",
                                "last_error": "Обработка карты не завершена; задача сохраняется для ограниченного повтора.",
                                "metrics": {
                                    **(plan.metrics if plan else {}),
                                    "duration_ms": max(0, (time.monotonic() - started_at) * 1000),
                                },
                            },
                        )
                        await db.unit_of_work.commit()
            raise
