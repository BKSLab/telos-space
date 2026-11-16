"""Composition root и lifecycle транспорта общего чата."""

import asyncio
import json
import logging
from collections import Counter
from time import monotonic

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from src.clients.chat_redis import ChatRedisClient
from src.core.settings import Settings
from src.dependencies.scopes import build_chat_scope
from src.exceptions.project_chats import ChatServiceError
from src.realtime.chat_hub import ChatHub
from src.services.chat_attachments import ChatAttachmentsService
from src.services.chat_commands import ChatCommandsService
from src.services.chat_delivery import ChatDeliveryService
from src.services.chat_presenter import ChatPresenter
from src.services.project_chats import ProjectChatService
from src.storage.chat_attachments import ChatAttachmentStorage

logger = logging.getLogger(__name__)
CHAT_RUNTIME_KEY = "project_chat_runtime"


class ChatRuntime:
    """Один Redis transport и набор фоновых задач на процесс FastAPI."""

    def __init__(
        self, *, transport, redis, chat, commands, files, delivery, hub, settings, pool, db_metrics
    ):
        self.transport, self.redis, self.chat = transport, redis, chat
        self.commands, self.files, self.delivery = commands, files, delivery
        self.hub, self.settings = hub, settings
        self.ready = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.pool, self.db_metrics = pool, db_metrics

    async def start(self) -> None:
        """Fail-fast проверка обязательного Redis и запуск подписки до приёма сокетов."""
        await self.transport.ping()
        self.tasks = [
            asyncio.create_task(self._listen(), name="chat-redis-listener"),
            asyncio.create_task(self._publish(), name="chat-outbox"),
            asyncio.create_task(self._sweep(), name="chat-presence"),
        ]
        await asyncio.wait_for(self.ready.wait(), timeout=10)
        logger.info("✅ Общий чат: Redis subscription и outbox запущены.")

    async def close(self) -> None:
        """Останавливает фоновые задачи до освобождения пула PostgreSQL."""
        self.ready.clear()
        self.hub.close_all(1001)
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.transport.aclose()

    async def _listen(self):
        while True:
            try:
                async with self.transport.pubsub() as subscription:
                    await subscription.psubscribe("project_chat:*")
                    # Ждём подтверждение Redis, а не только отправку SUBSCRIBE в сокет.
                    while True:
                        confirmation = await subscription.get_message(
                            ignore_subscribe_messages=False, timeout=3
                        )
                        if confirmation and confirmation["type"] == "psubscribe":
                            break
                    self.ready.set()
                    while True:
                        message = await subscription.get_message(
                            ignore_subscribe_messages=True, timeout=1.0
                        )
                        if message is not None:
                            self.hub.broadcast(json.loads(message["data"]))
            except (RedisError, OSError, ValueError):
                self.ready.clear()
                self.hub.close_all()
                self.hub.metrics["redis_subscription_failures"] += 1
                logger.warning(
                    "Связь Redis чата потеряна; сокеты переподключатся и восстановят события."
                )
                await asyncio.sleep(1)

    async def _publish(self):
        while True:
            try:
                await self.ready.wait()
                started = monotonic()
                count = await self.delivery.flush()
                self.hub.metrics["published_events_total"] += count
                if count:
                    logger.info(
                        "chat.outbox count=%s duration_ms=%.1f",
                        count,
                        (monotonic() - started) * 1000,
                    )
            except (ChatServiceError, OSError):
                self.hub.metrics["publish_failures_total"] += 1
                logger.exception("Outbox чата сохранён и будет отправлен повторно.")
                self.hub.close_all()
                await asyncio.sleep(1)
            await asyncio.sleep(self.settings.chat_outbox_poll_seconds)

    async def _sweep(self):
        last_cleanup, last_metrics = 0.0, 0.0
        while True:
            try:
                await self.ready.wait()
                for project_id in {conn.project_id for conn in self.hub.connections.values()}:
                    await self.redis.state(project_id)
                    await self.redis.state(project_id, kind="typing")
                if monotonic() - last_cleanup >= self.settings.chat_cleanup_seconds:
                    await self.files.cleanup()
                    last_cleanup = monotonic()
                if monotonic() - last_metrics >= 30:
                    logger.info(
                        "chat.metrics %s",
                        {
                            **self.hub.snapshot(),
                            **self.db_metrics,
                            **self.chat.metrics,
                            **self.commands.metrics,
                            **self.files.metrics,
                            **self.redis.metrics,
                            "db_pool_used": self.pool.checkedout(),
                            "db_pool_free": self.pool.checkedin(),
                        },
                    )
                    last_metrics = monotonic()
            except (ChatServiceError, OSError):
                logger.exception("Ошибка обслуживания временного состояния чата.")
            await asyncio.sleep(self.settings.chat_ephemeral_sweep_seconds)


def build_chat_runtime(*, settings: Settings, session_factory) -> ChatRuntime:
    """Собирает явный граф зависимостей без глобального локатора ресурсов."""
    config = settings.chat
    transport = Redis.from_url(
        config.chat_redis_url,
        decode_responses=True,
        protocol=2,
        max_connections=config.chat_redis_max_connections,
        socket_connect_timeout=config.chat_redis_timeout,
        socket_timeout=config.chat_redis_timeout,
        health_check_interval=15,
        retry=Retry(NoBackoff(), 0),
    )
    redis = ChatRedisClient(
        transport,
        presence_ttl=config.chat_presence_ttl_seconds,
        typing_ttl=config.chat_typing_ttl_seconds,
    )
    db_metrics = Counter()
    scope = build_chat_scope(
        session_factory=session_factory,
        invite_code=settings.auth.registration_invite_code.get_secret_value(),
        metrics=db_metrics,
    )
    from src.dependencies.agent_context import build_context_evidence_service

    chat = ProjectChatService(
        scope,
        ChatPresenter(
            build_context_evidence_service(session_factory=session_factory, settings=settings)
        ),
    )
    files = ChatAttachmentsService(
        chat,
        ChatAttachmentStorage(settings.app.uploads_path / "chat"),
        config.chat_draft_retention_hours,
    )
    return ChatRuntime(
        transport=transport,
        redis=redis,
        chat=chat,
        commands=ChatCommandsService(
            chat,
            redis,
            send_limit=config.chat_send_per_minute,
            action_limit=config.chat_actions_per_minute,
        ),
        files=files,
        delivery=ChatDeliveryService(chat, redis, config.chat_outbox_batch_size),
        hub=ChatHub(queue_size=config.chat_queue_size, max_connections=config.chat_max_connections),
        settings=config,
        pool=session_factory.kw["bind"].sync_engine.pool,
        db_metrics=db_metrics,
    )
