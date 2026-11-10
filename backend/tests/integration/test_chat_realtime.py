"""Redis fan-out, отказы и реальный ASGI WebSocket с короткими DB-scope."""

import asyncio
import json
from contextlib import suppress
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select

from src.api.v1.endpoints.project_chat_ws import router
from src.db.models import ApiToken, ApiTokenScope, ChatEvent, ProjectMember
from src.dependencies.settings import get_app_settings
from src.exceptions.project_chats import ChatRateLimitError, ChatUnavailableError
from src.realtime.chat_hub import ChatHub
from src.realtime.runtime import CHAT_RUNTIME_KEY
from src.schemas.project_chats import ChatMessageCreate
from src.services.chat_delivery import ChatDeliveryService
from src.utils.api_tokens import build_display_prefix, generate_token_secret, hash_token_secret
from src.utils.tokens import create_access_token


async def wait_event(connection, kind):
    async with asyncio.timeout(8):
        while True:
            event = await connection.queue.get()
            if event["type"] == kind:
                return event


async def test_real_redis_delivers_between_instances_and_revokes_all_tabs(runtimes):
    first, second = runtimes.first, runtimes.second
    own = first.hub.connect(1, 1)
    member = second.hub.connect(1, 2)
    other_project = second.hub.connect(2, 1)
    # Первичные membership-события могут ещё доходить при запуске publisher.
    row = await first.chat.send(
        1, 1, ChatMessageCreate(content="Между экземплярами", client_message_id=uuid4())
    )
    left, right = await asyncio.gather(
        wait_event(own, "message.created"), wait_event(member, "message.created")
    )
    assert left["data"]["message"]["id"] == right["data"]["message"]["id"] == row.id
    while not other_project.queue.empty():
        assert other_project.queue.get_nowait().get("data", {}).get("message_id") != row.id
    async with runtimes.env.factory() as db:
        await db.execute(
            delete(ProjectMember).where(ProjectMember.project_id == 1, ProjectMember.user_id == 2)
        )
        await db.commit()
    await asyncio.wait_for(member.closed.wait(), 8)
    assert member.close_code == 4403 and not own.closed.is_set()


async def test_presence_accounts_for_two_tabs_and_typing_expires(runtimes):
    redis = runtimes.first.redis
    redis.typing_ttl = 1
    await redis.state(1, 1, "tab-a")
    await redis.state(1, 1, "tab-b")
    await redis.state(1, 1, "tab-a", remove=True)
    assert (await redis.snapshot(1))["presence"] == [1]
    await redis.state(1, 1, "tab-b", kind="typing")
    await asyncio.sleep(1.05)
    await redis.state(1, kind="typing")
    assert (await redis.snapshot(1))["typing"] == []
    await redis.state(1, 1, "tab-b", remove=True)
    assert (await redis.snapshot(1))["presence"] == []


async def test_rate_limit_is_shared_by_instances(runtimes):
    await runtimes.first.redis.rate_limit(1, 1, "test", limit=1)
    with pytest.raises(ChatRateLimitError):
        await runtimes.second.redis.rate_limit(1, 1, "test", limit=1)


async def test_outbox_survives_publish_failure_and_releases_db_before_network(chat_env):
    row = await chat_env.service.send(
        1, 1, ChatMessageCreate(content="Не потерять", client_message_id=uuid4())
    )
    published = []

    class FailingPublisher:
        async def publish(self, project_id, event):
            assert chat_env.engine.sync_engine.pool.checkedout() == 0
            raise ChatUnavailableError("Имитация недоступности Redis.")

    delivery = ChatDeliveryService(chat_env.service, FailingPublisher(), 100)
    with pytest.raises(ChatUnavailableError):
        await delivery.flush()
    async with chat_env.factory() as db:
        events = list((await db.execute(select(ChatEvent))).scalars())
        assert all(event.published_at is None for event in events)

    class Publisher:
        async def publish(self, project_id, event):
            assert chat_env.engine.sync_engine.pool.checkedout() == 0
            published.append(event)

    delivery.redis = Publisher()
    await delivery.flush()
    assert any(event["data"].get("message_id") == row.id for event in published)
    assert await delivery.flush() == 0


def test_slow_client_cannot_block_other_clients():
    hub = ChatHub(queue_size=2, max_connections=10)
    slow, fast = hub.connect(1, 1), hub.connect(1, 2)
    for seq in range(10):
        hub.broadcast({"type": "message.created", "project_id": 1, "seq": seq, "data": {}})
        fast.queue.get_nowait()
    assert slow.closed.is_set() and slow.close_code == 1013
    assert not fast.closed.is_set() and slow.queue.qsize() == 2


