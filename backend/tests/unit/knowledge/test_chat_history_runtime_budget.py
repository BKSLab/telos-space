"""Native history reads fit alongside the fixed 15-message window without losing cursors."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from src.agent.aris import ArisMode
from src.agent.chat_context import chat_receipt
from src.agent.context import AgentContextBudget, encode, estimate_tokens
from src.agent.runtime import ArisDeps, ArisRuntime, _fit_result, _traffic_tokens, metrics
from src.agent.tools import AgentTool, AgentToolContext, AgentToolExecutor
from src.prompts.project_agent import ARIS_BASE_PROMPTS
from src.schemas.chat_history import (
    ChatHistoryFiltersSchema,
    ChatHistoryMessageSchema,
    ChatHistoryPageSchema,
)
from src.schemas.project_tool_inputs import ChatMessageInput, ChatMessagesInput, EmptyInput
from src.services.agent_chat_reference_registry import ChatReferenceRegistry
from tests.unit.knowledge.agent_models import final, prompt_payload


def message(message_id: int, *, with_file: bool = False) -> dict:
    content = f"Сообщение {message_id}: проверю результат и пришлю ответ после повторной проверки."
    created = datetime(2026, 11, 12, 10, tzinfo=UTC) + timedelta(minutes=message_id)
    reference = {
        "kind": "project_chat",
        "chat_id": 2,
        "message_id": message_id,
        "project_id": 2,
        "project_key": "TELOS",
        "author_id": 2,
        "author_name": "Андрей Проверочный",
        "created_at": created,
        "revision": 1,
        "content_hash": sha256(content.encode()).hexdigest(),
        "quote": content,
        "start": 0,
        "end": len(content),
        "offset_unit": "unicode_codepoints",
        "state": "available",
    }
    attachments = (
        [
            {
                "file_id": "8d06b2c4-d25b-4c2b-8a0c-7a274a7247cb",
                "message_id": message_id,
                "original_name": "Результаты.md",
                "content_type": "text/markdown",
                "size": 40000,
                "created_at": created,
                "status": "ready",
                "total_chars": 40000,
                "original_chars": 40000,
                "content_hash": "a" * 64,
                "excerpt": "Результаты проверки.",
            }
        ]
        if with_file
        else []
    )
    return ChatHistoryMessageSchema(
        id=message_id,
        chat_id=2,
        kind="project_chat",
        project_id=2,
        project_key="TELOS",
        author_id=2,
        author_name="Андрей Проверочный",
        author_type="USER",
        created_at=created,
        revision=1,
        content=content,
        text_truncated=False,
        content_offset=0,
        content_total_chars=len(content),
        next_content_offset=None,
        reply_to_message_id=None,
        attachments=attachments,
        reference=reference,
    ).model_dump(mode="json")


def page(rows: list[dict], *, before: int | None = None, after: int | None = None) -> dict:
    return ChatHistoryPageSchema(
        items=rows,
        has_more=False,
        next_before_message_id=None,
        next_after_message_id=None,
        applied_filters=ChatHistoryFiltersSchema(
            chat_id=2,
            kind="project_chat",
            timezone="Europe/Samara",
            before_message_id=before,
            after_message_id=after,
            date_from="2026-11-12",
            date_to="2026-11-12",
        ),
    ).model_dump(mode="json")


def dense_initial_messages():
    rows = [message(number, with_file=True) for number in range(86, 101)]
    for row in rows:
        file = row["attachments"][0]
        file["file_id"] = str(UUID(int=row["id"]))
        file["excerpt"] = "Короткие исходные результаты проверки интерфейса. " * 8
        row["entity_references"] = [
            {
                "entity_type": "task",
                "entity_id": row["id"],
                "document_ref": None,
                "source_id": f"task:{row['id']}",
                "task_id": row["id"],
                "key": f"TELOS-{row['id']}",
                "title": "Проверить работу интерфейса проекта",
            }
        ]
    return rows


def current_message(question):
    row = message(101)
    row.update(content=question, content_total_chars=len(question))
    row["reference"].update(
        content_hash=sha256(question.encode()).hexdigest(), quote=question, end=len(question)
    )
    return row


@pytest.mark.parametrize("direction", ["default", "before", "after"])
def test_compacted_history_pages_can_be_fully_read_without_gaps_or_duplicates(direction):
    registry = ChatReferenceRegistry()
    rows = [message(number) for number in range(2, 52)]
    registry.register(deepcopy(rows))
    records = deepcopy(registry.records)
    cursor = 1 if direction == "after" else 1000 if direction == "before" else None
    observed = []
    while True:
        after = cursor if direction == "after" else None
        before = cursor if direction != "after" else None
        remaining = [
            row
            for row in rows
            if (after is None or row["id"] > after) and (before is None or row["id"] < before)
        ]
        if not remaining:
            break
        raw = page(deepcopy(remaining), before=before, after=after)
        registry.register(raw)
        original = deepcopy(raw)
        fitted = _fit_result(raw, 900)
        assert estimate_tokens(encode(fitted)) <= 900
        assert raw == original
        kept = fitted["items"]
        ids = [item["id"] for item in kept]
        assert ids == sorted(ids)
        assert ids == [
            item["id"]
            for item in (remaining[: len(ids)] if after is not None else remaining[-len(ids) :])
        ]
        for item in kept:
            assert item["message_reference_handle"] in registry.records
            assert item["author_id"] == 2 and item["author_name"] == "Андрей Проверочный"
            assert item["created_at"] and item["attachments"] == []
            assert item["content"] == message(item["id"])["content"]
        observed.extend(ids)
        if not fitted["has_more"]:
            break
        assert fitted["items_omitted"] == len(remaining) - len(kept)
        if direction == "after":
            cursor = fitted["next_after_message_id"]
            assert cursor == ids[-1] and fitted["next_before_message_id"] is None
        else:
            cursor = fitted["next_before_message_id"]
            assert cursor == ids[0] and fitted["next_after_message_id"] is None
    assert sorted(observed) == list(range(2, 52))
    assert len(observed) == len(set(observed))
    assert registry.records == records


def test_history_receipt_retains_verified_handles_and_continuation_without_duplicate_snapshots():
    registry = ChatReferenceRegistry()
    raw = page([message(5, with_file=True), message(6)], before=100)
    registry.register(raw)
    original = deepcopy(raw)
    records = deepcopy(registry.records)
    receipt = chat_receipt(raw)
    assert raw == original and registry.records == records
    assert [row["id"] for row in receipt["messages"]] == [5, 6]
    assert receipt["applied_filters"] == {
        key: value for key, value in raw["applied_filters"].items() if value is not None
    }
    for row in receipt["messages"]:
        record = registry.records[row["message_reference_handle"]]
        assert record.message_id == row["id"] and record.author_name == "Андрей Проверочный"
        assert record.quote == original["items"][row["id"] - 5]["content"]
        assert not {"content", "content_hash", "author_name", "created_at"}.intersection(row)
    assert estimate_tokens(encode(receipt)) < estimate_tokens(encode(raw)) // 2


async def test_native_search_then_exact_quote_fits_real_shared_prompt_and_fifteen_messages():
    registry = ChatReferenceRegistry()
    initial = dense_initial_messages()
    question = "12 ноября обсуждали баг. Что написал Андрей? Найди и процитируй его ответ."
    current = current_message(question)
    chat = {
        "kind": "project_chat",
        "chat_id": 2,
        "timezone": "Europe/Samara",
        "current_datetime": "2026-11-14T22:00:00+04:00",
        "messages": initial,
        "current_message": current,
        "quoted_message": None,
    }
    registry.register(chat)
    structure = [
        {
            key: deepcopy(row[key])
            for key in (
                "id",
                "chat_id",
                "kind",
                "author_id",
                "author_name",
                "created_at",
                "revision",
                "reply_to_message_id",
                "attachments",
                "entity_references",
            )
        }
        for row in initial
    ]
    pages = [
        page([message(number) for number in range(1, 16)]),
        page([message(number) for number in range(1, 51)]),
    ]
    read = AsyncMock(side_effect=deepcopy(pages))
    exact = AsyncMock(return_value={"message": message(5), "neighbors": [message(4), message(6)]})
    executor = AgentToolExecutor(
        [
            AgentTool("telos_list_chat_messages", "Найти сообщения", ChatMessagesInput, read),
            AgentTool("telos_get_chat_message", "Прочитать сообщение", ChatMessageInput, exact),
        ]
    )
    system = ARIS_BASE_PROMPTS[ArisMode.PROJECT_CHAT]
    budget = AgentContextBudget(system_prompt=system, max_tokens=12000, history_tokens=1200)
    content = {
        "question": question,
        "current_date": "2026-11-14",
        "chat_context": chat,
        "current_participant": {"user_id": 1, "display_name": "Кирилл Проверочный"},
        "project_reference": {"id": 2, "key": "TELOS"},
        "writes_allowed": False,
    }
    budget.prepare(content)
    deps = ArisDeps(
        execution=AgentToolContext(project_id=2, user_id=1, mode=ArisMode.PROJECT_CHAT),
        executor=executor,
        system_prompt=system,
        content=content,
        budget=budget,
        tool_rounds=8,
        defer_workflows=True,
        load_context=AsyncMock(),
    )

    async def register(tool, result):
        registry.register(result)

    deps.on_result = register
    step = 0
    selected = None

    async def respond(messages, info):
        nonlocal step, selected
        step += 1
        assert budget.tokens(deps.content) + _traffic_tokens(messages) <= 12000
        history = prompt_payload(messages)["chat_context"]["messages"]
        assert len(history) == 15
        for index, row in enumerate(history):
            for key, value in structure[index].items():
                if key == "attachments":
                    assert [file["file_id"] for file in row[key]] == [
                        file["file_id"] for file in value
                    ]
                elif key == "entity_references":
                    assert [
                        {name: item for name, item in ref.items() if item is not None}
                        for ref in row[key]
                    ] == [
                        {name: item for name, item in ref.items() if item is not None}
                        for ref in value
                    ]
                else:
                    assert row.get(key) == value
        if step == 1:
            return ModelResponse(
                parts=[ToolCallPart("telos_load_capability", {"id": "chat_history"})]
            )
        if step in {2, 3}:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "telos_list_chat_messages",
                        {
                            "date_from": "2026-11-12",
                            "date_to": "2026-11-12",
                            "limit": 50 if step == 3 else 15,
                        },
                    )
                ]
            )
        if step == 4:
            return ModelResponse(parts=[ToolCallPart("telos_get_chat_message", {"message_id": 5})])
        for entry in reversed(messages):
            for part in entry.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == "telos_get_chat_message":
                    assert part.content["message"]["content"] == message(5)["content"]
                    selected = part.content["message"]["message_reference_handle"]
                    assert registry.records[selected].message_id == 5
                    return final("Нашёл ответ Андрея.", message_references=[selected])
        raise AssertionError("Exact message read is missing")

    result = await ArisRuntime(model=FunctionModel(respond), executor=executor).run(
        deps, metadata={}
    )
    assert result.output is not None and result.output.message_references == [selected]
    assert result.requests == step == 5 and read.await_count == 2 and exact.await_count == 1
    assert registry.records[selected].quote == message(5)["content"]


async def test_saved_summary_remains_readable_with_dense_initial15_and_protected_reports_loader():
    question = "Арис, покажи краткое резюме последнего сохранённого обзора проекта."
    initial = dense_initial_messages()
    current = current_message(question)
    registry = ChatReferenceRegistry()
    chat = {
        "kind": "project_chat",
        "chat_id": 2,
        "timezone": "Europe/Samara",
        "messages": initial,
        "current_message": current,
        "quoted_message": None,
    }
    registry.register(chat)
    preserved = [
        (
            row["id"],
            row["author_id"],
            row["author_name"],
            row["created_at"],
            row["attachments"][0]["file_id"],
            row["entity_references"][0]["entity_id"],
            row["message_reference_handle"],
        )
        for row in initial
    ]
    reader = AsyncMock(
        return_value={
            "found": True,
            "source_id": "analytics_report:7",
            "source_handle": "SRC_verified_summary",
            "provenance": "generated_analysis",
            "report": {
                "created_at": "2026-11-12T10:00:00Z",
                "headline": "Сохранённый обзор проекта",
                "summary_md": "Согласовали историю проекта и проверку интерфейса: SUMMARY_CONTROL.\n"
                + "Подробности сохранённого обзора проекта. " * 180,
            },
        }
    )
    executor = AgentToolExecutor(
        [
            AgentTool(
                "telos_get_latest_project_summary",
                "Прочитать сохранённый обзор",
                EmptyInput,
                reader,
            )
        ]
    )
    system = ARIS_BASE_PROMPTS[ArisMode.PROJECT_CHAT]
    budget = AgentContextBudget(system_prompt=system, max_tokens=12000, history_tokens=1200)
    content = {
        "question": question,
        "chat_context": chat,
        "current_date": "2026-11-15",
        "project_reference": {"id": 2, "key": "TELOS"},
        "current_participant": {"user_id": 1, "name": "Synthetic Smoke"},
        "writes_allowed": False,
    }
    budget.prepare(content)
    deps = ArisDeps(
        execution=AgentToolContext(project_id=2, user_id=1, mode=ArisMode.PROJECT_CHAT),
        executor=executor,
        system_prompt=system,
        content=content,
        budget=budget,
        tool_rounds=8,
        defer_workflows=True,
        load_context=AsyncMock(),
    )
    deps.write_gate_factory = Mock(
        side_effect=AssertionError("Readonly summary must not open WriteGate")
    )
    step, loader_content = 0, None

    async def respond(messages, _info):
        nonlocal step, loader_content
        step += 1
        assert budget.tokens(deps.content) + _traffic_tokens(messages) <= 12000
        rows = prompt_payload(messages)["chat_context"]["messages"]
        assert preserved == [
            (
                row["id"],
                row["author_id"],
                row["author_name"],
                row["created_at"],
                row["attachments"][0]["file_id"],
                row["entity_references"][0]["entity_id"],
                row["message_reference_handle"],
            )
            for row in rows
        ]
        assert prompt_payload(messages)["question"] == question
        if step == 1:
            return ModelResponse(parts=[ToolCallPart("telos_load_capability", {"id": "reports"})])
        loader = next(
            part
            for msg in messages
            for part in msg.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "telos_load_capability"
        )
        if step == 2:
            loader_content = encode(loader.content)
            return ModelResponse(parts=[ToolCallPart("telos_get_latest_project_summary", {})])
        assert encode(loader.content) == loader_content
        report = next(
            part.content
            for msg in reversed(messages)
            for part in msg.parts
            if isinstance(part, ToolReturnPart)
            and part.tool_name == "telos_get_latest_project_summary"
        )
        assert report["found"] and report["source_id"] == "analytics_report:7"
        assert "SUMMARY_CONTROL" in report["report"]["summary_md"]
        return final("Согласовали историю проекта и проверку интерфейса.")

    result = await ArisRuntime(model=FunctionModel(respond), executor=executor).run(
        deps, metadata={}
    )
    assert result.output is not None and result.requests == step == 3
    reader.assert_awaited_once()
    deps.write_gate_factory.assert_not_called()


async def test_protected_loader_overflow_has_context_reason_without_exposing_data():
    reader = AsyncMock()
    executor = AgentToolExecutor(
        [
            AgentTool("telos_get_latest_project_summary", "Прочитать обзор", EmptyInput, reader),
        ]
    )
    system = ARIS_BASE_PROMPTS[ArisMode.PROJECT_CHAT]
    budget = AgentContextBudget(
        system_prompt=system, max_tokens=estimate_tokens(system) + 180, history_tokens=1200
    )
    deps = ArisDeps(
        execution=AgentToolContext(project_id=2, user_id=1, mode=ArisMode.PROJECT_CHAT),
        executor=executor,
        system_prompt=system,
        content={"question": "Прочитай сохранённый обзор."},
        budget=budget,
        tool_rounds=8,
        defer_workflows=True,
        load_context=AsyncMock(),
    )
    calls = 0

    async def respond(messages, _info):
        nonlocal calls
        calls += 1
        assert calls == 1, "The protected loader must not be silently discarded"
        return ModelResponse(parts=[ToolCallPart("telos_load_capability", {"id": "reports"})])

    result = await ArisRuntime(model=FunctionModel(respond), executor=executor).run(
        deps, metadata={}
    )
    assert result.output is None and result.requests == calls == 1
    assert metrics(result)["exhaustion_reason"] == "context"
    reader.assert_not_awaited()
