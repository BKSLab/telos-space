"""Минимальная локальная identity и активность фотографии после файлового чтения."""

from unittest.mock import AsyncMock

import pytest

from src.db.models.users import User
from src.exceptions.users import AvatarNotFoundError
from src.repositories.users import UsersRepository
from src.services.users import UsersService
from src.storage.avatars import AvatarStorage


def person(*, user_id=2, active=True, avatar="2/photo.png"):
    return User(
        id=user_id,
        username=f"person{user_id}",
        first_name="Иван",
        last_name="Тестов",
        middle_name=None,
        password_hash="!",
        is_active=active,
        avatar_key=avatar,
        email="private@example.test",
        phone="private",
        telegram="private",
    )


async def test_user_search_is_service_method_and_returns_only_safe_identity():
    repository = AsyncMock(spec=UsersRepository)
    repository.search.return_value = [person()]
    service = UsersService(repository, AsyncMock(spec=AvatarStorage))
    result = await service.search_users(query="Тестов", limit=3)
    assert [user.id for user in result] == [2]
    assert set(result[0].model_dump()) == {
        "id",
        "username",
        "first_name",
        "last_name",
        "middle_name",
        "has_avatar",
    }
    assert result[0].has_avatar


async def test_identity_avatar_rejects_missing_inactive_and_avatarless_targets_before_io():
    for target in (None, person(active=False), person(avatar=None)):
        repository = AsyncMock(spec=UsersRepository)
        repository.get_by_id.return_value = target
        storage = AsyncMock(spec=AvatarStorage)
        with pytest.raises(AvatarNotFoundError):
            await UsersService(repository, storage).get_identity_avatar(2)
        storage.read.assert_not_awaited()


@pytest.mark.parametrize("change", [None, "disabled", "photo_replaced"])
async def test_identity_avatar_uses_target_and_discards_photo_if_identity_changes_during_io(change):
    target = person()
    repository = AsyncMock(spec=UsersRepository)
    repository.get_by_id.return_value = target
    storage = AsyncMock(spec=AvatarStorage)

    async def read(*, storage_key):
        assert storage_key == "2/photo.png"
        if change == "disabled":
            target.is_active = False
        elif change == "photo_replaced":
            target.avatar_key = "2/new.png"
        return b"photo-of-target-2"

    storage.read.side_effect = read
    if change is None:
        assert await UsersService(repository, storage).get_identity_avatar(2) == (
            b"photo-of-target-2",
            "image/png",
        )
    else:
        with pytest.raises(AvatarNotFoundError):
            await UsersService(repository, storage).get_identity_avatar(2)
