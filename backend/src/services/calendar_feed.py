"""Секретная ссылка на личный календарь: хранится хеш, права владельца проверяются при каждом запросе."""

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from src.exceptions.calendar import CalendarServiceError
from src.exceptions.calendar_feed_tokens import (
    CalendarFeedNotFoundError,
    CalendarFeedServiceError,
    CalendarFeedTokensRepositoryError,
)
from src.exceptions.users import UsersRepositoryError
from src.repositories.calendar_feed_tokens import CalendarFeedTokensRepository
from src.repositories.users import UsersRepository
from src.schemas.my_calendar import IcsTokenSchema
from src.services.personal_calendar import PersonalCalendarService
from src.utils.api_tokens import hash_token_secret
from src.utils.ics import IcsEvent, render_calendar

logger = logging.getLogger(__name__)

FEED_TOKEN_PREFIX = "cal_"
FEED_TOKEN_ENTROPY_BYTES = 32
FEED_TOKEN_MAX_LENGTH = 128
FEED_PAST_DAYS = 30
FEED_FUTURE_DAYS = 180
FEED_NAME = "TELOS SPACE — мои задачи"
DONE_MARK = "✓ "
MILESTONE_MARK = "◆ "


@dataclass(frozen=True, slots=True)
class IssuedFeedToken:
    """Только что выпущенный секрет ссылки — показывается один раз."""

    secret: str
    created_at: datetime


class CalendarFeedService:
    """Выпуск, отзыв ссылки подписки и сборка ленты .ics."""

    def __init__(
        self,
        *,
        tokens_repository: CalendarFeedTokensRepository,
        users_repository: UsersRepository,
        calendar_service: PersonalCalendarService,
    ):
        self.tokens_repository = tokens_repository
        self.users_repository = users_repository
        self.calendar_service = calendar_service

    async def get_status(self, user_id: int) -> IcsTokenSchema:
        """Есть ли у пользователя ссылка подписки и когда выпущена.

        Args:
            user_id: Владелец ссылки.

        Returns:
            Состояние ссылки без самой ссылки.

        Raises:
            CalendarFeedServiceError: Если прочитать состояние не удалось.
        """
        try:
            token = await self.tokens_repository.get_by_user(user_id)
        except CalendarFeedTokensRepositoryError as error:
            raise CalendarFeedServiceError(str(error)) from error
        if token is None:
            return IcsTokenSchema(exists=False)
        return IcsTokenSchema(exists=True, created_at=token.created_at)

    async def issue(self, user_id: int) -> IssuedFeedToken:
        """Выпускает новую ссылку; прежняя сразу перестаёт работать.

        Args:
            user_id: Владелец ссылки.

        Returns:
            Секрет и момент выпуска.

        Raises:
            CalendarFeedServiceError: Если выпустить ссылку не удалось.
        """
        secret = f"{FEED_TOKEN_PREFIX}{secrets.token_urlsafe(FEED_TOKEN_ENTROPY_BYTES)}"
        try:
            token = await self.tokens_repository.replace(
                user_id=user_id, token_hash=hash_token_secret(secret), commit=True
            )
        except CalendarFeedTokensRepositoryError as error:
            raise CalendarFeedServiceError(str(error)) from error
        logger.info("✅ Выпущена ссылка подписки на календарь пользователя id=%s.", user_id)
        return IssuedFeedToken(secret=secret, created_at=token.created_at)

    async def revoke(self, user_id: int) -> None:
        """Отзывает ссылку подписки; отсутствие ссылки ошибкой не считается.

        Args:
            user_id: Владелец ссылки.

        Raises:
            CalendarFeedServiceError: Если отозвать ссылку не удалось.
        """
        try:
            deleted = await self.tokens_repository.delete_by_user(user_id=user_id, commit=True)
        except CalendarFeedTokensRepositoryError as error:
            raise CalendarFeedServiceError(str(error)) from error
        logger.info(
            "✅ Ссылка подписки пользователя id=%s отозвана: %s.",
            user_id,
            "была" if deleted else "не было",
        )

    async def render_feed(self, *, secret: str, app_url: str, today: date | None = None) -> str:
        """Собирает ленту .ics владельца ссылки с его правами на момент запроса.

        Args:
            secret: Секрет из адреса ленты.
            app_url: Адрес интерфейса трекера без завершающей косой черты.
            today: Текущая дата; по умолчанию — дата UTC сервера.

        Returns:
            Текст ``text/calendar``.

        Raises:
            CalendarFeedNotFoundError: Если ссылка неизвестна, отозвана или
                владелец отключён.
            CalendarFeedServiceError: Если собрать ленту не удалось.
        """
        if not secret.startswith(FEED_TOKEN_PREFIX) or len(secret) > FEED_TOKEN_MAX_LENGTH:
            raise CalendarFeedNotFoundError()
        today = today or datetime.now(UTC).date()
        try:
            token = await self.tokens_repository.get_by_hash(hash_token_secret(secret))
            if token is None:
                raise CalendarFeedNotFoundError()
            user = await self.users_repository.get_by_id(user_id=token.user_id)
            if user is None or not user.is_active:
                raise CalendarFeedNotFoundError()
            items = await self.calendar_service.get_feed_items(
                user_id=user.id,
                date_from=today - timedelta(days=FEED_PAST_DAYS),
                date_to=today + timedelta(days=FEED_FUTURE_DAYS),
                today=today,
            )
        except (CalendarFeedTokensRepositoryError, UsersRepositoryError) as error:
            raise CalendarFeedServiceError(str(error)) from error
        except CalendarServiceError as error:
            raise CalendarFeedServiceError(error.error_details) from error
        stamp = datetime.now(UTC)
        # UID уже сохранённых событий сохраняются при смене бренда: подписчики
        # должны обновлять прежние события, а не создавать их дубликаты.
        events = [
            IcsEvent(
                uid=f"task-{task.id}@vera-task-tracker",
                summary=f"{DONE_MARK if task.is_done else ''}{task.key} {task.title}",
                date_from=min(task.start_date or task.due_date, task.due_date),
                date_to=task.due_date,
                stamp=task.updated_at or stamp,
                url=f"{app_url}/my-tasks?task={task.id}",
                description=task.source.name,
            )
            for task in items.tasks
            if task.due_date is not None
        ]
        events += [
            IcsEvent(
                uid=(
                    f"milestone-{milestone.id}@vera-task-tracker"
                    if milestone.id is not None
                    else f"project-deadline-{milestone.project.id}@vera-task-tracker"
                ),
                summary=f"{MILESTONE_MARK}{milestone.project.key}: {milestone.title}",
                date_from=milestone.due_date,
                date_to=milestone.due_date,
                stamp=stamp,
                url=f"{app_url}/projects/{milestone.project.key}/calendar",
                description=milestone.project.name,
            )
            for milestone in items.milestones
        ]
        logger.info(
            "✅ Лента календаря пользователя id=%s: событий %s.", token.user_id, len(events)
        )
        return render_calendar(name=FEED_NAME, events=events)
