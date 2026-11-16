"""Все фактически прочитанные версии проверяются независимо от model handles."""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from src.db.models.api_tokens import ApiTokenScope
from src.exceptions.base import ServiceError
from src.exceptions.context_selection import ContextSelectionChangedError
from src.knowledge.catalog import build_catalog, digest
from src.schemas.document_catalog import DocumentCatalogItemSchema
from src.schemas.git_sources import GitChangeProof, GitSourceProof
from src.schemas.knowledge import (
    AssertionReferenceSchema,
    ExternalSourceReferenceSchema,
    decode_answer_sources,
)
from src.schemas.project_knowledge import ProjectKnowledgeObservationSchema
from src.schemas.tasks import TaskSchema
from src.services.auth import Principal
from src.services.context_results import ContextReadResult, HistoryReadResult, WebPageObservation
from src.services.document_catalog import DocumentCatalogScopeFactory, DocumentCatalogService
from src.services.git_sources import GitSourceService
from src.services.project_knowledge import ProjectKnowledgeService
from src.services.project_knowledge_scope import ProjectKnowledgeScopeFactory

HIDDEN_CONTEXT_ANSWER = "Источники этого ответа изменились или сейчас недоступны. Задайте вопрос заново, чтобы Арис проверил актуальные материалы."


@asynccontextmanager
async def agent_context_budget(agent):
    """Очередь начинает один Git ledger до history preparation и сохраняет до finish."""
    tools = getattr(agent, "context_tools", None)
    if tools is None:
        yield None
    else:
        async with tools.git.read_budget() as budget:
            yield budget


def source_signature(source, projection="source") -> str:
    # Паспорт не меняется при добавлении связи с новой задачей. Его смысл и
    # context_revision проверяются, связи отдельных материалов — их полным hash.
    if source.kind == "project":
        properties = {
            key: value for key, value in source.properties.items() if key not in {"updated_at"}
        }
        return digest([source.title, source.text, properties])
    if projection == "stage_summary":
        return digest(
            [source.title, source.properties.get("name"), source.properties.get("is_done_stage")]
        )
    return source.source_hash


class InternalObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: int = Field(gt=0)
    source_id: str
    source_hash: str
    projection: Literal["source", "stage_summary"] = "source"


def task_signature(value, projection="full") -> str:
    return digest(
        {
            name: getattr(value, name, None)
            for name in (
                "id",
                "project_id",
                "stage_id",
                "title",
                "description_md",
                "priority",
                "role",
                "assignee",
                "start_date",
                "due_date",
                "updated_at",
                "status",
                "revision",
                "checklist_revision",
            )
            if projection != "summary" or name != "description_md"
        }
    )


class TaskObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: int = Field(gt=0)
    task_hash: str
    projection: Literal["full", "summary"] = "full"


class ProjectReadObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    project_id: int = Field(gt=0)
    generation: int = Field(ge=0)


def document_signature(value: DocumentCatalogItemSchema) -> str:
    return digest(value.model_dump(mode="python", exclude={"capabilities"}))


class DocumentObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    document_ref: str
    project_id: int | None
    document_hash: str


class SavedContextGuard(BaseModel):
    """Server-only JSON без текстов, credentials и web grants."""

    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=1, ge=1, le=1)
    origin_read_scopes: list[str] = Field(default_factory=list)
    projects: list[int] = Field(default_factory=list)
    project_reads: list[ProjectReadObservation] = Field(default_factory=list)
    restricted_projects: list[int] = Field(default_factory=list)
    internal: list[InternalObservation] = Field(default_factory=list)
    observations: list[ProjectKnowledgeObservationSchema] = Field(default_factory=list)
    git_files: list[GitSourceProof] = Field(default_factory=list)
    git_changes: list[GitChangeProof] = Field(default_factory=list)
    tasks: list[TaskObservation] = Field(default_factory=list)
    documents: list[DocumentObservation] = Field(default_factory=list)


