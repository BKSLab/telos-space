"""Смысловой поиск MCP: соединение с БД не удерживается внешним вызовом.

Обращение к эмбеддингам и Qdrant идёт по сети и занимает секунды. Если
делать его внутри области сессии, соединение с PostgreSQL всё это время
остаётся занятым, и пул исчерпывается на нескольких параллельных вызовах.
Поэтому здесь проверяется порядок: аутентификация и доступ — в короткой
области, внешний вызов — после её закрытия.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.app_state import (
    RUNTIME_STATE_KEY,
    SESSION_FACTORY_STATE_KEY,
    SETTINGS_STATE_KEY,
)
from src.exceptions.clients import ClientError
from src.mcp_server import context as ctx
from src.mcp_server import server as srv
from tests.unit.mcp_server.conftest import PROJECT_ID, make_principal, make_services


class ScopeTracker:
    """Фабрика сессий, помнящая, открыта ли сейчас область работы с БД."""

    def __init__(self) -> None:
        self.active = False
        self.opened = 0

    def __call__(self) -> "ScopeTracker":
        return self

    async def __aenter__(self) -> object:
        self.active = True
        self.opened += 1
        return object()

    async def __aexit__(self, *exc_info) -> bool:
        self.active = False
        return False


class RecordingRuntime:
    """Клиенты знаний, фиксирующие состояние DB-области в момент вызова."""

    def __init__(self, tracker: ScopeTracker, hits: list) -> None:
        self.tracker = tracker
        self.scope_active_during_call: list[bool] = []
        self.embedding_client = SimpleNamespace(get_embedding=self._embedding)
        self.qdrant_client = SimpleNamespace(search=self._search)
        self._hits = hits

    async def _embedding(self, text: str) -> list[float]:
        self.scope_active_during_call.append(self.tracker.active)
        return [0.1, 0.2]

    async def _search(self, **kwargs) -> list:
        self.scope_active_during_call.append(self.tracker.active)
        self.search_kwargs = kwargs
        return self._hits


class SearchContext:
    """Контекст вызова со state приложения и включённой базой знаний."""

    def __init__(self, runtime: object, tracker: ScopeTracker) -> None:
        self.headers = {"Authorization": "Bearer telos_token_test"}
        state = SimpleNamespace(
            **{
                RUNTIME_STATE_KEY: runtime,
                SETTINGS_STATE_KEY: SimpleNamespace(
                    knowledge=SimpleNamespace(
                        knowledge_enabled=True,
                        qdrant_score_threshold=0.42,
                        knowledge_chunk_target_chars=2200,
                        knowledge_chunk_overlap_chars=300,
                    )
                ),
                SESSION_FACTORY_STATE_KEY: tracker,
            }
        )
        self.request_context = SimpleNamespace(request=SimpleNamespace(state=state))


def hit(entity_type: str = "task", **overrides) -> SimpleNamespace:
    """Фрагмент ответа Qdrant."""
    payload = {
        "source_id": "task:100",
        "entity_type": entity_type,
        "task_key": "PROJ-142",
        "title": "Собрать отчёт",
        "text": "Решили считать отчёт по фактическим датам.",
    }
    payload.update(overrides)
    return SimpleNamespace(payload=payload, score=0.876_8)


@pytest.fixture
def search(monkeypatch: pytest.MonkeyPatch):
    """Готовит вызов инструмента с отслеживаемой областью сессии."""

    def install(hits: list | None = None):
        services = make_services()
        services.auth.resolve_principal.return_value = make_principal()
        monkeypatch.setattr(ctx, "build_tool_services", lambda **_: services)

        tracker = ScopeTracker()
        runtime = RecordingRuntime(tracker, [] if hits is None else hits)
        return SearchContext(runtime, tracker), tracker, runtime, services

    return install


async def test_semantic_search_returns_current_catalog_views(search) -> None:
    from src.knowledge.catalog import SourceType

    context, _, runtime, services = search([hit("document")])
    current = [{"source_id": "document:9", "text": "Актуальное решение", "next_offset": 1200}]
    services.query.search_knowledge.return_value = current
    result = await srv.search_project_knowledge(
        context, project_key="PROJ", query="отчёт", entity_type=SourceType.DOCUMENT
    )
    assert result == current
    services.query.search_knowledge.assert_awaited_once_with(
        project_id=PROJECT_ID,
        query="отчёт",
        semantic_hits=runtime._hits,
        entity_type=SourceType.DOCUMENT,
        limit=10,
        target_chars=2200,
        overlap_chars=300,
    )
    assert runtime.search_kwargs["entity_type"] == SourceType.DOCUMENT


async def test_search_keeps_external_calls_outside_db_and_falls_back_to_fts(search) -> None:
    context, tracker, runtime, services = search([hit()])
    services.query.search_knowledge.return_value = []
    await srv.search_project_knowledge(context, project_key="PROJ", query="отчёт")
    services.access.ensure_project_access.assert_awaited_once_with(project_id=PROJECT_ID, user_id=1)
    assert runtime.search_kwargs["score_threshold"] == 0.42
    assert runtime.scope_active_during_call == [False, False] and not tracker.active
    context, _, runtime, services = search()
    context.request_context.request.state.app_settings.knowledge.knowledge_enabled = False
    await srv.search_project_knowledge(context, project_key="PROJ", query="отчёт")
    assert runtime.scope_active_during_call == []
    assert services.query.search_knowledge.await_args.kwargs["semantic_hits"] == []
    context, _, runtime, services = search()
    runtime.embedding_client.get_embedding = AsyncMock(side_effect=ClientError("qdrant 503"))
    await srv.search_project_knowledge(context, project_key="PROJ", query="отчёт")
    assert services.query.search_knowledge.await_args.kwargs["semantic_hits"] == []


async def test_full_source_read_uses_the_same_project_access_check(search):
    from src.schemas.knowledge import KnowledgeReadRequest

    context, _, runtime, services = search()
    request = KnowledgeReadRequest(name="telos_read_source", source_id="document:9", offset=1200)
    services.query.read_knowledge.return_value = {"text": "Продолжение", "next_offset": None}
    result = await srv.read_project_knowledge(context, project_key="PROJ", request=request)
    assert result["text"] == "Продолжение"
    services.access.ensure_project_access.assert_awaited_once_with(project_id=PROJECT_ID, user_id=1)
    services.query.read_knowledge.assert_awaited_once_with(project_id=PROJECT_ID, request=request)
    assert runtime.scope_active_during_call == []
