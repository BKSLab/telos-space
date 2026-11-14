"""Регистрация, вход и разрешение принципала запроса.

Аутентификация целиком принадлежит сервисному слою: и HTTP, и MCP задают
один и тот же вопрос «кто это и что ему можно», и ответ на него не должен
зависеть от транспорта.
"""

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from src.db.models.api_tokens import ApiTokenScope
from src.db.models.users import User
from src.exceptions.api_keys import ApiKeyInvalidError, ApiKeyScopeError, ApiKeysRepositoryError
from src.exceptions.api_tokens import ApiTokensRepositoryError
from src.exceptions.auth import (
    AuthServiceError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidInviteCodeError,
    NotAuthenticatedError,
)
from src.exceptions.users import (
    UsernameAlreadyExistsRepositoryError,
    UsernameConflictError,
    UsersRepositoryError,
)
from src.repositories.api_keys import ApiKeysRepository
from src.repositories.api_tokens import ApiTokensRepository
from src.repositories.users import UsersRepository
from src.schemas.users import UserSchema, UserSummarySchema
from src.utils.api_keys import is_api_key, key_prefix_of, verify_api_key
from src.utils.api_scopes import API_SCOPES, WRITE_SCOPES
from src.utils.api_tokens import TOKEN_PREFIX, hash_token_secret
from src.utils.request_actor import RequestActor
from src.utils.security import hash_password, secrets_match, verify_password
from src.utils.tokens import create_access_token, decode_access_token

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ApiKeyGrant:
    """Права ключа, принадлежащего локальному пользователю."""

    key_id: uuid.UUID
    label: str
    key_prefix: str
    scopes: frozenset[str]
    owner_user_id: int
    kind: str = "personal"


@dataclass(frozen=True, slots=True)
class Principal:
    """Кто выполняет запрос и с какими правами.

    Наружу отдаются только безопасные поля идентичности: ORM-модель
    пользователя не должна подниматься в транспортный слой.

    Сессия из cookie всегда имеет полные права: ограничение скоупа введено
    для внешних клиентов, а не для владельца, работающего в интерфейсе.

    Attributes:
        user_id: Идентификатор пользователя запроса.
        username: Логин пользователя.
        last_name: Фамилия.
        first_name: Имя.
        middle_name: Отчество, если оно указано.
        scope: Права предъявленных учётных данных.
        via_api_token: Предъявлен API-токен, а не сессия интерфейса.
    """

    user_id: int
    username: str
    last_name: str
    first_name: str
    middle_name: str | None
    scope: ApiTokenScope
    via_api_token: bool
    api_key: ApiKeyGrant | None = None
    api_token_id: int | None = None

    @property
    def login(self) -> str:
        return self.username

    def allows(self, scope: str) -> bool:
        if self.api_key is not None:
            return scope in self.api_key.scopes
        if not self.via_api_token:
            return True
        return scope in API_SCOPES and (scope not in WRITE_SCOPES or self.can_write)

    def require(self, scope: str) -> None:
        if not self.allows(scope):
            granted = sorted(self.api_key.scopes) if self.api_key else []
            raise ApiKeyScopeError(scope, granted)

    def request_actor(self) -> RequestActor:
        return RequestActor(
            user_id=self.user_id,
            name=self.full_name,
            via_api_key_label=self.api_key.label if self.api_key else None,
        )

    @property
    def can_write(self) -> bool:
        """Разрешено ли этим учётным данным изменять данные."""
        return self.scope is ApiTokenScope.WRITE

    @property
    def full_name(self) -> str:
        """Полное имя с отчеством для снимков авторства."""
        parts = (self.last_name, self.first_name, self.middle_name)
        return " ".join(part for part in parts if part) or self.username

    @property
    def short_name(self) -> str:
        """Имя без отчества для коротких подписей."""
        parts = (self.last_name, self.first_name)
        return " ".join(part for part in parts if part) or self.username


