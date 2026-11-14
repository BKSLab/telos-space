"""Доступ к таблице сервисных API-ключей."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Result, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.api_keys import ApiKey
from src.exceptions.api_keys import ApiKeysRepositoryError

logger = logging.getLogger(__name__)

# Отметка использования не чаще интервала: иначе каждый вызов агента
# превращал бы чтение в запись строки ключа.
LAST_USED_REFRESH = timedelta(minutes=5)


class ApiKeysRepository:
    """Репозиторий сервисных API-ключей."""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def get_by_id(self, key_id: uuid.UUID) -> ApiKey | None:
        """Возвращает ключ по идентификатору.

        Raises:
            ApiKeysRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            return await self.db_session.get(ApiKey, key_id, populate_existing=True)
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить API-ключ id=%s.", key_id, exc_info=True)
            raise ApiKeysRepositoryError(str(error)) from error

    async def get_active_by_prefix(self, key_prefix: str) -> ApiKey | None:
        """Возвращает неотозванный ключ по публичному префиксу.

        Срок действия здесь не проверяется: истёкший ключ сервис отличает
        от выдуманного, чтобы подсказать интегратору причину.

        Raises:
            ApiKeysRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(ApiKey)
                .where(
                    ApiKey.key_prefix == key_prefix,
                    ApiKey.is_active.is_(True),
                )
                .execution_options(populate_existing=True)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось проверить API-ключ.", exc_info=True)
            raise ApiKeysRepositoryError(str(error)) from error

    async def get_list(self) -> list[ApiKey]:
        """Возвращает все ключи, новые сверху.

        Raises:
            ApiKeysRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(ApiKey).order_by(ApiKey.created_at.desc(), ApiKey.key_prefix)
            )
            return list(result.scalars().all())
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось получить список API-ключей.", exc_info=True)
            raise ApiKeysRepositoryError(str(error)) from error

    async def get_list_by_owner(self, owner_user_id: int) -> list[ApiKey]:
        """Возвращает ключи владельца, новые сверху.

        Args:
            owner_user_id: Владелец ключей.

        Raises:
            ApiKeysRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            result: Result = await self.db_session.execute(
                select(ApiKey)
                .where(ApiKey.owner_user_id == owner_user_id)
                .order_by(ApiKey.created_at.desc(), ApiKey.key_prefix)
            )
            return list(result.scalars().all())
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            logger.error(
                "❌ Не удалось получить ключи владельца id=%s.", owner_user_id, exc_info=True
            )
            raise ApiKeysRepositoryError(str(error)) from error

    async def create(self, *, data: dict, commit: bool = True) -> ApiKey:
        """Сохраняет выпущенный ключ.

        Args:
            data: Поля ключа (хеш, префикс, владелец, права, срок, выдавший).
            commit: Зафиксировать транзакцию сразу.

        Returns:
            Сохранённый ключ с серверными значениями.

        Raises:
            ApiKeysRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            api_key = ApiKey(**data)
            self.db_session.add(api_key)
            await self.db_session.flush()
            if commit:
                await self.db_session.commit()
            return api_key
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось сохранить API-ключ.", exc_info=True)
            raise ApiKeysRepositoryError(str(error)) from error

    async def update(self, *, api_key: ApiKey, data: dict, commit: bool = True) -> ApiKey:
        """Меняет поля ключа.

        Args:
            api_key: Ключ из этой же сессии.
            data: Новые значения полей.
            commit: Зафиксировать транзакцию сразу.

        Returns:
            Обновлённый ключ.

        Raises:
            ApiKeysRepositoryError: Если запрос к БД завершился ошибкой.
        """
        try:
            for field, value in data.items():
                setattr(api_key, field, value)
            await self.db_session.flush()
            if commit:
                await self.db_session.commit()
            return api_key
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось изменить API-ключ id=%s.", api_key.id, exc_info=True)
            raise ApiKeysRepositoryError(str(error)) from error

    async def touch_last_used(self, api_key: ApiKey, *, commit: bool = True) -> None:
        """Отмечает использование ключа не чаще одного раза в интервал.

        Raises:
            ApiKeysRepositoryError: Если запрос к БД завершился ошибкой.
        """
        now = datetime.now(UTC)
        if api_key.last_used_at is not None and now - api_key.last_used_at < LAST_USED_REFRESH:
            return
        try:
            api_key.last_used_at = now
            await self.db_session.flush()
            if commit:
                await self.db_session.commit()
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            logger.error("❌ Не удалось отметить использование API-ключа.", exc_info=True)
            raise ApiKeysRepositoryError(str(error)) from error
