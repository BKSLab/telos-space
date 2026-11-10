"""Область базы закрыта во время внешнего вызова.

Это главный инвариант этапа 6. Обычные тесты его не видят: сценарий
отработает одинаково и с открытым соединением, и без него. Поэтому
область здесь отслеживается явно, а дублёр внешнего клиента проверяет
её состояние в момент своего вызова.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.clients.llm import LlmClient
from src.exceptions.ai import AiProviderError
from src.exceptions.clients import LlmClientError
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.wbs_nodes import WbsNodesRepository
from src.schemas.wbs_suggestion import WbsSuggestionSchema
from src.services.db_scope import WbsSuggestionScope
from src.services.knowledge_events import KnowledgeEvents
from src.services.wbs_suggestion import WbsSuggestionService

PROJECT = SimpleNamespace(
    id=1,
    key="PROJ",
    name="Проект",
    description_md=None,
)
TASK = SimpleNamespace(
    id=11,
    number=1,
    title="Задача",
    priority=SimpleNamespace(value="MEDIUM"),
    stage_id=1,
    wbs_node_id=None,
)


@dataclass
class ScopeTracker:
    """Считает открытия области и помнит, открыта ли она сейчас."""

    active: bool = False
    opened: int = 0
    states_during_external_call: list[bool] = field(default_factory=list)


def build_service(tracker: ScopeTracker) -> WbsSuggestionService:
    """Собирает сервис, у которого область отслеживается трекером."""
    projects = AsyncMock(spec=ProjectsRepository)
    projects.get_by_id.return_value = PROJECT
    nodes = AsyncMock(spec=WbsNodesRepository)
    nodes.get_by_project.return_value = []
    tasks = AsyncMock(spec=TasksRepository)
    tasks.get_by_project.return_value = [TASK]
    stages = AsyncMock(spec=ProjectStagesRepository)
    stages.get_by_project.return_value = [SimpleNamespace(id=1, name="Бэклог")]

    db = WbsSuggestionScope(
        projects=projects,
        wbs_nodes=nodes,
        tasks=tasks,
        stages=stages,
        activity=AsyncMock(spec=TaskActivityRepository),
        knowledge_events=AsyncMock(spec=KnowledgeEvents),
        unit_of_work=AsyncMock(spec=UnitOfWork),
    )

    @asynccontextmanager
    async def scope():
        tracker.opened += 1
        tracker.active = True
        try:
            yield db
        finally:
            tracker.active = False

    llm_client = AsyncMock(spec=LlmClient)

    async def answer(**_kwargs) -> WbsSuggestionSchema:
        # Дублёр модели наблюдает состояние области в момент вызова.
        tracker.states_during_external_call.append(tracker.active)
        return WbsSuggestionSchema()

    llm_client.get_structured_response.side_effect = answer
    return WbsSuggestionService(scope=scope, llm_client=llm_client)


@pytest.mark.asyncio
async def test_llm_is_called_after_the_database_scope_is_closed() -> None:
    """Внешний вызов начинается уже после закрытия области базы.

    Иначе соединение с PostgreSQL оставалось бы занятым всё время
    ожидания модели — сотни секунд в худшем случае.
    """
    tracker = ScopeTracker()
    service = build_service(tracker)

    await service.suggest(project_id=1)

    assert tracker.states_during_external_call == [False], (
        "Модель вызвана при открытой области базы."
    )
    assert tracker.active is False


@pytest.mark.asyncio
async def test_read_phase_opens_exactly_one_scope() -> None:
    """Подготовка снимка укладывается в одну короткую область."""
    tracker = ScopeTracker()
    service = build_service(tracker)

    await service.suggest(project_id=1)

    assert tracker.opened == 1


@pytest.mark.asyncio
async def test_scope_is_released_even_when_the_model_fails() -> None:
    """Сбой модели не оставляет область открытой.

    Незакрытая область означала бы соединение, навсегда выведенное из
    пула: несколько таких отказов — и пул пуст.
    """
    tracker = ScopeTracker()
    service = build_service(tracker)
    service.llm_client.get_structured_response.side_effect = LlmClientError("недоступен")

    with pytest.raises(AiProviderError):
        await service.suggest(project_id=1)

    assert tracker.active is False


@pytest.mark.asyncio
async def test_service_holds_no_session_or_repositories() -> None:
    """Сервис владеет только фабрикой области и клиентом.

    Ссылка на репозиторий означала бы, что сессия живёт столько же,
    сколько сам сервис, и короткая область ничего бы не дала.
    """
    tracker = ScopeTracker()
    service = build_service(tracker)

    attributes = set(vars(service))

    assert attributes == {"scope", "llm_client"}
