"""Идентичность сборки и нормализованный каталог фактического ``tools/list``.

Диагностика использует только исходники и публичные метаданные инструментов:
конфигурация, заголовки, ключи и результаты вызовов сюда не входят.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import ListToolsResult, Tool

MCP_APP_VERSION = "2.0.0"
CATALOG_FORMAT = 1
CATALOG_META_KEY = "telos-space/catalog"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )


def normalize_tools(tools: Iterable[Tool | Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Сортирует фактические wire-описания по имени, сохраняя все поля схем.

    Порядок ключей JSON несущественен; порядок вложенных массивов сохраняется.
    Описания, defaults, annotations и схемы результатов участвуют в сравнении.
    """
    normalized = [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(tool, Tool) else dict(tool)
        for tool in tools
    ]
    names = [tool.get("name") for tool in normalized]
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError("Каталог содержит инструмент без имени.")
    if len(set(names)) != len(names):
        raise ValueError("Каталог содержит повторяющиеся имена инструментов.")
    return sorted(normalized, key=lambda tool: tool["name"])


def catalog_hash(tools: Iterable[Tool | Mapping[str, Any]]) -> str:
    """Возвращает SHA-256 полного нормализованного массива инструментов."""
    payload = _canonical_json(normalize_tools(tools)).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@lru_cache(maxsize=1)
def build_version() -> str:
    """Идентифицирует код backend и lock зависимостей, доступные при запуске.

    Это отпечаток исходников сборки, а не Git SHA или идентификатор образа.
    LF/CRLF нормализуются, чтобы один checkout имел тот же отпечаток в Docker.
    """
    return source_build_version(Path(__file__).resolve().parents[2])


def source_build_version(backend_root: Path) -> str:
    """Вычисляет версию из явного каталога backend без чтения настроек."""
    paths = sorted(
        (backend_root / "src").rglob("*.py"),
        key=lambda path: path.relative_to(backend_root).as_posix(),
    )
    paths += [backend_root / "main.py", backend_root / "requirements.txt"]
    digest = hashlib.sha256()
    if not paths or any(not path.is_file() for path in paths):
        raise ValueError("Исходники backend для идентификации сборки недоступны.")
    for path in paths:
        digest.update(path.relative_to(backend_root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return f"{MCP_APP_VERSION}+source.{digest.hexdigest()}"


def catalog_metadata(tools: Iterable[Tool | Mapping[str, Any]], *, server_version: str) -> dict:
    """Возвращает безопасную краткую квитанцию каталога для ``tools/list``."""
    normalized = normalize_tools(tools)
    return {
        "format": CATALOG_FORMAT,
        "sha256": catalog_hash(normalized),
        "tool_count": len(normalized),
        "build_version": server_version,
        "mcp_sdk_version": version("mcp"),
    }


class CatalogDiagnostics:
    """Добавляет отпечаток реального ответа, не меняя состав инструментов."""

    def __init__(self, server_version: str) -> None:
        self.server_version = server_version

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext,
    ) -> HandlerResult:
        result = await call_next(ctx)
        if ctx.method != "tools/list":
            return result
        if isinstance(result, ListToolsResult):
            result = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            metadata = catalog_metadata(result["tools"], server_version=self.server_version)
            # Отпечаток относится к текущему ответу. У пагинированного сервера
            # только probe, дочитавший все страницы, может хешировать весь каталог.
            metadata["scope"] = "response"
            metadata["complete"] = not (ctx.params or {}).get("cursor") and not result.get("nextCursor")
            result = {**result, "_meta": {
                **(result.get("_meta") or {}), CATALOG_META_KEY: metadata,
            }}
        return result
