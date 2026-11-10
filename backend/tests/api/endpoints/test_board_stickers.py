from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from main import app
from src.dependencies.services import get_board_stickers_service
from src.exceptions.board_stickers import (
    BoardStickerNotFoundError,
    BoardStickerRevisionConflictError,
    BoardStickerTaskMismatchError,
)
from src.exceptions.boards import BoardNotFoundError
from src.schemas.board_stickers import BoardStickerSchema
from src.services.board_stickers import BoardStickersService


def sticker_schema(*, revision: int = 1) -> BoardStickerSchema:
    now = datetime.now(UTC)
    return BoardStickerSchema(
        id=4,
        project_id=1,
        board_id=3,
        body="Согласовать API",
        color="#fff19a",
        canvas_x=40.0,
        canvas_y=40.0,
        created_by_user_id=1,
        created_by_username_snapshot="tester",
        created_by_display_name_snapshot="Тестов Тест",
        task_ids=[11],
        revision=revision,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_list_stickers_returns_contract(api_client: AsyncClient) -> None:
    service = AsyncMock(spec=BoardStickersService)
    service.list_stickers.return_value = [sticker_schema()]
    app.dependency_overrides[get_board_stickers_service] = lambda: service

    response = await api_client.get("/api/v1/projects/1/boards/3/stickers")

    assert response.status_code == 200
    assert response.json()[0]["task_ids"] == [11]
    service.list_stickers.assert_awaited_once_with(1, 3)


@pytest.mark.asyncio
async def test_create_sticker_uses_authenticated_user(api_client: AsyncClient) -> None:
    service = AsyncMock(spec=BoardStickersService)
    service.create_sticker.return_value = sticker_schema()
    app.dependency_overrides[get_board_stickers_service] = lambda: service

    response = await api_client.post(
        "/api/v1/projects/1/boards/3/stickers",
        json={
            "body": "Согласовать API",
            "color": "#fff19a",
            "task_ids": [11],
            "canvas_x": 298.5,
            "canvas_y": 42.0,
        },
    )

    assert response.status_code == 201
    call = service.create_sticker.await_args.kwargs
    assert call["project_id"] == 1
    assert call["board_id"] == 3
    assert call["author_username"] == "tester"
    assert call["data"].task_ids == [11]
    assert call["data"].canvas_x == 298.5
    assert call["data"].canvas_y == 42.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (BoardStickerNotFoundError(4), 404),
        (BoardNotFoundError(3), 404),
        (BoardStickerRevisionConflictError(4, 2), 409),
        (BoardStickerTaskMismatchError({99}), 422),
    ],
)
async def test_patch_maps_domain_errors(
    api_client: AsyncClient,
    error: Exception,
    status_code: int,
) -> None:
    service = AsyncMock(spec=BoardStickersService)
    service.update_sticker.side_effect = error
    app.dependency_overrides[get_board_stickers_service] = lambda: service

    response = await api_client.patch(
        "/api/v1/projects/1/boards/3/stickers/4",
        json={"revision": 2, "body": "Новый текст"},
    )

    assert response.status_code == status_code


@pytest.mark.asyncio
async def test_empty_sticker_is_rejected_before_service(api_client: AsyncClient) -> None:
    service = AsyncMock(spec=BoardStickersService)
    app.dependency_overrides[get_board_stickers_service] = lambda: service

    response = await api_client.post(
        "/api/v1/projects/1/boards/3/stickers",
        json={"body": "   "},
    )

    assert response.status_code == 422
    service.create_sticker.assert_not_awaited()


@pytest.mark.asyncio
async def test_sticker_write_endpoints_pass_revision_and_coordinates(
    api_client: AsyncClient,
) -> None:
    """PATCH и DELETE передают ревизию, перенос передаёт координаты и отклоняет выход за диапазон."""

    service = AsyncMock(spec=BoardStickersService)
    service.update_sticker.return_value = sticker_schema(revision=3)
    app.dependency_overrides[get_board_stickers_service] = lambda: service

    response = await api_client.patch(
        "/api/v1/projects/1/boards/3/stickers/4",
        json={"revision": 2, "body": "Новый текст", "task_ids": []},
    )

    assert response.status_code == 200
    call = service.update_sticker.await_args.kwargs
    assert call["sticker_id"] == 4
    assert call["data"].revision == 2

    service = AsyncMock(spec=BoardStickersService)
    app.dependency_overrides[get_board_stickers_service] = lambda: service

    response = await api_client.delete("/api/v1/projects/1/boards/3/stickers/4?revision=3")

    assert response.status_code == 204
    service.delete_sticker.assert_awaited_once_with(
        project_id=1,
        board_id=3,
        sticker_id=4,
        revision=3,
    )

    service = AsyncMock(spec=BoardStickersService)
    service.move_sticker.return_value = sticker_schema()
    app.dependency_overrides[get_board_stickers_service] = lambda: service

    response = await api_client.patch(
        "/api/v1/projects/1/boards/3/stickers/4/position",
        json={"canvas_x": 315.25, "canvas_y": -80.0, "z_index": 7},
    )

    assert response.status_code == 200
    call = service.move_sticker.await_args.kwargs
    assert (call["project_id"], call["board_id"], call["sticker_id"]) == (1, 3, 4)
    assert call["data"].canvas_x == 315.25
    assert call["data"].canvas_y == -80.0
    assert call["data"].z_index == 7

    service = AsyncMock(spec=BoardStickersService)
    app.dependency_overrides[get_board_stickers_service] = lambda: service

    response = await api_client.patch(
        "/api/v1/projects/1/boards/3/stickers/4/position",
        json={"canvas_x": 1_000_001, "canvas_y": 0},
    )

    assert response.status_code == 422
    service.move_sticker.assert_not_awaited()