class SocketHarness:
    """ASGI-клиент работает в том же event loop, что asyncpg и runtime."""

    def __init__(self, app, runtime, config, user_id=1, origin="http://testserver", bearer=None):
        self.incoming, self.outgoing = asyncio.Queue(), asyncio.Queue()
        self.incoming.put_nowait({"type": "websocket.connect"})
        headers = [
            (b"host", b"testserver"),
            (b"origin", origin.encode()),
            (
                b"cookie",
                f"{config.auth.session_cookie_name}={create_access_token(user_id)}".encode(),
            ),
        ]
        if bearer:
            headers.append((b"authorization", f"Bearer {bearer}".encode()))
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": "/api/v1/projects/1/chat/ws",
            "raw_path": b"/api/v1/projects/1/chat/ws",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "subprotocols": [],
            "state": {CHAT_RUNTIME_KEY: runtime},
        }
        self.task = asyncio.create_task(app(scope, self.incoming.get, self.outgoing.put))

    async def ready(self):
        async with asyncio.timeout(8):
            while True:
                event = await self.outgoing.get()
                if event["type"] == "websocket.close":
                    return event
                if (
                    event["type"] == "websocket.send"
                    and json.loads(event["text"])["type"] == "ready"
                ):
                    return event

    async def close(self):
        self.incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(self.task, 8)


async def test_more_idle_websockets_than_db_pool_and_origin_guard(runtimes):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_app_settings] = lambda: runtimes.config
    sockets = [SocketHarness(app, runtimes.first, runtimes.config) for _ in range(12)]
    try:
        ready = await asyncio.gather(*(socket.ready() for socket in sockets))
        assert all(event["type"] == "websocket.send" for event in ready)
        # Два соединения пула обслужили 12 живых сокетов и свободны для обычной работы.
        async with asyncio.timeout(2):
            assert (await runtimes.first.chat.get_chat(1, 1)).id > 0
        assert runtimes.env.engine.sync_engine.pool.checkedout() <= 1  # outbox может читать batch
        forbidden = SocketHarness(
            app, runtimes.first, runtimes.config, origin="https://foreign.example"
        )
        assert (await forbidden.ready())["code"] == 4403
        await forbidden.task
    finally:
        await asyncio.gather(*(socket.close() for socket in sockets))


async def test_redis_subscriber_interruption_forces_resync_and_burst_recovers(runtimes):
    first, second = runtimes.first, runtimes.second
    old = [runtime.hub.connect(1, 1) for runtime in (first, second)]
    cursor = (await first.chat.get_chat(1, 1)).event_cursor
    # Разрыв настоящих Pub/Sub TCP соединений, без mock listener/retry.
    assert await runtimes.transport.execute_command("CLIENT", "KILL", "TYPE", "pubsub") == 2
    await asyncio.gather(*(asyncio.wait_for(connection.closed.wait(), 8) for connection in old))
    assert all(connection.close_code == 1013 for connection in old)
    await asyncio.gather(
        *(asyncio.wait_for(runtime.ready.wait(), 8) for runtime in (first, second))
    )
    for runtime, connection in zip((first, second), old, strict=True):
        runtime.hub.disconnect(connection)
    listener = second.hub.connect(1, 2)
    rows = await asyncio.gather(
        *(
            first.chat.send(
                1, 1, ChatMessageCreate(content=f"Серия {index}", client_message_id=uuid4())
            )
            for index in range(24)
        )
    )
    expected = {row.id for row in rows}
    received = set()
    async with asyncio.timeout(12):
        while received != expected:
            event = await wait_event(listener, "message.created")
            received.add(event["data"]["message_id"])
    replay = await second.chat.events(1, 2, cursor)
    assert {
        event.data["message_id"] for event in replay.events if event.type == "message.created"
    } == expected
    assert len({row.seq for row in rows}) == 24
    assert first.hub.metrics["redis_subscription_failures"] >= 1


async def test_reconnect_storm_releases_pool_and_read_token_cannot_send(runtimes):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_app_settings] = lambda: runtimes.config
    for _ in range(3):
        sockets = [SocketHarness(app, runtimes.first, runtimes.config) for _ in range(12)]
        try:
            assert all(
                event["type"] == "websocket.send"
                for event in await asyncio.gather(*(socket.ready() for socket in sockets))
            )
        finally:
            await asyncio.gather(*(socket.close() for socket in sockets))
        assert len(runtimes.first.hub.connections) == 0
    secret = generate_token_secret()
    async with runtimes.env.factory() as db:
        db.add(
            ApiToken(
                user_id=2,
                name="WS read",
                token_hash=hash_token_secret(secret),
                prefix=build_display_prefix(secret),
                scope=ApiTokenScope.READ,
            )
        )
        await db.commit()
    socket = SocketHarness(app, runtimes.first, runtimes.config, bearer=secret)
    try:
        ready = json.loads((await socket.ready())["text"])
        assert ready["data"]["can_write"] is False
        socket.incoming.put_nowait(
            {
                "type": "websocket.receive",
                "text": json.dumps(
                    {
                        "type": "message.send",
                        "request_id": str(uuid4()),
                        "data": {"content": "Запрещено", "client_message_id": str(uuid4())},
                    }
                ),
            }
        )
        async with asyncio.timeout(8):
            while True:
                response = await socket.outgoing.get()
                data = json.loads(response.get("text", "{}"))
                if data.get("type") == "error":
                    assert data["status"] == 403
                    break
        async with runtimes.env.factory() as db:
            await db.execute(
                delete(ProjectMember).where(
                    ProjectMember.project_id == 1, ProjectMember.user_id == 2
                )
            )
            await db.commit()
        async with asyncio.timeout(8):
            while True:
                response = await socket.outgoing.get()
                if response["type"] == "websocket.close":
                    assert response["code"] == 4403
                    break
        assert (await runtimes.first.chat.history(1, 1)).messages == []
    finally:
        await socket.close()
