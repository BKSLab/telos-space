"""Полный снимок MCP-каталога: ``python -m src.mcp_server.catalog_probe --help``."""

import argparse
import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx2
from mcp.client import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation, PaginatedRequestParams

from src.mcp_server.catalog import (
    CATALOG_FORMAT,
    CATALOG_META_KEY,
    catalog_hash,
    normalize_tools,
)


async def remote_snapshot(url: str, *, token: str | None = None, request_seconds: float = 20) -> dict:
    """Читает все страницы каталога в новой настоящей Streamable HTTP сессии.

    URL и credentials в снимок не входят. Вызовы доменных инструментов отсутствуют.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx2.AsyncClient(
        headers=headers, timeout=request_seconds, follow_redirects=True,
    ) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(
                *streams, read_timeout_seconds=request_seconds,
                client_info=Implementation(name="telos-catalog-probe", version="1"),
            ) as session:
                initialized = await session.initialize()
                tools = []
                cursors = set()
                cursor = None
                page_metadata = []
                while True:
                    page = await session.list_tools(
                        params=PaginatedRequestParams(cursor=cursor) if cursor else None,
                    )
                    tools.extend(page.tools)
                    page_metadata.append((page.meta or {}).get(CATALOG_META_KEY))
                    cursor = page.next_cursor
                    if cursor is None:
                        break
                    if cursor in cursors:
                        raise ValueError("Сервер повторил cursor каталога.")
                    cursors.add(cursor)
                normalized = normalize_tools(tools)
                return {
                    "format": CATALOG_FORMAT,
                    "captured_at": datetime.now(UTC).isoformat(),
                    "source": "streamable-http/new-session",
                    "server_info": initialized.server_info.model_dump(
                        mode="json", by_alias=True, exclude_none=True,
                    ),
                    "protocol_version": initialized.protocol_version,
                    "capabilities": initialized.capabilities.model_dump(
                        mode="json", by_alias=True, exclude_none=True,
                    ),
                    "sha256": catalog_hash(normalized),
                    "tools": normalized,
                    "server_catalog_metadata": page_metadata,
                }


async def local_snapshot() -> dict:
    """Читает тот же серверный реестр без env, БД и внешних соединений."""
    from src.mcp_server.server import mcp_server

    tools = normalize_tools(await mcp_server.list_tools())
    return {
        "format": CATALOG_FORMAT,
        "captured_at": datetime.now(UTC).isoformat(),
        "source": "local/server-list-tools",
        "server_info": {"name": mcp_server.name, "version": mcp_server.version},
        "sha256": catalog_hash(tools),
        "tools": tools,
    }


def compare_snapshots(before: dict, after: dict) -> dict:
    """Сравнивает полные каталоги; число инструментов само по себе не критерий."""
    for snapshot in (before, after):
        if any(not isinstance(tool.get("inputSchema"), dict) for tool in snapshot["tools"]):
            raise ValueError("Для сравнения нужен полный tools/list с JSON Schema.")
    first = {tool["name"]: tool for tool in normalize_tools(before["tools"])}
    second = {tool["name"]: tool for tool in normalize_tools(after["tools"])}
    return {
        "same_catalog": catalog_hash(first.values()) == catalog_hash(second.values()),
        "before_server_info": before.get("server_info"),
        "after_server_info": after.get("server_info"),
        "before_sha256": catalog_hash(first.values()),
        "after_sha256": catalog_hash(second.values()),
        "added": sorted(second.keys() - first.keys()),
        "removed": sorted(first.keys() - second.keys()),
        "changed": {
            name: sorted(
                key for key in first[name].keys() | second[name].keys()
                if key not in first[name] or key not in second[name]
                or json.dumps(first[name][key], sort_keys=True)
                != json.dumps(second[name][key], sort_keys=True)
            )
            for name in sorted(first.keys() & second.keys())
            if catalog_hash([first[name]]) != catalog_hash([second[name]])
        },
    }


def main() -> int:
    """Сохраняет полный JSON, выводя только безопасную квитанцию или diff."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    local = commands.add_parser("local", help="Каталог текущего кода без env и соединений")
    local.add_argument("--output", type=Path, required=True)
    remote = commands.add_parser("remote", help="Свежая сессия сервера, без вызовов инструментов")
    remote.add_argument("url")
    remote.add_argument("--output", type=Path, required=True)
    remote.add_argument("--token-env", help="Имя env-переменной с ключом; значение не выводится")
    remote.add_argument("--timeout", type=float, default=20)
    compare = commands.add_parser("compare", help="Сравнить два полных JSON-снимка")
    compare.add_argument("before", type=Path)
    compare.add_argument("after", type=Path)
    arguments = parser.parse_args()
    # HTTP/SDK logger может включать URL или серверные payload; CLI печатает
    # только квитанцию. Credentials не передаются аргументом командной строки.
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        if arguments.command == "compare":
            result = compare_snapshots(
                json.loads(arguments.before.read_text(encoding="utf-8")),
                json.loads(arguments.after.read_text(encoding="utf-8")),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["same_catalog"] else 1
        if arguments.command == "local":
            snapshot = asyncio.run(local_snapshot())
        else:
            token = os.environ[arguments.token_env] if arguments.token_env else None
            snapshot = asyncio.run(remote_snapshot(
                arguments.url, token=token, request_seconds=arguments.timeout,
            ))
        arguments.output.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        print(json.dumps({
            "server_info": snapshot["server_info"],
            "sha256": snapshot["sha256"],
            "tool_count": len(snapshot["tools"]),
        }, ensure_ascii=False))
        return 0
    except Exception as error:
        print(f"Каталог не проверен: {type(error).__name__}. Проверьте адрес, доступ и JSON.")
        return 2
    finally:
        logging.disable(previous_logging_disable)


if __name__ == "__main__":
    raise SystemExit(main())
