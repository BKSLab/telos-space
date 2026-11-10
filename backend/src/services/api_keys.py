"""Выпуск, изменение и отзыв API-ключей локальных пользователей.

Проверка ключа на запросе живёт в ``AuthService``: там единственная точка
аутентификации всех транспортов. Здесь — выпуск и сопровождение ключей владельца.
"""

import logging
import uuid
from datetime import UTC, datetime

from src.db.models.api_keys import ApiKey
from src.exceptions.api_keys import (
    ApiKeyNotFoundError,
    ApiKeyOwnerNotFoundError,
)
from src.repositories.api_keys import ApiKeysRepository
from src.repositories.users import UsersRepository
from src.schemas.api_keys import (
    ApiKeyCreatedSchema,
    ApiKeySchema,
    ApiKeyUpdateSchema,
    ApiScopeSchema,
    PersonalApiKeyCreateSchema,
    PersonalApiKeyUpdateSchema,
)
from src.utils.api_keys import generate_api_key, hash_api_key
from src.utils.api_scopes import API_SCOPES, PERSONAL_SCOPES

logger = logging.getLogger(__name__)


class ApiKeysService:
    """Сервис выдачи и сопровождения сервисных ключей."""

    def __init__(self, keys_repository: ApiKeysRepository, users_repository: UsersRepository):
        """Создаёт сервис.

        Args:
            keys_repository: Репозиторий ключей.
            users_repository: Репозиторий пользователей — для владельца ключа.
        """
        self.keys_repository = keys_repository
        self.users_repository = users_repository

    @staticmethod
    def list_scopes() -> list[ApiScopeSchema]:
        """Возвращает реестр прав для формы выдачи и справки."""
        return [ApiScopeSchema(code=code, description=text) for code, text in API_SCOPES.items()]

    async def list_keys(self) -> list[ApiKeySchema]:
        """Возвращает все ключи без секретов, новые сверху."""
        return [ApiKeySchema.model_validate(item) for item in await self.keys_repository.get_list()]

    async def get_key(self, key_id: uuid.UUID) -> ApiKeySchema:
        """Возвращает ключ по идентификатору.

        Raises:
            ApiKeyNotFoundError: Ключа нет.
        """
        api_key = await self.keys_repository.get_by_id(key_id)
        if api_key is None:
            raise ApiKeyNotFoundError(key_id)
        return ApiKeySchema.model_validate(api_key)

    async def update_key(
        self,
        key_id: uuid.UUID,
        data: ApiKeyUpdateSchema,
        *,
        actor_user_id: int | None,
        actor_name: str | None,
    ) -> ApiKeySchema:
        """Меняет имя, права, срок; ``is_active=false`` отзывает ключ.

        Args:
            key_id: UUID ключа.
            data: Переданные поля.
            actor_user_id: Кто меняет; ``None`` — инициатор не указан.
            actor_name: Его имя для следа отзыва.

        Returns:
            Обновлённый ключ.

        Raises:
            ApiKeyNotFoundError: Ключа нет.
        """
        api_key = await self.keys_repository.get_by_id(key_id)
        if api_key is None:
            raise ApiKeyNotFoundError(key_id)
        sent = data.model_dump(exclude_unset=True)
        changes: dict = {}
        for field in ("label", "scopes"):
            if sent.get(field) is not None:
                changes[field] = sent[field].strip() if field == "label" else sent[field]
        if "expires_at" in sent:
            # Срок — единственное поле, где явный null осмыслен: «бессрочно».
            changes["expires_at"] = sent["expires_at"]
        if sent.get("is_active") is not None and sent["is_active"] != api_key.is_active:
            changes["is_active"] = sent["is_active"]
            if sent["is_active"] is False:
                # Отзыв оставляет след: кто и когда закрыл доступ.
                changes.update(
                    revoked_at=datetime.now(UTC),
                    revoked_by_user_id=actor_user_id,
                    revoked_by_name=actor_name,
                )
                logger.info("🚫 Отозван API-ключ %s, отозвал=%s.", api_key.key_prefix, actor_name)
            else:
                changes.update(revoked_at=None, revoked_by_user_id=None, revoked_by_name=None)
        if not changes:
            return ApiKeySchema.model_validate(api_key)
        updated = await self.keys_repository.update(api_key=api_key, data=changes)
        return ApiKeySchema.model_validate(updated)

    @staticmethod
    def list_personal_scopes() -> list[ApiScopeSchema]:
        """Возвращает права, доступные личному ключу, для формы и справки."""
        return [
            ApiScopeSchema(code=code, description=API_SCOPES[code])
            for code in sorted(PERSONAL_SCOPES)
        ]

    async def list_personal_keys(self, owner_user_id: int) -> list[ApiKeySchema]:
        """Возвращает личные ключи пользователя без секретов, новые сверху."""
        keys = await self.keys_repository.get_list_by_owner(owner_user_id)
        return [ApiKeySchema.model_validate(item) for item in keys]

    async def create_personal_key(
        self,
        data: PersonalApiKeyCreateSchema,
        *,
        owner_user_id: int,
    ) -> ApiKeyCreatedSchema:
        """Выпускает личный ключ: владелец и выдавший — сам пользователь.

        Права ограничены белым списком личного ключа (проверяет схема):
        действовать от чужого имени личный ключ не может.

        Args:
            data: Имя, права и срок.
            owner_user_id: Локальный пользователь, выпускающий ключ себе.

        Returns:
            Ключ вместе с ``raw_key``.

        Raises:
            ApiKeyOwnerNotFoundError: Владельца нет или он отключён.
        """
        owner = await self.users_repository.get_by_id(user_id=owner_user_id)
        if owner is None or not owner.is_active:
            raise ApiKeyOwnerNotFoundError(owner_user_id)
        raw_key, key_prefix = generate_api_key()
        created = await self.keys_repository.create(
            data={
                "label": data.label.strip(),
                "key_hash": hash_api_key(raw_key),
                "key_prefix": key_prefix,
                "owner_user_id": owner.id,
                "owner_name": " ".join(
                    part for part in (owner.last_name, owner.first_name, owner.middle_name) if part
                )
                or owner.username,
                "kind": "personal",
                "scopes": list(data.scopes),
                "expires_at": data.expires_at,
                "is_active": True,
                "created_by_user_id": owner.id,
                "created_by_name": " ".join(
                    part for part in (owner.last_name, owner.first_name, owner.middle_name) if part
                )
                or owner.username,
            }
        )
        logger.info(
            "✅ Выпущен личный API-ключ %s владелец=%s права=%s.",
            key_prefix,
            owner.username,
            created.scopes,
        )
        return ApiKeyCreatedSchema(
            **ApiKeySchema.model_validate(created).model_dump(), raw_key=raw_key
        )

    async def get_personal_key(self, key_id: uuid.UUID, owner_user_id: int) -> ApiKey:
        """Возвращает личный ключ владельца.

        Чужой ключ неотличим от несуществующего: иначе перебором
        выяснялись бы чужие ключи.

        Raises:
            ApiKeyNotFoundError: Ключа нет или он принадлежит другому владельцу.
        """
        api_key = await self.keys_repository.get_by_id(key_id)
        if (
            api_key is None
            or api_key.owner_user_id != owner_user_id
        ):
            raise ApiKeyNotFoundError(key_id)
        return api_key

    async def update_personal_key(
        self,
        key_id: uuid.UUID,
        data: PersonalApiKeyUpdateSchema,
        *,
        owner_user_id: int,
        owner_name: str,
    ) -> ApiKeySchema:
        """Меняет личный ключ владельца; ``is_active=false`` отзывает его.

        Args:
            key_id: UUID ключа.
            data: Переданные поля.
            owner_user_id: Владелец ключа (и отозвавший).
            owner_name: Его имя для следа отзыва.

        Returns:
            Обновлённый ключ.

        Raises:
            ApiKeyNotFoundError: Ключа нет или он принадлежит другому владельцу.
        """
        api_key = await self.get_personal_key(key_id, owner_user_id)
        sent = data.model_dump(exclude_unset=True)
        changes: dict = {}
        for field in ("label", "scopes"):
            if sent.get(field) is not None:
                changes[field] = sent[field].strip() if field == "label" else sent[field]
        if "expires_at" in sent:
            changes["expires_at"] = sent["expires_at"]
        if sent.get("is_active") is not None and sent["is_active"] != api_key.is_active:
            changes["is_active"] = sent["is_active"]
            if sent["is_active"] is False:
                changes.update(
                    revoked_at=datetime.now(UTC),
                    revoked_by_user_id=owner_user_id,
                    revoked_by_name=owner_name,
                )
                logger.info(
                    "🚫 Отозван личный API-ключ %s, отозвал владелец=%s.",
                    api_key.key_prefix,
                    owner_name,
                )
            else:
                changes.update(revoked_at=None, revoked_by_user_id=None, revoked_by_name=None)
        if not changes:
            return ApiKeySchema.model_validate(api_key)
        updated = await self.keys_repository.update(api_key=api_key, data=changes)
        return ApiKeySchema.model_validate(updated)