@dataclass
class ContextEvidence:
    saved: SavedContextGuard = field(default_factory=SavedContextGuard)
    web_pages: list[tuple[int, WebPageObservation]] = field(default_factory=list)
    external: dict[str, ExternalSourceReferenceSchema] = field(default_factory=dict)
    assertions: dict[str, AssertionReferenceSchema] = field(default_factory=dict)
    handles: dict[str, str] = field(default_factory=dict)
    git_budget: Any = None

    def policy(self, scopes) -> None:
        self.saved.origin_read_scopes = sorted(set(self.saved.origin_read_scopes) | set(scopes))

    @property
    def has_materials(self) -> bool:
        return bool(
            self.saved.internal
            or self.saved.observations
            or self.saved.git_files
            or self.saved.git_changes
            or self.saved.tasks
            or self.saved.documents
            or self.saved.project_reads
            or self.web_pages
        )

    def task(self, value: TaskSchema, *, projection="full") -> None:
        self.saved.tasks = [item for item in self.saved.tasks if item.task_id != value.id] + [
            TaskObservation(
                task_id=value.id, task_hash=task_signature(value, projection), projection=projection
            )
        ]

    def document(self, value: DocumentCatalogItemSchema) -> None:
        self.saved.documents = [
            item for item in self.saved.documents if item.document_ref != value.ref
        ] + [
            DocumentObservation(
                document_ref=value.ref,
                project_id=value.ownership.project_id,
                document_hash=document_signature(value),
            )
        ]

    def project(self, project_id: int) -> None:
        if project_id not in self.saved.projects:
            self.saved.projects.append(project_id)

    def internal(self, project_id, source, *, projection="source") -> None:
        self.project(project_id)
        item = InternalObservation(
            project_id=project_id,
            source_id=source.source_id,
            source_hash=source_signature(source, projection),
            projection=projection,
        )
        self.saved.internal = [
            old
            for old in self.saved.internal
            if (old.project_id, old.source_id) != (project_id, source.source_id)
        ] + [item]

    def register(self, result: dict) -> None:
        if isinstance(result, HistoryReadResult):
            for guard in result.guards:
                self.inherit(SavedContextGuard.model_validate(guard))
            return
        if not isinstance(result, ContextReadResult):
            return
        self.policy(result.origin_read_scopes)
        if result.restricted:
            self.policy({"knowledge:read", "projects:read"})
        self.project(result.project_id)
        if result.restricted and result.project_id not in self.saved.restricted_projects:
            self.saved.restricted_projects.append(result.project_id)
        self.saved.project_reads.extend(
            item for item in result.project_reads if item not in self.saved.project_reads
        )
        self.saved.observations.extend(
            item for item in result.observations if item not in self.saved.observations
        )
        for proof in result.git_proofs:
            target = (
                self.saved.git_files
                if isinstance(proof, GitSourceProof)
                else self.saved.git_changes
            )
            if proof not in target:
                target.append(proof)
        self.web_pages.extend((result.project_id, item) for item in result.web_pages)
        for reference in result.external_sources:
            self.external[reference.source_id] = reference
            self.handles.setdefault(reference.source_id, "EXT_" + uuid4().hex[:16])
        for reference in result.assertions:
            key = str(reference.claim_id)
            self.assertions[key] = reference
            self.handles.setdefault(key, "CLM_" + uuid4().hex[:16])

        def visit(value):
            if isinstance(value, dict):
                key = value.get("source_id") or value.get("id")
                if str(key) in self.handles:
                    value["source_handle"] = self.handles[str(key)]
                for name, item in list(value.items()):
                    if name not in {"properties", "dialog_history"}:
                        visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(result)

    def inherit(self, guard: SavedContextGuard) -> None:
        """Старый ответ является материалом нынешнего, даже без выбранных цитат."""
        for name in (
            "origin_read_scopes",
            "projects",
            "restricted_projects",
            "project_reads",
            "internal",
            "observations",
            "git_files",
            "git_changes",
            "tasks",
            "documents",
        ):
            target = getattr(self.saved, name)
            target.extend(item for item in getattr(guard, name) if item not in target)

    def references(self, primary_handle=None):
        external = [
            item.model_copy(update={"is_primary": self.handles[key] == primary_handle})
            for key, item in self.external.items()
        ]
        assertions = [
            item.model_copy(update={"is_primary": self.handles[key] == primary_handle})
            for key, item in self.assertions.items()
        ]
        return {"external_sources": external[:20], "assertion_references": assertions[:20]}


