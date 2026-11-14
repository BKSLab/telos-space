import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import Result, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.api_tokens import ApiToken, ApiTokenScope
from src.exceptions.api_tokens import ApiTokensRepositoryError

logger = logging.getLogger(__name__)

LAST_USED_REFRESH = timedelta(minutes=5)


class ApiTokensRepository:
    """Репозиторий токенов доступа внешних клиентов."""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def create(
        self,
        *,
        user_id: int,
        name: str,
        token_hash: str,
        prefix: str,
        scope: ApiTokenScope,
        expires_at: datetime | None,
        commit: bool = True,
    ) -> ApiToken:
        """Сохраняет выпущенный токен.

        Args:
            user_id: Владелец токена.
            name: Человекочитаемое имя.
            token_hash: SHA-256 от секрета.
            prefix: Первые символы секрета для отображения.
            scope: Права токена.
            expires_at: Момент истечения или ``None`` для бессрочного токена.

        Returns:
            Сохранённый токен.

        Raises:
            ApiTokensRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            token = ApiToken(
                user_id=user_id,
                name=name,
                token_hash=token_hash,
                prefix=prefix,
                scope=scope,
                expires_at=expires_at,
            )
            self.db_session.add(token)
            # flush выполняется всегда: без него при commit=False запись
            # не дошла бы до базы и не получила серверных значений.
            await self.db_session.flush()
            if commit:
                await self.db_session.commit()
            return token
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось сохранить токен доступа.", exc_info=True)
            raise ApiTokensRepositoryError(str(error)) from error

    async def get_active_by_hash(self, token_hash: str) -> ApiToken | None:
        """Возвращает действующий токен по хешу секрета.

        Отозванные и истёкшие токены не возвращаются: проверка выполняется
        в запросе, чтобы вызывающий код не мог её случайно пропустить.

        Args:
            token_hash: SHA-256 от предъявленного секрета.

        Returns:
            Действующий токен или ``None``.

        Raises:
            ApiTokensRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(ApiToken)
                .where(
                    ApiToken.token_hash == token_hash,
                    ApiToken.revoked_at.is_(None),
                    or_(
                        ApiToken.expires_at.is_(None),
                        ApiToken.expires_at > datetime.now(UTC),
                    ),
                )
                .execution_options(populate_existing=True)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось проверить токен доступа.", exc_info=True)
            raise ApiTokensRepositoryError(str(error)) from error

    async def get_active_by_id(self, token_id: int) -> ApiToken | None:
        """Server-only повторная проверка identity без секрета и audit-commit."""
        try:
            result = await self.db_session.execute(
                select(ApiToken)
                .where(
                    ApiToken.id == token_id,
                    ApiToken.revoked_at.is_(None),
                    or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > datetime.now(UTC)),
                )
                .execution_options(populate_existing=True)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise ApiTokensRepositoryError(str(error)) from error

    async def get_by_user(self, user_id: int) -> list[ApiToken]:
        """Возвращает токены пользователя, новые сверху.

        Args:
            user_id: Владелец токенов.

        Returns:
            Список токенов, включая отозванные и истёкшие.

        Raises:
            ApiTokensRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(ApiToken)
                .where(ApiToken.user_id == user_id)
                .order_by(ApiToken.created_at.desc(), ApiToken.id.desc())
            )
            return list(result.scalars().all())
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить токены пользователя id=%s.", user_id, exc_info=True
            )
            raise ApiTokensRepositoryError(str(error)) from error

    async def count_active_by_user(self, user_id: int) -> int:
        """Возвращает число действующих токенов пользователя.

        Args:
            user_id: Владелец токенов.

        Returns:
            Количество неотозванных и неистёкших токенов.

        Raises:
            ApiTokensRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(ApiToken.id).where(
                    ApiToken.user_id == user_id,
                    ApiToken.revoked_at.is_(None),
                    or_(
                        ApiToken.expires_at.is_(None),
                        ApiToken.expires_at > datetime.now(UTC),
                    ),
                )
            )
            return len(list(result.scalars().all()))
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось сосчитать токены пользователя id=%s.", user_id, exc_info=True
            )
            raise ApiTokensRepositoryError(str(error)) from error

    async def revoke(
        self,
        *,
        token_id: int,
        user_id: int,
        commit: bool = True,
    ) -> bool:
        """Отзывает токен, если он принадлежит пользователю.

        Args:
            token_id: Идентификатор токена.
            user_id: Владелец, от имени которого выполняется отзыв.

        Returns:
            ``True``, если токен найден и отозван.

        Raises:
            ApiTokensRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(ApiToken).where(
                    ApiToken.id == token_id,
                    ApiToken.user_id == user_id,
                )
            )
            token = result.scalar_one_or_none()
            if token is None:
                return False
            if token.revoked_at is None:
                token.revoked_at = datetime.now(UTC)
                await self.db_session.flush()
                if commit:
                    await self.db_session.commit()
            return True
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось отозвать токен id=%s.", token_id, exc_info=True)
            raise ApiTokensRepositoryError(str(error)) from error

    async def touch_last_used(
        self,
        token: ApiToken,
        *,
        commit: bool = True,
    ) -> None:
        """Отмечает использование токена не чаще одного раза в интервал.

        Запись на каждый вызов инструмента превратила бы чтение в запись,
        поэтому отметка обновляется приблизительно.

        Args:
            token: Токен, которым выполнен запрос.

        Raises:
            ApiTokensRepositoryError: Если запрос к БД завершился ошибкой.
        """
        now = datetime.now(UTC)
        if token.last_used_at is not None and now - token.last_used_at < LAST_USED_REFRESH:
            return
        try:
            token.last_used_at = now
            await self.db_session.flush()
            if commit:
                await self.db_session.commit()
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось отметить использование токена.", exc_info=True)
            raise ApiTokensRepositoryError(str(error)) from error
