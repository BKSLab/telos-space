"""A small history page must remain readable alongside the protected workflow and chat."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel

from src.agent.aris import ArisMode
from src.agent.context import AgentContextBudget, encode
from src.agent.runtime import (
    ArisDeps,
    ArisRuntime,
    _compact_oldest_result,
    _compact_project_history_returns,
    _shrink_read_returns,
    _traffic_tokens,
)
from src.agent.tools import AgentToolContext
from src.prompts.project_agent import ARIS_BASE_PROMPTS
from src.schemas.project_history import ProjectHistoryItem, ProjectHistoryPage
from src.services.agent_chat_reference_registry import ChatReferenceRegistry
from src.services.agent_project_actions import list_project_history
from tests.unit.knowledge.agent_models import final, prompt_payload
from tests.unit.knowledge.test_aris_runtime import registry_executor
from tests.unit.knowledge.test_chat_history_runtime_budget import (
    current_message,
    dense_initial_messages,
)


def history_runtime_case(page_size=20):
    question = "Арис, что было за сегодня в проекте?"
    initial = dense_initial_messages()
    for row in initial[5:]:
        row["attachments"] = []
    chat = {
        "kind": "project_chat",
        "chat_id": 2,
        "timezone": "Europe/Samara",
        "messages": initial,
        "current_message": current_message(question),
        "quoted_message": None,
    }
    registry = ChatReferenceRegistry()
    registry.register(chat)
    preserved = [row["message_reference_handle"] for row in chat["messages"]]
    events = [
        ProjectHistoryItem(
            id=f"task_activity:{number}",
            kind="task_activity",
            title="Обновление задачи · Проверить сохранение результатов и карточку задачи",
            detail="Статус изменён: В работе → Завершена. Проверка интерфейса выполнена.",
            created_at=datetime(2026, 11, 20, 10, number, tzinfo=UTC),
            actor_name="Андрей Проверочный",
            actor_user_id=1,
            task_id=number,
            event_type="status_changed",
            from_value="В работе",
            to_value="Завершена",
        )
        for number in range(1, 7)
    ]
    for event in events[4:]:
        event.kind = "task_report"
        event.id = f"task_report:{event.task_id}"
        event.title = f"Итоговый отчёт · Проверить результаты задачи {event.task_id}"
        event.detail = "Опубликован отчёт: результаты проверки сохранены и доступны участникам."
        event.event_type = event.from_value = event.to_value = None
        event.report_id, event.report_revision = event.task_id, 1

    async def read(_project_id, filters):
        end = filters.offset + page_size
        return ProjectHistoryPage(
            items=events[filters.offset : end], next_offset=end if end < len(events) else None
        )

    database = SimpleNamespace(project_history=SimpleNamespace(read=AsyncMock(side_effect=read)))
    raw_results = []

    async def history(ctx, args):
        result = await list_project_history(database, ctx, args)
        raw_results.append(deepcopy(result))
        return result

    executor = registry_executor()
    name = "telos_list_project_history"
    executor.tools[name] = replace(executor.tools[name], handler=history)
    system = ARIS_BASE_PROMPTS[ArisMode.PROJECT_CHAT]
    budget = AgentContextBudget(system_prompt=system, max_tokens=12000, history_tokens=1200)
    content = {
        "question": question,
        "chat_context": chat,
        "current_date": "2026-11-20",
        "project_reference": {"id": 2, "key": "TELOS"},
        "current_participant": {"user_id": 1, "display_name": "Андрей Проверочный"},
        "writes_allowed": False,
    }
    budget.prepare(content)
    deps = ArisDeps(
        execution=AgentToolContext(
            project_id=2,
            user_id=1,
            mode=ArisMode.PROJECT_CHAT,
            timezone="Europe/Samara",
            principal=Mock(allows=Mock(return_value=True)),
        ),
        executor=executor,
        system_prompt=system,
        content=content,
        budget=budget,
        tool_rounds=4,
        defer_workflows=True,
        load_context=AsyncMock(),
    )
    deps.write_gate_factory = Mock(side_effect=AssertionError("History is readonly"))
    return deps, events, raw_results, preserved


def history_rows(page):
    if "item_fields" in page:
        return [
            {**page.get("item_defaults", {}), **dict(zip(page["item_fields"], row, strict=True))}
            for row in page["items"]
        ]
    return page.get("items", [])


@pytest.mark.parametrize("page_size", [20, 3])
async def test_today_history_is_read_to_completion_with_dense_chat_and_real_project_workflow(
    page_size,
):
    deps, events, raw_results, preserved = history_runtime_case(page_size)
    budget, content, executor = deps.budget, deps.content, deps.executor
    question, name = content["question"], "telos_list_project_history"
    step, loader_content, observed = 0, None, []

    async def respond(messages, _info):
        nonlocal step, loader_content
        step += 1
        assert budget.tokens(content) + _traffic_tokens(messages) <= 12000
        payload = prompt_payload(messages)
        assert payload["question"] == question
        assert [
            row["message_reference_handle"] for row in payload["chat_context"]["messages"]
        ] == preserved
        if step == 1:
            return ModelResponse(parts=[ToolCallPart("telos_load_capability", {"id": "project"})])
        loader = next(
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "telos_load_capability"
        )
        if step == 2:
            loader_content = encode(loader.content)
            return ModelResponse(parts=[ToolCallPart(name, {"period": "today"})])
        assert encode(loader.content) == loader_content
        page = next(
            part.content
            for message in reversed(messages)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == name
        )
        for row in history_rows(page):
            assert {key: value for key, value in row.items() if value is not None} == next(
                event.model_dump(mode="json", exclude_none=True)
                for event in events
                if event.id == row["id"]
            )
        observed.extend(row["id"] for row in history_rows(page))
        assert page["period"] == raw_results[0]["period"]
        if page["has_more"]:
            assert page["next_arguments"]["offset"] == len(observed)
            return ModelResponse(parts=[ToolCallPart(name, page["next_arguments"])])
        visible_events = {
            row["id"]: row
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == name
            for row in history_rows(part.content)
        }
        assert set(visible_events) == {event.id for event in events}
        for event in events:
            assert {
                key: value for key, value in visible_events[event.id].items() if value is not None
            } == event.model_dump(mode="json", exclude_none=True)
        return final("Сегодня завершили четыре задачи и опубликовали два отчёта.")

    result = await ArisRuntime(model=FunctionModel(respond), executor=executor).run(
        deps, metadata={}
    )
    assert result.output is not None, (step, result.exhaustion_reason, budget.tokens(content))
    assert observed == [event.id for event in events]
    assert len(encode(raw_results[0])) < 4500
    assert len(deps._domain_steps) <= 4
    deps.write_gate_factory.assert_not_called()


def test_runtime_page_reduction_requires_known_offset_and_preserves_protected_results():
    rows = [{"source_id": f"task:{number}"} for number in range(4)]
    page = {"items": rows, "next_offset": 24}
    protected = [
        ("telos_list_tasks", page),  # The page start cannot be inferred from its end.
        ("telos_list_tasks", {"sources": rows}),
        ("telos_load_capability", {**page, "offset": 20}),
        ("telos_update_task", {**page, "offset": 20, "action": {"status": "completed"}}),
    ]
    for name, content in protected:
        messages = [ModelRequest(parts=[ToolReturnPart(name, deepcopy(content), "call")])]
        original = deepcopy(messages)
        assert not _shrink_read_returns(messages, include_pages=True)
        assert messages == original


def test_personal_history_pages_are_identified_by_wrapper_call_and_retained_for_final_answer():
    _, events, _, _ = history_runtime_case()
    name = "telos_run_project_action"
    pages = [
        {
            "items": [event.model_dump(mode="json") for event in events[offset : offset + 3]],
            "offset": offset,
            "next_offset": 3 if offset == 0 else None,
        }
        for offset in (0, 3)
    ]
    originals = deepcopy(pages)
    messages = []
    for index, page in enumerate(pages):
        arguments = {
            "project_key": "TELOS",
            "name": "telos_list_project_history",
            "arguments": {"offset": index * 3},
        }
        messages.extend(
            [
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            name, arguments if index == 0 else encode(arguments), f"history-{index}"
                        )
                    ]
                ),
                ModelRequest(parts=[ToolReturnPart(name, page, f"history-{index}")]),
            ]
        )
    # A matching DTO shape alone must not turn another action or unmatched result into history.
    unrelated = deepcopy(pages[0])
    messages[:0] = [
        ModelResponse(parts=[ToolCallPart(name, {"name": "telos_list_tasks"}, "other")]),
        ModelRequest(parts=[ToolReturnPart(name, unrelated, "other")]),
        ModelRequest(parts=[ToolReturnPart(name, unrelated, "unmatched")]),
        ModelResponse(
            parts=[ToolCallPart(name, "{", "invalid"), ToolCallPart(name, "[]", "array")]
        ),
        ModelRequest(
            parts=[
                RetryPromptPart("Invalid JSON", tool_name=name, tool_call_id="invalid"),
                RetryPromptPart("Expected object", tool_name=name, tool_call_id="array"),
            ]
        ),
    ]
    while _compact_project_history_returns(messages):
        pass
    assert "item_fields" not in messages[1].parts[0].content
    assert "item_fields" not in messages[2].parts[0].content
    while _compact_oldest_result(messages, keep_from=len(messages) - 1):
        pass
    visible = [
        row
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_call_id.startswith("history-")
        for row in history_rows(part.content)
    ]
    assert [{key: value for key, value in row.items() if value is not None} for row in visible] == [
        event.model_dump(mode="json", exclude_none=True) for event in events
    ]
    # Later pressure may reduce the newest page, but not erase events already read earlier.
    first_page = deepcopy(messages[-3].parts[0].content)
    assert _shrink_read_returns(messages, include_pages=True)
    assert messages[-3].parts[0].content == first_page
    assert pages == originals
