import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path,
    Query,
    Response,
    UploadFile,
    status,
)

from src.api.v1.responses import (
    FORBIDDEN_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVER_ERROR_RESPONSE,
    VALIDATION_RESPONSE,
)
from src.dependencies.auth import PrincipalDep, require_write_scope
from src.dependencies.services import UsersServiceDep
from src.exceptions.auth import AuthServiceError
from src.exceptions.users import UsersServiceError
from src.schemas.users import PasswordChangeSchema, UserSchema, UserSummarySchema, UserUpdateSchema

router = APIRouter(prefix="/users", tags=["users"])
logger = logging.getLogger(__name__)

UNAUTHORIZED_RESPONSE = {
    "description": "Требуется вход в систему.",
    "content": {"application/json": {"example": {"detail": "Требуется вход в систему."}}},
}

IDENTITY_AVATAR_HEADERS = {
    "Cache-Control": "private, no-cache, must-revalidate",
    "Vary": "Cookie, Authorization",
    "X-Content-Type-Options": "nosniff",
}


@router.patch(
    path="/me",
    dependencies=[Depends(require_write_scope)],
    status_code=status.HTTP_200_OK,
    summary="Изменить профиль",
    description="Обновляет ФИО и контакты текущего пользователя.",
    operation_id="updateCurrentUser",
    response_description="Обновлённый профиль.",
    responses={
        401: UNAUTHORIZED_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_model=UserSchema,
)
async def update_me(
    data: UserUpdateSchema,
    principal: PrincipalDep,
    service: UsersServiceDep,
) -> UserSchema:
    """Обновляет профиль текущего пользователя.

    Args:
        data: Изменяемые поля профиля.
        principal: Принципал текущего запроса.
        service: Сервис профиля.

    Returns:
        Обновлённая карточка пользователя.

    Raises:
        HTTPException: Если обновить профиль не удалось.
    """
    logger.info("🚀 Запрос PATCH /users/me. Пользователь: %s.", principal.username)
    try:
        result = await service.update_profile(
            user_id=principal.user_id,
            data=data.model_dump(exclude_unset=True),
        )
        logger.info("✅ Профиль пользователя %s обновлён.", principal.username)
        return result
    except UsersServiceError as error:
        logger.exception("❌ Ошибка PATCH /users/me. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/me/password",
    dependencies=[Depends(require_write_scope)],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Сменить пароль",
    description="Меняет пароль после проверки текущего. Новый пароль вводится дважды.",
    operation_id="changeCurrentUserPassword",
    response_description="Пароль изменён.",
    responses={
        400: VALIDATION_RESPONSE,
        401: UNAUTHORIZED_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def change_password(
    data: PasswordChangeSchema,
    principal: PrincipalDep,
    service: UsersServiceDep,
) -> None:
    """Меняет пароль текущего пользователя.

    Args:
        data: Текущий и новый пароль.
        principal: Принципал текущего запроса.
        service: Сервис профиля.

    Returns:
        ``None`` после успешной смены.

    Raises:
        HTTPException: Если текущий пароль неверен или сменить не удалось.
    """
    logger.info("🚀 Запрос POST /users/me/password. Пользователь: %s.", principal.username)
    try:
        await service.change_password(
            user_id=principal.user_id,
            current_password=data.current_password,
            new_password=data.password,
        )
        logger.info("✅ Пароль пользователя %s изменён.", principal.username)
    except (UsersServiceError, AuthServiceError) as error:
        logger.warning("⚠️ Ошибка POST /users/me/password. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.post(
    path="/me/avatar",
    dependencies=[Depends(require_write_scope)],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Загрузить фотографию",
    description="Сохраняет фотографию профиля: JPEG, PNG или WebP до 5 МБ.",
    operation_id="uploadCurrentUserAvatar",
    response_description="Фотография сохранена.",
    responses={
        401: UNAUTHORIZED_RESPONSE,
        413: VALIDATION_RESPONSE,
        415: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def upload_avatar(
    principal: PrincipalDep,
    service: UsersServiceDep,
    file: Annotated[UploadFile, File(description="Файл изображения до 5 МБ.")],
) -> None:
    """Загружает фотографию профиля.

    Args:
        principal: Принципал текущего запроса.
        service: Сервис профиля.
        file: Загружаемый файл изображения.

    Returns:
        ``None`` после успешной загрузки.

    Raises:
        HTTPException: Если тип или размер не подходят, либо сохранить не удалось.
    """
    logger.info("🚀 Запрос POST /users/me/avatar. Файл: %r.", file.filename)
    try:
        await service.set_avatar(
            user_id=principal.user_id,
            content_type=file.content_type or "",
            content=await file.read(),
        )
        logger.info("✅ Фотография пользователя %s загружена.", principal.username)
    except UsersServiceError as error:
        logger.warning("⚠️ Ошибка POST /users/me/avatar. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    path="/me/avatar",
    status_code=status.HTTP_200_OK,
    summary="Получить фотографию",
    description="Отдаёт фотографию профиля текущего пользователя.",
    operation_id="getCurrentUserAvatar",
    response_description="Содержимое файла фотографии.",
    responses={
        401: UNAUTHORIZED_RESPONSE,
        404: NOT_FOUND_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
    response_class=Response,
)
async def get_avatar(principal: PrincipalDep, service: UsersServiceDep) -> Response:
    """Отдаёт фотографию профиля.

    Args:
        principal: Принципал текущего запроса.
        service: Сервис профиля.

    Returns:
        Ответ с бинарным содержимым изображения.

    Raises:
        HTTPException: Если фотографии нет или прочитать её не удалось.
    """
    logger.info("🚀 Запрос GET /users/me/avatar. Пользователь: %s.", principal.username)
    try:
        content, content_type = await service.get_avatar(user_id=principal.user_id)
        # Фотография меняется редко, но приватна: кэшируем только в браузере.
        return Response(
            content=content,
            media_type=content_type,
            headers={"Cache-Control": "private, max-age=60"},
        )
    except UsersServiceError as error:
        logger.info("ℹ️ Ошибка GET /users/me/avatar. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.delete(
    path="/me/avatar",
    dependencies=[Depends(require_write_scope)],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить фотографию",
    description="Удаляет фотографию профиля текущего пользователя.",
    operation_id="deleteCurrentUserAvatar",
    response_description="Фотография удалена.",
    responses={
        401: UNAUTHORIZED_RESPONSE,
        404: NOT_FOUND_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def delete_avatar(principal: PrincipalDep, service: UsersServiceDep) -> None:
    """Удаляет фотографию профиля.

    Args:
        principal: Принципал текущего запроса.
        service: Сервис профиля.

    Returns:
        ``None`` после успешного удаления.

    Raises:
        HTTPException: Если фотографии нет или удалить её не удалось.
    """
    logger.info("🚀 Запрос DELETE /users/me/avatar. Пользователь: %s.", principal.username)
    try:
        await service.delete_avatar(user_id=principal.user_id)
        logger.info("✅ Фотография пользователя %s удалена.", principal.username)
    except UsersServiceError as error:
        logger.warning("⚠️ Ошибка DELETE /users/me/avatar. Детали: %s", error)
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@router.get(
    "",
    response_model=list[UserSummarySchema],
    summary="Найти локальных пользователей",
    description="Ищет активных локальных пользователей по имени или логину. Доступно вошедшему пользователю; API-ключ требует projects:read. Контакты и поля авторизации не выдаются.",
    response_description="Безопасные identities для выбора участников.",
    operation_id="searchUsers",
    responses={
        401: UNAUTHORIZED_RESPONSE,
        403: FORBIDDEN_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def search_users(
    principal: PrincipalDep,
    service: UsersServiceDep,
    query: Annotated[
        str | None,
        Query(max_length=200, description="Подстрока имени или логина.", examples=["Иван"]),
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=100, description="Максимальное число identities.", examples=[20])
    ] = 20,
) -> list[UserSummarySchema]:
    """Возвращает только безопасную публичную identity активных пользователей."""
    try:
        return await service.search_users(query=query, limit=limit)
    except UsersServiceError as error:
        raise HTTPException(error.status_code, detail=error.detail) from error


@router.get(
    "/{user_id}/avatar",
    response_class=Response,
    summary="Получить аватар пользователя",
    description="Отдаёт только фотографию активной локальной identity вошедшему пользователю. API-ключ требует projects:read, как каталог users. Неактивная, отсутствующая identity и отсутствие фотографии дают 404; контакты и профиль не возвращаются.",
    operation_id="getUserAvatar",
    response_description="Бинарная фотография локального пользователя.",
    responses={
        200: {
            "content": {
                media: {"schema": {"type": "string", "format": "binary"}}
                for media in ("image/jpeg", "image/png", "image/webp")
            }
        },
        401: UNAUTHORIZED_RESPONSE,
        403: FORBIDDEN_RESPONSE,
        404: NOT_FOUND_RESPONSE,
        422: VALIDATION_RESPONSE,
        500: SERVER_ERROR_RESPONSE,
    },
)
async def get_user_avatar(
    user_id: Annotated[
        int,
        Path(gt=0, description="ID активного локального пользователя из identities.", examples=[2]),
    ],
    principal: PrincipalDep,
    service: UsersServiceDep,
) -> Response:
    """Фото чужой identity выбирается по path ID, а не по текущему Principal."""
    try:
        content, content_type = await service.get_identity_avatar(user_id=user_id)
        return Response(content=content, media_type=content_type, headers=IDENTITY_AVATAR_HEADERS)
    except UsersServiceError as error:
        raise HTTPException(
            error.status_code, detail=error.detail, headers=IDENTITY_AVATAR_HEADERS
        ) from error
