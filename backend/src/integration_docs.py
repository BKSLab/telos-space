"""Экспорт фактических HTTP/MCP-контрактов в единый каталог справки.

CLI использует инертные настройки и не запускает lifespan, БД или модели.
Источник методов и схем — зарегистрированное приложение, а не копия API.
"""

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path

from fastapi.routing import APIRoute, APIWebSocketRoute

from src.api.v1.api_key_routes import API_KEY_ROUTE_SCOPES, extra_scopes, required_scope
from src.utils.api_scopes import API_SCOPES, SCOPE_TASKS_READ

TARGET = Path(__file__).resolve().parents[2] / "frontend/src/content/integrations.generated.json"
PUBLIC = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/register"),
}
SUBSCRIPTIONS = {("GET", "/api/v1/ics/{token}.ics")}
# Выдача файла проверяет этот scope в короткой области AttachmentDownloadService.
DETACHED_SCOPES = {
    ("GET", "/api/v1/tasks/{task_id}/attachments/{attachment_id}/content"): SCOPE_TASKS_READ,
}
# Эти сценарии проверяют READ/WRITE в короткой сервисной auth/access области.
DETACHED_WRITE_ROUTES = {
    ("POST", "/api/v1/projects/{project_id}/files"),
    ("POST", "/api/v1/tasks/{task_id}/attachments"),
    ("PATCH", "/api/v1/tasks/{task_id}/attachments/{attachment_id}"),
    ("POST", "/api/v1/tasks/{task_id}/attachments/{attachment_id}/retry"),
    ("POST", "/api/v1/tasks/{task_id}/comments/with-files"),
}


def dependency_names(route: APIRoute) -> set[str]:
    """Возвращает полный граф зависимостей без выполнения запросов."""
    names = set()
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        if dependant.call is not None:
            names.add(getattr(dependant.call, "__name__", ""))
        pending.extend(dependant.dependencies)
    return names


def _id(kind: str, name: str) -> str:
    return f"{kind}-{hashlib.sha256(name.encode()).hexdigest()[:16]}"


def _group(name: str) -> str:
    if "project_tool" in name:
        return "project-actions"
    for group, words in (
        ("risks", ("risk",)),
        ("boards", ("board", "sticker", "diagram", "shape")),
        ("knowledge", ("knowledge",)),
        ("structure", ("wbs", "structure", "place_task")),
        ("comments", ("comment",)),
        ("reports", ("report",)),
        ("documents", ("document",)),
        ("task-attachments", ("attachment",)),
        ("calendar", ("calendar", "milestone", "dates", "baseline", "dependency")),
        ("agent", ("ask", "request", "pending")),
        ("projects", ("project", "member", "stage")),
    ):
        if any(word in name for word in words):
            return group
    return "tasks"


