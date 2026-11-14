"""Файлы чата входят в бюджет и читаются без внешней бизнес-транзакции."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.agent.action_catalog import PROJECT_ACTIONS
from src.agent.tools import AgentToolContext
from src.exceptions.agent_tools import AgentToolArgumentsError
from src.schemas.chat_files import ChatFileSchema, ChatFileTextSchema
from src.schemas.chat_history import ChatHistoryPageSchema
from src.schemas.project_tool_inputs import ChatFileReadInput
from src.services.agent_actions import AgentActionsService
from src.services.agent_chat_file_actions import read_chat_file
from src.services.agent_chat_files import AgentChatFilesService
from src.services.agent_chat_history import AgentChatHistoryService


def context():
    return AgentToolContext(project_id=1, user_id=2, message_id=10, run_id=uuid4())


def file(index=0, status="ready"):
    return ChatFileSchema(file_id=uuid4(), original_name=f"file-{index}.md", content_type="text/markdown",
        size=50000, message_id=8, created_at=datetime.now(UTC), status=status,
        excerpt="старый краткий фрагмент", total_chars=20000, original_chars=20000)


def page(item, text="", status=None):
    return ChatFileTextSchema(**{**item.model_dump(), "status": status or item.status},
        text=text, offset=0, next_offset=len(text) if text else None)


@pytest.mark.asyncio
async def test_history_tool_preserves_server_context_without_business_transaction():
    def unexpected_scope():
        raise AssertionError("History must use its own short read scopes")

    reader = AsyncMock(spec=AgentChatHistoryService)
    reader.list_messages.return_value = ChatHistoryPageSchema(
        items=[], has_more=False, next_before_message_id=None, next_after_message_id=None,
        applied_filters={"chat_id": 1, "kind": "conversation", "timezone": "Europe/Samara"},
    )
    ctx = AgentToolContext(project_id=1, user_id=2, message_id=10, run_id=uuid4(), timezone="Europe/Samara")
    service = AgentActionsService(scope=unexpected_scope, actions=PROJECT_ACTIONS, chat_history=reader)
    result = await service.execute(context=ctx, name="telos_list_chat_messages", arguments={"query": "баг"})
    assert result["items"] == []
    used = reader.list_messages.await_args.kwargs
    assert used["context"].message_id == ctx.message_id and used["context"].run_id == ctx.run_id
    assert used["context"].timezone == "Europe/Samara" and used["query"] == "баг"
    with pytest.raises(AgentToolArgumentsError):
        await service.execute(context=ctx, name="telos_list_chat_messages", arguments={"conversation_id": 999})


@pytest.mark.asyncio
async def test_agent_read_waits_for_preparation_without_model_or_extraction_calls():
    reader = AsyncMock(spec=AgentChatFilesService)
    reader.wait_seconds, reader.poll_seconds = 0.1, 0.001
    item = file(status="pending")
    reader.read_text.side_effect = [page(item), page(item, status="processing"), page(item, text="Готовый текст", status="ready")]
    args = ChatFileReadInput(file_id=item.file_id, q="условие", offset=123, max_chars=4000)
    result = await read_chat_file(reader, context(), args)
    assert result["status"] == "ready" and result["text"] == "Готовый текст"
    assert reader.read_text.await_count == 3
    assert all(call.kwargs["q"] == "условие" and call.kwargs["offset"] == 123 for call in reader.read_text.await_args_list)
    reader.process_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_timeout_is_explicit_and_does_not_retry_ocr():
    reader = AsyncMock(spec=AgentChatFilesService)
    reader.wait_seconds, reader.poll_seconds = 0, 1
    item = file(status="processing")
    reader.read_text.return_value = page(item)
    result = await read_chat_file(reader, context(), ChatFileReadInput(file_id=item.file_id))
    assert result["status"] == "processing" and result["text"] == ""
    reader.read_text.assert_awaited_once()
    reader.process_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_file_tool_does_not_open_business_uow_and_uses_only_server_context():
    def unexpected_scope():
        raise AssertionError("Business DB connection must not be held while awaiting preparation")
    reader = AsyncMock(spec=AgentChatFilesService)
    reader.wait_seconds, reader.poll_seconds = 0, 1
    item = file()
    reader.read_text.return_value = page(item, text="Доступный текст")
    service = AgentActionsService(scope=unexpected_scope, actions=PROJECT_ACTIONS, chat_files=reader)
    ctx = context()
    result = await service.execute(context=ctx, name="telos_read_chat_file", arguments={"file_id": str(item.file_id)})
    assert result["text"] == "Доступный текст"
    used = reader.read_text.await_args.kwargs["context"]
    assert used.message_id == ctx.message_id and used.run_id == ctx.run_id and used.user_id == ctx.user_id
    with pytest.raises(AgentToolArgumentsError):
        await service.execute(context=ctx, name="telos_read_chat_file", arguments={"file_id": str(item.file_id), "conversation_id": 999})
