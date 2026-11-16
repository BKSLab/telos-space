"""Тонкие ARIS/MCP-адаптеры общих сервисов, без собственных правил доступа."""

from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from src.agent.tools import AgentToolContext
from src.exceptions.agent_tools import AgentToolAccessError
from src.exceptions.context_selection import ContextSelectionChangedError
from src.schemas.git_sources import GitSourceProof
from src.schemas.knowledge import ExternalSourceReferenceSchema
from src.services.context_evidence import ContextEvidenceService
from src.services.context_results import ContextReadResult, WebPageObservation, git_change_reference
from src.services.context_selection import ContextSelectionService, claim_reference, map_summary
from src.services.git_sources import GitSourceService
from src.services.project_knowledge import ProjectKnowledgeService
from src.services.web_research import WebResearchService


def context_handler(name: str) -> Callable[..., Awaitable[dict]]:
    async def run(service, actor, parameters):
        return await service.execute(name, actor, parameters)

    return run


def git_reference(project_id, item) -> ExternalSourceReferenceSchema:
    return ExternalSourceReferenceSchema(
        source_id=item.source_id,
        kind="git_file",
        project_id=project_id,
        title=item.title[:512],
        url=item.source_url,
        excerpt=(getattr(item, "text", None) or getattr(item, "excerpt", ""))[:1200] or None,
        content_hash=item.text_hash,
        coverage="partial_snapshot" if item.partial else "snapshot",
        connection_id=item.connection_id,
        repository_id=item.repository_id,
        ref=item.ref,
        commit_sha=item.commit_sha,
        path=item.path,
        blob_sha=item.blob_sha,
        line_start=item.line_start,
        line_end=item.line_end,
        state="stale" if item.stale else "current",
    )


def web_reference(project_id, item) -> ExternalSourceReferenceSchema:
    return ExternalSourceReferenceSchema(
        source_id=item.source_id,
        kind="web_page",
        project_id=project_id,
        title=item.title,
        url=item.url,
        excerpt=item.text[:1200] or None,
        content_hash=item.content_hash,
        fetched_at=item.fetched_at,
        coverage=item.coverage,
    )


class AgentContextToolsService:
    def __init__(
        self,
        *,
        knowledge: ProjectKnowledgeService,
        git: GitSourceService,
        web: WebResearchService,
        selection: ContextSelectionService,
        evidence: ContextEvidenceService,
    ) -> None:
        self.knowledge, self.git, self.web, self.selection = knowledge, git, web, selection
        self.evidence = evidence

    async def execute(self, name: str, context: AgentToolContext, parameters: BaseModel) -> dict:
        if (
            context.principal is None
            or context.user_id != context.principal.user_id
            or context.project_id is None
        ):
            raise AgentToolAccessError("Контексту нужны проверенный участник и текущий проект.")
        project_id, principal = context.project_id, context.principal
        scope = dict(project_id=project_id, principal=principal)
        args = parameters.model_dump()
        if name == "telos_select_context":
            return await self.selection.select(
                **scope, request=parameters, request_text=context.request_text
            )
        if name == "telos_get_knowledge_map":
            value = await self.knowledge.read_map(**scope, **args)
            return ContextReadResult(
                map_summary(value),
                project_id=project_id,
                observations=(value.observation,),
                assertions=tuple(claim_reference(project_id, item) for item in value.items),
            )
        if name == "telos_get_knowledge_claim":
            before = await self.knowledge.capture_observation(**scope, source_ids=[])
            value = await self.knowledge.get_claim(**scope, **args)
            observation = await self.knowledge.capture_observation(
                **scope, source_ids=value.source_ids
            )
            if before.generation != observation.generation:
                raise ContextSelectionChangedError()
            return ContextReadResult(
                value.model_dump(mode="json"),
                project_id=project_id,
                observations=(observation,),
                assertions=(claim_reference(project_id, value),),
            )
        if name == "telos_list_git_connections":
            value = await self.git.list_connections(**scope, **args)
            return ContextReadResult(value.model_dump(mode="json"), project_id=project_id)
        if name == "telos_search_git_sources":
            args["connection_ids"] = tuple(args["connection_ids"])
            value = await self.git.search(**scope, **args)
            return ContextReadResult(
                value.model_dump(mode="json"),
                project_id=project_id,
                git_proofs=tuple(
                    GitSourceProof.model_validate(
                        item.model_dump(include=set(GitSourceProof.model_fields))
                    )
                    for item in value.items
                ),
                external_sources=tuple(git_reference(project_id, item) for item in value.items),
            )
        if name == "telos_read_git_file":
            value = await self.git.read_file(**scope, **args)
            return ContextReadResult(
                value.model_dump(mode="json"),
                project_id=project_id,
                git_proofs=(
                    GitSourceProof.model_validate(
                        value.model_dump(include=set(GitSourceProof.model_fields))
                    ),
                ),
                external_sources=(git_reference(project_id, value),),
            )
        if name == "telos_get_git_map":
            value = await self.git.get_map(**scope)
            return ContextReadResult(
                value.model_dump(mode="json", exclude={"sources"}),
                project_id=project_id,
                git_proofs=tuple(value.sources),
            )
        if name == "telos_list_task_git_links":
            value = await self.git.get_task_links(**scope, **args)
            return ContextReadResult(
                value.model_dump(mode="json", exclude={"sources"}),
                project_id=project_id,
                git_proofs=tuple(value.sources),
                external_sources=tuple(
                    git_change_reference(project_id, item) for item in value.items
                ),
            )
        if name in {"telos_request_web_search", "telos_request_web_page"}:
            if context.approval_id is None:
                raise AgentToolAccessError("Публичный ввод требует сохранённого решения человека.")
            if name == "telos_request_web_search":
                value = await self.web.approve_topic(
                    **scope, **args, approval_id=str(context.approval_id)
                )
            else:
                value = await self.web.approve_url(
                    **scope, **args, approval_id=str(context.approval_id)
                )
            return value.model_dump(mode="json")
        if name == "telos_search_web":
            value = await self.web.search(**scope, **args)
            return self._web_result(project_id, value.model_dump(mode="json"), value.items)
        if name == "telos_read_web_page":
            value = await self.web.read(**scope, **args)
            return self._web_result(project_id, value.model_dump(mode="json"), [value])
        raise AgentToolAccessError("Неизвестный инструмент контекста.")

    @staticmethod
    def _web_result(project_id, value, items):
        return ContextReadResult(
            value,
            project_id=project_id,
            external_sources=tuple(web_reference(project_id, item) for item in items),
            web_pages=tuple(
                WebPageObservation(
                    item.page_token, item.source_id, item.url, item.content_hash, item.coverage
                )
                for item in items
            ),
        )
