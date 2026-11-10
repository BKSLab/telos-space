"""Настоящий TCP Streamable HTTP: каталог, старое подключение и перезапуск.

Тестовые изменения локальны для отдельного MCPServer и не меняют приложение.
Внешний Codex не участвует: его lifecycle и cache API здесь недоступны.
"""

import asyncio
import json
import socket
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from mcp.client import Client, ClientSession
from mcp.client.caching import CacheConfig
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ListToolsResult, Tool, ToolListChangedNotification
from sse_starlette import sse

from src.mcp_server.catalog import CATALOG_META_KEY, CatalogDiagnostics, catalog_hash
from src.mcp_server.catalog_probe import remote_snapshot


@asynccontextmanager
async def serve(mcp: MCPServer | Server, *, port: int = 0, json_response: bool = False):
    """Запускает SDK ASGI на loopback TCP с отдельным lifespan и сессиями."""
    sse.AppStatus.should_exit = False
    sock = socket.socket()
    sock.bind(("127.0.0.1", port))
    sock.listen(128)
    app = mcp.streamable_http_app(streamable_http_path="/", json_response=json_response)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", timeout_graceful_shutdown=1))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}/"
    finally:
        server.should_exit = True
        sse.AppStatus.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=10)
        finally:
            # SSE 3.4.8 оставляет watcher после закрытия ответов. Его нужно
            # завершить до сброса process-global флага: иначе он увидит остановленный
            # uvicorn и закроет SSE следующего сервера в этом же event loop.
            watchers = [
                pending for pending in asyncio.all_tasks()
                if getattr(pending.get_coro(), "cr_code", None) is sse._shutdown_watcher.__code__
            ]
            for watcher in watchers:
                watcher.cancel()
            await asyncio.gather(*watchers, return_exceptions=True)
            sock.close()
            sse.AppStatus.should_exit = False


def isolated_server(version: str = "test-v1") -> MCPServer:
    return MCPServer(name="isolated-catalog", version=version, middleware=[CatalogDiagnostics(version)])


async def read_before(full_text: bool = False) -> dict:
    """Читает тестовый текст, по умолчанию — превью."""
    return {"full_text": full_text}


async def read_after(full_text: bool = True, request_id: str | None = None) -> dict:
    """Читает обновлённый тестовый текст с новым значением по умолчанию."""
    return {"full_text": full_text, "request_id": request_id}


async def test_old_and_new_sessions_observe_schema_change_and_explicit_legacy_notification():
    server = isolated_server()
    server.add_tool(read_before, name="telos_read_source")

    @server.tool(name="telos_change_catalog")
    async def change_catalog(context: Context) -> str:
        server.remove_tool("telos_read_source")
        server.add_tool(read_after, name="telos_read_source")
        await context.session.send_tool_list_changed()
        return "changed"

    received = asyncio.Event()

    async def on_message(message):
        if isinstance(message, ToolListChangedNotification):
            received.set()

    async with serve(server) as endpoint:
        async with streamable_http_client(endpoint) as streams:
            async with ClientSession(*streams, message_handler=on_message) as old:
                initialized = await old.initialize()
                # Статический каталог приложения не обещает legacy live reload.
                assert initialized.capabilities.tools.list_changed is False
                before = await old.list_tools()
                await old.call_tool("telos_change_catalog")
                async with asyncio.timeout(5):
                    await received.wait()
                after = await old.list_tools()
                assert catalog_hash(before.tools) != catalog_hash(after.tools)
                assert before.meta[CATALOG_META_KEY]["sha256"] == catalog_hash(before.tools)
                assert after.meta[CATALOG_META_KEY]["sha256"] == catalog_hash(after.tools)
                updated = next(tool for tool in after.tools if tool.name == "telos_read_source")
                assert updated.input_schema["properties"]["full_text"]["default"] is True
                assert "request_id" in updated.input_schema["properties"]
                fresh = await remote_snapshot(endpoint)
                assert fresh["sha256"] == catalog_hash(after.tools)
                assert fresh["server_info"]["version"] == "test-v1"


