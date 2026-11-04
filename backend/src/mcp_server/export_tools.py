"""Прежний CLI экспорта MCP: обновляет единый каталог API/MCP справки.

Основная команда — ``python -m src.integration_docs``. Дубликаты каталога
в docs и frontend больше не создаются.
"""

import json


def render() -> str:
    """Сохраняет прежний программный формат сокращённого перечня tt_* инструментов."""
    from src.mcp_server.registry import tool_catalog
    from src.mcp_server.server import mcp_server  # noqa: F401

    return json.dumps(tool_catalog(), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    from src.integration_docs import main as export_catalog

    return export_catalog()


if __name__ == "__main__":
    raise SystemExit(main())
