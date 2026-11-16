"""Сборка публичного исследования из lifespan-ресурсов и коротких DB-областей."""

from typing import Annotated

from fastapi import Depends

from src.db.session import async_session_factory
from src.dependencies.document_catalog import (
    DocumentCatalogServiceDep,
    build_document_catalog_scope,
)
from src.dependencies.http_client import KnowledgeRuntimeDep
from src.dependencies.settings import SettingsDep
from src.services.web_research import WebResearchService


def get_web_research_service(
    settings: SettingsDep,
    runtime: KnowledgeRuntimeDep,
    documents: DocumentCatalogServiceDep,
) -> WebResearchService:
    """Возвращает сервис без активного DB checkout на время сети."""
    return WebResearchService(
        scope=build_document_catalog_scope(
            session_factory=async_session_factory, settings=settings
        ),
        client=runtime.web_context,
        signer=runtime.web_grants,
        documents=documents,
        enabled=settings.web_research.web_research_enabled,
        search_enabled=bool(settings.web_research.brave_search_api_key.get_secret_value()),
    )


WebResearchServiceDep = Annotated[WebResearchService, Depends(get_web_research_service)]