class ContextEvidenceService:
    def __init__(
        self,
        *,
        scope: ProjectKnowledgeScopeFactory,
        task_scope: DocumentCatalogScopeFactory,
        documents: DocumentCatalogService,
        knowledge: ProjectKnowledgeService,
        git: GitSourceService,
    ) -> None:
        self.scope, self.knowledge, self.git = scope, knowledge, git
        self.task_scope = task_scope
        self.documents = documents

    @staticmethod
    def session_principal(user_id: int) -> Principal:
        """Только для уже проверенного локального UI user_id, не credential API."""
        return Principal(
            user_id=user_id,
            username="",
            last_name="",
            first_name="",
            middle_name=None,
            scope=ApiTokenScope.READ,
            via_api_token=False,
        )

    async def capture_project(
        self, *, project_id: int, principal: Principal
    ) -> ProjectReadObservation:
        """Private watch уже разрешённого legacy read, без нового права на карту."""
        async with self.scope() as db:
            current = await db.auth.refresh_principal(principal)
            await db.access.ensure_project_access(project_id=project_id, user_id=current.user_id)
            state = await db.knowledge.get_state(project_id)
            return ProjectReadObservation(
                project_id=project_id, generation=state["generation"] if state else 0
            )

    async def revalidate_after_action(
        self, evidence: ContextEvidence, *, principal: Principal
    ) -> None:
        """Own write не делает прежние неизменённые source versions устаревшими.

        Проверяем native hashes и remote ACL, затем каждую per-source identity.
        Неизвестное gen0 разрешено только у паспорта с native context_revision;
        legacy project-wide watches и изменённые sources не переписываются.
        """
        await self.validate(
            evidence.saved.model_copy(update={"observations": [], "project_reads": []}),
            principal=principal,
        )
        refreshed = []
        for previous in evidence.saved.observations:
            current = await self.knowledge.capture_observation(
                project_id=previous.project_id,
                principal=principal,
                source_ids=[source.source_id for source in previous.sources],
            )
            if current.generation != previous.generation:
                by_id = {source.source_id: source for source in current.sources}
                for source in previous.sources:
                    if by_id.get(source.source_id) != source:
                        raise ContextSelectionChangedError()
                    if source.generation == 0 and not (
                        source.source_id == f"project:{previous.project_id}"
                        and any(
                            item.project_id == previous.project_id
                            and item.source_id == source.source_id
                            for item in evidence.saved.internal
                        )
                    ):
                        raise ContextSelectionChangedError()
            refreshed.append(current)
        evidence.saved.observations = refreshed

    @staticmethod
    def _require_read_policy(saved: SavedContextGuard, principal: Principal) -> None:
        if (
            principal.api_key is not None
            and not saved.origin_read_scopes
            and not saved.restricted_projects
            and (saved.internal or saved.tasks or saved.documents or saved.project_reads)
        ):
            raise ContextSelectionChangedError()
        for scope in saved.origin_read_scopes:
            principal.require(scope)

    async def validate(self, saved: SavedContextGuard, *, principal: Principal) -> None:
        async with self.scope() as db:
            principal = await db.auth.refresh_principal(principal)
            self._require_read_policy(saved, principal)
        for expected in saved.project_reads:
            if (
                await self.capture_project(project_id=expected.project_id, principal=principal)
                != expected
            ):
                raise ContextSelectionChangedError()
        for item in saved.documents:
            current = await self.documents.get(
                document_ref=item.document_ref, user_id=principal.user_id, principal=principal
            )
            if document_signature(current) != item.document_hash:
                raise ContextSelectionChangedError()
        if saved.tasks:
            async with self.task_scope() as db:
                current = await db.auth.refresh_principal(principal)
                for item in saved.tasks:
                    await db.access.ensure_task_access(
                        task_id=item.task_id, user_id=current.user_id
                    )
                    task = await db.tasks.get_by_id(item.task_id)
                    if task is None or task_signature(task, item.projection) != item.task_hash:
                        raise ContextSelectionChangedError()
        for observation in saved.observations:
            await self.knowledge.validate_observation(
                project_id=observation.project_id, principal=principal, observation=observation
            )
        for project_id in saved.projects:
            proofs = [
                proof
                for proof in [*saved.git_files, *saved.git_changes]
                if proof.project_id == project_id
            ]
            if proofs:
                await self.git.validate_sources(
                    project_id=project_id, principal=principal, sources=proofs
                )
        # SQL после сетевой remote-ACL проверки: изменения во время сети тоже видны.
        async with self.scope() as db:
            current = await db.auth.refresh_principal(principal)
            self._require_read_policy(saved, current)
            if saved.restricted_projects:
                current.require("knowledge:read")
                current.require("projects:read")
            for project_id in saved.projects:
                await db.access.ensure_project_access(
                    project_id=project_id, user_id=current.user_id
                )
                items = [item for item in saved.internal if item.project_id == project_id]
                if items:
                    catalog = build_catalog(
                        project_id, await db.sources.get_project_rows(project_id)
                    )
                    for item in items:
                        source = catalog.sources.get(item.source_id)
                        if (
                            source is None
                            or source_signature(source, item.projection) != item.source_hash
                        ):
                            raise ContextSelectionChangedError()
            # Сверяем epochs ещё раз после remote ACL и SQL чтения.
        for observation in saved.observations:
            await self.knowledge.validate_observation(
                project_id=observation.project_id, principal=principal, observation=observation
            )

    async def present(
        self, values: list[dict], *, user_id: int | None, principal: Principal | None = None
    ) -> tuple[dict, bool]:
        """Ничего не удаляет из БД; body запрещён без проверки всех used sources."""
        guards = [value for value in values if value.get("reference_type") == "context_guard"]
        try:
            public = decode_answer_sources(values)
        except ValueError:
            return {"sources": [], "external_sources": [], "assertion_references": []}, False
        has_context = bool(guards or any(public.values()))
        if not has_context:
            return public, True
        try:
            if user_id is None or len(guards) != 1:
                raise ContextSelectionChangedError()
            saved = SavedContextGuard.model_validate(guards[0]["guard"])
            if principal is not None and principal.user_id != user_id:
                raise ContextSelectionChangedError()
            await self.validate(saved, principal=principal or self.session_principal(user_id))
        except (ServiceError, ValueError, KeyError):
            # Ни excerpt, ни title/path/URL недоступного источника в ответ не попадают.
            return {"sources": [], "external_sources": [], "assertion_references": []}, False
        for source in public["external_sources"]:
            if source.kind == "web_page":
                # В history это сохранённое наблюдение, не новый сетевой fetch.
                source.state = "stale"
        return public, True

    async def present_many(self, pages, *, user_id: int, principal: Principal | None = None):
        """Один foreground HTTP attempt budget на страницу history, включая все proofs."""
        async with self.git.read_budget():
            return [
                await self.present(values, user_id=user_id, principal=principal) for values in pages
            ]
