"""Арис обнаруживает сохранённый обзор и читает его без WriteGate или генерации."""

from unittest.mock import AsyncMock, Mock

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.agent.runtime import TimRuntime
from src.agent.tim import TimMode
from src.agent.tools import AgentTool, AgentToolExecutor
from src.prompts.project_agent import TIM_BASE_PROMPTS, TIM_WORKFLOW_INSTRUCTIONS
from src.schemas.project_tool_inputs import EmptyInput
from tests.unit.knowledge.agent_models import final
from tests.unit.knowledge.test_tim_runtime import deps_for


@pytest.mark.parametrize("found", [True, False])
@pytest.mark.parametrize("mode", [TimMode.PROJECT_CHAT, TimMode.WIDGET_PROJECT])
async def test_saved_summary_is_discovered_in_reports_and_returned_to_model(found, mode):
    report = {
        "found": found,
        "report": {"created_at": "2026-11-07T08:00:00Z", "headline": "Полный сохранённый анализ"}
        if found
        else None,
        "source_id": "analytics_report:7" if found else None,
        "provenance": "generated_analysis",
    }
    reader = AsyncMock(return_value=report)
    executor = AgentToolExecutor(
        [
            AgentTool(
                name="telos_get_latest_project_summary",
                description="Последний сохранённый обзор проекта",
                parameters=EmptyInput,
                handler=reader,
                mutating=False,
            ),
        ]
    )
    deps = deps_for(executor, can_write=False, mode=mode)
    deps.defer_workflows = True
    deps.system_prompt = TIM_BASE_PROMPTS[mode]
    deps.content["question"] = "Тебе доступен последний разбор проекта?"
    deps.load_context = AsyncMock()
    deps.write_gate_factory = Mock(
        side_effect=AssertionError("Чтение не требует разрешения записи")
    )
    step = 0

    async def respond(messages, info: AgentInfo):
        nonlocal step
        step += 1
        visible = {tool.name for tool in info.function_tools}
        if step == 1:
            assert visible == {"telos_load_capability"}
            assert "сохранённый обзор" in info.instructions.lower()
            return ModelResponse(parts=[ToolCallPart("telos_load_capability", {"id": "reports"})])
        if step == 2:
            assert visible == {"telos_load_capability", "telos_get_latest_project_summary"}
            assert "found=false" in TIM_WORKFLOW_INSTRUCTIONS["reports"]
            assert "created_at" in TIM_WORKFLOW_INSTRUCTIONS["reports"]
            return ModelResponse(parts=[ToolCallPart("telos_get_latest_project_summary", {})])
        returned = [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "telos_get_latest_project_summary"
        ]
        assert returned[-1]["found"] is found
        assert returned[-1]["report"] == report["report"]
        return final("Обзор прочитан." if found else "Сохранённого обзора пока нет.")

    await TimRuntime(model=FunctionModel(respond), executor=executor).run(deps, metadata={})
    reader.assert_awaited_once()
    deps.load_context.assert_awaited_once_with("reports")
    deps.write_gate_factory.assert_not_called()
