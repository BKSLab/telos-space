import logging

from src.exceptions.auth import WrongCurrentPasswordError
from src.exceptions.storage import AvatarStorageError
from src.exceptions.users import (
    AvatarNotFoundError,
    AvatarOperationError,
    AvatarTooLargeError,
    AvatarUnsupportedTypeError,
    UserNotFoundError,
    UsersRepositoryError,
    UsersServiceError,
)
from src.repositories.users import UsersRepository
from src.schemas.users import UserSchema, UserSummarySchema
from src.services.auth import to_user_schema, to_user_summary
from src.storage.avatars import AvatarStorage
from src.utils.security import hash_password, verify_password

logger = logging.getLogger(__name__)

MAX_AVATAR_BYTES = 5 * 1024 * 1024
AVATAR_EXTENSIONS: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class UsersService:
    """Сервис профиля пользователя."""

    def __init__(
        self,
        users_repository: UsersRepository,
        avatar_storage: AvatarStorage,
    ):
        self.users_repository = users_repository
        self.avatar_storage = avatar_storage
        self.max_avatar_bytes = MAX_AVATAR_BYTES

    async def get_user(self, user_id: int) -> UserSchema:
        """Возвращает карточку пользователя.

        Args:
            user_id: Идентификатор пользователя.

        Returns:
            Карточка пользователя.

        Raises:
            UserNotFoundError: Если пользователь не найден.
            UsersServiceError: Если получить пользователя не удалось.
        """
        try:
            return to_user_schema(await self._get_user(user_id=user_id))
        except UsersRepositoryError as error:
            logger.error("❌ Ошибка получения пользователя id=%s.", user_id, exc_info=True)
            raise UsersServiceError(str(error)) from error

    async def update_profile(self, user_id: int, data: dict) -> UserSchema:
        """Обновляет ФИО и контакты пользователя.

        Args:
            user_id: Идентификатор пользователя.
            data: Изменяемые поля профиля.

        Returns:
            Обновлённая карточка пользователя.

        Raises:
            UserNotFoundError: Если пользователь не найден.
            UsersServiceError: Если обновить профиль не удалось.
        """
        try:
            user = await self._get_user(user_id=user_id)
            updated = await self.users_repository.update(user=user, data=data, commit=True)
            return to_user_schema(updated)
        except UsersRepositoryError as error:
            logger.error("❌ Ошибка обновления профиля id=%s.", user_id, exc_info=True)
            raise UsersServiceError(str(error)) from error

    async def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
    ) -> None:
        """Меняет пароль после проверки текущего.

        Args:
            user_id: Идентификатор пользователя.
            current_password: Действующий пароль.
            new_password: Новый пароль.

        Returns:
            ``None`` после успешной смены.

        Raises:
            UserNotFoundError: Если пользователь не найден.
            WrongCurrentPasswordError: Если текущий пароль неверен.
            UsersServiceError: Если сменить пароль не удалось.
        """
        try:
            user = await self._get_user(user_id=user_id)
            if not verify_password(current_password, user.password_hash):
                raise WrongCurrentPasswordError(user_id=user_id)
            await self.users_repository.update(
                user=user,
                data={"password_hash": hash_password(new_password)},
                commit=True,
            )
            logger.info("✅ Пароль пользователя id=%s изменён.", user_id)
        except UsersRepositoryError as error:
            logger.error("❌ Ошибка смены пароля id=%s.", user_id, exc_info=True)
            raise UsersServiceError(str(error)) from error

    async def set_avatar(self, user_id: int, content_type: str, content: bytes) -> None:
        """Сохраняет новую фотографию профиля вместо прежней.

        Args:
            user_id: Идентификатор пользователя.
            content_type: MIME-тип загружаемого файла.
            content: Бинарное содержимое файла.

        Returns:
            ``None`` после успешной загрузки.

        Raises:
            UserNotFoundError: Если пользователь не найден.
            AvatarUnsupportedTypeError: Если тип файла не поддерживается.
            AvatarTooLargeError: Если файл слишком большой.
            UsersServiceError: Если сохранить фотографию не удалось.
        """
        extension = AVATAR_EXTENSIONS.get(content_type.split(";", maxsplit=1)[0].strip().lower())
        if extension is None:
            raise AvatarUnsupportedTypeError(content_type=content_type)
        if len(content) > self.max_avatar_bytes:
            raise AvatarTooLargeError(max_bytes=self.max_avatar_bytes)

        try:
            user = await self._get_user(user_id=user_id)
            previous_key = user.avatar_key
            storage_key = await self.avatar_storage.save(
                user_id=user_id,
                extension=extension,
                content=content,
            )
            await self.users_repository.update(
                user=user,
                data={"avatar_key": storage_key},
                commit=True,
            )
            if previous_key is not None:
                await self._remove_file(storage_key=previous_key)
            logger.info("✅ Фотография пользователя id=%s обновлена.", user_id)
        except AvatarStorageError as error:
            logger.error("❌ Хранилище не сохранило фотографию id=%s.", user_id, exc_info=True)
            raise AvatarOperationError(str(error)) from error
        except UsersRepositoryError as error:
            logger.error("❌ Ошибка сохранения фотографии id=%s.", user_id, exc_info=True)
            raise UsersServiceError(str(error)) from error

    async def get_avatar(self, user_id: int) -> tuple[bytes, str]:
        """Возвращает содержимое фотографии и её MIME-тип.

        Args:
            user_id: Идентификатор пользователя.

        Returns:
            Пара из бинарного содержимого и MIME-типа.

        Raises:
            UserNotFoundError: Если пользователь не найден.
            AvatarNotFoundError: Если фотография не загружена.
            UsersServiceError: Если прочитать фотографию не удалось.
        """
        try:
            user = await self._get_user(user_id=user_id)
            if user.avatar_key is None:
                raise AvatarNotFoundError(user_id=user_id)
            content = await self.avatar_storage.read(storage_key=user.avatar_key)
            return content, _content_type_for(user.avatar_key)
        except AvatarStorageError as error:
            logger.error("❌ Хранилище не отдало фотографию id=%s.", user_id, exc_info=True)
            raise AvatarOperationError(str(error)) from error
        except UsersRepositoryError as error:
            logger.error("❌ Ошибка чтения фотографии id=%s.", user_id, exc_info=True)
            raise UsersServiceError(str(error)) from error

    async def get_identity_avatar(self, user_id: int) -> tuple[bytes, str]:
        """Читает только фото активной локальной identity, доступной в каталоге users.

        Ни профиль, ни контакты не возвращаются. Неактивная, отсутствующая
        identity и отсутствующая фотография дают одинаковый not-found.
        """
        try:
            user = await self.users_repository.get_by_id(user_id=user_id)
            if user is None or not user.is_active or not user.avatar_key:
                raise AvatarNotFoundError(user_id=user_id)
            storage_key = user.avatar_key
            content = await self.avatar_storage.read(storage_key=storage_key)
            current = await self.users_repository.get_by_id(user_id=user_id)
            if current is None or not current.is_active or current.avatar_key != storage_key:
                raise AvatarNotFoundError(user_id=user_id)
            return content, _content_type_for(storage_key)
        except AvatarStorageError as error:
            if isinstance(error.__cause__, FileNotFoundError):
                raise AvatarNotFoundError(user_id=user_id) from error
            raise AvatarOperationError("Не удалось прочитать фотографию пользователя.") from error
        except UsersRepositoryError as error:
            raise UsersServiceError("Не удалось проверить фотографию пользователя.") from error

    async def delete_avatar(self, user_id: int) -> None:
        """Удаляет фотографию профиля.

        Args:
            user_id: Идентификатор пользователя.

        Returns:
            ``None`` после успешного удаления.

        Raises:
            UserNotFoundError: Если пользователь не найден.
            AvatarNotFoundError: Если фотография не загружена.
            UsersServiceError: Если удалить фотографию не удалось.
        """
        try:
            user = await self._get_user(user_id=user_id)
            if user.avatar_key is None:
                raise AvatarNotFoundError(user_id=user_id)
            storage_key = user.avatar_key
            await self.users_repository.update(
                user=user,
                data={"avatar_key": None},
                commit=True,
            )
            await self._remove_file(storage_key=storage_key)
        except AvatarStorageError as error:
            logger.error("❌ Хранилище не удалило фотографию id=%s.", user_id, exc_info=True)
            raise AvatarOperationError(str(error)) from error
        except UsersRepositoryError as error:
            logger.error("❌ Ошибка удаления фотографии id=%s.", user_id, exc_info=True)
            raise UsersServiceError(str(error)) from error

    async def search_users(
        self, query: str | None = None, limit: int = 20
    ) -> list[UserSummarySchema]:
        """Ищет активные локальные identities без контактов и полей авторизации."""
        try:
            users = await self.users_repository.search(query=query, limit=limit)
            return [to_user_summary(user) for user in users]
        except UsersRepositoryError as error:
            raise UsersServiceError("Не удалось найти локальных пользователей.") from error

    async def _get_user(self, user_id: int):
        """Возвращает пользователя или поднимает доменную ошибку."""
        user = await self.users_repository.get_by_id(user_id=user_id)
        if user is None:
            raise UserNotFoundError(user_id=user_id)
        return user

    async def _remove_file(self, storage_key: str) -> None:
        """Best-effort удаляет файл: запись в БД уже обновлена."""
        try:
            await self.avatar_storage.delete(storage_key=storage_key)
        except AvatarStorageError:
            logger.warning(
                "⚠️ Не удалось удалить файл фотографии %s.",
                storage_key,
                exc_info=True,
            )


def _content_type_for(storage_key: str) -> str:
    """Определяет MIME-тип по расширению сохранённого файла."""
    for content_type, extension in AVATAR_EXTENSIONS.items():
        if storage_key.endswith(extension):
            return content_type
    return "application/octet-stream"
