"""Версии и утверждения поверх каталога; одна операция — один SQL-запрос."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Uuid, any_, bindparam, func, select, text, update
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.knowledge_index_jobs import (
    KnowledgeEntityType,
    KnowledgeIndexJob,
    KnowledgeIndexOperation,
    KnowledgeIndexStatus,
)
from src.db.models.project_knowledge import (
    ProjectKnowledgeClaim,
    ProjectKnowledgeClaimRevision,
    ProjectKnowledgeEvidence,
    ProjectKnowledgeManifest,
    ProjectKnowledgeSource,
    ProjectKnowledgeSourceVersion,
    ProjectKnowledgeState,
)
from src.db.models.projects import Project
from src.exceptions.project_knowledge import ProjectKnowledgeRepositoryError
from src.knowledge.project_map import MAP_JOB_ENTITY_ID

# Ни названия, ни цитаты старой версии не покидают SQL, если хотя бы одна
# зависимость удалена/изменена. Поколения проверяются независимо от хэша A→B→A.
_CLAIMS = """
WITH evidence_data AS (
 SELECT e.claim_id, e.claim_revision,
        bool_and(h.source_id IS NOT NULL AND NOT h.is_deleted
          AND h.generation = v.generation AND h.current_version_id = v.id
          AND NOT EXISTS (
            SELECT 1 FROM jsonb_each_text(v.dependencies) d
            LEFT JOIN project_knowledge_sources dh
              ON dh.project_id = v.project_id AND dh.source_id = d.key
            WHERE dh.source_id IS NULL OR dh.is_deleted OR dh.generation <> d.value::bigint
          )) AS fresh,
        jsonb_agg(jsonb_build_object(
          'source_version_id', v.id, 'source_id', v.source_id,
          'source_generation', v.generation, 'native_revision', v.native_revision,
          'source_hash', v.source_hash, 'content_hash', v.content_hash,
          'representation', v.representation, 'document_ref', v.document_ref,
          'task_id', v.task_id, 'title', v.title, 'quote', e.quote,
          'start', e.start, 'end', e."end", 'field_path', e.field_path,
          'value', e.field_value, 'checked_at', e.checked_at,
          'coverage', CASE WHEN e.quote IS NULL THEN 'structured_field' ELSE 'exact_fragment' END
        ) ORDER BY e.id) AS evidence
 FROM project_knowledge_evidence e
 JOIN project_knowledge_source_versions v ON v.id = e.source_version_id AND v.project_id = e.project_id
 LEFT JOIN project_knowledge_sources h ON h.project_id = v.project_id AND h.source_id = v.source_id
 WHERE e.project_id = :project_id
 GROUP BY e.claim_id, e.claim_revision
), claim_data AS (
 SELECT c.*, r.text, r.kind, r.acceptance, r.verification, r.provenance,
        r.subject, r.value_hash, r.replaced_by, r.reason,
        COALESCE(e.fresh, false) AND c.state IN ('current','conflicting') AS fresh,
        e.evidence
 FROM project_knowledge_claims c
 JOIN project_knowledge_claim_revisions r ON r.claim_id = c.id AND r.revision = c.revision AND r.project_id = c.project_id
 LEFT JOIN evidence_data e ON e.claim_id = c.id AND e.claim_revision = c.revision
 WHERE c.project_id = :project_id
), current_claims AS (
 SELECT d.*, COALESCE((
   SELECT jsonb_agg(conflict.id ORDER BY conflict.id) FROM (
     SELECT other.id FROM claim_data other
     WHERE d.fresh AND other.fresh AND d.subject IS NOT NULL
       AND other.subject = d.subject AND other.value_hash <> d.value_hash AND other.id <> d.id
     ORDER BY other.id LIMIT 20
   ) conflict
 ), '[]'::jsonb) AS conflicts
 FROM claim_data d
)
"""


class ProjectKnowledgeRepository:
    """Короткие операции без commit: транзакцией владеет сценарий."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def _execute(self, statement, parameters=None):
        try:
            return await self.db_session.execute(statement, parameters or {})
        except SQLAlchemyError:
            # SQL-параметры содержат evidence: не логируем исходное исключение.
            await self.db_session.rollback()
            raise ProjectKnowledgeRepositoryError(ProjectKnowledgeRepositoryError.detail) from None

    async def lock_project(self, project_id: int) -> int | None:
        """Порядок блокировок projects→state/history совпадает с downgrade."""
        return (
            await self._execute(
                select(Project.id)
                .where(Project.id == project_id)
                .with_for_update(read=True, key_share=True)
            )
        ).scalar_one_or_none()

    async def ensure_state(self, project_id: int) -> dict:
        statement = (
            insert(ProjectKnowledgeState)
            .values(project_id=project_id)
            .on_conflict_do_update(
                index_elements=[ProjectKnowledgeState.project_id], set_={"project_id": project_id}
            )
            .returning(*ProjectKnowledgeState.__table__.columns)
        )
        return dict((await self._execute(statement)).mappings().one())

    async def get_state(self, project_id: int, *, for_update: bool = False) -> dict | None:
        statement = select(*ProjectKnowledgeState.__table__.columns).where(
            ProjectKnowledgeState.project_id == project_id
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._execute(statement)).mappings().one_or_none()
        return dict(row) if row else None

    async def seed_sources(self, project_id: int, source_ids: Sequence[str]) -> None:
        if not source_ids:
            return
        await self._execute(
            insert(ProjectKnowledgeSource)
            .values(
                [{"project_id": project_id, "source_id": source_id} for source_id in source_ids]
            )
            .on_conflict_do_nothing(
                index_elements=[ProjectKnowledgeSource.project_id, ProjectKnowledgeSource.source_id]
            )
        )

    async def get_sources(
        self, project_id: int, *, source_ids: Sequence[str] | None = None
    ) -> dict[str, dict]:
        statement = select(*ProjectKnowledgeSource.__table__.columns).where(
            ProjectKnowledgeSource.project_id == project_id
        )
        if source_ids is not None:
            if not source_ids:
                return {}
            statement = statement.where(ProjectKnowledgeSource.source_id.in_(source_ids))
        return {row["source_id"]: dict(row) for row in (await self._execute(statement)).mappings()}

    async def save_versions(self, versions: list[dict]) -> None:
        if not versions:
            return
        await self._execute(
            insert(ProjectKnowledgeSourceVersion)
            .values(versions)
            .on_conflict_do_nothing(index_elements=[ProjectKnowledgeSourceVersion.id])
        )

    async def get_versions(self, project_id: int, version_ids: Sequence[UUID]) -> dict[UUID, dict]:
        if not version_ids:
            return {}
        result = await self._execute(
            select(*ProjectKnowledgeSourceVersion.__table__.columns).where(
                ProjectKnowledgeSourceVersion.project_id == project_id,
                ProjectKnowledgeSourceVersion.id
                == any_(bindparam("version_ids", value=list(version_ids), type_=ARRAY(Uuid))),
            )
        )
        return {row["id"]: dict(row) for row in result.mappings()}

    async def save_source_checkpoint(
        self, *, project_id: int, source_id: str, generation: int, values: dict
    ) -> None:
        await self._execute(
            update(ProjectKnowledgeSource)
            .where(
                ProjectKnowledgeSource.project_id == project_id,
                ProjectKnowledgeSource.source_id == source_id,
                ProjectKnowledgeSource.generation == generation,
                ProjectKnowledgeSource.is_deleted.is_(False),
            )
            .values(**values, updated_at=func.now())
        )

    async def mark_absent_sources(
        self, *, project_id: int, current_source_ids: Sequence[str]
    ) -> None:
        """Перенесённый/исчезнувший источник не остаётся текущим в прежнем проекте."""
        await self._execute(
            text("""
            UPDATE project_knowledge_sources SET is_deleted=true,current_version_id=NULL,status='skipped',updated_at=now()
            WHERE project_id=:project_id AND NOT (source_id=ANY(CAST(:source_ids AS text[])))
        """),
            {"project_id": project_id, "source_ids": list(current_source_ids)},
        )

    async def get_claim_heads(self, project_id: int, source_ids: Sequence[str]) -> dict[UUID, dict]:
        if not source_ids:
            return {}
        result = await self._execute(
            select(*ProjectKnowledgeClaim.__table__.columns).where(
                ProjectKnowledgeClaim.project_id == project_id,
                ProjectKnowledgeClaim.source_id.in_(source_ids),
            )
        )
        return {row["id"]: dict(row) for row in result.mappings()}

    async def invalidate_source_claims(self, *, project_id: int, source_id: str) -> None:
        await self._execute(
            update(ProjectKnowledgeClaim)
            .where(
                ProjectKnowledgeClaim.project_id == project_id,
                ProjectKnowledgeClaim.source_id == source_id,
            )
            .values(state="superseded")
        )

    async def save_claims(self, claims: list[dict]) -> None:
        if not claims:
            return
        statement = insert(ProjectKnowledgeClaim).values(claims)
        await self._execute(
            statement.on_conflict_do_update(
                index_elements=[ProjectKnowledgeClaim.id],
                set_={
                    name: getattr(statement.excluded, name)
                    for name in ("revision", "category", "state", "fingerprint", "updated_at")
                },
            )
        )

    async def append_revisions(self, revisions: list[dict]) -> None:
        if revisions:
            await self._execute(insert(ProjectKnowledgeClaimRevision).values(revisions))

    async def append_evidence(self, evidence: list[dict]) -> None:
        if evidence:
            await self._execute(insert(ProjectKnowledgeEvidence).values(evidence))

    async def save_manifest(self, manifest: dict) -> None:
        await self._execute(insert(ProjectKnowledgeManifest).values(**manifest))

    async def publish(self, *, project_id: int, generation: int, values: dict) -> bool:
        result = await self._execute(
            update(ProjectKnowledgeState)
            .where(
                ProjectKnowledgeState.project_id == project_id,
                ProjectKnowledgeState.generation == generation,
            )
            .values(**values, updated_at=func.now())
            .returning(ProjectKnowledgeState.project_id)
        )
        return result.scalar_one_or_none() is not None

    async def enqueue_refresh(self, project_id: int) -> None:
        """Тот же persistent outbox; marker не запускает платный semantic reindex."""
        await self._execute(
            insert(KnowledgeIndexJob)
            .values(
                project_id=project_id,
                entity_type=KnowledgeEntityType.PROJECT,
                entity_id=MAP_JOB_ENTITY_ID,
                operation=KnowledgeIndexOperation.UPSERT,
                status=KnowledgeIndexStatus.PENDING,
                transaction_id=func.txid_current(),
                attempts=0,
            )
            .on_conflict_do_nothing(
                index_elements=[KnowledgeIndexJob.project_id, KnowledgeIndexJob.transaction_id]
            )
        )

    async def enqueue_missing_projects(self, *, limit: int = 20) -> int:
        """Постепенная инициализация существующих проектов, без data migration."""
        result = await self._execute(
            text("""
          INSERT INTO knowledge_index_jobs (project_id,entity_type,entity_id,operation,status,attempts,transaction_id)
          SELECT p.id,'PROJECT',:marker,'UPSERT','PENDING',0,txid_current()
          FROM projects p LEFT JOIN project_knowledge_states s ON s.project_id=p.id
          WHERE (s.project_id IS NULL OR s.status='empty') AND NOT EXISTS (
            SELECT 1 FROM knowledge_index_jobs j WHERE j.project_id=p.id AND j.status IN ('PENDING','PROCESSING')
          ) ORDER BY p.id LIMIT :limit
          ON CONFLICT(project_id,transaction_id) DO NOTHING RETURNING id
        """),
            {"marker": MAP_JOB_ENTITY_ID, "limit": limit},
        )
        return len(result.all())

    async def get_claim_page(
        self,
        *,
        project_id: int,
        limit: int,
        offset: int,
        category: str | None = None,
        exclude_category: str | None = None,
        claim_id: UUID | None = None,
        query: str | None = None,
    ) -> dict:
        result = await self._execute(
            text(
                _CLAIMS
                + """
        , filtered AS (
          SELECT * FROM current_claims WHERE
            ((CAST(:claim_id AS uuid) IS NULL AND fresh) OR id=CAST(:claim_id AS uuid))
            AND (CAST(:category AS text) IS NULL OR category=:category)
            AND (CAST(:exclude_category AS text) IS NULL OR category<>:exclude_category)
            AND (CAST(:query AS text) IS NULL OR
                 (fresh AND strpos(lower(text), lower(CAST(:query AS text))) > 0))
        ), page AS (
          SELECT jsonb_build_object(
            'id',id,'revision',revision,'kind',kind,'category',category,
            'text',CASE WHEN fresh THEN text ELSE NULL END,
            'state',CASE WHEN NOT fresh THEN 'stale' WHEN jsonb_array_length(conflicts)>0 THEN 'conflicting' ELSE 'current' END,
            'acceptance',acceptance,'verification',verification,
            'provenance',CASE WHEN fresh THEN provenance ELSE
              jsonb_build_object('origin','derived','algorithm','grounded_literal_v1','explanation','Прежние доказательства больше не доступны в этой версии.') END,
            'source_ids',CASE WHEN fresh THEN (SELECT jsonb_agg(DISTINCT x->>'source_id') FROM jsonb_array_elements(evidence) x) ELSE '[]'::jsonb END,
            'evidence',CASE WHEN fresh THEN evidence ELSE '[]'::jsonb END,
            'conflicts_with',CASE WHEN fresh THEN conflicts ELSE '[]'::jsonb END,
            'updated_at',updated_at,'replaced_by',replaced_by,
            'reason',CASE WHEN NOT fresh THEN 'source_changed_or_unavailable' WHEN jsonb_array_length(conflicts)>0 THEN 'explicit_values_differ; смысл и область требуют проверки' ELSE reason END
          ) AS item FROM filtered ORDER BY category,id LIMIT :limit OFFSET :offset
        ) SELECT COALESCE((SELECT jsonb_agg(item) FROM page),'[]'::jsonb) AS items,
                 (SELECT count(*) FROM filtered) AS total
        """
            ),
            {
                "project_id": project_id,
                "claim_id": claim_id,
                "category": category,
                "exclude_category": exclude_category,
                "query": (query or "").strip() or None,
                "limit": limit,
                "offset": offset,
            },
        )
        return dict(result.mappings().one())

    async def get_claim_counts(self, project_id: int) -> dict:
        result = await self._execute(
            text(
                _CLAIMS
                + """
          SELECT count(*) FILTER (WHERE fresh AND jsonb_array_length(conflicts)=0) AS claims_current,
                 count(*) FILTER (WHERE fresh AND jsonb_array_length(conflicts)>0) AS claims_conflicting,
                 count(*) FILTER (WHERE NOT fresh) AS claims_stale
          FROM current_claims
        """
            ),
            {"project_id": project_id},
        )
        return dict(result.mappings().one())
