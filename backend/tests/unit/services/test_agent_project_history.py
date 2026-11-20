"""Периоды истории Ариса и неизменяемые аргументы продолжения страницы."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.agent.tools import AgentToolContext
from src.schemas.project_history import ProjectHistoryItem, ProjectHistoryPage
from src.schemas.project_tool_inputs import ProjectHistoryInput
from src.services import agent_project_actions


class FrozenDatetime(datetime):
    instant = datetime(2026, 11, 20, 21, 30, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz)


def test_history_input_rejects_ambiguous_periods_and_invalid_filters():
    for arguments in (
        {"period": "week"},
        {"period": "today", "since": "2026-11-20", "until": "2026-11-20"},
        {"since": "2026-11-20"},
        {"until": "2026-11-20"},
        {"since": "2026-11-21", "until": "2026-11-20"},
        {"since": "20.11.2026", "until": "2026-11-20"},
        {"timezone": "Unknown/Zone"},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"offset": 2**63},
        {"kind": "private_chat"},
        {"actor_user_id": 0},
        {"task_id": -1},
        {"event_id": "invalid"},
        {"q": "a\x00b"},
        {"project_id": 2},
    ):
        with pytest.raises(ValidationError):
            ProjectHistoryInput.model_validate(arguments)
    assert ProjectHistoryInput().limit == 20
    assert ProjectHistoryInput(limit=100).limit == 100


async def test_relative_periods_use_local_today_and_request_timezone(monkeypatch):
    monkeypatch.setattr(agent_project_actions, "datetime", FrozenDatetime)
    reader = AsyncMock(return_value=ProjectHistoryPage(items=[], next_offset=None))
    db = SimpleNamespace(project_history=SimpleNamespace(read=reader))
    ctx = AgentToolContext(project_id=1, user_id=1, timezone="Europe/Samara")
    cases = (
        ({}, "2026-11-15", "2026-11-21", "Europe/Samara"),
        ({"period": "last_7_days"}, "2026-11-15", "2026-11-21", "Europe/Samara"),
        ({"period": "today"}, "2026-11-21", "2026-11-21", "Europe/Samara"),
        ({"period": "yesterday"}, "2026-11-20", "2026-11-20", "Europe/Samara"),
        ({"period": "today", "timezone": "UTC"}, "2026-11-20", "2026-11-20", "UTC"),
        ({"period": "today", "timezone": "America/New_York"}, "2026-11-20", "2026-11-20", "America/New_York"),
        ({"since": "2026-03-08", "until": "2026-03-08", "timezone": "America/New_York"}, "2026-03-08", "2026-03-08", "America/New_York"),
    )
    for arguments, since, until, timezone in cases:
        response = await agent_project_actions.list_project_history(
            db, ctx, ProjectHistoryInput.model_validate(arguments)
        )
        assert response["period"] == {"since": since, "until": until, "timezone": timezone}
        assert response["items"] == []
        assert response["has_more"] is False
        assert response["next_offset"] is None and response["next_arguments"] is None
        project_id, filters = reader.call_args.args
        assert project_id == 1
        assert filters.since.isoformat() == since and filters.until.isoformat() == until
        assert filters.timezone == timezone


async def test_next_arguments_pin_absolute_dates_and_preserve_every_filter(monkeypatch):
    monkeypatch.setattr(agent_project_actions, "datetime", FrozenDatetime)
    item = ProjectHistoryItem(
        id="task_activity:81", kind="task_activity", title="Смена стадии · Поставка",
        detail="Ожидание → Готово", created_at=FrozenDatetime.instant, actor_name="Анна",
        actor_user_id=2, task_id=3, event_type="STAGE_CHANGED",
        from_value="Ожидание", to_value="Готово",
    )
    reader = AsyncMock(side_effect=[
        ProjectHistoryPage(items=[item], next_offset=5),
        ProjectHistoryPage(items=[], next_offset=None),
    ])
    db = SimpleNamespace(project_history=SimpleNamespace(read=reader))
    ctx = AgentToolContext(project_id=1, user_id=1, timezone="Europe/Samara")
    arguments = {
        "period": "today", "limit": 1, "offset": 4, "kind": "task_activity",
        "q": "Поставка", "actor": "Анна", "actor_user_id": 2,
        "task_id": 3, "event_id": "task_activity:81",
    }
    page = await agent_project_actions.list_project_history(
        db, ctx, ProjectHistoryInput.model_validate(arguments)
    )
    assert page["items"] == [item.model_dump(mode="json")]
    assert page["has_more"] is True and page["next_offset"] == 5
    assert page["next_arguments"] == {
        **{key: value for key, value in arguments.items() if key != "period"},
        "offset": 5, "since": "2026-11-21", "until": "2026-11-21",
        "timezone": "Europe/Samara",
    }
    # Следующая страница после полуночи не переключается на новый день.
    monkeypatch.setattr(FrozenDatetime, "instant", datetime(2026, 11, 22, 12, tzinfo=UTC))
    last = await agent_project_actions.list_project_history(
        db, ctx, ProjectHistoryInput.model_validate(page["next_arguments"])
    )
    assert last["period"] == page["period"]
    assert last["next_arguments"] is None and last["has_more"] is False


async def test_runtime_compaction_keeps_history_continuation_without_skipping_events():
    from copy import deepcopy

    from src.agent.context import encode, estimate_tokens
    from src.agent.runtime import _fit_result

    ctx = AgentToolContext(project_id=1, user_id=1, timezone="Europe/Samara")
    for offset, next_offset in ((0, 20), (20, 40), (40, None)):
        items = [
            ProjectHistoryItem(
                id=f"task_comment:{offset + number}", kind="task_comment",
                title="Комментарий · " + "Подготовка поставки " * 12,
                detail="Подробности согласования поставки и проверки результата. " * 8,
                created_at=FrozenDatetime.instant, actor_name="Анна", actor_user_id=2,
                task_id=3, comment_id=offset + number,
            )
            for number in range(1, 21)
        ]
        reader = AsyncMock(return_value=ProjectHistoryPage(items=items, next_offset=next_offset))
        db = SimpleNamespace(project_history=SimpleNamespace(read=reader))
        arguments = {
            "since": "2026-11-01", "until": "2026-11-21", "limit": 20,
            "offset": offset, "kind": "task_comment", "q": "Поставка",
            "actor": "Анна", "actor_user_id": 2, "task_id": 3,
        }
        raw = await agent_project_actions.list_project_history(
            db, ctx, ProjectHistoryInput.model_validate(arguments)
        )
        original = deepcopy(raw)
        fitted = _fit_result(raw, 1600)
        assert raw == original
        assert estimate_tokens(encode(fitted)) <= 1600
        assert 0 < len(fitted["items"]) < len(items)
        assert [item["id"] for item in fitted["items"]] == [
            item.id for item in items[:len(fitted["items"])]]
        assert fitted["offset"] == offset
        assert fitted["next_offset"] == offset + len(fitted["items"])
        assert fitted["has_more"] is True
        assert fitted["next_arguments"] == {
            **arguments, "timezone": "Europe/Samara", "offset": fitted["next_offset"],
        }
        assert fitted["period"] == raw["period"]
        assert "period" not in fitted["next_arguments"]
