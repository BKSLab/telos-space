"""Справочник должен точно отражать зарегистрированный HTTP/MCP-контракт."""

import hashlib

from fastapi.routing import APIWebSocketRoute

from main import app
from src.agent.action_catalog import PROJECT_ACTIONS
from src.integration_docs import TARGET, build_catalog, render
from src.mcp_server.server import mcp_server


async def test_help_catalog_covers_actual_routes_tools_actions_and_stays_current():
    catalog = await build_catalog(app)
    actual_http = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
    }
    assert {
        (item["method"], item["path"]) for item in catalog["api"] if item["kind"] == "http"
    } == actual_http
    assert {item["path"] for item in catalog["api"] if item["kind"] == "websocket"} == {
        route.path for route in app.routes if isinstance(route, APIWebSocketRoute)
    }
    tools = {tool.name: tool for tool in await mcp_server.list_tools()}
    assert {item["name"] for item in catalog["mcp"]} == tools.keys()
    for item in catalog["mcp"]:
        assert item["inputSchema"] == tools[item["name"]].model_dump(by_alias=True)["inputSchema"]
    assert {item["name"] for item in catalog["actions"]} == {
        action.name for action in PROJECT_ACTIONS
    }
    entries = catalog["api"] + catalog["mcp"] + catalog["actions"]
    assert len({item["id"] for item in entries}) == len(entries)
    assert all(scope in catalog["scopes"] for item in entries for scope in item["scopes"] or [])
    generated = hashlib.sha256(render(catalog).encode()).hexdigest()
    saved = hashlib.sha256(TARGET.read_text(encoding="utf-8").encode()).hexdigest()
    assert generated == saved, "Обновите справку: python -m src.integration_docs"


async def test_context_catalog_describes_actual_credentials_and_combined_scopes():
    catalog = await build_catalog(app)
    api = {item["name"]: item for item in catalog["api"]}
    base = "/api/v1/projects/{project_id}"
    for suffix in ("topics", "pages"):
        row = api[f"POST {base}/web-research/{suffix}"]
        assert row["access"] == "session" and row["tokens"] == []
    credential = api[f"PUT {base}/git/connections/{{connection_id}}/credential"]
    assert credential["access"] == "session" and credential["scopes"] is None
    event = api[f"POST {base}/git/connections/{{connection_id}}/events"]
    assert event["access"] == "signature" and event["tokens"] == []
    links = api[f"GET {base}/git/tasks/{{task_id}}/links"]
    assert set(links["scopes"]) == {"tasks:read", "projects:read", "knowledge:read"}
    save = api[f"POST {base}/web-research/documents"]
    assert set(save["scopes"]) == {"projects:write", "projects:read", "knowledge:read"}
    assert save["tokens"] == ["WRITE"]
    actions = {item["name"]: item for item in catalog["actions"]}
    tools = {item["name"]: item for item in catalog["mcp"]}
    for entries in (actions, tools):
        assert set(entries["telos_select_context"]["scopes"]) == {
            "projects:read", "knowledge:read"
        }
        assert set(entries["telos_save_web_research"]["scopes"]) == {
            "projects:write", "projects:read", "knowledge:read"
        }
        assert set(entries["telos_list_task_git_links"]["scopes"]) == {
            "tasks:read", "projects:read", "knowledge:read"
        }
    for name in ("telos_request_web_search", "telos_request_web_page"):
        assert actions[name]["tokens"] == ["READ", "WRITE"]
        assert actions[name]["requiresConfirmation"]
