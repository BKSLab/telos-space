import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

from src.api.v1.responses import (
    NOT_FOUND_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
from src.dependencies.auth import SessionPrincipalDep
from src.dependencies.services import ApiTokensServiceDep
from src.exceptions.api_tokens import ApiTokensServiceError
from src.schemas.api_tokens import (
    ApiTokenCreatedSchema,
    ApiTokenCreateSchema,
    ApiTokenSchema,
)

router = APIRouter(prefix="/users/me/tokens", tags=["api-tokens"])
logger = logging.getLogger(__name__)

FORBIDDEN_RESPONSE = {
    "description": "Операция доступна только из интерфейса.",
    "content": {
        "application/json": {
            "example": {"detail": "Управление токенами доступно только из интерфейса."}
        }
    },
}


@router.get(
    path="",
    status_code=status.HTTP_200_OK,
    summary="Список токенов доступа",
    description="Возвращает токены текущего пользователя без секретов.",
    operation_id="listApiTokens",
    response_description="Токены пользователя, новые сверху.",
    responses={403: FORBIDDEN_RESPONSE, 500: SERVER_ERROR_RESPONSE},
    response_model=list[ApiTokenSchema],
)
async def list_tokens(
    principal: SessionPrincipalDep,
    service: ApiTokensServiceDep,
) -> list[ApiTokenSchema]:
    """Возвращает токены доступа текущего пользователя."""
    try:
        return await service.list_tokens(principal.user_id)
    except ApiTokensServiceError as error:
        logger.exception("❌ Не удалось получить токены пользователя id=%s.", principal.user_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="",
    status_code=status.HTTP_201_CREATED,
    summary="Выпустить токен доступа",
    description=(
        "Выпускает токен для внешнего клиента. Секрет возвращается единственный раз "
        "и в дальнейшем не восстанавливается."
    ),
    operation_id="createApiToken",
    response_description="Карточка токена вместе с секретом.",
    responses={
        403: FORBIDDEN_RESPONSE,
        409: {"description": "Достигнут предел числа действующих токенов."},
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=ApiTokenCreatedSchema,
)
async def create_token(
    principal: SessionPrincipalDep,
    data: ApiTokenCreateSchema,
    service: ApiTokensServiceDep,
) -> ApiTokenCreatedSchema:
    """Выпускает новый токен доступа."""
    try:
        return await service.issue_token(user_id=principal.user_id, data=data)
    except ApiTokensServiceError as error:
        logger.exception("❌ Не удалось выпустить токен пользователю id=%s.", principal.user_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.delete(
    path="/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отозвать токен доступа",
    description="Отзывает токен текущего пользователя без возможности восстановления.",
    operation_id="revokeApiToken",
    response_description="Токен отозван.",
    responses={
        403: FORBIDDEN_RESPONSE,
        404: NOT_FOUND_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def revoke_token(
    principal: SessionPrincipalDep,
    token_id: Annotated[int, Path(gt=0, description="Идентификатор токена.")],
    service: ApiTokensServiceDep,
) -> None:
    """Отзывает токен доступа текущего пользователя."""
    try:
        await service.revoke_token(token_id=token_id, user_id=principal.user_id)
    except ApiTokensServiceError as error:
        logger.exception("❌ Не удалось отозвать токен id=%s.", token_id)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error
