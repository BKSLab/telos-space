"""Смена бренда календаря сохраняет идентификаторы событий действующих подписок."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.repositories.calendar_feed_tokens import CalendarFeedTokensRepository
from src.repositories.users import UsersRepository
from src.services.calendar_feed import CalendarFeedService
from src.services.personal_calendar import PersonalCalendarService


async def test_telos_calendar_keeps_existing_task_milestone_and_project_deadline_uids():
    tokens = AsyncMock(spec=CalendarFeedTokensRepository)
    tokens.get_by_hash.return_value = SimpleNamespace(user_id=1)
    users = AsyncMock(spec=UsersRepository)
    users.get_by_id.return_value = SimpleNamespace(id=1, is_active=True)
    calendar = AsyncMock(spec=PersonalCalendarService)
    project = SimpleNamespace(id=4, key="PROJ", name="Проект")
    today = date(2026, 11, 3)
    calendar.get_feed_items.return_value = SimpleNamespace(
        tasks=[
            SimpleNamespace(
                id=7,
                key="PROJ-7",
                title="Подготовить план",
                is_done=False,
                start_date=None,
                due_date=today,
                updated_at=datetime(2026, 11, 3, tzinfo=UTC),
                source=project,
            )
        ],
        milestones=[
            SimpleNamespace(id=8, project=project, title="Приёмка", due_date=today),
            SimpleNamespace(id=None, project=project, title="Дедлайн проекта", due_date=today),
        ],
    )
    service = CalendarFeedService(
        tokens_repository=tokens, users_repository=users, calendar_service=calendar
    )

    body = await service.render_feed(
        secret="cal_synthetic", app_url="https://telos.test", today=today
    )

    assert "PRODID:-//TELOS SPACE//Calendar//RU\r\n" in body
    assert "X-WR-CALNAME:TELOS SPACE — мои задачи\r\n" in body
    assert {line for line in body.split("\r\n") if line.startswith("UID:")} == {
        "UID:task-7@telos-space",
        "UID:milestone-8@telos-space",
        "UID:project-deadline-4@telos-space",
    }
