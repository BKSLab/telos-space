"""Мой календарь, календарь портфеля, производственный календарь и ссылка подписки.

Контракт — ``docs/calendar.md``.
"""

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from src.api.v1.responses import FORBIDDEN_RESPONSE, SERVER_ERROR_RESPONSE, VALIDATION_RESPONSE
from src.dependencies.auth import PrincipalDep, SessionPrincipalDep
from src.dependencies.services import CalendarFeedServiceDep, PersonalCalendarServiceDep
from src.dependencies.settings import PublicAppUrlDep, SettingsDep
from src.exceptions.calendar import CalendarServiceError
from src.exceptions.calendar_feed_tokens import CalendarFeedServiceError
from src.schemas.enums import TaskPriority, TaskStatus
from src.schemas.my_calendar import (
    HolidaysResponseSchema,
    IcsTokenSchema,
    MyCalendarResponseSchema,
    MyUnscheduledTasksPageSchema,
    PortfolioCalendarResponseSchema,
)

router = APIRouter(tags=["calendar"])
logger = logging.getLogger(__name__)

DateFromQuery = Annotated[date, Query(description="Первый день диапазона включительно.")]
DateToQuery = Annotated[date, Query(description="Последний день диапазона включительно.")]
TodayQuery = Annotated[date, Query(description="Текущая дата в часовом поясе клиента.")]


