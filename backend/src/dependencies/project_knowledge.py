"""Композиция карты: request-сценарии и worker используют короткие сессии."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.settings import Settings
from src.db.session import async_session_factory
from src.dependencies.document_catalog import assemble_document_catalog_scope
from src.dependencies.settings import SettingsDep
from src.repositories.knowledge_sources import KnowledgeSourcesRepository
from src.repositories.project_knowledge import ProjectKnowledgeRepository
from src.repositories.unit_of_work import UnitOfWork
from src.services.project_knowledge import ProjectKnowledgeService
from src.services.project_knowledge_scope import (
    ProjectKnowledgeScope,
    ProjectKnowledgeScopeFactory,
    ProjectKnowledgeUpdateScope,
    ProjectKnowledgeUpdateScopeFactory,
)


def build_project_knowledge_scope(
    *, session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> ProjectKnowledgeScopeFactory:
    @asynccontextmanager
    async def scope() -> AsyncIterator[ProjectKnowledgeScope]:
        async with session_factory() as session:
            unit_of_work = UnitOfWork(session)
            common = assemble_document_catalog_scope(
                session=session, settings=settings, unit_of_work=unit_of_work
            )
            yield ProjectKnowledgeScope(
                knowledge=ProjectKnowledgeRepository(session),
                sources=KnowledgeSourcesRepository(session),
                unit_of_work=unit_of_work,
                auth=common.auth,
                access=common.access,
            )

    return scope


def build_project_knowledge_update_scope(
    *, session_factory: async_sessionmaker[AsyncSession]
) -> ProjectKnowledgeUpdateScopeFactory:
    @asynccontextmanager
    async def scope() -> AsyncIterator[ProjectKnowledgeUpdateScope]:
        async with session_factory() as session:
            yield ProjectKnowledgeUpdateScope(
                knowledge=ProjectKnowledgeRepository(session),
                sources=KnowledgeSourcesRepository(session),
                unit_of_work=UnitOfWork(session),
            )

    return scope


def build_project_knowledge_service(
    *, session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> ProjectKnowledgeService:
    return ProjectKnowledgeService(
        scope=build_project_knowledge_scope(session_factory=session_factory, settings=settings)
    )


def get_project_knowledge_service(settings: SettingsDep) -> ProjectKnowledgeService:
    return build_project_knowledge_service(session_factory=async_session_factory, settings=settings)


ProjectKnowledgeServiceDep = Annotated[
    ProjectKnowledgeService, Depends(get_project_knowledge_service)
]
