"""Личные API-ключи пользователя: выпуск и управление своими ключами.

API-ключи принадлежат выпустившему их пользователю
и действуют в пределах его доступа и явно выбранных прав. Управление — только из интерфейса (cookie-сессия): по токену или ключу
управлять ключами нельзя, иначе утёкший секрет выпустил бы себе замену.
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

from src.api.v1.responses import NOT_FOUND_RESPONSE, SERVER_ERROR_RESPONSE, VALIDATION_RESPONSE
from src.dependencies.auth import SessionPrincipalDep
from src.dependencies.services import ApiKeysServiceDep
from src.exceptions.api_keys import ApiKeysServiceError
from src.schemas.api_keys import (
    ApiKeyCreatedSchema,
    ApiKeySchema,
    ApiScopeSchema,
    PersonalApiKeyCreateSchema,
    PersonalApiKeyUpdateSchema,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/users/me/api-keys",
    tags=["api-keys"],
)

FORBIDDEN_RESPONSE = {
    "description": "Операция доступна только из интерфейса.",
    "content": {
        "application/json": {
            "example": {"detail": "Управление токенами доступно только из интерфейса."}
        }
    },
}
_COMMON = {403: FORBIDDEN_RESPONSE, 500: SERVER_ERROR_RESPONSE}

KeyIdPath = Annotated[uuid.UUID, Path(description="UUID ключа.")]


@router.get(
    path="",
    status_code=status.HTTP_200_OK,
    summary="Мои API-ключи",
    description="Личные ключи текущего пользователя без секретов, новые сверху.",
    operation_id="listPersonalApiKeys",
    response_description="Личные ключи пользователя.",
    responses=_COMMON,
    response_model=list[ApiKeySchema],
)
async def list_personal_keys(
    principal: SessionPrincipalDep,
    service: ApiKeysServiceDep,
) -> list[ApiKeySchema]:
    """Возвращает личные ключи текущего пользователя."""
    try:
        return await service.list_personal_keys(principal.user_id)
    except ApiKeysServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/scopes",
    status_code=status.HTTP_200_OK,
    summary="Права, доступные личному ключу",
    description=(
        "Подмножество реестра прав: без подмены владельца — действовать можно только от собственного "
        "имени и в пределах своего доступа."
    ),
    operation_id="listPersonalApiKeyScopes",
    response_description="Доступные личному ключу права.",
    responses={403: FORBIDDEN_RESPONSE},
    response_model=list[ApiScopeSchema],
)
async def list_personal_key_scopes(
    principal: SessionPrincipalDep,
    service: ApiKeysServiceDep,
) -> list[ApiScopeSchema]:
    """Возвращает права, которые пользователь может выдать личному ключу."""
    return service.list_personal_scopes()


@router.post(
    path="",
    status_code=status.HTTP_201_CREATED,
    summary="Выпустить личный API-ключ",
    description=(
        "Выпускает ключ для своих скриптов и клиентов: он работает с вашими правами "
        "в проектах, суженными выданными правами. Секрет возвращается **один раз** "
        "в поле `raw_key`: хранится только его хеш."
    ),
    operation_id="createPersonalApiKey",
    response_description="Ключ вместе с секретом.",
    responses={**_COMMON, 422: VALIDATION_RESPONSE},
    response_model=ApiKeyCreatedSchema,
)
async def create_personal_key(
    data: PersonalApiKeyCreateSchema,
    principal: SessionPrincipalDep,
    service: ApiKeysServiceDep,
) -> ApiKeyCreatedSchema:
    """Выпускает личный ключ текущему пользователю."""
    try:
        return await service.create_personal_key(data, owner_user_id=principal.user_id)
    except ApiKeysServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.patch(
    path="/{key_id}",
    status_code=status.HTTP_200_OK,
    summary="Изменить или отозвать личный ключ",
    description=(
        "Меняет имя, права и срок своего ключа. `is_active=false` отзывает ключ: "
        "он перестаёт приниматься сразу. Изменить можно только свой ключ; "
        "чужой ключ отвечает 404."
    ),
    operation_id="updatePersonalApiKey",
    response_description="Обновлённый ключ.",
    responses={**_COMMON, 404: NOT_FOUND_RESPONSE, 422: VALIDATION_RESPONSE},
    response_model=ApiKeySchema,
)
async def update_personal_key(
    key_id: KeyIdPath,
    data: PersonalApiKeyUpdateSchema,
    principal: SessionPrincipalDep,
    service: ApiKeysServiceDep,
) -> ApiKeySchema:
    """Изменяет или отзывает личный ключ текущего пользователя."""
    try:
        return await service.update_personal_key(
            key_id,
            data,
            owner_user_id=principal.user_id,
            owner_name=principal.short_name,
        )
    except ApiKeysServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
