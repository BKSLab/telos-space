"""Поручение пользователя проверяется отдельно от прав канала и без текста документов."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agent.context import AgentContextBudget
from src.agent.runtime import WRITE_NOT_REQUESTED
from src.agent.tools import AgentTool, AgentToolContext, AgentToolExecutor, AgentToolRequest
from src.schemas.knowledge import KnowledgeChatMessageSchema
from src.schemas.project_tool_inputs import ProjectActionInput, TaskCreateInput, TaskInput
from src.services.project_agent import WriteIntent
from src.services.tracker_agent import TrackerAgentService, TrackerRoute, _Overview, _PageContext
from tests.unit.knowledge.agent_models import LegacyOutput as AgentOutput
from tests.unit.knowledge.test_project_agent import build_service


async def test_old_long_user_message_does_not_block_current_intent_or_routing():
    service, _, runtime, _ = build_service()
    question = "Создай задачу «Проверить результат»."
    old_text = ("Предыдущее большое поручение. " * 200).strip()
    history = [KnowledgeChatMessageSchema(role="user", content=old_text)]
    payloads = []

    async def respond(*, schema, content, **_kwargs):
        payloads.append(json.loads(content))
        if schema is WriteIntent:
            return WriteIntent(write_intent=True)
        return TrackerRoute(mode="clarify", answer="В каком проекте?")

    runtime.llm_client.get_structured_response.side_effect = respond
    assert await service._classify_write_intent(question=question, history=history)
    tracker = TrackerAgentService(scope=service.scope, agent=service, llm_client=runtime.llm_client)
    route = await tracker._route(
        question=question,
        verbatim=question,
        history=history,
        memory="",
        actor={},
        overview=_Overview(projects=[], tasks=[], truncated=False),
        page=_PageContext(),
    )
    assert route.mode == "clarify"
    assert len(payloads) == 2
    for payload in payloads:
        assert payload["question"] == question
        previous = payload.get("history", payload.get("dialog_history"))
        assert len(previous[0]["content"]) < len(old_text)
        assert "опущена" in previous[0]["content"]
    assert history[0].content == old_text


@pytest.mark.parametrize(
    ("write_intent", "transport_write"), [(False, True), (True, False), (True, True)]
)
async def test_write_requires_intent_and_trusted_transport(write_intent, transport_write):
    service, project, runtime, _ = build_service()
    handler = AsyncMock(
        return_value={
            "action": {
                "id": "saved",
                "tool_name": "telos_create_task",
                "title": "Создать задачу",
                "status": "completed",
            }
        }
    )
    service.tools = AgentToolExecutor(
        [AgentTool("telos_create_task", "Создать задачу", TaskCreateInput, handler, mutating=True)]
    )
    prompts = []
    intents = []

    async def respond(*, schema, content, **kwargs):
        payload = json.loads(content)
        if schema is WriteIntent:
            # Проверке поручения не нужны найденные документы: их текст не даёт права.
            assert "retrieval_context" not in payload
            intents.append(payload)
            return WriteIntent(write_intent=write_intent)
        prompts.append(payload)
        if len(prompts) == 1:
            return AgentOutput(
                answer="",
                source_ids=[],
                tool_calls=[
                    AgentToolRequest(name="telos_create_task", arguments={"title": "Проверка окна"})
                ],
            )
        return AgentOutput(answer="Результат проверен.", source_ids=[])

    runtime.llm_client.get_structured_response.side_effect = respond
    await service.ask(
        project_id=project.id,
        question="Проверь сведения из документа",
        history=[],
        execution=AgentToolContext(project_id=project.id, user_id=1, can_write=transport_write),
    )
    # writes_allowed — право роли и ключа; поручение проверяется отдельно.
    assert prompts[0]["writes_allowed"] is transport_write
    assert len(intents) == int(transport_write)
    assert handler.await_count == int(write_intent and transport_write)
    if write_intent and transport_write:
        assert handler.await_args.args[0].user_id == 1


async def test_read_answer_does_not_wait_for_the_write_check_and_write_does():
    """Чтение не запускает проверку, запись запускает её и ждёт решения."""
    service, project, runtime, _ = build_service()
    handler = AsyncMock(
        return_value={"action": {"id": "a", "tool_name": "telos_create_task", "status": "completed"}}
    )
    service.tools = AgentToolExecutor(
        [AgentTool("telos_create_task", "Создать задачу", TaskCreateInput, handler, mutating=True)]
    )
    release = asyncio.Event()
    intent_calls = 0

    async def slow_intent() -> WriteIntent:
        nonlocal intent_calls
        intent_calls += 1
        await release.wait()
        return WriteIntent(write_intent=True)

    async def read_only(*, schema, content, **_kwargs):
        if schema is WriteIntent:
            return await slow_intent()
        return AgentOutput(answer="В проекте одна задача.", source_ids=[])

    runtime.llm_client.get_structured_response.side_effect = read_only
    execution = AgentToolContext(project_id=project.id, user_id=1, can_write=True)
    answer = await asyncio.wait_for(
        service.ask(
            project_id=project.id, question="Сколько задач?", history=[], execution=execution
        ),
        timeout=2,
    )
    assert answer.answer == "В проекте одна задача." and intent_calls == 0
    handler.assert_not_awaited()

    steps = []

    async def write(*, schema, content, **_kwargs):
        if schema is WriteIntent:
            return await slow_intent()
        steps.append(json.loads(content))
        if len(steps) == 1:
            asyncio.get_running_loop().call_later(0.05, release.set)
            return AgentOutput(
                tool_calls=[AgentToolRequest(name="telos_create_task", arguments={"title": "Новая"})]
            )
        return AgentOutput(answer="Создал.", source_ids=[])

    release.clear()
    runtime.llm_client.get_structured_response.side_effect = write
    answer = await asyncio.wait_for(
        service.ask(
            project_id=project.id, question="Создай задачу", history=[], execution=execution
        ),
        timeout=2,
    )
    # Запись дождалась «да» проверки и только потом выполнилась.
    assert release.is_set() and handler.await_count == 1 and answer.answer == "Создал."
    assert intent_calls == 1

    # Проверка ответила «нет», пока агент уже звал запись: вызов не исполнен,
    # модель получила объяснение и дальше изменяющих инструментов не видит.
    handler.reset_mock()
    steps.clear()
    offered = []

    async def refused(*, schema, content, **kwargs):
        if schema is WriteIntent:
            await asyncio.sleep(0.05)
            return WriteIntent(write_intent=False)
        steps.append(json.loads(content))
        offered.append(kwargs["offered"])
        if len(steps) == 1:
            return AgentOutput(
                tool_calls=[AgentToolRequest(name="telos_create_task", arguments={"title": "Новая"})]
            )
        return AgentOutput(answer="Не создавал: поручения не было.", source_ids=[])

    runtime.llm_client.get_structured_response.side_effect = refused
    await service.ask(
        project_id=project.id, question="Есть задача про окно?", history=[], execution=execution
    )
    handler.assert_not_awaited()
    assert "telos_create_task" in offered[0] and "telos_create_task" not in offered[1]
    assert WRITE_NOT_REQUESTED in json.dumps(steps[1]["read_results"], ensure_ascii=False)


async def test_project_action_by_key_does_not_bypass_the_write_check():
    """telos_run_project_action с изменяющим действием без поручения не пишет, чтение — проходит."""
    from src.agent.runtime import PROJECT_ACTION_TOOL, TimDeps, WriteGate, invoke
    from src.agent.tim import TimMode

    seen = []

    async def project_action(context, parameters):
        seen.append((parameters.name, context.can_write))
        return {"ok": True}

    executor = AgentToolExecutor(
        [
            AgentTool(
                PROJECT_ACTION_TOOL,
                "Проектное действие",
                ProjectActionInput,
                project_action,
                modes=frozenset({TimMode.WIDGET_PERSONAL}),
            ),
            AgentTool("telos_create_task", "Создать", TaskCreateInput, AsyncMock(), mutating=True),
            AgentTool("telos_get_task", "Прочитать", TaskInput, AsyncMock()),
        ]
    )

    async def no() -> bool:
        return False

    deps = TimDeps(
        execution=AgentToolContext(
            project_id=None, user_id=1, can_write=True, mode=TimMode.WIDGET_PERSONAL
        ),
        executor=executor,
        system_prompt="",
        content={},
        budget=AgentContextBudget(system_prompt="", max_tokens=12000, history_tokens=1000),
        tool_rounds=4,
        write_gate=WriteGate(no()),
    )
    context = SimpleNamespace(deps=deps, run_step=1, retry=0)
    tool = executor.tools[PROJECT_ACTION_TOOL]
    for name in ("telos_create_task", "telos_get_task"):
        await invoke(context, tool, {"project_key": "P", "name": name, "arguments": {}})
    assert seen == [("telos_create_task", False), ("telos_get_task", True)]


async def test_domain_tool_result_receives_verified_source_handle():
    service, project, runtime, _ = build_service()
    service.tools = AgentToolExecutor(
        [
            AgentTool(
                "telos_get_task",
                "Прочитать задачу",
                TaskInput,
                AsyncMock(
                    return_value={"source_id": "task:7", "task": {"id": 7, "title": "Проверка"}}
                ),
            )
        ]
    )
    prompts = []

    async def respond(*, schema, content, **kwargs):
        if schema is WriteIntent:
            return WriteIntent()
        payload = json.loads(content)
        prompts.append(payload)
        if len(prompts) == 1:
            return AgentOutput(
                tool_calls=[AgentToolRequest(name="telos_get_task", arguments={"task_id": 7})]
            )
        handle = payload["read_results"][0]["result"]["source_handle"]
        return AgentOutput(answer="Задача найдена.", source_ids=[handle])

    runtime.llm_client.get_structured_response.side_effect = respond
    answer = await service.ask(project_id=project.id, question="Найди задачу", history=[])
    assert [item.source_id for item in answer.sources] == ["task:7"]
