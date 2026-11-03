"""Цикл Ариса на PydanticAI: инструменты из реестра, права, ModelRetry, бюджет, телеметрия.

Настоящая LLM не вызывается: FunctionModel/TestModel ведут цикл, а
инструменты исполняет тот же ``AgentToolExecutor``, что и в сервисе.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from pydantic_ai.messages import (
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from src.agent.capabilities import WORKFLOWS
from src.agent.context import AgentContextBudget, encode, estimate_tokens
from src.agent.runtime import MAX_CALLS_PER_STEP, TimDeps, TimRuntime, WriteGate, tool_json_schema
from src.agent.tim import TimMode
from src.agent.tools import AgentTool, AgentToolContext, AgentToolExecutor
from src.dependencies.services import TIM_ACTIONS, build_project_tool_executor
from src.prompts.project_agent import (
    TIM_BASE_PROMPTS,
    TIM_SYSTEM_PROMPTS,
    TIM_WORKFLOW_COMMON,
    TIM_WORKFLOW_INSTRUCTIONS,
)
from src.schemas.knowledge import KnowledgeReadParameters
from src.schemas.project_tool_inputs import TaskCreateInput, TaskInput
from src.services.agent_actions import AgentActionsService
from tests.unit.knowledge.agent_models import final, prompt_payload


def registry_executor() -> AgentToolExecutor:
    """Полный реестр Ариса: чтение источников, действия проекта и личные действия."""
    return build_project_tool_executor(
        MagicMock(), AgentActionsService(scope=MagicMock(), actions=TIM_ACTIONS)
    )


def deps_for(
    executor: AgentToolExecutor,
    *,
    can_write: bool = True,
    mode: TimMode = TimMode.WIDGET_PROJECT,
    allowed_tools: frozenset[str] | None = None,
    context_tokens: int = 12000,
    tool_rounds: int = 4,
) -> TimDeps:
    content = {"question": "Вопрос", "current_date": date(2026, 9, 30).isoformat()}
    return TimDeps(
        execution=AgentToolContext(
            project_id=1,
            user_id=1,
            can_write=can_write,
            mode=mode,
            allowed_tools=allowed_tools,
        ),
        executor=executor,
        system_prompt="Инструкция",
        content=content,
        budget=AgentContextBudget(
            system_prompt="Инструкция", max_tokens=context_tokens, history_tokens=1000
        ),
        tool_rounds=tool_rounds,
    )


def small_executor() -> tuple[AgentToolExecutor, AsyncMock, AsyncMock]:
    create = AsyncMock(
        return_value={
            "action": {
                "id": "a1",
                "tool_name": "create_task",
                "title": "Создать задачу",
                "status": "completed",
                "result": {"task": {"id": 5, "key": "P-5"}},
            }
        }
    )
    read = AsyncMock(return_value={"task": {"id": 7, "title": "Проверка"}})
    executor = AgentToolExecutor(
        [
            AgentTool("get_task", "Прочитать задачу", TaskInput, read),
            AgentTool("create_task", "Создать задачу", TaskCreateInput, create, mutating=True),
        ]
    )
    return executor, read, create


async def test_tools_are_generated_from_the_registry_without_hand_written_copies():
    executor = registry_executor()
    runtime = TimRuntime(model=TestModel(), executor=executor)
    offered = []

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        offered.extend(info.function_tools)
        return final("Готово.")

    runtime.agent.model = FunctionModel(respond)
    deps = deps_for(executor)
    result = await runtime.run(deps, metadata={})

    assert result.output.answer == "Готово."
    expected = {
        tool.name: tool for tool in executor.tools.values() if TimMode.WIDGET_PROJECT in tool.modes
    }
    assert {item.name for item in offered} == set(expected)
    for item in offered:
        tool = expected[item.name]
        assert item.description == tool.description
        assert item.parameters_json_schema == tool_json_schema(tool)
        # $defs раскрыты: OpenAI-совместимые шлюзы ссылки понимают не всегда.
        assert "$defs" not in encode(item.parameters_json_schema)


async def test_write_tools_are_not_offered_without_write_right_or_key_scope():
    executor = registry_executor()
    mutating = {name for name, tool in executor.tools.items() if tool.mutating}
    for can_write, allowed, expect in (
        (False, None, set()),
        (True, frozenset({"create_task"}), {"create_task"}),
        (True, None, None),
    ):
        runtime = TimRuntime(model=TestModel(), executor=executor)
        offered = []

        async def respond(messages, info: AgentInfo, offered=offered) -> ModelResponse:
            offered.extend(item.name for item in info.function_tools)
            return final("Ок.")

        runtime.agent.model = FunctionModel(respond)
        await runtime.run(
            deps_for(executor, can_write=can_write, allowed_tools=allowed), metadata={}
        )
        visible = set(offered) & mutating
        if expect is None:
            assert visible == {
                name for name in mutating if TimMode.WIDGET_PROJECT in executor.tools[name].modes
            }
        else:
            assert visible == expect
        # Чтение доступно всегда.
        assert "list_sources" in offered


async def test_hidden_write_tool_called_by_name_is_not_executed():
    executor, _read, create = small_executor()
    runtime = TimRuntime(model=TestModel(), executor=executor)
    steps = []

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        steps.append([item.name for item in info.function_tools])
        if len(steps) == 1:
            # Модель «помнит» имя и зовёт инструмент, которого ей не дали.
            return ModelResponse(parts=[ToolCallPart("create_task", {"title": "Взлом"})])
        return final("Не могу изменить проект.")

    runtime.agent.model = FunctionModel(respond)
    result = await runtime.run(deps_for(executor, can_write=False), metadata={})

    assert steps[0] == ["get_task"]
    create.assert_not_awaited()
    assert result.output.answer == "Не могу изменить проект."


async def test_final_answer_does_not_skip_a_write_in_the_same_model_response():
    """Итоговая реплика не отменяет действие из того же ответа модели."""
    executor, _read, create = small_executor()

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                *final("Задача создана.").parts,
                ToolCallPart("create_task", {"title": "Проверка миграции"}),
            ]
        )

    runtime = TimRuntime(model=FunctionModel(respond), executor=executor)
    result = await runtime.run(deps_for(executor), metadata={})

    create.assert_awaited_once()
    assert result.output.answer == "Задача создана."
    assert result.requests == 1
    assert [item["tool"] for item in result.tool_calls] == ["create_task"]


async def test_invalid_arguments_become_model_retry_with_field_and_format():
    executor, read, _create = small_executor()
    runtime = TimRuntime(model=TestModel(), executor=executor)
    retries = []

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        last = messages[-1]
        retries.extend(part for part in last.parts if isinstance(part, RetryPromptPart))
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("get_task", {"task_id": "семь"})])
        if not any(isinstance(part, ToolReturnPart) for part in last.parts):
            return ModelResponse(parts=[ToolCallPart("get_task", {"task_id": 7})])
        return final("Задача прочитана.")

    runtime.agent.model = FunctionModel(respond)
    result = await runtime.run(deps_for(executor), metadata={})

    assert result.output.answer == "Задача прочитана."
    assert len(retries) == 1
    message = retries[0].model_response()
    assert "task_id" in message and "целое число" in message
    read.assert_awaited_once()
    assert [item.get("refused") for item in result.tool_calls] == ["arguments", None]


async def test_last_step_offers_no_tools_and_limits_end_the_run_gracefully():
    executor, read, _create = small_executor()
    runtime = TimRuntime(model=TestModel(), executor=executor)
    offered = []

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        offered.append([item.name for item in info.function_tools])
        # Модель никогда не отвечает — только читает.
        return ModelResponse(parts=[ToolCallPart("get_task", {"task_id": 7})])

    runtime.agent.model = FunctionModel(respond)
    result = await runtime.run(deps_for(executor, tool_rounds=2), metadata={})

    assert result.output is None
    assert offered[:2] == [["get_task", "create_task"], ["get_task", "create_task"]]
    assert all(step == [] for step in offered[2:])
    assert read.await_count == 2


async def test_more_than_eight_calls_in_one_step_are_refused():
    executor, read, _create = small_executor()
    runtime = TimRuntime(model=TestModel(), executor=executor)

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart("get_task", {"task_id": index + 1})
                    for index in range(MAX_CALLS_PER_STEP + 1)
                ]
            )
        return final("Готово.")

    runtime.agent.model = FunctionModel(respond)
    result = await runtime.run(deps_for(executor), metadata={})

    assert read.await_count == MAX_CALLS_PER_STEP
    assert sum(item.get("refused") == "step_limit" for item in result.tool_calls) == 1


async def test_history_processor_keeps_input_within_context_budget():
    page = "Длинный текст документа. " * 900
    reader = AsyncMock(side_effect=lambda context, parameters: {"source_id": "d", "text": page})
    executor = AgentToolExecutor(
        [AgentTool("read_source", "Прочитать источник", KnowledgeReadParameters, reader)]
    )
    runtime = TimRuntime(model=TestModel(), executor=executor)
    deps = deps_for(executor, context_tokens=9000, tool_rounds=5)
    deps.content["retrieval_context"] = [
        {"source_id": f"t:{i}", "text": "x" * 900} for i in range(40)
    ]
    sizes = []

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        total = 0
        for message in messages:
            for part in message.parts:
                content = getattr(part, "content", None)
                if content is not None:
                    total += estimate_tokens(
                        content if isinstance(content, str) else encode(content)
                    )
        sizes.append(total)
        if len(sizes) <= 4:
            return ModelResponse(parts=[ToolCallPart("read_source", {"source_id": "d"})])
        return final("Прочитано.")

    runtime.agent.model = FunctionModel(respond)
    result = await runtime.run(deps, metadata={})

    assert result.output.answer == "Прочитано."
    assert reader.await_count == 4
    # Каждый шаг укладывается в бюджет с запасом на системную инструкцию.
    assert max(sizes) <= 9000
    # Ранние чтения свёрнуты в квитанции, стартовый контекст сокращён явно.
    assert deps.content.get("retrieval_context_omitted")


@pytest.mark.parametrize("can_write", [False, True])
async def test_project_action_wrapper_does_not_bypass_write_right(can_write):
    handler = AsyncMock(return_value={"action": {"id": "x", "status": "completed"}})
    actions = AgentActionsService(scope=MagicMock(), actions=TIM_ACTIONS)
    actions.execute = handler
    query = MagicMock()
    query.resolve_project_id = AsyncMock(return_value=2)
    executor = build_project_tool_executor(query, actions)
    from src.agent.tools import AgentToolRequest

    result = await executor.execute(
        AgentToolContext(
            project_id=None, user_id=1, can_write=can_write, mode=TimMode.WIDGET_PERSONAL
        ),
        AgentToolRequest(
            name="run_project_action",
            arguments={
                "project_key": "OTHER",
                "name": "create_task",
                "arguments": {"title": "Новая"},
            },
        ),
    )
    assert handler.await_count == int(can_write)
    if not can_write:
        assert "error" in result


async def test_arguments_still_wrong_after_retries_do_not_fail_the_answer():
    executor, read, _create = small_executor()
    runtime = TimRuntime(model=TestModel(), executor=executor)
    calls = []

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        calls.append(len(messages))
        if len(calls) <= 3:
            return ModelResponse(parts=[ToolCallPart("get_task", {"task_id": "семь"})])
        return final("Не смог прочитать задачу.")

    runtime.agent.model = FunctionModel(respond)
    result = await runtime.run(deps_for(executor), metadata={})

    assert result.output.answer == "Не смог прочитать задачу."
    read.assert_not_awaited()
    assert [item.get("refused") for item in result.tool_calls] == ["arguments"] * 3


@pytest.mark.parametrize(
    ("model_name", "options", "expected_fields"),
    [
        ("google/gemini-3.7-flash", {}, {}),
        (
            "qwen/qwen3.8-flash",
            {"agent_reasoning_enabled": True, "agent_provider": "alibaba"},
            {
                "reasoning": {"enabled": True},
                "provider": {"only": ["alibaba"], "allow_fallbacks": False},
            },
        ),
        (
            "qwen/qwen3.8-flash",
            {"agent_reasoning_enabled": False, "agent_provider": "alibaba"},
            {
                "reasoning": {"enabled": False},
                "provider": {"only": ["alibaba"], "allow_fallbacks": False},
            },
        ),
        (
            "qwen/qwen3.5-flash-02-23",
            {"agent_reasoning_enabled": False, "agent_provider": "alibaba"},
            {
                "reasoning": {"enabled": False},
                "provider": {"only": ["alibaba"], "allow_fallbacks": False},
            },
        ),
        (
            "openai/gpt-6-luna",
            {
                "agent_reasoning_enabled": False,
                "agent_reasoning_effort": "none",
                "agent_provider": "openai",
            },
            {
                "reasoning": {"enabled": False, "effort": "none"},
                "provider": {"only": ["openai"], "allow_fallbacks": False},
            },
        ),
    ],
)
async def test_openai_compatible_model_sends_native_tools_to_polza_endpoint(
    model_name, options, expected_fields
):
    """Модель агента ходит в тот же адрес polza.ai с ключом и нативными tools."""
    import json as jsonlib

    import httpx

    from src.clients.agent_model import build_agent_model
    from src.core.settings import LlmSettings

    requests = []

    def reply(request: httpx.Request) -> httpx.Response:
        payload = jsonlib.loads(request.content)
        requests.append((request, payload))
        if len(requests) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "get_task", "arguments": '{"task_id": 7}'},
                    }
                ],
            }
        else:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c2",
                        "type": "function",
                        "function": {
                            "name": "final_result",
                            "arguments": '{"answer": "Задача в работе.", "source_ids": []}',
                        },
                    }
                ],
            }
        return httpx.Response(
            200,
            json={
                "id": f"r{len(requests)}",
                "object": "chat.completion",
                "created": 0,
                "model": payload["model"],
                "provider": "test-provider",
                "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "cost_rub": 0 if len(requests) == 1 else 0.01234,
                },
            },
        )

    settings = LlmSettings(
        _env_file=None,
        llm_api_key="secret-key",
        llm_api_url="https://polza.ai/api/v1/chat/completions",
        agent_model=model_name,
        **options,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http_client:
        model = build_agent_model(settings, http_client)
        executor, read, _create = small_executor()
        runtime = TimRuntime(model=model, executor=executor)
        result = await runtime.run(deps_for(executor, can_write=False), metadata={})

    assert result.output.answer == "Задача в работе."
    read.assert_awaited_once()
    first, payload = requests[0]
    assert str(first.url) == "https://polza.ai/api/v1/chat/completions"
    assert first.headers["authorization"] == "Bearer secret-key"
    assert payload["model"] == model_name
    for _request, body in requests:
        assert body["tool_choice"] == "auto"
        assert {
            key: body[key] for key in ("provider", "reasoning") if key in body
        } == expected_fields
    assert len({body["prompt_cache_key"] for _, body in requests}) == len(requests)
    names = {tool["function"]["name"] for tool in payload["tools"]}
    assert names == {"get_task", "final_result"}
    # Результат инструмента вернулся модели вторым запросом.
    assert any(item.get("role") == "tool" for item in requests[1][1]["messages"])
    # Инструкция не меняется от шага к шагу: префикс запроса кэшируется провайдером.
    assert requests[0][1]["messages"][0] == requests[1][1]["messages"][0]
    assert requests[0][1]["messages"][0]["role"] == "system"
    # Токены берутся из ответа шлюза, которого нет в каталоге genai-prices.
    assert (result.input_tokens, result.output_tokens) == (20, 10)
    assert [item["input_tokens"] for item in result.model_requests] == [10, 10]
    assert [item["tool_calls"] for item in result.model_requests] == [1, 1]
    assert all(item["ms"] >= 0 for item in result.model_requests)
    assert [item["cost_rub"] for item in result.model_requests] == [0, 0.01234]
    assert [item["provider_request_id"] for item in result.model_requests] == ["r1", "r2"]
    assert all(
        item["provider"] == "test-provider" and item["model"] == model_name
        for item in result.model_requests
    )


@pytest.mark.parametrize(
    ("agent_options", "read_timeout", "attempts"),
    [
        ({"agent_request_timeout_seconds": 30, "agent_request_attempts": 2}, 30, 2),
        ({}, 300, 3),
    ],
    ids=["agent-limits", "legacy-defaults"],
)
async def test_native_model_limits_http_timeout_attempts_and_preserves_legacy_defaults(
    monkeypatch, agent_options, read_timeout, attempts
):
    """SDK повторяет HTTP-таймаут только в пределах настроенного числа попыток."""
    import httpx
    from pydantic_ai.exceptions import ModelTimeoutError

    from src.clients.agent_model import build_agent_model
    from src.core.settings import LlmSettings

    monkeypatch.delenv("AGENT_REQUEST_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("AGENT_REQUEST_ATTEMPTS", raising=False)
    requests = []

    def timeout_response(request):
        requests.append(request)
        raise httpx.ReadTimeout("synthetic model timeout", request=request)

    settings = LlmSettings(
        _env_file=None,
        llm_api_key="test",
        llm_api_url="https://polza.ai/api/v1/chat/completions",
        agent_model="openai/gpt-6-luna",
        agent_reasoning_enabled=False,
        agent_reasoning_effort="none",
        agent_provider="openai",
        llm_timeout=300,
        llm_retries=3,
        **agent_options,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(timeout_response), timeout=300
    ) as http:
        executor, read, create = small_executor()
        runtime = TimRuntime(model=build_agent_model(settings, http), executor=executor)
        with pytest.raises(ModelTimeoutError):
            await runtime.run(deps_for(executor), metadata={})

    assert len(requests) == attempts
    assert all(request.extensions["timeout"]["read"] == read_timeout for request in requests)
    read.assert_not_awaited()
    create.assert_not_awaited()


@pytest.mark.parametrize("retry_succeeds", [True, False], ids=["final-recovered", "final-timeout"])
async def test_native_final_response_timeout_does_not_repeat_a_completed_create_task(
    retry_succeeds,
):
    """HTTP-повтор после квитанции create_task не запускает заново runtime или запись."""
    import json as jsonlib

    import httpx
    from pydantic_ai.exceptions import ModelTimeoutError

    from src.clients.agent_model import build_agent_model
    from src.core.settings import LlmSettings

    executor, read, create = small_executor()
    requests = []

    def reply(request):
        body = jsonlib.loads(request.content)
        requests.append((request, body))
        if len(requests) == 1:
            name, arguments = "create_task", {"title": "Проверка таймаута"}
        else:
            create.assert_awaited_once()
            if len(requests) == 2 or not retry_succeeds:
                raise httpx.ReadTimeout("synthetic final response timeout", request=request)
            name, arguments = "final_result", {"answer": "Задача создана.", "source_ids": []}
        return httpx.Response(
            200,
            json={
                "id": f"r{len(requests)}",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": f"c{len(requests)}",
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": jsonlib.dumps(arguments),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    settings = LlmSettings(
        _env_file=None,
        llm_api_key="test",
        llm_api_url="https://polza.ai/api/v1/chat/completions",
        agent_model="openai/gpt-6-luna",
        agent_reasoning_enabled=False,
        agent_reasoning_effort="none",
        agent_provider="openai",
        llm_timeout=300,
        llm_retries=3,
        agent_request_timeout_seconds=30,
        agent_request_attempts=2,
    )
    deps = deps_for(executor)
    write_intent = AsyncMock(return_value=True)
    deps.write_gate = WriteGate(write_intent())
    deps.on_result = AsyncMock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply), timeout=300) as http:
        runtime = TimRuntime(model=build_agent_model(settings, http), executor=executor)
        if retry_succeeds:
            result = await runtime.run(deps, metadata={})
            assert result.output.answer == "Задача создана."
            assert result.requests == 2
        else:
            with pytest.raises(ModelTimeoutError):
                await runtime.run(deps, metadata={})

    assert len(requests) == 3  # create_task, затем две HTTP-попытки итогового ответа.
    assert all(request.extensions["timeout"]["read"] == 30 for request, _ in requests)
    assert requests[1][1] == requests[2][1]
    receipt = next(item for item in requests[1][1]["messages"] if item["role"] == "tool")
    assert jsonlib.loads(receipt["content"])["action"]["status"] == "completed"
    assert jsonlib.loads(receipt["content"])["action"]["id"] == "a1"
    create.assert_awaited_once()
    read.assert_not_awaited()
    write_intent.assert_awaited_once()
    deps.on_result.assert_awaited_once()
    assert [call["tool"] for call in deps.calls] == ["create_task"]


async def test_steps_notice_appears_only_near_the_end():
    """Счётчик шагов добавляется в инструкцию только в конце: иначе кэш префикса не работает."""
    executor, _read, _create = small_executor()
    runtime = TimRuntime(model=TestModel(), executor=executor)
    instructions = []

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        instructions.append(messages[-1].instructions)
        if len(instructions) < 4:
            return ModelResponse(parts=[ToolCallPart("get_task", {"task_id": 7})])
        return final("Готово.")

    runtime.agent.model = FunctionModel(respond)
    result = await runtime.run(deps_for(executor, tool_rounds=4), metadata={})

    assert result.output.answer == "Готово."
    assert instructions[0] == instructions[1] == "Инструкция"
    assert "Осталось шагов с инструментами: 2." in instructions[2]
    assert "Осталось шагов с инструментами: 1." in instructions[3]


def test_tracker_route_accepts_null_answer() -> None:
    """Gemini присылает answer: null при выборе проекта — маршрут должен разобраться."""
    from src.services.tracker_agent import TrackerRoute

    route = TrackerRoute.model_validate({"mode": "project", "project_key": "EVL", "answer": None})
    assert route.answer == ""


async def test_text_answer_is_allowed_so_tool_choice_is_not_required():
    """Gemini через шлюз не принимает tool_choice=required при 20+ инструментах."""
    from pydantic_ai.messages import TextPart

    executor = registry_executor()
    runtime = TimRuntime(model=TestModel(), executor=executor)
    seen = {}

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        seen["allow_text_output"] = info.allow_text_output
        return ModelResponse(parts=[TextPart("Срок задачи — 3 октября.")])

    runtime.agent.model = FunctionModel(respond)
    result = await runtime.run(deps_for(executor), metadata={})

    assert seen["allow_text_output"] is True
    assert result.output.answer == "Срок задачи — 3 октября."
    assert result.output.source_ids == []


async def test_gateway_502_after_the_last_tool_step_gives_an_incomplete_answer_not_a_failure():
    """Шаги исчерпаны, а Gemini через polza всё равно зовёт инструмент — ответ «неполная проверка»."""
    from pydantic_ai.exceptions import ModelHTTPError

    executor, read, _create = small_executor()
    runtime = TimRuntime(model=TestModel(), executor=executor)
    calls = 0

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if not info.function_tools:
            raise ModelHTTPError(502, "google/gemini-3.7-flash", {"code": "BAD_GATEWAY"})
        return ModelResponse(parts=[ToolCallPart("get_task", {"task_id": 7})])

    runtime.agent.model = FunctionModel(respond)
    result = await runtime.run(deps_for(executor, tool_rounds=2), metadata={})

    assert result.output is None and read.await_count == 2 and calls == 3

    async def early(messages, info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(502, "google/gemini-3.7-flash", {"code": "BAD_GATEWAY"})

    runtime.agent.model = FunctionModel(early)
    # Сбой шлюза до исчерпания шагов — настоящая ошибка, её не маскируем.
    with pytest.raises(ModelHTTPError):
        await runtime.run(deps_for(executor, tool_rounds=2), metadata={})


async def test_small_talk_offers_only_native_loading_without_domain_context_or_write_check():
    executor = registry_executor()
    deps = deps_for(executor)
    deps.defer_workflows = True
    deps.system_prompt = TIM_BASE_PROMPTS[TimMode.WIDGET_PROJECT]
    deps.content["question"] = "Привет, как тебя зовут?"
    deps.load_context = AsyncMock()
    deps.write_gate_factory = Mock()

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        assert {tool.name for tool in info.function_tools} == {"load_capability"}
        assert "tasks:" in info.instructions
        assert "checklist_revision" not in info.instructions
        assert "СХЕМЫ НА ДОСКАХ" not in info.instructions
        assert len(deps.system_prompt) < len(TIM_SYSTEM_PROMPTS[TimMode.WIDGET_PROJECT]) // 3
        return final("Привет! Я Арис, проектный менеджер.")

    runtime = TimRuntime(model=FunctionModel(respond), executor=executor)
    result = await runtime.run(deps, metadata={})

    assert result.requests == 1
    assert result.output.answer == "Привет! Я Арис, проектный менеджер."
    assert result.tool_calls == []
    deps.load_context.assert_not_awaited()
    deps.write_gate_factory.assert_not_called()


async def test_old_assistant_names_remain_in_history_with_current_aris_instructions():
    """Старая переписка сохраняется дословно; текущая личность задаётся системной инструкцией."""
    history = [
        {"role": "user", "content": "Тим, расскажи о проекте."},
        {"role": "assistant", "content": "Я Вера, проектный менеджер. Готова помочь."},
    ]
    memory = "Вера договорилась проверить сроки задач."
    for mode in TimMode:
        for deferred, prompts in ((False, TIM_SYSTEM_PROMPTS), (True, TIM_BASE_PROMPTS)):
            executor, read, create = small_executor()
            deps = deps_for(executor, mode=mode, context_tokens=24000)
            deps.defer_workflows = deferred
            deps.system_prompt = prompts[mode]
            deps.content.update(
                question="Как тебя зовут сейчас?",
                dialog_history=history,
                dialog_memory=memory,
            )
            deps.budget = AgentContextBudget(
                system_prompt=deps.system_prompt, max_tokens=24000, history_tokens=1000
            )

            async def respond(messages, info: AgentInfo) -> ModelResponse:
                assert "Тебя зовут Арис (ARIS)" in info.instructions
                assert "TELOS SPACE" in info.instructions and "мужском роде" in info.instructions
                assert "Прежние имена в истории" in info.instructions
                assert "Вера" not in info.instructions and "Тим" not in info.instructions
                payload = prompt_payload(messages)
                assert payload["dialog_history"] == history
                assert payload["dialog_memory"] == memory
                return final("Я Арис, проектный менеджер TELOS SPACE. Готов помочь.")

            runtime = TimRuntime(model=FunctionModel(respond), executor=executor)
            result = await runtime.run(deps, metadata={})
            assert result.requests == 1 and result.tool_calls == []
            read.assert_not_awaited()
            create.assert_not_awaited()


async def test_native_workflow_loads_context_rules_and_tools_then_resets_for_the_next_run():
    """Приветствие с поручением проходит один loader и реальную запись с WriteGate."""
    executor, _, create = small_executor()
    executor = AgentToolExecutor(
        [
            *executor.tools.values(),
            AgentTool("get_board_scene", "Прочитать доску", KnowledgeReadParameters, AsyncMock()),
        ]
    )
    deps = deps_for(executor, tool_rounds=1)
    deps.defer_workflows = True
    deps.system_prompt = TIM_BASE_PROMPTS[TimMode.WIDGET_PROJECT]
    deps.content["question"] = "Привет! Создай задачу «Новая»."
    deps.write_gate_factory = Mock(side_effect=lambda: WriteGate(AsyncMock(return_value=True)()))

    async def load(group_id):
        assert group_id == "tasks"
        deps.content["current_postgres_state"] = {"loaded": True}

    deps.load_context = AsyncMock(side_effect=load)
    steps = 0

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal steps
        steps += 1
        visible = {tool.name for tool in info.function_tools}
        if steps == 1:
            assert visible == {"load_capability"}
            assert "checklist_revision" not in info.instructions
            deps.write_gate_factory.assert_not_called()
            return ModelResponse(parts=[ToolCallPart("load_capability", {"id": "tasks"})])
        if steps == 2:
            assert visible == {"load_capability", "get_task", "create_task"}
            assert "get_board_scene" not in visible
            from tests.unit.knowledge.agent_models import prompt_payload

            assert prompt_payload(messages)["current_postgres_state"] == {"loaded": True}
            loaded = [
                part.content["instructions"]
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_name == "load_capability"
            ]
            assert len(loaded) == 1
            assert TIM_WORKFLOW_INSTRUCTIONS["tasks"].strip() in loaded[0]
            deps.write_gate_factory.assert_not_called()
            return ModelResponse(parts=[ToolCallPart("create_task", {"title": "Новая"})])
        assert visible == set()
        return final("Создала задачу.")

    runtime = TimRuntime(model=FunctionModel(respond), executor=executor)
    result = await runtime.run(deps, metadata={})
    assert result.requests == 3
    assert result.prompt_chars == len(encode(deps.content))
    assert result.output.answer == "Создала задачу."
    create.assert_awaited_once()
    deps.load_context.assert_awaited_once_with("tasks")
    deps.write_gate_factory.assert_called_once_with()
    assert [call["tool"] for call in result.tool_calls] == ["load_capability", "create_task"]
    deps.write_gate.close()

    # Один и тот же агент не переносит раскрытый набор в новый вопрос.
    next_deps = deps_for(executor)
    next_deps.defer_workflows = True
    next_deps.load_context = AsyncMock()

    async def next_response(messages, info: AgentInfo) -> ModelResponse:
        assert {tool.name for tool in info.function_tools} == {"load_capability"}
        assert "checklist_revision" not in info.instructions
        return final("Здравствуйте.")

    runtime.agent.model = FunctionModel(next_response)
    await runtime.run(next_deps, metadata={})
    next_deps.load_context.assert_not_awaited()


async def test_loaded_workflows_keep_mode_scope_and_read_only_restrictions():
    executor = registry_executor()
    all_grouped = [name for group in WORKFLOWS for name in group.tools]
    assert len(all_grouped) == len(set(all_grouped))
    assert set(executor.tools) <= set(all_grouped)
    for mode, can_write, scopes, group_id in (
        (TimMode.PROJECT_CHAT, True, frozenset({"create_task"}), "tasks"),
        (TimMode.WIDGET_PROJECT, False, None, "tasks"),
        (TimMode.WIDGET_PERSONAL, True, frozenset({"create_personal_task"}), "personal"),
    ):
        deps = deps_for(executor, mode=mode, can_write=can_write, allowed_tools=scopes)
        deps.defer_workflows = True
        deps.load_context = AsyncMock()
        steps = 0
        group = next(group for group in WORKFLOWS if group.id == group_id)

        async def respond(
            messages, info: AgentInfo, deps=deps, group=group, group_id=group_id
        ) -> ModelResponse:
            nonlocal steps
            steps += 1
            if steps == 1:
                assert {tool.name for tool in info.function_tools} == {"load_capability"}
                for workflow in WORKFLOWS:
                    if not any(deps.allowed(name) for name in workflow.tools):
                        assert f"- {workflow.id}:" not in info.instructions
                return ModelResponse(parts=[ToolCallPart("load_capability", {"id": group_id})])
            expected = {name for name in group.tools if deps.allowed(name)}
            assert {tool.name for tool in info.function_tools} == expected | {"load_capability"}
            assert len(expected) <= 11
            return final("Проверено.")

        runtime = TimRuntime(model=FunctionModel(respond), executor=executor)
        await runtime.run(deps, metadata={})
        deps.load_context.assert_awaited_once_with(group_id)


async def test_loading_a_forbidden_workflow_cannot_read_context_or_reveal_project_tools():
    executor = registry_executor()
    deps = deps_for(executor, mode=TimMode.WIDGET_PERSONAL)
    deps.defer_workflows = True
    deps.load_context = AsyncMock()
    steps = 0

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal steps
        steps += 1
        assert {tool.name for tool in info.function_tools} == {"load_capability"}
        if steps == 1:
            return ModelResponse(parts=[ToolCallPart("load_capability", {"id": "sources"})])
        assert any(
            isinstance(part, RetryPromptPart) and "No capability found" in part.content
            for message in messages
            for part in message.parts
        )
        return final("Набор недоступен.")

    runtime = TimRuntime(model=FunctionModel(respond), executor=executor)
    result = await runtime.run(deps, metadata={})
    assert result.output.answer == "Набор недоступен."
    deps.load_context.assert_not_awaited()


async def test_a_domain_tool_cannot_execute_before_its_workflow_is_loaded():
    executor, read, create = small_executor()
    deps = deps_for(executor)
    deps.defer_workflows = True
    deps.load_context = AsyncMock()
    steps = 0

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal steps
        steps += 1
        if steps == 1:
            return ModelResponse(parts=[ToolCallPart("create_task", {"title": "Скрытая"})])
        assert any(
            isinstance(part, RetryPromptPart) for message in messages for part in message.parts
        )
        return final("Действие не выполнено.")

    runtime = TimRuntime(model=FunctionModel(respond), executor=executor)
    await runtime.run(deps, metadata={})
    create.assert_not_awaited()
    read.assert_not_awaited()
    deps.load_context.assert_not_awaited()


async def test_lazy_write_check_starts_only_for_valid_permitted_mutations_and_denies_writes():
    executor, read, create = small_executor()
    deps = deps_for(executor)
    deps.write_gate_factory = Mock(side_effect=lambda: WriteGate(AsyncMock(return_value=False)()))
    steps = 0

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal steps
        steps += 1
        if steps == 1:
            deps.write_gate_factory.assert_not_called()
            return ModelResponse(parts=[ToolCallPart("get_task", {"task_id": 7})])
        if steps == 2:
            deps.write_gate_factory.assert_not_called()
            return ModelResponse(parts=[ToolCallPart("create_task", {})])
        if steps == 3:
            deps.write_gate_factory.assert_not_called()
            return ModelResponse(
                parts=[
                    ToolCallPart("create_task", {"title": "Одна"}, tool_call_id="a"),
                    ToolCallPart("create_task", {"title": "Вторая"}, tool_call_id="b"),
                ]
            )
        return final("Запись не разрешена.")

    runtime = TimRuntime(model=FunctionModel(respond), executor=executor)
    result = await runtime.run(deps, metadata={})
    assert result.output.answer == "Запись не разрешена."
    read.assert_awaited_once()
    create.assert_not_awaited()
    deps.write_gate_factory.assert_called_once_with()
    deps.write_gate.close()


async def test_deferred_instructions_survive_history_result_compaction():
    reader = AsyncMock(return_value={"source_id": "d", "text": "Подробный текст. " * 1800})
    executor = AgentToolExecutor(
        [AgentTool("read_source", "Прочитать источник", KnowledgeReadParameters, reader)]
    )
    deps = deps_for(executor, context_tokens=9000, tool_rounds=4)
    deps.defer_workflows = True
    deps.content["retrieval_context"] = [
        {"source_id": f"t:{index}", "text": "x" * 900} for index in range(40)
    ]
    steps = 0
    compacted = []

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal steps
        steps += 1
        if steps == 1:
            return ModelResponse(parts=[ToolCallPart("load_capability", {"id": "sources"})])
        for message in messages:
            for part in message.parts:
                if isinstance(part, ToolReturnPart):
                    if part.tool_name == "load_capability":
                        assert TIM_WORKFLOW_INSTRUCTIONS["sources"] in part.content["instructions"]
                    elif isinstance(part.content, dict) and part.content.get("context_compacted"):
                        compacted.append(part.tool_name)
        if steps < 5:
            return ModelResponse(parts=[ToolCallPart("read_source", {"source_id": "d"})])
        return final("Прочитала.")

    runtime = TimRuntime(model=FunctionModel(respond), executor=executor)
    result = await runtime.run(deps, metadata={})
    assert result.output.answer == "Прочитала."
    assert reader.await_count == 3
    assert compacted


async def test_deferred_repeated_calls_end_as_incomplete_after_the_actual_loader_rounds():
    executor, read, _ = small_executor()
    deps = deps_for(executor, tool_rounds=2)
    deps.defer_workflows = True
    steps = 0

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal steps
        steps += 1
        if steps == 1:
            return ModelResponse(parts=[ToolCallPart("load_capability", {"id": "tasks"})])
        return ModelResponse(parts=[ToolCallPart("get_task", {"task_id": 7})])

    runtime = TimRuntime(model=FunctionModel(respond), executor=executor)
    result = await runtime.run(deps, metadata={})
    assert result.output is None
    assert read.await_count == 2
    assert result.requests == steps == 5  # one loader + two domain + two answer attempts


@pytest.mark.parametrize("host", ["polza.ai", "other.test"])
async def test_gateway_sends_and_measures_only_visible_schemas_before_and_after_loading(host):
    """Проверка wire payload через настоящий SDK и MockTransport, без настоящей LLM."""
    import json
    from dataclasses import replace

    import httpx
    from pydantic_ai.settings import ModelSettings

    from src.clients.agent_model import GatewayChatModel, build_agent_model
    from src.core.settings import LlmSettings

    seen = []
    route_labels = set()

    def reply(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if host == "polza.ai":
            label = payload.get("prompt_cache_key")
            if label in route_labels:
                return httpx.Response(400, json={"error": "No endpoints found"})
            route_labels.add(label)
        seen.append(payload)
        message = {"role": "assistant", "content": "Задача EVL-1 прочитана."}
        if len(seen) <= 2:
            name, arguments = (
                ("load_capability", {"id": "tasks"})
                if len(seen) == 1
                else ("get_task", {"task_key": "EVL-1"})
            )
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"step{len(seen)}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
        return httpx.Response(
            200,
            json={
                "id": "greeting",
                "object": "chat.completion",
                "created": 0,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if len(seen) <= 2 else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    read = AsyncMock(return_value={"task": {"id": 7, "key": "EVL-1"}})
    executor = AgentToolExecutor(
        [
            replace(tool, handler=read) if tool.name == "get_task" else tool
            for tool in registry_executor().tools.values()
        ]
    )
    deps = deps_for(executor)
    deps.defer_workflows = True
    deps.system_prompt = TIM_BASE_PROMPTS[TimMode.WIDGET_PROJECT]
    settings = LlmSettings(
        llm_api_key="unused-test-key", llm_api_url=f"https://{host}/api/v1/chat/completions"
    )
    extra_body = {"reasoning": {"effort": "low"}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        model = build_agent_model(settings, client)
        model = GatewayChatModel(
            settings.agent_model,
            provider=model.provider,
            settings=ModelSettings(extra_body=extra_body, temperature=0.3),
        )
        runtime = TimRuntime(model=model, executor=executor)
        result = await runtime.run(deps, metadata={})

    assert result.requests == len(seen) == 3
    assert result.output.answer == "Задача EVL-1 прочитана."
    read.assert_awaited_once()
    assert {item["function"]["name"] for item in seen[0]["tools"]} == {
        "load_capability",
        "final_result",
    }
    assert seen[0]["tool_choice"] == "auto"
    wire_counts = [
        sum(item["function"]["name"] != "final_result" for item in payload["tools"])
        for payload in seen
    ]
    assert wire_counts == [1, 11, 11]
    assert [item["tools_offered"] for item in result.model_requests] == wire_counts
    assert all(payload["reasoning"] == extra_body["reasoning"] for payload in seen)
    assert all(payload["temperature"] == 0.3 for payload in seen)
    assert extra_body == {"reasoning": {"effort": "low"}}
    if host == "polza.ai":
        assert len(route_labels) == 3 and None not in route_labels
    else:
        assert all("prompt_cache_key" not in payload for payload in seen)
    assert "checklist_revision" not in seen[0]["messages"][0]["content"]


async def test_native_loads_share_the_eight_call_limit_and_serialize_context_reads():
    import asyncio

    executor = registry_executor()
    deps = deps_for(executor)
    deps.defer_workflows = True
    active = 0
    maximum = 0

    async def load(group_id):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1

    deps.load_context = AsyncMock(side_effect=load)
    steps = 0

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal steps
        steps += 1
        if steps == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart("load_capability", {"id": group.id}, tool_call_id=group.id)
                    for group in WORKFLOWS[: MAX_CALLS_PER_STEP + 1]
                ]
            )
        loaded_rules = [
            part.content.get("instructions", "")
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "load_capability"
        ]
        assert sum(TIM_WORKFLOW_COMMON in rules for rules in loaded_rules) == 1
        return final("Выбраны нужные данные.")

    runtime = TimRuntime(model=FunctionModel(respond), executor=executor)
    result = await runtime.run(deps, metadata={})
    assert result.output.answer == "Выбраны нужные данные."
    assert deps.load_context.await_count == MAX_CALLS_PER_STEP
    assert maximum == 1
    assert sum(call.get("refused") == "step_limit" for call in result.tool_calls) == 1


async def test_project_action_wrapper_starts_no_gate_for_reads_or_missing_scopes_and_denies_writes():
    handler = AsyncMock(return_value={"project": {"key": "OTHER"}})
    actions = AgentActionsService(scope=MagicMock(), actions=TIM_ACTIONS)
    actions.execute = handler
    query = MagicMock()
    query.resolve_project_id = AsyncMock(return_value=2)
    executor = build_project_tool_executor(query, actions)
    deps = deps_for(
        executor, mode=TimMode.WIDGET_PERSONAL, allowed_tools=frozenset({"create_task"})
    )
    deps.defer_workflows = True
    deps.write_gate_factory = Mock(side_effect=lambda: WriteGate(AsyncMock(return_value=False)()))
    steps = 0

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal steps
        steps += 1
        if steps == 1:
            return ModelResponse(parts=[ToolCallPart("load_capability", {"id": "project_actions"})])
        if steps < 5:
            deps.write_gate_factory.assert_not_called()
            name, arguments = {
                2: ("update_task", {"task_id": 7, "title": "Нет scope"}),
                3: ("get_project", {}),
                4: ("create_task", {"title": "Нет поручения"}),
            }[steps]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_project_action",
                        {"project_key": "OTHER", "name": name, "arguments": arguments},
                    )
                ]
            )
        return final("Прочитала проект; изменения не выполнены.")

    runtime = TimRuntime(model=FunctionModel(respond), executor=executor)
    result = await runtime.run(deps, metadata={})
    assert result.output.answer == "Прочитала проект; изменения не выполнены."
    assert handler.await_count == 1
    assert handler.await_args.kwargs["name"] == "get_project"
    deps.write_gate_factory.assert_called_once_with()
    deps.write_gate.close()
