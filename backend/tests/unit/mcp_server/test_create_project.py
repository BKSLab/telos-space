"""Контракт нового инструмента создания проекта и общий валидатор паспорта."""

from uuid import uuid4

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from src.mcp_server.registry import TOOL_REGISTRY
from src.mcp_server.server import mcp_server
from src.schemas.projects import ProjectCreateSchema
from src.utils.api_scopes import SCOPE_PROJECTS_WRITE


async def test_project_creation_catalog_has_rest_fields_and_rejects_client_owner():
    tool = mcp_server._tool_manager.get_tool("tt_create_project")
    schema = tool.parameters
    assert set(schema["required"]) == {"key", "name", "request_id"}
    assert set(schema["properties"]) == set(ProjectCreateSchema.model_fields) | {"actor", "request_id"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["request_id"]["format"] == "uuid"
    assert schema["properties"]["key"]["pattern"] == ProjectCreateSchema.model_fields["key"].metadata[0].pattern
    assert schema["properties"]["status"]["default"] == "PLANNING"
    assert schema["properties"]["color"]["default"] == "#58a6ff"
    assert "project_key" not in schema["properties"]
    spec = TOOL_REGISTRY[tool.name]
    assert spec.scope == SCOPE_PROJECTS_WRITE and spec.write
    with pytest.raises(ToolError, match="owner_id"):
        await tool.run(
            {"key": "NEW", "name": "Новый проект", "request_id": str(uuid4()), "owner_id": 999},
            context=None,
        )
    with pytest.raises(ToolError, match="uuid"):
        await tool.run({"key": "NEW", "name": "Новый проект", "request_id": "invalid"}, context=None)
