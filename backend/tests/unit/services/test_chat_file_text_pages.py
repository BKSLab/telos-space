"""Страницы и поиск сохраняют смещения Unicode и ограничивают выдачу."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.exceptions.agent_files import AgentFileNotFoundError, AgentFileValidationError
from src.services.agent_chat_files import AgentChatFilesService, ChatFileContext, _page


def test_unicode_pages_and_literal_search_are_bounded():
    text = "🙂Я[A.]я" + "x" * 20 + "[a.]" + "z" * 20
    first = _page(text, offset=0, max_chars=2, q=None)
    assert first == {
        "text": "🙂Я",
        "offset": 0,
        "next_offset": 2,
        "matches": [],
        "total_matches": None,
    }
    next_page = _page(text, offset=first["next_offset"], max_chars=4, q=None)
    assert next_page["text"] == "[A.]"
    found = _page(text, offset=0, max_chars=12, q="[a.]")
    assert found["total_matches"] == 2
    assert found["matches"] == [{"start": 2, "end": 6}]
    assert len(found["text"]) <= 12
    following = _page(text, offset=found["next_offset"], max_chars=12, q="[a.]")
    assert following["matches"] == [{"start": 27, "end": 31}]
    assert following["next_offset"] is None
    absent = _page(text, offset=0, max_chars=8000, q="not found")
    assert absent["text"] == "" and absent["total_matches"] == 0
    assert _page(text, offset=1000, max_chars=4, q=None)["next_offset"] is None
    repeated = _page("a" * 20000, offset=0, max_chars=8000, q="a")
    assert repeated["total_matches"] == 20000
    assert len(repeated["matches"]) == 20 and repeated["next_offset"] == 20
    partial = _page("needle", offset=0, max_chars=1, q="needle")
    assert partial["matches"] == [{"start": 0, "end": 6}]


async def test_active_context_and_limits_are_required_before_file_lookup():
    service = AgentChatFilesService(
        scope=None,
        agent_storage=None,
        chat_storage=None,
        vision=None,
    )
    db = SimpleNamespace(files=SimpleNamespace(conversation_context=AsyncMock()))
    for context in (
        ChatFileContext(user_id=1, project_id=1),
        ChatFileContext(user_id=1, project_id=1, message_id=1),
        ChatFileContext(user_id=1, project_id=1, message_id=1, chat_turn_id=2, run_id=uuid4()),
    ):
        with pytest.raises(AgentFileNotFoundError):
            await service._require(db, context)
    db.files.conversation_context.assert_not_called()
    for offset, size in ((-1, 1), (0, 0), (0, 8001)):
        with pytest.raises(AgentFileValidationError):
            service._validate_page(offset=offset, limit=size, maximum=8000)
    with pytest.raises(AgentFileValidationError):
        service._query("a" * 201)


async def test_extraction_states_and_explicit_resource_limit(tmp_path):
    from src.clients.vision import DisabledVisionCapability
    from src.services.agent_chat_files import _Location
    from src.storage.task_attachments import TaskAttachmentStorage

    storage = TaskAttachmentStorage(tmp_path)
    service = AgentChatFilesService(
        scope=None,
        agent_storage=storage,
        chat_storage=storage,
        vision=DisabledVisionCapability(),
        max_text_chars=8,
    )
    file = {
        "file_id": uuid4(),
        "original_name": "sample.txt",
        "content_type": "text/plain",
        "message_id": 1,
        "created_at": datetime.now(UTC),
    }
    location = _Location("conversation", 1, 1)
    for name, content, status in (
        ("sample.txt", "🙂привет мир".encode(), "ready"),
        ("empty.txt", b" \n ", "empty"),
        ("picture.png", b"image", "disabled"),
        ("legacy.doc", b"legacy", "unsupported"),
        ("broken.pdf", b"broken", "failed"),
    ):
        key = await storage.save(1, ".txt", content)
        result = await service._extract(
            location,
            {
                **file,
                "original_name": name,
                "storage_key": key,
                "size": len(content),
            },
        )
        assert result["status"] == status
        if status == "ready":
            assert result["content"] == "🙂привет "
            assert result["truncated"] and result["original_chars"] == 11
            assert result["total_chars"] == 8
        else:
            assert result["detail"] and not result["content"]
