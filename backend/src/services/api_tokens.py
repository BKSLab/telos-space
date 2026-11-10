import logging
from datetime import UTC, datetime, timedelta

from src.db.models.api_tokens import ApiToken, ApiTokenScope
from src.exceptions.api_tokens import (
    ApiTokenLimitExceededError,
    ApiTokenNotFoundError,
    ApiTokensRepositoryError,
    ApiTokensServiceError,
)
from src.repositories.api_tokens import ApiTokensRepository
from src.schemas.api_tokens import (
    ApiTokenCreatedSchema,
    ApiTokenCreateSchema,
    ApiTokenSchema,
)
from src.utils.api_tokens import (
    TOKEN_PREFIX,
    build_display_prefix,
    generate_token_secret,
    hash_token_secret,
)

logger = logging.getLogger(__name__)


class ApiTokensService:
    """Сервис токенов доступа внешних клиентов."""

    def __init__(self, tokens_repository: ApiTokensRepository, *, max_active_tokens: int):
        """Создаёт сервис токенов доступа.

        Args:
            tokens_repository: Репозиторий токенов.
            max_active_tokens: Предел одновременно действующих токенов
                одного пользователя.
        """
        self.tokens_repository = tokens_repository
        self.max_active_tokens = max_active_tokens

    async def list_tokens(self, user_id: int) -> list[ApiTokenSchema]:
        """Возвращает токены пользователя без секретов.

        Args:
            user_id: Владелец токенов.

        Returns:
            Список карточек токенов, новые сверху.

        Raises:
            ApiTokensServiceError: Если репозиторий вернул ошибку.
        """
        try:
            tokens = await self.tokens_repository.get_by_user(user_id)
        except ApiTokensRepositoryError as error:
            raise ApiTokensServiceError(str(error)) from error
        return [ApiTokenSchema.model_validate(token) for token in tokens]

    async def issue_token(
        self,
        *,
        user_id: int,
        data: ApiTokenCreateSchema,
    ) -> ApiTokenCreatedSchema:
        """Выпускает токен и возвращает секрет единственный раз.

        Args:
            user_id: Владелец токена.
            data: Имя, права и срок жизни.

        Returns:
            Карточка токена вместе с секретом.

        Raises:
            ApiTokenLimitExceededError: Если исчерпан лимит действующих токенов.
            ApiTokensServiceError: Если репозиторий вернул ошибку.
        """
        try:
            active = await self.tokens_repository.count_active_by_user(user_id)
            if active >= self.max_active_tokens:
                raise ApiTokenLimitExceededError(self.max_active_tokens)

            secret = generate_token_secret()
            ttl_days = data.ttl_days
            expires_at = (
                datetime.now(UTC) + timedelta(days=ttl_days) if ttl_days is not None else None
            )
            token = await self.tokens_repository.create(
                user_id=user_id,
                name=data.name,
                token_hash=hash_token_secret(secret),
                prefix=build_display_prefix(secret),
                scope=ApiTokenScope(data.scope),
                expires_at=expires_at,
                commit=True,
            )
        except ApiTokensRepositoryError as error:
            raise ApiTokensServiceError(str(error)) from error

        logger.info("✅ Выпущен токен доступа id=%s для пользователя id=%s.", token.id, user_id)
        return ApiTokenCreatedSchema(
            token=ApiTokenSchema.model_validate(token),
            secret=secret,
        )

    async def revoke_token(self, *, token_id: int, user_id: int) -> None:
        """Отзывает токен пользователя.

        Args:
            token_id: Идентификатор токена.
            user_id: Владелец, от имени которого выполняется отзыв.

        Raises:
            ApiTokenNotFoundError: Если токен не найден или чужой.
            ApiTokensServiceError: Если репозиторий вернул ошибку.
        """
        try:
            revoked = await self.tokens_repository.revoke(
                token_id=token_id,
                user_id=user_id,
                commit=True,
            )
        except ApiTokensRepositoryError as error:
            raise ApiTokensServiceError(str(error)) from error
        if not revoked:
            raise ApiTokenNotFoundError(token_id)
        logger.info("✅ Отозван токен доступа id=%s пользователя id=%s.", token_id, user_id)

    async def resolve_secret(self, secret: str) -> ApiToken | None:
        """Находит действующий токен по предъявленному секрету.

        Args:
            secret: Значение из заголовка ``Authorization``.

        Returns:
            Действующий токен или ``None``.

        Raises:
            ApiTokensServiceError: Если репозиторий вернул ошибку.
        """
        if not secret.startswith(TOKEN_PREFIX):
            return None
        try:
            return await self.tokens_repository.get_active_by_hash(hash_token_secret(secret))
        except ApiTokensRepositoryError as error:
            raise ApiTokensServiceError(str(error)) from error
