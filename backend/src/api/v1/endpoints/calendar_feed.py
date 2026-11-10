"""Секретная ссылка на личный календарь: хранится хеш, права владельца проверяются при каждом запросе."""

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Response, status

from src.api.v1.responses import NOT_FOUND_RESPONSE, SERVER_ERROR_RESPONSE
from src.dependencies.services import CalendarFeedServiceDep
from src.dependencies.settings import PublicAppUrlDep
from src.exceptions.calendar_feed_tokens import CalendarFeedServiceError

router = APIRouter(tags=["calendar"])
logger = logging.getLogger(__name__)

ICS_MEDIA_TYPE = "text/calendar; charset=utf-8"


@router.get(
    path="/ics/{token}.ics",
    status_code=status.HTTP_200_OK,
    summary="Лента подписки на мой календарь",
    description=(
        "Публичная лента iCalendar по секретной ссылке, без входа по паролю: мои задачи со "
        "сроком и вехи моих проектов в окне −30…+180 дней. Неизвестная, отозванная ссылка "
        "или отключённый владелец — 404."
    ),
    operation_id="getIcsFeed",
    response_class=Response,
    responses={
        200: {"content": {"text/calendar": {}}, "description": "Лента iCalendar."},
        404: NOT_FOUND_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def get_ics_feed(
    token: Annotated[str, Path(min_length=1, max_length=256, description="Секрет ссылки.")],
    service: CalendarFeedServiceDep,
    app_url: PublicAppUrlDep,
) -> Response:
    """Отдаёт ленту .ics владельца ссылки."""
    # Сам секрет в журнал не пишется: журнал читают шире, чем владелец ссылки.
    logger.info("🚀 Запрос GET /ics/<секрет>.ics.")
    try:
        body = await service.render_feed(secret=token, app_url=app_url)
    except CalendarFeedServiceError as error:
        if error.status_code == status.HTTP_404_NOT_FOUND:
            logger.info("ℹ️ Лента календаря по неизвестной ссылке.")
        else:
            logger.exception("❌ Ошибка GET /ics/<секрет>.ics. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    return Response(
        content=body,
        media_type=ICS_MEDIA_TYPE,
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": 'inline; filename="telos-space.ics"',
        },
    )
