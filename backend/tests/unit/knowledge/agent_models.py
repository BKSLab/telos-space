"""Модели PydanticAI для тестов агента без обращения к настоящей LLM.

``legacy_model`` сохраняет прежние сценарии тестов, написанные в форме
JSON-цикла (``answer``/``source_ids``/``tool_calls``/``reads``): на каждом шаге
FunctionModel передаёт дублёру ``llm_client.get_structured_response`` входной
JSON, дополненный ``read_results`` из результатов инструментов, и переводит
его ответ в нативные вызовы инструментов или в ``telos_submit_answer``. Инструменты
при этом исполняет настоящий цикл PydanticAI: prepare_tools, проверка
аргументов, реестр действий, бюджет контекста.
"""

import json
import re
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.agent.tools import AgentToolRequest
from src.schemas.knowledge import KnowledgeReadRequest


class LegacyOutput(BaseModel):
    """Прежняя форма шага JSON-цикла — только как язык сценариев тестов."""

    answer: str = ""
    source_ids: list[str] = Field(default_factory=list)
    reads: list[KnowledgeReadRequest] = Field(default_factory=list)
    tool_calls: list[AgentToolRequest] = Field(default_factory=list)


def prompt_payload(messages: list[ModelMessage]) -> dict[str, Any]:
    """Стартовый JSON вопроса (после сокращения history processor-ом)."""
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    return json.loads(part.content)
    raise AssertionError("В истории нет стартового JSON.")


def tool_results(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """Пары вызов/результат функциональных инструментов по порядку."""
    calls = {}
    results = []
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                calls[part.tool_call_id] = part
            elif isinstance(part, ToolReturnPart) and part.tool_name not in {
                "telos_submit_answer",
                "telos_load_capability",
            }:
                call = calls.get(part.tool_call_id)
                results.append(
                    {
                        "request": {
                            "name": part.tool_name,
                            "arguments": call.args_as_dict() if call else {},
                        },
                        "result": part.content,
                    }
                )
    return results


def final(answer: str = "", source_ids: list[str] | None = None) -> ModelResponse:
    """Ответ модели инструментом окончательного ответа."""
    return ModelResponse(
        parts=[
            ToolCallPart("telos_submit_answer", {"answer": answer, "source_ids": list(source_ids or [])})
        ]
    )


def legacy_model(llm_client) -> FunctionModel:
    """FunctionModel, делегирующая шаги прежнему дублёру ``get_structured_response``."""

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # Старые сценарии описывают доменные шаги, а не раскрытие capability.
        # Модель-дублёр сначала проходит настоящий loader, чтобы его инструменты,
        # права и ленивое чтение контекста исполнились тем же runtime, что в приложении.
        instructions = info.instructions or ""
        if any(tool.name == "telos_load_capability" for tool in info.function_tools):
            requested = {}
            loaded = set()
            for message in messages:
                for part in message.parts:
                    if isinstance(part, ToolCallPart) and part.tool_name == "telos_load_capability":
                        requested[part.tool_call_id] = part.args_as_dict().get("id")
                    elif isinstance(part, ToolReturnPart) and part.tool_name == "telos_load_capability":
                        loaded.add(requested.get(part.tool_call_id))
            available = re.findall(r"^- ([a-z_]+):", instructions, re.MULTILINE)
            remaining = [capability for capability in available if capability not in loaded]
            if remaining:
                return ModelResponse(
                    parts=[
                        ToolCallPart("telos_load_capability", {"id": capability})
                        for capability in remaining[:8]
                    ]
                )
        for message in messages:
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == "telos_load_capability":
                    if isinstance(part.content, dict):
                        instructions += "\n" + str(part.content.get("instructions", ""))
        payload = prompt_payload(messages)
        results = tool_results(messages)
        if results:
            payload["read_results"] = results
        output = await llm_client.get_structured_response(
            system_prompt=instructions,
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
            schema=LegacyOutput,
            max_completion_tokens=4000,
            offered=[tool.name for tool in info.function_tools],
        )
        calls = [ToolCallPart(request.name, request.arguments) for request in output.tool_calls] + [
            ToolCallPart(request.name, request.model_dump(mode="json", exclude={"name"}))
            for request in output.reads
        ]
        if calls:
            return ModelResponse(parts=calls)
        return final(output.answer, output.source_ids)

    return FunctionModel(respond)
