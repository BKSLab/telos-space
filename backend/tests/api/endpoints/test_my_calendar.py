"""HTTP-контракт моего календаря, портфеля, праздников и подписки .ics."""

from dataclasses import replace
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from main import app
from src.db.models.api_tokens import ApiTokenScope
from src.dependencies.auth import get_principal
from src.dependencies.services import get_calendar_feed_service, get_personal_calendar_service
from src.exceptions.calendar import CalendarRangeError
from src.exceptions.calendar_feed_tokens import CalendarFeedNotFoundError
from src.schemas.calendar import CalendarRangeSchema
from src.schemas.my_calendar import (
    IcsTokenSchema,
    MyCalendarResponseSchema,
    MyCalendarSummarySchema,
)
from src.services.auth import Principal
from src.services.calendar_feed import CalendarFeedService, IssuedFeedToken
from src.services.personal_calendar import PersonalCalendarService

TODAY = date(2026, 9, 28)
TOKEN_URL = "/api/v1/users/me/calendar/ics-token"


def feed_service() -> AsyncMock:
    """Сервис подписки без базы."""
    service = AsyncMock(spec=CalendarFeedService)
    service.get_status.return_value = IcsTokenSchema(exists=False)
    service.issue.return_value = IssuedFeedToken(
        secret="cal_secret", created_at=datetime(2026, 9, 28, tzinfo=UTC)
    )
    service.render_feed.return_value = "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
    return service


@pytest.mark.asyncio
async def test_my_calendar_forwards_filters_and_maps_range_error(api_client: AsyncClient) -> None:
    """Фильтры доходят до сервиса, status — по псевдониму, ошибка диапазона — 422."""
    service = AsyncMock(spec=PersonalCalendarService)
    service.get_my_range.return_value = MyCalendarResponseSchema(
        range=CalendarRangeSchema(date_from=TODAY, date_to=TODAY, today=TODAY),
        tasks=[],
        milestones=[],
        summary=MyCalendarSummarySchema(overdue=0, due_soon=0, unscheduled=0),
    )
    app.dependency_overrides[get_personal_calendar_service] = lambda: service
    params = {"date_from": "2026-09-01", "date_to": "2026-09-30", "today": TODAY.isoformat()}

    response = await api_client.get(
        "/api/v1/users/me/calendar",
        params={**params, "status": "DONE", "priority": "HIGH", "project_id": 3},
    )

    assert response.status_code == 200
    assert response.json()["truncated"] is False
    kwargs = service.get_my_range.await_args.kwargs
    assert (kwargs["user_id"], kwargs["status"].value, kwargs["priority"].value) == (
        1,
        "DONE",
        "HIGH",
    )
    assert kwargs["project_id"] == 3

    service.get_my_range.side_effect = CalendarRangeError("Слишком длинный диапазон.")
    assert (await api_client.get("/api/v1/users/me/calendar", params=params)).status_code == 422
    missing_today = {key: value for key, value in params.items() if key != "today"}
    assert (await api_client.get("/api/v1/users/me/calendar", params=missing_today)).status_code == 422


@pytest.mark.asyncio
async def test_holidays_are_served_for_known_and_unknown_years(api_client: AsyncClient) -> None:
    """Известный год — с данными, неизвестный — known=false, вне границ — 422."""
    app.dependency_overrides[get_personal_calendar_service] = lambda: AsyncMock(
        spec=PersonalCalendarService, get_holidays=PersonalCalendarService.get_holidays
    )

    known = await api_client.get("/api/v1/calendar/holidays", params={"year": 2026})
    unknown = await api_client.get("/api/v1/calendar/holidays", params={"year": 2031})
    invalid = await api_client.get("/api/v1/calendar/holidays", params={"year": 1800})

    assert known.status_code == 200
    payload = known.json()
    assert payload["known"] is True and "1466" in payload["source"]
    assert {"date": "2026-03-09", "kind": "DAY_OFF", "title": "Перенесённый выходной"} in payload[
        "days"
    ]
    assert unknown.json() == {"year": 2031, "known": False, "source": None, "days": []}
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_ics_token_is_managed_only_from_session_and_url_returned_once(
    api_client: AsyncClient, current_principal: Principal
) -> None:
    """Выпуск отдаёт полную ссылку один раз, отзыв — 204, API-токену — 403."""
    service = feed_service()
    app.dependency_overrides[get_calendar_feed_service] = lambda: service

    status = await api_client.get(TOKEN_URL)
    created = await api_client.post(TOKEN_URL)
    revoked = await api_client.delete(TOKEN_URL)

    assert status.json() == {"exists": False, "created_at": None, "url": None}
    assert created.status_code == 201
    assert created.json()["url"] == "http://test/api/v1/ics/cal_secret.ics"
    assert revoked.status_code == 204
    service.revoke.assert_awaited_once_with(1)

    token_principal = replace(current_principal, scope=ApiTokenScope.WRITE, via_api_token=True)
    app.dependency_overrides[get_principal] = lambda: token_principal
    for method in ("get", "post", "delete"):
        response = await getattr(api_client, method)(TOKEN_URL)
        assert response.status_code == 403, method


@pytest.mark.asyncio
async def test_public_feed_needs_no_login_and_hides_unknown_links(api_client: AsyncClient) -> None:
    """Лента отдаётся как text/calendar без входа; неизвестная ссылка — 404."""
    service = feed_service()
    app.dependency_overrides.pop(get_principal, None)
    app.dependency_overrides[get_calendar_feed_service] = lambda: service

    response = await api_client.get("/api/v1/ics/cal_secret.ics")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/calendar; charset=utf-8"
    assert response.text.startswith("BEGIN:VCALENDAR")
    assert service.render_feed.await_args.kwargs == {
        "secret": "cal_secret",
        "app_url": "http://test",
    }

    service.render_feed.side_effect = CalendarFeedNotFoundError()
    missing = await api_client.get("/api/v1/ics/cal_revoked.ics")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Календарь не найден."}
