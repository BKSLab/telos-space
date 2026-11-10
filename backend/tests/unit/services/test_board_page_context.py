"""Контекст доски — проверенная подсказка, без выдачи дополнительных прав."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.services.agent_conversations import AgentConversationsService


async def test_private_board_context_requires_owner_and_uses_trusted_title():
    repo = SimpleNamespace(
        get_active_free=AsyncMock(return_value=SimpleNamespace(id=5, title="Мои идеи"))
    )
    raw = {"board_id": 5, "board_title": "Подмена"}
    await AgentConversationsService._check_board_page(SimpleNamespace(boards=repo), 7, raw, None)
    repo.get_active_free.assert_awaited_once_with(board_id=5, owner_user_id=7)
    assert raw == {"board_id": 5, "board_title": "Мои идеи"}


async def test_foreign_board_context_is_removed():
    repo = SimpleNamespace(get_active_free=AsyncMock(return_value=None))
    raw = {"board_id": 5, "board_title": "Чужое"}
    await AgentConversationsService._check_board_page(SimpleNamespace(boards=repo), 7, raw, None)
    assert "board_id" not in raw and "board_title" not in raw


async def test_project_board_is_checked_with_the_authorized_project():
    repo = SimpleNamespace(
        get_active=AsyncMock(return_value=SimpleNamespace(id=3, title="Процесс"))
    )
    raw = {"board_id": 3, "project_key": "DEMO"}
    await AgentConversationsService._check_board_page(SimpleNamespace(boards=repo), 7, raw, 1)
    repo.get_active.assert_awaited_once_with(project_id=1, board_id=3)
    assert raw["board_title"] == "Процесс"


async def test_unknown_project_does_not_fall_back_to_private_board():
    repo = SimpleNamespace(get_active_free=AsyncMock())
    raw = {"board_id": 3, "project_key": "SECRET"}
    await AgentConversationsService._check_board_page(SimpleNamespace(boards=repo), 7, raw, None)
    assert "board_id" not in raw
    repo.get_active_free.assert_not_awaited()
