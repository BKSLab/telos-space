"""Общая композиция context tools для HTTP worker и MCP."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.settings import Settings
from src.db.session import async_session_factory
from src.dependencies.document_catalog import build_document_catalog_scope
from src.dependencies.git_sources import build_git_source_service
from src.dependencies.http_client import KnowledgeRuntimeDep
from src.dependencies.project_knowledge import build_project_knowledge_service
from src.dependencies.scopes import build_project_knowledge_query_scope
from src.dependencies.settings import SettingsDep
from src.knowledge.catalog import digest
from src.knowledge.runtime import KnowledgeRuntime
from src.services.agent_context_tools import AgentContextToolsService
from src.services.context_evidence import ContextEvidenceService
from src.services.context_selection import ContextSelectionService
from src.services.document_catalog import DocumentCatalogService
from src.services.knowledge_search import KnowledgeSearchService
from src.services.project_query import ProjectQueryService
from src.services.web_research import WebResearchService
from src.storage.task_attachments import TaskAttachmentStorage


def build_agent_context_tools(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    runtime: KnowledgeRuntime,
) -> AgentContextToolsService:
    knowledge = build_project_knowledge_service(session_factory=session_factory, settings=settings)
    query = ProjectQueryService(
        scope=build_project_knowledge_query_scope(session_factory=session_factory)
    )
    scope = build_document_catalog_scope(session_factory=session_factory, settings=settings)
    documents = DocumentCatalogService(
        scope=scope,
        storage=TaskAttachmentStorage(settings.app.uploads_path),
        knowledge_enabled=settings.knowledge.knowledge_enabled,
        max_file_size=settings.app.project_file_max_size,
        embedding_signature=digest(
            [settings.embedding.embedding_model, settings.embedding.embedding_dim]
        ),
        commit_writes=False,
    )
    search = KnowledgeSearchService(
        query=query,
        embedding_client=runtime.embedding_client,
        qdrant_client=runtime.qdrant_client,
        enabled=settings.knowledge.knowledge_enabled,
        score_threshold=settings.knowledge.qdrant_score_threshold,
        target_chars=settings.knowledge.knowledge_chunk_target_chars,
        overlap_chars=settings.knowledge.knowledge_chunk_overlap_chars,
    )
    git = build_git_source_service(session_factory=session_factory, settings=settings)
    return AgentContextToolsService(
        knowledge=knowledge,
        git=git,
        web=WebResearchService(
            scope=scope,
            documents=documents,
            client=runtime.web_context,
            signer=runtime.web_grants,
            enabled=settings.web_research.web_research_enabled,
            search_enabled=bool(settings.web_research.brave_search_api_key.get_secret_value()),
        ),
        selection=ContextSelectionService(knowledge=knowledge, query=query, search=search, git=git),
        evidence=ContextEvidenceService(
            scope=knowledge.scope,
            task_scope=scope,
            documents=documents,
            knowledge=knowledge,
            git=git,
        ),
    )


def build_context_evidence_service(
    *, session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> ContextEvidenceService:
    knowledge = build_project_knowledge_service(session_factory=session_factory, settings=settings)
    scope = build_document_catalog_scope(session_factory=session_factory, settings=settings)
    documents = DocumentCatalogService(
        scope=scope,
        storage=TaskAttachmentStorage(settings.app.uploads_path),
        knowledge_enabled=settings.knowledge.knowledge_enabled,
        max_file_size=settings.app.project_file_max_size,
        embedding_signature=digest(
            [settings.embedding.embedding_model, settings.embedding.embedding_dim]
        ),
        commit_writes=False,
    )
    return ContextEvidenceService(
        scope=knowledge.scope,
        task_scope=scope,
        documents=documents,
        knowledge=knowledge,
        git=build_git_source_service(session_factory=session_factory, settings=settings),
    )


def get_agent_context_tools(
    settings: SettingsDep, runtime: KnowledgeRuntimeDep
) -> AgentContextToolsService:
    return build_agent_context_tools(
        session_factory=async_session_factory, settings=settings, runtime=runtime
    )


AgentContextToolsDep = Annotated[AgentContextToolsService, Depends(get_agent_context_tools)]