@router.get(
    path="/users/me/calendar",
    status_code=status.HTTP_200_OK,
    summary="Мой календарь",
    description=(
        "Задачи, где я исполнитель, из всех проектов и разовые, пересекающие "
        "диапазон, и вехи проектов, где я в команде."
    ),
    operation_id="getMyCalendar",
    response_description="Задачи, вехи и сводка моего календаря.",
    responses={422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=MyCalendarResponseSchema,
)
async def get_my_calendar(
    principal: PrincipalDep,
    service: PersonalCalendarServiceDep,
    date_from: DateFromQuery,
    date_to: DateToQuery,
    today: TodayQuery,
    status_filter: Annotated[
        TaskStatus | None, Query(alias="status", description="Фильтр по состоянию задачи.")
    ] = None,
    priority: Annotated[TaskPriority | None, Query(description="Фильтр по приоритету.")] = None,
    project_id: Annotated[int | None, Query(gt=0, description="Только этот проект.")] = None,
) -> MyCalendarResponseSchema:
    """Возвращает мой календарь в диапазоне."""
    logger.info("🚀 Запрос GET /users/me/calendar. date_from=%s, date_to=%s.", date_from, date_to)
    try:
        return await service.get_my_range(
            user_id=principal.user_id,
            date_from=date_from,
            date_to=date_to,
            today=today,
            status=status_filter,
            priority=priority,
            project_id=project_id,
        )
    except CalendarServiceError as error:
        logger.exception("❌ Ошибка GET /users/me/calendar. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/users/me/calendar/unscheduled",
    status_code=status.HTTP_200_OK,
    summary="Мои задачи без дат",
    description="Курсорная страница моих незавершённых задач без start_date и due_date.",
    operation_id="getMyUnscheduledTasks",
    response_description="Страница моих задач без срока.",
    responses={422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=MyUnscheduledTasksPageSchema,
)
async def get_my_unscheduled_tasks(
    principal: PrincipalDep,
    service: PersonalCalendarServiceDep,
    today: TodayQuery,
    cursor: Annotated[int | None, Query(gt=0, description="Последний id прошлой страницы.")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Размер страницы.")] = 50,
) -> MyUnscheduledTasksPageSchema:
    """Возвращает страницу моих задач без дат."""
    logger.info("🚀 Запрос GET /users/me/calendar/unscheduled. cursor=%s.", cursor)
    try:
        return await service.get_my_unscheduled(
            user_id=principal.user_id, today=today, cursor=cursor, limit=limit
        )
    except CalendarServiceError as error:
        logger.exception("❌ Ошибка GET /users/me/calendar/unscheduled. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/portfolio/calendar",
    status_code=status.HTTP_200_OK,
    summary="Календарь портфеля",
    description=(
        "Проекты, пересекающие диапазон, с вехами и показателями; проекты без дат — в конце. "
        "admin и portfolio видят все проекты, остальные — где они в команде."
    ),
    operation_id="getPortfolioCalendar",
    response_description="Проекты на временной шкале.",
    responses={422: VALIDATION_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=PortfolioCalendarResponseSchema,
)
async def get_portfolio_calendar(
    principal: PrincipalDep,
    service: PersonalCalendarServiceDep,
    date_from: DateFromQuery,
    date_to: DateToQuery,
    today: TodayQuery,
) -> PortfolioCalendarResponseSchema:
    """Возвращает проекты на временной шкале портфеля."""
    logger.info("🚀 Запрос GET /portfolio/calendar. date_from=%s, date_to=%s.", date_from, date_to)
    try:
        return await service.get_portfolio(
            user_id=principal.user_id, date_from=date_from, date_to=date_to, today=today
        )
    except CalendarServiceError as error:
        logger.exception("❌ Ошибка GET /portfolio/calendar. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/calendar/holidays",
    status_code=status.HTTP_200_OK,
    summary="Производственный календарь РФ",
    description=(
        "Праздники, переносы выходных, рабочие и сокращённые дни года. Обычные субботы и "
        "воскресенья не перечисляются. Год без данных — known=false и пустой список."
    ),
    operation_id="getHolidays",
    response_description="Отклонения года от обычной пятидневки.",
    responses={422: VALIDATION_RESPONSE},
    response_model=HolidaysResponseSchema,
)
async def get_holidays(
    _principal: PrincipalDep,
    service: PersonalCalendarServiceDep,
    year: Annotated[int, Query(ge=1900, le=2100, description="Календарный год.")],
) -> HolidaysResponseSchema:
    """Возвращает производственный календарь на год."""
    return service.get_holidays(year)


@router.get(
    path="/users/me/calendar/ics-token",
    status_code=status.HTTP_200_OK,
    summary="Состояние ссылки подписки",
    description="Есть ли ссылка подписки на мой календарь; сама ссылка не возвращается.",
    operation_id="getIcsToken",
    response_description="Состояние ссылки подписки.",
    responses={403: FORBIDDEN_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=IcsTokenSchema,
)
async def get_ics_token(
    principal: SessionPrincipalDep, service: CalendarFeedServiceDep
) -> IcsTokenSchema:
    """Возвращает состояние ссылки подписки."""
    try:
        return await service.get_status(principal.user_id)
    except CalendarFeedServiceError as error:
        logger.exception("❌ Ошибка GET /users/me/calendar/ics-token. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/users/me/calendar/ics-token",
    status_code=status.HTTP_201_CREATED,
    summary="Выпустить ссылку подписки",
    description=(
        "Выпускает ссылку подписки на мой календарь для Google, Outlook, Яндекса. Прежняя "
        "ссылка сразу перестаёт работать. Ссылка возвращается только в этом ответе."
    ),
    operation_id="createIcsToken",
    response_description="Ссылка подписки.",
    responses={403: FORBIDDEN_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=IcsTokenSchema,
)
async def create_ics_token(
    principal: SessionPrincipalDep,
    service: CalendarFeedServiceDep,
    settings: SettingsDep,
    app_url: PublicAppUrlDep,
) -> IcsTokenSchema:
    """Выпускает или перевыпускает ссылку подписки."""
    logger.info("🚀 Запрос POST /users/me/calendar/ics-token. Пользователь id=%s.", principal.user_id)
    try:
        issued = await service.issue(principal.user_id)
    except CalendarFeedServiceError as error:
        logger.exception("❌ Ошибка POST /users/me/calendar/ics-token. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    return IcsTokenSchema(
        exists=True,
        created_at=issued.created_at,
        url=f"{app_url}{settings.app.api_v1_prefix}/ics/{issued.secret}.ics",
    )


@router.delete(
    path="/users/me/calendar/ics-token",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отозвать ссылку подписки",
    description="Ссылка подписки перестаёт работать. Если ссылки нет — тоже 204.",
    operation_id="revokeIcsToken",
    responses={403: FORBIDDEN_RESPONSE, 500: SERVER_ERROR_RESPONSE},
)
async def revoke_ics_token(
    principal: SessionPrincipalDep, service: CalendarFeedServiceDep
) -> Response:
    """Отзывает ссылку подписки."""
    logger.info("🚀 Запрос DELETE /users/me/calendar/ics-token. Пользователь id=%s.", principal.user_id)
    try:
        await service.revoke(principal.user_id)
    except CalendarFeedServiceError as error:
        logger.exception("❌ Ошибка DELETE /users/me/calendar/ics-token. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
