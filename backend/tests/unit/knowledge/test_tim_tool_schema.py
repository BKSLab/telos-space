"""Provider-facing tool schemas must describe every array item for Gemini."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from src.agent.runtime import tool_json_schema
from src.dependencies.services import TIM_ACTIONS, build_project_tool_executor
from src.schemas.board_diagrams import BoardShapeUpdatesInput
from src.schemas.board_shapes import MAX_SHAPE_COORDINATE
from src.services.agent_actions import AgentActionsService


def registry_executor():
    return build_project_tool_executor(
        MagicMock(), AgentActionsService(scope=MagicMock(), actions=TIM_ACTIONS)
    )


def check_array_items(value, path="$"):
    if isinstance(value, dict):
        if value.get("type") == "array":
            assert isinstance(value.get("items"), dict), f"{path}: array items missing"
        for key, item in value.items():
            check_array_items(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check_array_items(item, f"{path}[{index}]")


def test_every_registered_tool_has_typed_array_items_for_gemini():
    for tool in registry_executor().tools.values():
        check_array_items(tool_json_schema(tool), tool.name)


@pytest.mark.parametrize("name", ["telos_update_board_shapes", "telos_update_personal_board_shapes"])
def test_arrow_coordinates_have_portable_numeric_items_and_exact_pair_length(name):
    schema = tool_json_schema(registry_executor().tools[name])
    data = schema["properties"]["updates"]["items"]["properties"]["data"]
    points = next(item for item in data["properties"]["points"]["anyOf"] if item["type"] == "array")
    assert points["minItems"] == 2
    assert points["maxItems"] == 50
    point = points["items"]
    assert "prefixItems" not in point
    assert point["minItems"] == point["maxItems"] == 2
    assert point["items"] == {
        "type": "number",
        "minimum": -MAX_SHAPE_COORDINATE,
        "maximum": MAX_SHAPE_COORDINATE,
    }


@pytest.mark.parametrize(
    "point", [[1], [1, 2, 3], [float("nan"), 2], [MAX_SHAPE_COORDINATE + 1, 2]]
)
def test_portable_schema_keeps_strict_execution_coordinate_validation(point):
    with pytest.raises(ValidationError):
        BoardShapeUpdatesInput.model_validate(
            {
                "board_id": 6,
                "updates": [{"id": 1, "revision": 1, "data": {"points": [point, [3, 4]]}}],
            }
        )


def test_arrow_update_preserves_intermediate_points():
    parsed = BoardShapeUpdatesInput.model_validate(
        {
            "board_id": 6,
            "updates": [{"id": 1, "revision": 1, "data": {"points": [[1, 2], [3, 4], [5, 6]]}}],
        }
    )
    assert parsed.updates[0].data.model_dump(mode="json", exclude_unset=True) == {
        "points": [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
    }
