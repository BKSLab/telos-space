"""Краткий паспорт → релевантные источники → адресное дочитывание без новой RAG-системы."""

from src.exceptions.base import ServiceError
from src.exceptions.context_selection import (
    ContextSelectionArgumentsError,
    ContextSelectionChangedError,
)
from src.schemas.context_selection import ContextSelectionInput
from src.schemas.knowledge import AssertionReferenceSchema
from src.services.auth import Principal
from src.services.context_results import ContextReadResult, git_change_reference
from src.services.git_sources import GitSourceService
from src.services.knowledge_search import KnowledgeSearchService
from src.services.project_knowledge import ProjectKnowledgeService
from src.services.project_query import ProjectQueryService
from src.utils.project_context import project_context_values

ASPECT_FIELDS = {
    "goal": "goal",
    "current_state": "capabilities",
    "desired_change": "expected_result",
    "boundaries": "boundaries",
    "constraints": "constraints",
    "verification": "expected_result",
}


def claim_reference(project_id, item) -> AssertionReferenceSchema:
    """Карточка утверждения не объявляет его независимым доказательством."""
    return AssertionReferenceSchema(
        project_id=project_id,
        claim_id=item.id,
        revision=item.revision,
        text=(item.text or "")[:2400] or None,
        kind=item.kind,
        verification=item.verification,
        source_ids=item.source_ids[:30],
        state="unavailable"
        if not item.text
        else (
            "conflicting"
            if item.state == "conflicting"
            else ("current" if item.state == "current" else "stale")
        ),
    )


def map_summary(value) -> dict:
    """Короткая карта не дублирует тела evidence и private observation."""
    result = value.model_dump(
        mode="json",
        include={
            "project_id",
            "generation",
            "built_generation",
            "status",
            "total",
            "offset",
            "limit",
            "has_more",
            "next_offset",
        },
    )
    result["coverage"] = value.coverage.model_dump(
        mode="json",
        include={"method", "complete", "sources_total", "sources_pending", "sources_failed"},
    )
    result["gaps"] = [{"code": gap.code, "category": gap.category} for gap in value.gaps]
    result["read_claim"] = "telos_get_knowledge_claim"
    result["items"] = [
        {
            "id": str(item.id),
            "revision": item.revision,
            "kind": item.kind,
            "category": item.category,
            "state": item.state,
            "acceptance": item.acceptance,
            "verification": item.verification,
            "provenance": {
                "origin": item.provenance.origin,
                "independently_verified": item.provenance.independently_verified,
            },
            "text": (item.text or "")[:160] or None,
            "text_truncated": len(item.text or "") > 160,
            "source_ids": item.source_ids[:8],
            "conflicts_with": [str(key) for key in item.conflicts_with[:8]],
        }
        for item in value.items
    ]
    return result


