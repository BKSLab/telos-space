"""Проверки цикла фонового индексатора знаний.

Worker получает конфигурацию, очередь, фабрику индексатора и клиентов
конструктором, поэтому здесь ничего не подменяется через модульные
globals: дублёры передаются так же, как в приложении передаются
настоящие зависимости.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.db.models.knowledge_index_jobs import KnowledgeEntityType, KnowledgeIndexOperation
from src.exceptions.clients import VectorStoreClientError
from src.knowledge.project_map import MAP_JOB_ENTITY_ID
from src.knowledge.worker import KnowledgeWorker, WorkerConfig
from src.services.knowledge_index import KnowledgeIndexService, PreparedIndexAction
from src.services.knowledge_queue import JobOutcome, KnowledgeQueueService
from src.services.project_knowledge_updates import ProjectKnowledgeUpdatesService

CONFIG = WorkerConfig(
    poll_seconds=0.01,
    batch_size=8,
    max_attempts=3,
    retention=timedelta(days=7),
    cleanup_interval_seconds=3600,
)


def job(
    job_id: int,
    *,
    entity_type: KnowledgeEntityType = KnowledgeEntityType.TASK,
    operation: KnowledgeIndexOperation = KnowledgeIndexOperation.UPSERT,
) -> SimpleNamespace:
    """Задание очереди в том виде, в каком его отдаёт сервис очереди."""
    return SimpleNamespace(
        id=job_id,
        project_id=1,
        entity_id=str(job_id),
        entity_type=entity_type,
        operation=operation,
        attempts=1,
    )


def action(entity_id: int) -> PreparedIndexAction:
    from src.knowledge.catalog import build_catalog

    return PreparedIndexAction(project_id=1, catalog=build_catalog(1, {}))


class ScopeTracker:
    """Фабрика индексатора, помнящая, открыта ли сейчас DB-область."""

    def __init__(self, service: KnowledgeIndexService) -> None:
        self.service = service
        self.active = False
        self.opened = 0

    @asynccontextmanager
    async def __call__(self):
        self.active = True
        self.opened += 1
        try:
            yield self.service
        finally:
            self.active = False


def make_worker(
    *,
    service: KnowledgeIndexService | None = None,
    queue: KnowledgeQueueService | None = None,
    runtime: object | None = None,
    config: WorkerConfig = CONFIG,
    map_updates: ProjectKnowledgeUpdatesService | None = None,
) -> tuple[KnowledgeWorker, ScopeTracker]:
    """Собирает worker с наблюдаемыми зависимостями."""
    tracker = ScopeTracker(service or AsyncMock(spec=KnowledgeIndexService))
    worker = KnowledgeWorker(
        config=config,
        queue=queue or AsyncMock(spec=KnowledgeQueueService),
        index_service=tracker,
        runtime=runtime
        or SimpleNamespace(
            payload_indexes_backfill_pending=False,
            qdrant_client=SimpleNamespace(backfill_payload_indexes=AsyncMock()),
        ),
        map_updates=map_updates or AsyncMock(spec=ProjectKnowledgeUpdatesService),
    )
    return worker, tracker


async def test_map_stage_runs_without_semantic_clients_and_map_only_job_never_calls_them():
    for semantic_enabled in (False, True):
        service = AsyncMock(spec=KnowledgeIndexService)
        queue = AsyncMock(spec=KnowledgeQueueService)
        work = job(1)
        work.entity_id = MAP_JOB_ENTITY_ID
        queue.claim_next_batch.return_value = [work]
        updates = AsyncMock(spec=ProjectKnowledgeUpdatesService)
        worker, tracker = make_worker(service=service, queue=queue, map_updates=updates,
            config=replace(CONFIG, semantic_enabled=semantic_enabled))
        assert await worker._process_next()
        updates.process_project.assert_awaited_once_with(1)
        service.prepare.assert_not_awaited()
        assert tracker.opened == 0
        assert queue.finish.await_args.args[0] == [JobOutcome(job_id=1, chunks_count=0)]
    # Смешанная project batch сохраняет semantic source work после map marker.
    queue = AsyncMock(spec=KnowledgeQueueService)
    marker, source_job = job(1), job(2)
    marker.entity_id = MAP_JOB_ENTITY_ID
    queue.claim_next_batch.return_value = [marker, source_job]
    service = AsyncMock(spec=KnowledgeIndexService)
    service.prepare.return_value = action(2)
    service.execute_prepared.return_value = 2
    worker, _ = make_worker(service=service, queue=queue)
    await worker._process_next()
    service.prepare.assert_awaited_once_with(source_job)


def test_worker_config_is_taken_from_settings_once() -> None:
    """Конфигурация цикла снимается с настроек и дальше неизменна."""
    settings = SimpleNamespace(
        knowledge=SimpleNamespace(
            knowledge_index_poll_seconds=5,
            knowledge_embedding_batch_size=16,
            knowledge_index_max_attempts=4,
            knowledge_job_retention_days=14,
            knowledge_enabled=True,
        )
    )

    config = WorkerConfig.from_settings(settings)

    assert (config.poll_seconds, config.batch_size, config.max_attempts) == (5, 16, 4)
    assert config.retention == timedelta(days=14)
    with pytest.raises(AttributeError):
        config.batch_size = 1


async def test_startup_returns_interrupted_jobs_and_purges_old_ones() -> None:
    """При старте прерванные задания возвращаются, старые успешные удаляются."""
    stop_event = asyncio.Event()
    stop_event.set()
    queue = AsyncMock(spec=KnowledgeQueueService)
    worker, _ = make_worker(queue=queue)

    await worker.run(stop_event)

    queue.reset_interrupted.assert_awaited_once_with()
    queue.purge_succeeded.assert_awaited_once_with(retention=CONFIG.retention)


async def test_external_calls_run_after_the_database_scope_is_closed() -> None:
    """И одиночное задание, и пачка задач обращаются наружу с закрытой областью базы."""
    # Внешний вызов одиночного задания идёт без открытой DB-области.
    service = AsyncMock(spec=KnowledgeIndexService)
    worker, tracker = make_worker(service=service)

    async def prepare(_job) -> PreparedIndexAction:
        assert tracker.active is True
        return action(1)

    async def execute_prepared(_action) -> int:
        assert tracker.active is False
        return 4

    service.prepare.side_effect = prepare
    service.execute_prepared.side_effect = execute_prepared

    result = await worker._prepare_and_execute_job(job(1))

    assert result.error is None
    assert result.chunks_count == 4
    assert tracker.opened == 2
    service.extract.assert_awaited_once()
    service.persist_extractions.assert_awaited_once()


async def test_each_summary_is_saved_before_the_next_external_call():
    service = AsyncMock(spec=KnowledgeIndexService)
    worker, tracker = make_worker(service=service)
    service.prepare.return_value = action(1)
    service.execute_prepared.return_value = 2

    async def summaries(_action):
        assert not tracker.active
        yield {"source_id": "document:1", "summary": "Первое описание"}
        service.persist_summary.assert_awaited_once()
        assert not tracker.active
        yield {"source_id": "document:2", "summary": "Второе описание"}

    async def persist(_summary):
        assert tracker.active

    service.summarize = summaries
    service.persist_summary.side_effect = persist
    result = await worker._prepare_and_execute_job(job(1))
    assert result.error is None
    assert service.persist_summary.await_count == 2
    assert tracker.opened == 4


async def test_worker_persists_outcomes_and_survives_failures() -> None:
    """Итоги уходят через сервис очереди, ошибка сохраняется с текстом, отмена не считается провалом, пустая очередь не считается работой, сбой итерации не останавливает цикл."""
    # Статусы пачки сохраняет сервис очереди, а не сам цикл.
    service = AsyncMock(spec=KnowledgeIndexService)
    service.prepare.return_value = action(1)
    service.execute_prepared.return_value = 5
    queue = AsyncMock(spec=KnowledgeQueueService)
    queue.claim_next_batch.return_value = [job(1), job(2)]
    worker, _ = make_worker(service=service, queue=queue)

    assert await worker._process_next() is True

    queue.claim_next_batch.assert_awaited_once_with(limit=CONFIG.batch_size)
    outcomes, kwargs = queue.finish.await_args.args[0], queue.finish.await_args.kwargs
    assert outcomes == [
        JobOutcome(job_id=1, chunks_count=5),
        JobOutcome(job_id=2, chunks_count=5),
    ]
    assert kwargs == {"max_attempts": CONFIG.max_attempts}
    # Неуспешное задание уходит в очередь с текстом ошибки.
    service = AsyncMock(spec=KnowledgeIndexService)
    service.prepare.side_effect = RuntimeError("документ недоступен")
    queue = AsyncMock(spec=KnowledgeQueueService)
    queue.claim_next_batch.return_value = [job(1, entity_type=KnowledgeEntityType.DOCUMENT)]
    worker, _ = make_worker(service=service, queue=queue)

    assert await worker._process_next() is True

    outcomes = queue.finish.await_args.args[0]
    assert outcomes == [JobOutcome(job_id=1, error="документ недоступен")]
    # Остановка приложения не тратит попытку задания. Иначе каждый рестарт приближал бы задание к статусу FAILED, хотя ошибки индексации не было.
    service = AsyncMock(spec=KnowledgeIndexService)
    service.prepare.side_effect = asyncio.CancelledError()
    queue = AsyncMock(spec=KnowledgeQueueService)
    worker, _ = make_worker(service=service, queue=queue)

    with pytest.raises(asyncio.CancelledError):
        await worker._prepare_and_execute_job(job(1))

    queue.finish.assert_not_awaited()
    # Пустая очередь не считается обработкой и не трогает индексатор.
    queue = AsyncMock(spec=KnowledgeQueueService)
    queue.claim_next_batch.return_value = []
    worker, tracker = make_worker(queue=queue)

    assert await worker._process_next() is False

    assert tracker.opened == 0
    queue.finish.assert_not_awaited()
    # Сбой одной итерации логируется, но не выносит цикл наружу.
    stop_event = asyncio.Event()
    queue = AsyncMock(spec=KnowledgeQueueService)
    attempts = {"count": 0}

    async def fail_then_stop(**_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("очередь недоступна")
        stop_event.set()
        return []

    queue.claim_next_batch.side_effect = fail_then_stop
    worker, _ = make_worker(queue=queue)

    await worker.run(stop_event)

    assert attempts["count"] == 2


async def test_payload_index_backfill_retries_until_it_succeeds() -> None:
    """Отложенный backfill повторяется, пока Qdrant недоступен, и прекращается после первого успеха."""
    # Отложенный backfill повторяется до первого успеха и больше не идёт.
    backfill = AsyncMock(side_effect=[VectorStoreClientError("offline"), 2])
    runtime = SimpleNamespace(
        payload_indexes_backfill_pending=True,
        qdrant_client=SimpleNamespace(backfill_payload_indexes=backfill),
    )
    worker, _ = make_worker(runtime=runtime)

    await worker._backfill_payload_indexes_if_pending()
    assert runtime.payload_indexes_backfill_pending is True

    await worker._backfill_payload_indexes_if_pending()
    assert runtime.payload_indexes_backfill_pending is False

    await worker._backfill_payload_indexes_if_pending()
    assert backfill.await_count == 2
    # Недоступный Qdrant не прерывает цикл: очередь продолжает разбираться.
    stop_event = asyncio.Event()
    backfill = AsyncMock(side_effect=VectorStoreClientError("offline"))
    runtime = SimpleNamespace(
        payload_indexes_backfill_pending=True,
        qdrant_client=SimpleNamespace(backfill_payload_indexes=backfill),
    )
    queue = AsyncMock(spec=KnowledgeQueueService)

    async def claim_then_stop(**_kwargs):
        stop_event.set()
        return []

    queue.claim_next_batch.side_effect = claim_then_stop
    worker, _ = make_worker(queue=queue, runtime=runtime)

    await worker.run(stop_event)

    backfill.assert_awaited_once_with()
    queue.claim_next_batch.assert_awaited_once_with(limit=CONFIG.batch_size)
    assert runtime.payload_indexes_backfill_pending is True