def _http_entries(app, schema: dict) -> list[dict]:
    routes = {
        (method, route.path): route
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    missing = API_KEY_ROUTE_SCOPES.keys() - routes.keys()
    if missing:
        raise ValueError(f"Scopes перечисляют несуществующие маршруты: {sorted(missing)}")
    entries = []
    for path, methods in sorted(schema["paths"].items()):
        for method, operation in sorted(methods.items()):
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                continue
            method = method.upper()
            pair = (method, path)
            route = routes[pair]
            names = dependency_names(route)
            session = "require_session" in names
            write = bool(names & {"require_write_scope", "require_chat_write_scope"}) or pair in DETACHED_WRITE_ROUTES
            scope = required_scope(method, path) or DETACHED_SCOPES.get(pair)
            scopes = list(dict.fromkeys([scope, *extra_scopes(method, path)])) if scope else None
            description = operation.get("description", "")
            summary = operation.get("summary", route.name)
            if not re.search("[А-Яа-яЁё]", summary) and re.search("[А-Яа-яЁё]", description):
                summary = description.splitlines()[0].strip()
            entries.append(
                {
                    "id": _id("api", f"{method} {path}"),
                    "kind": "http",
                    "name": f"{method} {path}",
                    "method": method,
                    "path": path,
                    "group": (operation.get("tags") or ["other"])[0],
                    "summary": summary,
                    "description": description,
                    "operationId": operation.get("operationId", route.name),
                    "access": "public"
                    if pair in PUBLIC
                    else "subscription"
                    if pair in SUBSCRIPTIONS
                    else "session"
                    if session
                    else "authenticated",
                    "scopes": scopes,
                    "legacy": []
                    if session or pair in PUBLIC or pair in SUBSCRIPTIONS
                    else ["WRITE"]
                    if write
                    else ["READ", "WRITE"],
                    "parameters": operation.get("parameters", []),
                    "requestBody": operation.get("requestBody"),
                    "responses": operation.get("responses", {}),
                }
            )
    return entries


def _websockets(app) -> list[dict]:
    from src.schemas.project_chats import ChatCommand

    entries = []
    for route in app.routes:
        if not isinstance(route, APIWebSocketRoute):
            continue
        attention = route.path.endswith("/task-attention/ws")
        entries.append(
            {
                "id": _id("api", f"WS {route.path}"),
                "kind": "websocket",
                "method": "WS",
                "name": f"WS {route.path}",
                "path": route.path,
                "group": "websocket",
                "summary": "Изменения персонального внимания"
                if attention
                else "Общий чат проекта в реальном времени",
                "description": (
                    "Снимки task-attention и heartbeat ping. Авторизация cookie-сессией; внешние ключи и токены не принимаются. Origin проверяется при подключении."
                    if attention
                    else "Команды и события общего чата: отправка, правка и удаление сообщения, реакции, прочтение, typing и ping. Каждая команда содержит UUID request_id; сервер отвечает ready, ack/error и событиями. Cookie либо прежний Bearer tt_; ключи ttk_ не принимаются. Для изменения данных нужен WRITE. Доступ перепроверяется во время соединения."
                ),
                "access": "session" if attention else "authenticated",
                "scopes": None,
                "legacy": [] if attention else ["READ", "WRITE"],
                "parameters": [
                    {"name": name, "in": "path", "required": True, "schema": {"type": "integer"}}
                    for name in route.param_convertors
                ],
                "inputSchema": {"type": "string", "maxLength": 128}
                if attention
                else ChatCommand.model_json_schema(),
            }
        )
    return sorted(entries, key=lambda entry: entry["name"])


async def build_catalog(app) -> dict:
    """Собирает все объявленные методы, scopes и точные схемы аргументов."""
    from src.agent.action_catalog import PROJECT_ACTIONS
    from src.agent.action_scopes import ACTION_SCOPES
    from src.mcp_server.registry import TOOL_REGISTRY
    from src.mcp_server.server import mcp_server

    schema = app.openapi()
    tools = []
    for tool in sorted(await mcp_server.list_tools(), key=lambda item: item.name):
        canonical = tool.name if tool.name in TOOL_REGISTRY else f"tt_{tool.name}"
        spec = TOOL_REGISTRY[canonical]
        raw = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        tools.append(
            {
                "id": _id("mcp", tool.name),
                "kind": "mcp",
                "name": tool.name,
                "group": "legacy-aliases" if canonical != tool.name else _group(tool.name),
                "summary": spec.title,
                "description": tool.description or spec.description,
                "scopes": [spec.scope],
                "legacy": ["WRITE"] if spec.write else ["READ", "WRITE"],
                "write": spec.write,
                "aliasFor": canonical if canonical != tool.name else None,
                "inputSchema": raw["inputSchema"],
                "outputSchema": raw.get("outputSchema"),
            }
        )
    actions = []
    for action in sorted(PROJECT_ACTIONS, key=lambda item: item.name):
        scope = ACTION_SCOPES.get(action.name)
        actions.append(
            {
                "id": _id("mcp-action", action.name),
                "kind": "action",
                "name": action.name,
                "group": _group(action.name),
                "summary": action.title,
                "description": action.description,
                "scopes": list(dict.fromkeys(["projects:read", scope])) if scope else None,
                "legacy": ["WRITE"] if action.mutating else ["READ", "WRITE"],
                "write": action.mutating,
                "requiresConfirmation": action.requires_confirmation,
                "ownerOnly": action.owner_only,
                "needsProject": action.needs_project,
                "inputSchema": action.parameters.model_json_schema(),
            }
        )
    return {
        "version": 1,
        "scopes": API_SCOPES,
        "api": _http_entries(app, schema) + _websockets(app),
        "mcp": tools,
        "actions": actions,
        "schemas": schema.get("components", {}).get("schemas", {}),
    }


def metadata_app():
    """Создаёт приложение для схем без чтения env, соединений и lifespan."""
    from src.core import settings as c

    def init_only(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (init_settings,)

    for settings_cls in (c.SettingsBase, c.Settings):
        settings_cls.settings_customise_sources = classmethod(init_only)
    cfg = c.Settings(
        db=c.DBSettings(
            postgres_host="127.0.0.1",
            postgres_port=1,
            postgres_user="docs",
            postgres_password="unused",
            postgres_name="docs",
        ),
        auth=c.AuthSettings(
            jwt_secret="metadata-only-key-at-least-thirty-two-bytes",
            registration_invite_code="unused",
        ),
        llm=c.LlmSettings(llm_api_key="unused", llm_api_url="http://127.0.0.1:1"),
        embedding=c.EmbeddingSettings(
            embedding_api_key="unused", embedding_api_url="http://127.0.0.1:1"
        ),
    )
    c.get_settings = lambda: cfg
    from main import app

    return app


def render(catalog: dict) -> str:
    return json.dumps(catalog, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Проверить актуальность без записи")
    parser.add_argument("--output", type=Path, default=TARGET)
    args = parser.parse_args()
    catalog = asyncio.run(build_catalog(metadata_app()))
    text = render(catalog)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != text:
            print("Каталог устарел. Выполните python -m src.integration_docs")
            return 1
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    counts = {kind: len(catalog[kind]) for kind in ("api", "mcp", "actions")}
    print(f"Каталог подтверждён: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
