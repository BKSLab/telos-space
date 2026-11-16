"""Короткая аутентификация context API; read-only POST не требует write-токена."""

from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request

from src.core.settings import get_settings
from src.dependencies.document_catalog import DocumentCatalogServiceDep
from src.exceptions.auth import AuthServiceError
from src.services.auth import Principal
from src.utils.api_tokens import extract_bearer_secret
from src.utils.request_actor import set_request_actor

ContextSessionCookieDep = Annotated[str | None, Cookie(alias=get_settings().auth.session_cookie_name)]
ContextAuthorizationHeaderDep = Annotated[str | None, Header(alias="Authorization")]


async def get_context_principal(
    service: DocumentCatalogServiceDep,
    request: Request,
    session_cookie: ContextSessionCookieDep = None,
    authorization: ContextAuthorizationHeaderDep = None,
) -> Principal:
    """Scopes определяет сценарий, включая безопасные POST search/read и явные writes."""
    try:
        principal = await service.resolve_principal(
            session_token=session_cookie, bearer_secret=extract_bearer_secret(authorization)
        )
        if principal.api_key is not None:
            request.state.api_key = principal.api_key
            request.state.api_key_actor_login = principal.username
        set_request_actor(principal.request_actor())
        return principal
    except AuthServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


ContextPrincipalDep = Annotated[Principal, Depends(get_context_principal)]


async def require_context_session(principal: ContextPrincipalDep) -> Principal:
    """Разрешение egress выдаёт человек; Bearer использует approved pending."""
    if principal.via_api_token:
        raise HTTPException(status_code=403, detail="Разрешение требует сессии пользователя.")
    return principal


ContextSessionPrincipalDep = Annotated[Principal, Depends(require_context_session)]
