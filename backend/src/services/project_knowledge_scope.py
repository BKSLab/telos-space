"""Именованные короткие DB-области карты, без глобальных настроек."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from src.repositories.knowledge_sources import KnowledgeSourcesRepository
from src.repositories.project_knowledge import ProjectKnowledgeRepository
from src.repositories.unit_of_work import UnitOfWork
from src.services.access import AccessService
from src.services.auth import AuthService


@dataclass(frozen=True, slots=True)
class ProjectKnowledgeUpdateScope:
    """Снимок/публикация фоновой обработки без пользовательской impersonation."""

    knowledge: ProjectKnowledgeRepository
    sources: KnowledgeSourcesRepository
    unit_of_work: UnitOfWork


@dataclass(frozen=True, slots=True)
class ProjectKnowledgeScope(ProjectKnowledgeUpdateScope):
    """Публичное чтение обязательно перепроверяет текущие credentials и ACL."""

    auth: AuthService
    access: AccessService


ProjectKnowledgeScopeFactory = Callable[[], AbstractAsyncContextManager[ProjectKnowledgeScope]]
ProjectKnowledgeUpdateScopeFactory = Callable[
    [], AbstractAsyncContextManager[ProjectKnowledgeUpdateScope]
]
