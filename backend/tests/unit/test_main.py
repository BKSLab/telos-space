"""Проверки composition root: создание и освобождение ресурсов приложения."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

import src.main as main_module
from src.core.app_state import RUNTIME_STATE_KEY, SETTINGS_STATE_KEY
from src.core.settings import FileProcessingSettings
from src.exceptions.clients import VectorStoreClientError


async def test_validation_logs_do_not_include_private_chat_content(monkeypatch):
    """Системный handler сохраняет диагностику без входного текста сообщения."""
    warning = Mock()
    monkeypatch.setattr(main_module.logger, "warning", warning)
    private = "Приватная переписка, которой не должно быть в логах"
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/projects/1/chat/messages",
            "headers": [],
            "query_string": b"",
        }
    )
    error = RequestValidationError(
        [
            {
                "loc": ("body", "content"),
                "type": "string_too_long",
                "msg": "Too long",
                "input": private,
            }
        ]
    )
    response = await main_module.validation_exception_handler(request, error)
    assert response.status_code == 422
    assert "string_too_long" in str(warning.call_args)
    assert private not in str(warning.call_args)


@pytest.fixture
def composition(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Подменяет тяжёлые ресурсы и возвращает наблюдаемые за ними дублёры."""
    db_session = object()

    @asynccontextmanager
    async def session_factory():
        yield db_session

    http_client = object()
    qdrant_sdk = object()
    runtime = SimpleNamespace(
        qdrant_client=SimpleNamespace(backfill_payload_indexes=AsyncMock()),
        vision=object(),
    )
    created = SimpleNamespace(http=0, qdrant=0, runtime=0)

    def create_http_client(settings):
        created.http += 1
        return http_client

    def create_qdrant_client(settings):
        created.qdrant += 1
        return qdrant_sdk

    def build_runtime(*, settings, http_client, qdrant_client):
        created.runtime += 1
        return runtime

    settings = SimpleNamespace(
        app=SimpleNamespace(app_name="Test app", mcp_path="/mcp", uploads_path=Path("uploads")),
        knowledge=SimpleNamespace(knowledge_enabled=True),
        file_processing=FileProcessingSettings(_env_file=None),
        agent=SimpleNamespace(agent_concurrency=2),
        chat=SimpleNamespace(chat_cleanup_seconds=60, chat_draft_retention_hours=24),
    )
    close_runtime = AsyncMock()
    dispose_engine = AsyncMock()
    check_db = AsyncMock()
    worker_started = asyncio.Event()
    worker_built = SimpleNamespace(session_factory=None, settings=None, runtime=None)

    class FakeWorker:
        """Индексатор, собранный composition root-ом приложения."""

        async def run(self, stop_event: asyncio.Event) -> None:
            worker_started.set()
            await stop_event.wait()

    def build_worker(*, session_factory, settings, runtime):
        worker_built.session_factory = session_factory
        worker_built.settings = settings
        worker_built.runtime = runtime
        return FakeWorker()

    monkeypatch.setattr(main_module, "async_session_factory", session_factory)
    monkeypatch.setattr(main_module, "check_db_connection", check_db)
    monkeypatch.setattr(main_module, "create_http_client", create_http_client)
    monkeypatch.setattr(main_module, "create_qdrant_client", create_qdrant_client)
    monkeypatch.setattr(main_module, "build_knowledge_runtime", build_runtime)
    monkeypatch.setattr(main_module, "build_knowledge_worker", build_worker)
    agent_started = asyncio.Event()
    git_started = asyncio.Event()
    files_started = asyncio.Event()
    chat_files_started = asyncio.Event()
    chat_files_stopped = asyncio.Event()
    chat_files = object()
    build_chat_files = Mock(return_value=chat_files)
    chat_file_options = {}

    class FakeChatFileWorker:
        def __init__(self, **kwargs):
            chat_file_options.update(kwargs)

        async def run(self, stop_event):
            chat_files_started.set()
            try:
                await stop_event.wait()
            finally:
                chat_files_stopped.set()

    monkeypatch.setattr(main_module, "build_agent_chat_files_service", build_chat_files)
    monkeypatch.setattr(main_module, "ChatFilePreparationWorker", FakeChatFileWorker)

    class FakeFileWorker:
        async def run(self, stop_event):
            files_started.set()
            await stop_event.wait()

    monkeypatch.setattr(
        main_module, "build_file_processing_worker", lambda **kwargs: FakeFileWorker()
    )

    class FakeAgentWorker:
        async def run(self, stop_event):
            agent_started.set()
            await stop_event.wait()

    monkeypatch.setattr(main_module, "build_agent_worker", lambda **kwargs: FakeAgentWorker())

    class FakeGitWorker:
        async def run(self, stop_event):
            git_started.set()
            await stop_event.wait()

    monkeypatch.setattr(main_module, "build_git_source_service", lambda **kwargs: FakeGitWorker())
    monkeypatch.setattr(main_module, "close_knowledge_runtime", close_runtime)
    monkeypatch.setattr(main_module, "engine", SimpleNamespace(dispose=dispose_engine))
    monkeypatch.setattr(main_module, "settings", settings)
    monkeypatch.setattr(
        main_module,
        "build_chat_runtime",
        lambda **kwargs: SimpleNamespace(start=AsyncMock(), close=AsyncMock()),
    )
    attention_runtime = SimpleNamespace(start=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr(
        main_module, "build_task_attention_runtime", lambda **kwargs: attention_runtime
    )

    # Настоящий session manager MCP запускается один раз на экземпляр, а
    # тестов lifespan несколько: подменяем его на новый контекст в каждом.
    @asynccontextmanager
    async def session_manager_run():
        yield

    monkeypatch.setattr(
        main_module,
        "mcp_server",
        SimpleNamespace(session_manager=SimpleNamespace(run=session_manager_run)),
    )

    return SimpleNamespace(
        agent_worker_started=agent_started,
        git_worker_started=git_started,
        file_worker_started=files_started,
        chat_file_worker_started=chat_files_started,
        chat_file_worker_stopped=chat_files_stopped,
        build_chat_files=build_chat_files,
        chat_files=chat_files,
        chat_file_options=chat_file_options,
        db_session=db_session,
        runtime=runtime,
        settings=settings,
        created=created,
        close_runtime=close_runtime,
        dispose_engine=dispose_engine,
        check_db=check_db,
        worker_started=worker_started,
        worker_built=worker_built,
        attention_runtime=attention_runtime,
    )


async def test_lifespan_creates_and_closes_resources_exactly_once(
    composition: SimpleNamespace,
) -> None:
    """Каждый сетевой ресурс создаётся и закрывается ровно один раз."""
    async with main_module.lifespan(FastAPI()):
        await asyncio.wait_for(composition.worker_started.wait(), timeout=1)
        await asyncio.wait_for(composition.chat_file_worker_started.wait(), timeout=1)

    assert composition.created.http == 1
    assert composition.created.qdrant == 1
    assert composition.created.runtime == 1
    composition.check_db.assert_awaited_once_with(db_session=composition.db_session)
    composition.close_runtime.assert_awaited_once_with(composition.runtime)
    composition.dispose_engine.assert_awaited_once_with()
    composition.attention_runtime.start.assert_awaited_once_with()
    composition.attention_runtime.close.assert_awaited_once_with()
    assert composition.chat_file_worker_stopped.is_set()


async def test_lifespan_gives_the_worker_its_dependencies_explicitly(
    composition: SimpleNamespace,
) -> None:
    """Индексатор получает ресурсы приложения, а не ищет их сам.

    Пока worker брал фабрику сессий и клиентов из модульных globals, его
    зависимости не были видны ни в сигнатуре, ни в composition root.
    """
    async with main_module.lifespan(FastAPI()):
        await asyncio.wait_for(composition.worker_started.wait(), timeout=1)

    assert composition.worker_built.runtime is composition.runtime
    assert composition.worker_built.settings is composition.settings
    assert composition.worker_built.session_factory is not None
    composition.build_chat_files.assert_called_once_with(
        session_factory=composition.worker_built.session_factory,
        settings=composition.settings,
        vision=composition.runtime.vision,
    )
    assert composition.chat_file_options == {
        "files": composition.chat_files,
        "poll_seconds": composition.settings.file_processing.file_processing_poll_seconds,
        "concurrency": 2,
        "cleanup_seconds": 60,
        "retention_hours": 24,
    }


async def test_lifespan_publishes_resources_in_request_state(
    composition: SimpleNamespace,
) -> None:
    """Ресурсы попадают в состояние запроса, доступное и MCP-транспорту."""
    async with main_module.lifespan(FastAPI()) as state:
        assert state[RUNTIME_STATE_KEY] is composition.runtime
        assert state[SETTINGS_STATE_KEY] is composition.settings
        assert state["task_attention_runtime"] is composition.attention_runtime


async def test_lifespan_starts_application_when_qdrant_is_unavailable(
    composition: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Недоступный Qdrant не блокирует запуск основного трекера."""
    backfill = AsyncMock(side_effect=VectorStoreClientError("Qdrant offline"))
    composition.runtime.qdrant_client.backfill_payload_indexes = backfill
    warning = Mock()
    monkeypatch.setattr(main_module.logger, "warning", warning)

    async with main_module.lifespan(FastAPI()):
        await asyncio.wait_for(composition.worker_started.wait(), timeout=1)

    backfill.assert_awaited_once_with()
    assert composition.runtime.payload_indexes_backfill_pending is True
    composition.close_runtime.assert_awaited_once_with(composition.runtime)
    warning.assert_called_once_with(
        "⚠️ Qdrant недоступен при старте; backfill payload-индексов отложен.",
        exc_info=True,
    )


async def test_lifespan_cancels_a_stuck_worker_and_still_frees_resources(
    monkeypatch: pytest.MonkeyPatch,
    composition: SimpleNamespace,
) -> None:
    """Зависший индексатор снимается, ресурсы всё равно освобождаются.

    Без отмены остановка приложения ждала бы worker неограниченно долго,
    а клиенты и пул соединений остались бы открытыми.
    """
    cancelled = asyncio.Event()

    class StuckWorker:
        """Индексатор, не реагирующий на признак остановки."""

        async def run(self, stop_event: asyncio.Event) -> None:
            composition.worker_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    monkeypatch.setattr(
        main_module,
        "build_knowledge_worker",
        lambda **_kwargs: StuckWorker(),
    )

    async with main_module.lifespan(FastAPI()):
        await asyncio.wait_for(composition.worker_started.wait(), timeout=1)

    assert cancelled.is_set()
    composition.close_runtime.assert_awaited_once_with(composition.runtime)
    composition.dispose_engine.assert_awaited_once_with()


async def test_lifespan_updates_map_without_semantic_index_when_knowledge_is_disabled(
    composition: SimpleNamespace,
) -> None:
    """Карта работает без семантического индекса, Qdrant backfill не вызывается."""
    composition.settings.knowledge.knowledge_enabled = False

    async with main_module.lifespan(FastAPI()):
        await asyncio.wait_for(composition.agent_worker_started.wait(), timeout=1)
        await asyncio.wait_for(composition.git_worker_started.wait(), timeout=1)
        await asyncio.wait_for(composition.file_worker_started.wait(), timeout=1)
        await asyncio.wait_for(composition.chat_file_worker_started.wait(), timeout=1)

    assert composition.worker_started.is_set() is True
    assert composition.worker_built.runtime is composition.runtime
    composition.runtime.qdrant_client.backfill_payload_indexes.assert_not_awaited()
    composition.dispose_engine.assert_awaited_once_with()


async def test_file_processing_starts_before_qdrant_backfill(composition):
    """Даже ожидающий Qdrant не задерживает независимую обработку файлов."""

    async def backfill():
        await asyncio.wait_for(composition.file_worker_started.wait(), timeout=1)
        await asyncio.wait_for(composition.chat_file_worker_started.wait(), timeout=1)

    composition.runtime.qdrant_client.backfill_payload_indexes.side_effect = backfill
    async with main_module.lifespan(FastAPI()):
        assert composition.file_worker_started.is_set()