class AuthService:
    """Сервис регистрации, входа и разрешения принципала запроса."""

    def __init__(
        self,
        users_repository: UsersRepository,
        *,
        tokens_repository: ApiTokensRepository,
        invite_code: str,
        api_keys_repository: ApiKeysRepository | None = None,
    ):
        """Создаёт сервис аутентификации.

        Args:
            users_repository: Репозиторий пользователей.
            tokens_repository: Репозиторий API-токенов.
            invite_code: Ожидаемый код приглашения. Передаётся значением:
                сервис не должен знать о конфигурации приложения.
        """
        self.users_repository = users_repository
        self.tokens_repository = tokens_repository
        self.invite_code = invite_code
        self.api_keys_repository = api_keys_repository

    async def resolve_principal(
        self,
        *,
        session_token: str | None,
        bearer_secret: str | None,
        record_usage: bool = True,
    ) -> Principal:
        """Определяет пользователя запроса по API-токену либо по сессии.

        Единственная точка аутентификации приложения: и HTTP-эндпоинты, и
        MCP-инструменты проходят через неё, поэтому правила прав не
        дублируются и не расходятся между транспортами.

        Токен побеждает cookie: иначе ограничение скоупа обходилось бы
        предъявлением обоих способов сразу.

        Args:
            session_token: Значение cookie сессии, если оно предъявлено.
            bearer_secret: Секрет из заголовка ``Authorization``.
            record_usage: Отмечать использование отдельным commit; повторная
                проверка внутри доменной транзакции передаёт False.

        Returns:
            Принципал запроса вместе с его правами.

        Raises:
            NotAuthenticatedError: Если учётные данные отсутствуют или
                недействительны. Отозванный, истёкший и выдуманный токен
                неотличимы для клиента намеренно.
            InactiveUserError: Если учётная запись отключена.
            AuthServiceError: Если проверить учётные данные не удалось.
        """
        if bearer_secret is not None:
            if is_api_key(bearer_secret):
                return await self._principal_from_api_key(
                    secret=bearer_secret, record_usage=record_usage
                )
            if bearer_secret.startswith(TOKEN_PREFIX):
                return await self._principal_from_token(
                    secret=bearer_secret, record_usage=record_usage
                )
            raise NotAuthenticatedError()
        if not session_token:
            raise NotAuthenticatedError()
        user_id = decode_access_token(session_token)
        if user_id is None:
            raise NotAuthenticatedError()
        user = await self._load_active_user(user_id)
        return _to_principal(user, scope=ApiTokenScope.WRITE, via_api_token=False)

    async def refresh_principal(self, principal: Principal) -> Principal:
        """Повторно проверяет server-only identity фонового действия без хранения секрета.

        Scopes не расширяются относительно аутентифицированного снимка. Публичный
        HTTP/MCP transport по-прежнему обязан сначала проверить сами credentials.
        """
        user = await self._load_active_user(principal.user_id)
        if principal.api_key is None:
            if principal.via_api_token:
                if principal.api_token_id is None:
                    raise NotAuthenticatedError()
                try:
                    token = await self.tokens_repository.get_active_by_id(principal.api_token_id)
                except ApiTokensRepositoryError as error:
                    raise AuthServiceError(str(error)) from error
                if token is None or token.user_id != principal.user_id:
                    raise NotAuthenticatedError()
                scope = (
                    ApiTokenScope.READ
                    if ApiTokenScope.READ in {principal.scope, token.scope}
                    else ApiTokenScope.WRITE
                )
                return replace(
                    _to_principal(user, scope=scope, via_api_token=True), api_token_id=token.id
                )
            return _to_principal(user, scope=principal.scope, via_api_token=principal.via_api_token)
        if self.api_keys_repository is None:
            raise ApiKeyInvalidError()
        try:
            key = await self.api_keys_repository.get_by_id(principal.api_key.key_id)
        except ApiKeysRepositoryError as error:
            raise AuthServiceError(str(error)) from error
        if (
            key is None
            or key.owner_user_id != principal.user_id
            or key.kind not in {"personal", "service"}
            or not key.is_active
            or key.revoked_at is not None
            or (key.expires_at is not None and key.expires_at <= datetime.now(UTC))
        ):
            raise ApiKeyInvalidError()
        scopes = frozenset(key.scopes) & principal.api_key.scopes & API_SCOPES.keys()
        current = _to_principal(
            user,
            scope=ApiTokenScope.WRITE if scopes & WRITE_SCOPES else ApiTokenScope.READ,
            via_api_token=True,
        )
        return replace(
            current,
            api_key=ApiKeyGrant(
                key_id=key.id,
                label=key.label,
                key_prefix=key.key_prefix,
                scopes=frozenset(scopes),
                owner_user_id=user.id,
                kind=key.kind,
            ),
        )

    async def _principal_from_token(self, *, secret: str, record_usage: bool = True) -> Principal:
        """Проверяет предъявленный секрет и собирает принципала."""
        try:
            token = await self.tokens_repository.get_active_by_hash(hash_token_secret(secret))
        except ApiTokensRepositoryError as error:
            logger.error("❌ Ошибка проверки токена доступа.", exc_info=True)
            raise AuthServiceError(str(error)) from error

        if token is None:
            raise NotAuthenticatedError()

        user = await self._load_active_user(token.user_id)
        if record_usage:
            try:
                await self.tokens_repository.touch_last_used(token, commit=True)
            except ApiTokensRepositoryError:
                # Отметка использования — диагностика, а не условие доступа.
                logger.warning("⚠️ Не удалось отметить использование токена id=%s.", token.id)
        return replace(
            _to_principal(user, scope=token.scope, via_api_token=True), api_token_id=token.id
        )

    async def _principal_from_api_key(self, *, secret: str, record_usage: bool = True) -> Principal:
        prefix = key_prefix_of(secret)
        if prefix is None or self.api_keys_repository is None:
            raise ApiKeyInvalidError()
        try:
            key = await self.api_keys_repository.get_active_by_prefix(prefix)
            if key is None or not verify_api_key(secret, key.key_hash):
                raise ApiKeyInvalidError()
            if key.kind not in {"personal", "service"} or key.owner_user_id is None:
                raise ApiKeyInvalidError()
            if key.expires_at is not None and key.expires_at <= datetime.now(UTC):
                raise ApiKeyInvalidError()
            user = await self._load_active_user(key.owner_user_id)
            scopes = frozenset(str(scope) for scope in key.scopes if scope in API_SCOPES)
            principal = _to_principal(
                user,
                scope=ApiTokenScope.WRITE if scopes & WRITE_SCOPES else ApiTokenScope.READ,
                via_api_token=True,
            )
            grant = ApiKeyGrant(
                key_id=key.id,
                label=key.label,
                key_prefix=key.key_prefix,
                scopes=scopes,
                owner_user_id=user.id,
                kind=key.kind,
            )
            if record_usage:
                try:
                    await self.api_keys_repository.touch_last_used(key, commit=True)
                except ApiKeysRepositoryError:
                    logger.warning("Не удалось отметить использование API-ключа id=%s.", key.id)
            return replace(principal, api_key=grant)
        except ApiKeysRepositoryError as error:
            raise AuthServiceError(str(error)) from error

    async def _load_active_user(self, user_id: int) -> User:
        """Загружает пользователя и проверяет, что он не отключён."""
        try:
            user = await self.users_repository.get_by_id(user_id=user_id)
        except UsersRepositoryError as error:
            logger.error("❌ Ошибка загрузки пользователя сессии.", exc_info=True)
            raise AuthServiceError(str(error)) from error

        if user is None:
            # Учётные данные ещё валидны, но пользователя уже удалили.
            raise NotAuthenticatedError()
        if not user.is_active:
            raise InactiveUserError(user_id=user.id)
        return user

    async def register(self, data: dict) -> UserSchema:
        """Регистрирует пользователя после проверки кода приглашения.

        Args:
            data: Поля регистрации, включая пароль и код приглашения.

        Returns:
            Карточка созданного пользователя.

        Raises:
            InvalidInviteCodeError: Если код приглашения не подошёл.
            UsernameConflictError: Если логин уже занят.
            AuthServiceError: Если создать пользователя не удалось.
        """
        payload = dict(data)
        invite_code = str(payload.pop("invite_code", ""))
        password = str(payload.pop("password", ""))
        payload.pop("password_confirm", None)

        if not secrets_match(invite_code, self.invite_code):
            logger.warning("⚠️ Регистрация с неверным кодом приглашения.")
            raise InvalidInviteCodeError()

        try:
            user = await self.users_repository.save(
                data={**payload, "password_hash": hash_password(password)},
                commit=True,
            )
            logger.info("✅ Зарегистрирован пользователь %s.", user.username)
            return to_user_schema(user)
        except UsernameAlreadyExistsRepositoryError as error:
            logger.warning("⚠️ Логин %s уже занят.", error.username)
            raise UsernameConflictError(username=error.username) from error
        except UsersRepositoryError as error:
            logger.error("❌ Ошибка регистрации пользователя.", exc_info=True)
            raise AuthServiceError(str(error)) from error

    async def register_and_login(self, data: dict) -> tuple[UserSchema, str]:
        """Регистрирует пользователя и сразу открывает ему сессию.

        Регистрация без входа не имеет смысла для клиента: он всё равно
        тут же логинится. Держать эти два шага вместе — задача сервиса, а
        не транспорта.

        Args:
            data: Поля регистрации, включая пароль и код приглашения.

        Returns:
            Карточка созданного пользователя и подписанный токен сессии.

        Raises:
            InvalidInviteCodeError: Если код приглашения не подошёл.
            UsernameConflictError: Если логин уже занят.
            AuthServiceError: Если создать пользователя не удалось.
        """
        user = await self.register(data=data)
        return user, create_access_token(user_id=user.id)

    async def login(self, username: str, password: str) -> tuple[UserSchema, str]:
        """Проверяет пару логин и пароль и выпускает токен сессии.

        Args:
            username: Логин пользователя.
            password: Пароль в открытом виде.

        Returns:
            Карточка пользователя и подписанный токен сессии.

        Raises:
            InvalidCredentialsError: Если логин не найден или пароль неверен.
            InactiveUserError: Если учётная запись отключена.
            AuthServiceError: Если выполнить вход не удалось.
        """
        try:
            user = await self.users_repository.get_by_username(username=username)
            # Ответ одинаков для отсутствующего логина и неверного пароля,
            # иначе существующие логины можно перебрать по коду ответа.
            if user is None or not verify_password(password, user.password_hash):
                raise InvalidCredentialsError(username=username)
            if not user.is_active:
                raise InactiveUserError(user_id=user.id)

            logger.info("✅ Вход пользователя %s.", user.username)
            return to_user_schema(user), create_access_token(user_id=user.id)
        except UsersRepositoryError as error:
            logger.error("❌ Ошибка входа пользователя.", exc_info=True)
            raise AuthServiceError(str(error)) from error


def to_user_schema(user: User) -> UserSchema:
    """Преобразует ORM-пользователя в схему ответа без чувствительных полей."""
    return UserSchema(
        id=user.id,
        username=user.username,
        last_name=user.last_name,
        first_name=user.first_name,
        middle_name=user.middle_name,
        email=user.email,
        phone=user.phone,
        telegram=user.telegram,
        has_avatar=user.avatar_key is not None,
        created_at=user.created_at,
    )


def to_user_summary(user: User) -> UserSummarySchema:
    """Преобразует пользователя в безопасную идентичность для команды."""
    return UserSummarySchema(
        id=user.id,
        username=user.username,
        last_name=user.last_name,
        first_name=user.first_name,
        middle_name=user.middle_name,
        has_avatar=user.avatar_key is not None,
    )


def _to_principal(user: User, *, scope: ApiTokenScope, via_api_token: bool) -> Principal:
    """Собирает принципала из пользователя и прав предъявленных данных."""
    return Principal(
        user_id=user.id,
        username=user.username,
        last_name=user.last_name,
        first_name=user.first_name,
        middle_name=user.middle_name,
        scope=scope,
        via_api_token=via_api_token,
    )