class ContextSelectionService:
    def __init__(
        self,
        *,
        knowledge: ProjectKnowledgeService,
        query: ProjectQueryService,
        search: KnowledgeSearchService,
        git: GitSourceService,
    ) -> None:
        self.knowledge = knowledge
        self.query = query
        self.search = search
        self.git = git

    async def select(
        self,
        *,
        project_id: int,
        principal: Principal,
        request: ContextSelectionInput,
        request_text: str | None = None,
    ) -> ContextReadResult:
        """Читает через общие сервисы; scopes/ACL проверяет knowledge до/после внешних вызовов."""
        before = await self.knowledge.capture_observation(
            project_id=project_id, principal=principal, source_ids=[]
        )
        for quote in request.provided.values():
            if request_text is None or quote not in request_text:
                raise ContextSelectionArgumentsError()
        project = await self.query.get_project_overview(project_id=project_id)
        candidates = await self.search.search(
            project_id=project_id,
            query=request.question,
            entity_type=None,
            limit=request.limit,
        )
        knowledge = await self.knowledge.read_map(
            project_id=project_id,
            principal=principal,
            limit=request.limit,
            offset=request.map_offset,
        )
        relevant_tasks = list(request.task_ids)
        for candidate in candidates:
            source_id = candidate.get("source_id", "")
            identifier = (
                int(source_id.split(":")[1])
                if source_id.startswith("task:")
                else candidate.get("task_id")
            )
            if isinstance(identifier, int) and identifier > 0 and identifier not in relevant_tasks:
                relevant_tasks.append(identifier)
        completion, git_proofs, completion_gaps, external = [], [], [], []
        for task_id in relevant_tasks[:1]:
            try:
                links = await self.git.get_task_links(
                    project_id=project_id, principal=principal, task_id=task_id
                )
                page = links.model_dump(mode="json", exclude={"sources"})
                page["items"] = page["items"][:5]
                page["omitted_changes"] = max(0, len(links.items) - 5)
                page["partial"] = links.partial or bool(page["omitted_changes"])
                for item in page["items"]:
                    item["omitted_evidence"] = max(0, len(item["evidence"]) - 6)
                    item["evidence"] = item["evidence"][:6]
                    item["evidence_partial"] = item["evidence_partial"] or bool(
                        item["omitted_evidence"]
                    )
                    item["source_id"] = (
                        f"git_change:{item['change_id']}:{item['task_id']}:{item['id']}"
                    )
                page["read"] = {
                    "tool": "telos_list_task_git_links",
                    "arguments": {"task_id": task_id},
                }
                completion.append(page)
                git_proofs.extend(links.sources)
                external.extend(git_change_reference(project_id, item) for item in links.items[:5])
                if links.partial or any(item.evidence_partial for item in links.items):
                    completion_gaps.append(
                        {
                            "aspect": "completion_evidence",
                            "status": "partial_search",
                            "message": "Связанные PR/CI/deploy прочитаны лишь в указанном охвате; отсутствие подтверждения не означает отсутствие релиза.",
                            "task_id": task_id,
                        }
                    )
            except ServiceError as error:
                completion_gaps.append(
                    {
                        "aspect": "completion_evidence",
                        "status": "denied"
                        if error.status_code in {401, 403, 404}
                        else "unavailable",
                        "message": "Доказательства изменений сейчас недоступны; статус задачи не доказывает проверку или production.",
                        "task_id": task_id,
                    }
                )
        source_ids = {f"project:{project_id}", *(item["source_id"] for item in candidates)}
        for claim in knowledge.items:
            source_ids.update(claim.source_ids)
        observation = await self.knowledge.capture_observation(
            project_id=project_id,
            principal=principal,
            source_ids=sorted(source_ids),
        )
        if (
            before.generation != observation.generation
            or knowledge.generation != observation.generation
        ):
            raise ContextSelectionChangedError()
        values = project_context_values(project.description_sections, project.description)
        gaps = completion_gaps
        for aspect in dict.fromkeys(request.aspects or ASPECT_FIELDS):
            if aspect not in request.provided and not values[ASPECT_FIELDS[aspect]]:
                gaps.append(
                    {
                        "aspect": aspect,
                        "status": "missing",
                    }
                )
        if knowledge.status in {"pending", "updating"}:
            gaps.append(
                {
                    "aspect": "map",
                    "status": "processing",
                    "message": "Текущая карта ещё готовится; доступный SQL-источник можно читать отдельно.",
                }
            )
        if knowledge.built_generation != knowledge.generation:
            gaps.append(
                {
                    "aspect": "map",
                    "status": "stale",
                    "message": "Построенная карта не покрывает текущее поколение всех источников.",
                }
            )
        conflicts = [str(item.id) for item in knowledge.items if item.state == "conflicting"]
        if conflicts:
            gaps.append(
                {
                    "aspect": "sources",
                    "status": "conflicting",
                    "message": "Текущие утверждения расходятся; дочитайте evidence и не выбирайте удобную версию молча.",
                }
            )
        gaps.append(
            {
                "aspect": "search",
                "status": "partial_search",
                "message": "Это ранжированные кандидаты по конкретному запросу, а не исчерпывающая проверка отсутствия сведений.",
            }
        )
        if knowledge.has_more:
            gaps.append(
                {
                    "aspect": "map",
                    "status": "partial_search",
                    "message": "Показана страница карты; next_offset продолжает чтение.",
                }
            )
        return ContextReadResult(
            {
                "project": {
                    "id": project_id,
                    "key": project.summary.project_key,
                    "name": project.summary.name,
                    "context": project.context,
                },
                "provided": [
                    {"aspect": aspect, "quote": quote, "provenance": "current_request"}
                    for aspect, quote in request.provided.items()
                ],
                "map": map_summary(knowledge),
                "source_candidates": [
                    {
                        "source_id": item["source_id"],
                        "entity_type": item.get("entity_type"),
                        "title": str(item.get("title", ""))[:160],
                        "preview": str(item.get("excerpt") or item.get("text") or "")[:240],
                        "candidate_only": True,
                    }
                    for item in candidates
                ],
                "completion_evidence": completion,
                "completion_next_reads": [
                    {"tool": "telos_list_task_git_links", "arguments": {"task_id": task_id}}
                    for task_id in relevant_tasks[1:3]
                ],
                "completion_rule": "Proposed/merged, CI exact repository/SHA и current deploy exact SHA/environment — разные факты. Поздний CI и rollback меняют proof. Done/отчёт не доказывает production.",
                "gaps": gaps,
                "conflicts": conflicts,
                "next_reads": [
                    {
                        "tool": "telos_read_source",
                        "arguments": {
                            "source_id": item["source_id"],
                            "offset": (item.get("matched_locations") or [{"offset": 0}])[0].get(
                                "offset", 0
                            ),
                        },
                    }
                    for item in candidates
                ],
                "sufficiency": "question_specific",
                "writes_performed": False,
            },
            project_id=project_id,
            observations=(observation, knowledge.observation),
            git_proofs=tuple(git_proofs),
            external_sources=tuple(external),
            assertions=tuple(claim_reference(project_id, item) for item in knowledge.items),
        )