async def test_legacy_notification_invalidates_cached_listing_and_refresh_refetches():
    server = isolated_server()
    server.add_tool(read_before, name="telos_read_source")

    @server.tool(name="telos_notify_change")
    async def notify_change(context: Context) -> str:
        await context.session.send_tool_list_changed()
        return "notified"

    received = asyncio.Event()

    async def on_message(message):
        if isinstance(message, ToolListChangedNotification):
            received.set()

    async with serve(server) as endpoint:
        async with Client(
            endpoint, mode="legacy", cache=CacheConfig(default_ttl_ms=60000),
            message_handler=on_message,
        ) as old:
            assert old.server_capabilities.tools.list_changed is False
            before = await old.list_tools()
            server.remove_tool("telos_read_source")
            server.add_tool(read_after, name="telos_read_source")
            # add_tool сам не отправляет событие; локальная копия клиента устаревает.
            assert catalog_hash((await old.list_tools()).tools) == catalog_hash(before.tools)
            refreshed = await old.list_tools(cache_mode="refresh")
            assert catalog_hash(refreshed.tools) != catalog_hash(before.tools)
            server.add_tool(lambda: "new", name="telos_get_added")
            assert catalog_hash((await old.list_tools()).tools) == catalog_hash(refreshed.tools)
            await old.call_tool("telos_notify_change")
            async with asyncio.timeout(5):
                await received.wait()
            notified = await old.list_tools()
            assert "telos_get_added" in {tool.name for tool in notified.tools}
            assert notified.meta[CATALOG_META_KEY]["sha256"] == catalog_hash(notified.tools)
            assert (await remote_snapshot(endpoint))["sha256"] == catalog_hash(notified.tools)


async def test_restart_requires_a_new_session_and_reports_the_new_build():
    before = isolated_server("build-before")
    before.add_tool(read_before, name="telos_read_source")
    headers = {"Accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient(headers=headers) as http:
        async with serve(before) as endpoint:
            async with http.stream("POST", endpoint, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "catalog-restart-test", "version": "1"},
                },
            }) as initialized:
                initialized.raise_for_status()
                old_id = initialized.headers["mcp-session-id"]
                # Как SDK-клиент: читаем RPC-frame, не ожидаем закрытия SSE.
                async for line in initialized.aiter_lines():
                    if line.startswith("data:"):
                        result = json.loads(line[5:])["result"]
                        break
            port = httpx.URL(endpoint).port
            assert result["serverInfo"]["version"] == "build-before"
            await http.post(endpoint, headers={
                "Mcp-Session-Id": old_id, "MCP-Protocol-Version": result["protocolVersion"],
            }, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        after = isolated_server("build-after")
        after.add_tool(read_after, name="telos_read_source")
        async with serve(after, port=port) as restarted:
            stale = await http.post(restarted, headers={
                "Mcp-Session-Id": old_id, "MCP-Protocol-Version": result["protocolVersion"],
            }, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            assert stale.status_code == 404
            fresh = await remote_snapshot(restarted)
            assert fresh["server_info"]["version"] == "build-after"
            assert fresh["tools"][0]["inputSchema"]["properties"]["full_text"]["default"] is True


async def test_probe_reads_every_page_and_keeps_page_hash_separate_from_catalog_hash():
    tools = [
        Tool(name=name, input_schema={"type": "object"})
        for name in ("telos_get_first", "telos_get_second")
    ]

    async def list_page(_context, params):
        if params and params.cursor:
            return ListToolsResult(tools=[tools[1]])
        return ListToolsResult(tools=[tools[0]], next_cursor="second")

    server = Server(name="paged-catalog", version="paged", on_list_tools=list_page)
    server.middleware.append(CatalogDiagnostics("paged"))
    async with serve(server) as endpoint:
        full = await remote_snapshot(endpoint)
    assert full["sha256"] == catalog_hash(tools)
    assert len(full["server_catalog_metadata"]) == 2
    for metadata, tool in zip(full["server_catalog_metadata"], tools, strict=True):
        assert metadata["scope"] == "response"
        assert metadata["complete"] is False
        assert metadata["tool_count"] == 1
        assert metadata["sha256"] == catalog_hash([tool])
        assert metadata["sha256"] != full["sha256"]

    async def repeated_cursor(_context, _params):
        return ListToolsResult(tools=[tools[0]], next_cursor="again")

    repeated = Server(name="repeated-cursor", on_list_tools=repeated_cursor)
    async with serve(repeated) as endpoint:
        # anyio оборачивает ошибку тела SDK client в ExceptionGroup.
        with pytest.raises(ExceptionGroup, match="unhandled errors") as caught:
            await remote_snapshot(endpoint)
    assert "повторил cursor" in str(caught.value.exceptions[0].exceptions[0])
