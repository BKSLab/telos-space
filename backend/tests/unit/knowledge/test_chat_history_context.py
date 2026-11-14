"""История сохраняет структурные связи, а цитаты требуют выданного сервером handle."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.test import TestModel

from src.agent.context import AgentContextBudget, encode
from src.agent.runtime import ArisRuntime
from src.agent.tools import AgentToolContext, AgentToolExecutor
from src.exceptions.ai import AgentAnswerError
from src.exceptions.project_chats import ChatConflictError
from src.schemas.chat_references import ChatMessageReferenceSchema
from src.services.agent_chat_history import AgentChatHistoryService
from src.services.agent_chat_reference_registry import ChatReferenceRegistry
from tests.unit.knowledge.agent_models import LegacyOutput
from tests.unit.knowledge.test_project_agent import build_service


def message(index=12, text="Андрей: исправлю баг завтра. 🙂", kind="project_chat"):
    reference = ChatMessageReferenceSchema(
        kind=kind, chat_id=1, message_id=index, project_id=1 if kind == "project_chat" else None,
        project_key="PROJ" if kind == "project_chat" else None,
        author_id=2, author_name="Андрей", created_at=datetime.now(UTC), revision=1,
        content_hash=hashlib.sha256(text.encode()).hexdigest(), quote=text[:1000],
        start=0, end=len(text[:1000]),
    )
    return {
        "id": index, "chat_id": 1, "kind": kind, "project_id": reference.project_id,
        "project_key": reference.project_key, "author_id": 2, "author_name": "Андрей",
        "author_type": "USER", "created_at": reference.created_at.isoformat(), "revision": 1,
        "content": text, "content_offset": 0, "content_total_chars": len(text),
        "next_content_offset": None, "text_truncated": False,
        "reply_to_message_id": index - 1, "attachments": [],
        "reference": reference.model_dump(mode="json"),
    }


def test_budget_retains_all_fifteen_message_ids_authors_links_and_attachment_ownership():
    rows = [message(index, "длинное обсуждение 🙂 " * 500) for index in range(1, 16)]
    for row in rows:
        row["entity_references"] = [{"entity_type": "task", "entity_id": row["id"], "key": f"PROJ-{row['id']}"}]
        row["attachments"] = [{"file_id": str(uuid4()), "original_name": f"{row['id']}.md",
            "excerpt": "текст файла " * 150, "total_chars": 40000}]
    content = {"question": "Что написал Андрей?", "chat_context": {
        "current_message": message(16, "Что написал Андрей?"), "messages": rows,
        "quoted_message": message(100, "Старая цитируемая реплика " * 100),
    }, "read_results": [{"result": {"text": "Результат другого инструмента " * 2000}}]}
    registry = ChatReferenceRegistry()
    registry.register(content["chat_context"])
    identity = [(row["id"], row["author_id"], row["created_at"], row["reply_to_message_id"],
                 row["attachments"][0]["file_id"], row["message_reference_handle"]) for row in rows]
    budget = AgentContextBudget(system_prompt="Правила", max_tokens=6000, history_tokens=1200)
    budget.prepare(content)
    first = budget.fit(content)
    assert budget.tokens(content) <= 6000
    assert len(content["chat_context"]["messages"]) == 15
    assert identity == [(row["id"], row["author_id"], row["created_at"], row["reply_to_message_id"],
                        row["attachments"][0]["file_id"], row["message_reference_handle"]) for row in rows]
    assert content["chat_context"]["current_message"]["content"] == "Что написал Андрей?"
    assert any(row["text_truncated"] for row in rows)
    assert budget.fit(content) == first
    receipt = AgentContextBudget._receipt({"items": rows, "has_more": True, "next_before_message_id": 1})
    assert [row["id"] for row in receipt["messages"]] == list(range(1, 16))
    assert receipt["next_before_message_id"] == 1 and receipt["has_more"]
    assert all(row["message_reference_handle"].startswith("MSG_") for row in receipt["messages"])
    assert [row["entity_references"][0]["entity_id"] for row in receipt["messages"]] == list(range(1, 16))


@pytest.mark.parametrize("personal", [False, True])
async def test_real_agent_loop_returns_server_verified_message_quote_without_flat_files(personal):
    service, project, clients, _ = build_service()
    source = message(kind="conversation" if personal else "project_chat")
    original = ChatMessageReferenceSchema.model_validate(source["reference"])
    reader = AsyncMock(spec=AgentChatHistoryService)
    reader.verify_reference.side_effect = lambda **kwargs: kwargs["reference"]
    service.chat_history = reader
    ctx = AgentToolContext(project_id=None if personal else project.id, user_id=1,
                           message_id=20, run_id=uuid4(), timezone="Europe/Samara")
    chat = {"chat_id": 1, "kind": source["kind"], "messages": [source], "current_message": message(20, "Процитируй Андрея")}

    async def answer(*, schema, content, **kwargs):
        payload = json.loads(content)
        assert payload["dialog_history"] == [] and "uploaded_files" not in payload
        shown = payload["chat_context"]["messages"][0]
        assert "reference" not in shown
        return LegacyOutput(answer="Андрей сообщил о сроке исправления.", message_references=[shown["message_reference_handle"]])

    clients.llm_client.get_structured_response.side_effect = answer
    common = dict(question="Процитируй Андрея", history=[], actor={"user_id": 1}, execution=ctx, chat_context=chat)
    if personal:
        result = await service.ask_personal(**common, memory="")
    else:
        result = await service.ask(**common, project_id=project.id)
    assert result.message_references == [original]
    reader.verify_reference.assert_awaited_once()
    assert reader.verify_reference.await_args.kwargs["context"].timezone == "Europe/Samara"


async def test_citation_handle_cannot_be_forged_reused_or_publish_stale_text():
    registry = ChatReferenceRegistry()
    source = message()
    registry.register(source)
    handle = source["message_reference_handle"]
    ctx = AgentToolContext(project_id=1, user_id=1, chat_turn_id=10, run_id=uuid4())
    reader = AsyncMock(spec=AgentChatHistoryService)
    for unknown in ("task:12", "MSG_from_another_run_1", "invented"):
        with pytest.raises(AgentAnswerError):
            await registry.select(handles=[unknown], history=reader, context=ctx)
    reader.verify_reference.assert_not_awaited()
    with pytest.raises(AgentAnswerError):
        await ChatReferenceRegistry().select(handles=[handle], history=reader, context=ctx)
    reader.verify_reference.side_effect = ChatConflictError("Сообщение изменилось")
    with pytest.raises(AgentAnswerError, match="изменились"):
        await registry.select(handles=[handle], history=reader, context=ctx)
    reader.verify_reference.assert_awaited_once()


async def test_runtime_refuses_oversized_mandatory_traffic_before_next_model_call():
    runtime = ArisRuntime(model=TestModel(), executor=AgentToolExecutor([]))
    content = {"question": "Обязательный вопрос"}
    budget = AgentContextBudget(system_prompt="Правила", max_tokens=9000, history_tokens=1200)
    deps = SimpleNamespace(defer_workflows=False, budget=budget, content=content, system_prompt="Правила")
    ctx = SimpleNamespace(deps=deps)
    messages = [ModelRequest(parts=[UserPromptPart(encode(deepcopy(content))),
                ToolReturnPart("telos_read_source", {"mandatory": "x" * 60000}, tool_call_id="read")])]
    with pytest.raises(UsageLimitExceeded, match="превышают бюджет"):
        await runtime._fit_history(ctx, messages)


@pytest.mark.parametrize("address", ["", "Арис, "])
def test_long_current_question_is_preserved_once_instead_of_overflowing_with_its_duplicate(address):
    question = "Длинное поручение пользователю. " * 240
    current = message(20, (address + question).strip())
    # Настоящий initial_context передаёт страницу 2000, но hash/длину полного оригинала.
    current["content"] = current["content"][:2000]
    current["text_truncated"] = True
    current["next_content_offset"] = 2000
    content = {"question": question.strip(), "chat_context": {
        "current_message": current, "messages": [message(index, "Короткая реплика") for index in range(1, 16)],
    }}
    ChatReferenceRegistry().register(content["chat_context"])
    budget = AgentContextBudget(system_prompt="Правила", max_tokens=8000, history_tokens=1200)
    budget.prepare(content)
    budget.fit(content)
    assert content["question"] == question.strip()
    assert current["content_in_question"] is True and current["content"] == ""
    assert current["next_content_offset"] == 0
    assert current["message_reference_handle"].startswith("MSG_")
    assert len(content["chat_context"]["messages"]) == 15
    assert budget.tokens(content) <= 8000


def test_equal_prefix_does_not_hide_a_different_current_message():
    question = ("Длинное поручение пользователю. " * 240).strip()
    current = message(20, "Арис, " + question + " Иной хвост оригинала.")
    current["content"] = current["content"][:2000]
    current["next_content_offset"] = 2000
    content = {"question": question, "chat_context": {"current_message": current,
               "messages": [message(index, "Короткая реплика") for index in range(1, 16)]}}
    ChatReferenceRegistry().register(content["chat_context"])
    budget = AgentContextBudget(system_prompt="Правила", max_tokens=8000, history_tokens=1200)
    budget.prepare(content)
    with pytest.raises(ValueError, match="контекст превышает бюджет"):
        budget.fit(content)
    assert current["content"] and not current.get("content_in_question")
