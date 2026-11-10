"""Свободные (личные) доски на настоящем PostgreSQL.

Доска без проекта видна и изменяема только автору: чужой пользователь
получает 404 на любой операции — с доской, стикером или фигурой. Связи
стикеров с задачами на такой доске запрещены, а проектные выборки свободные
доски и их стикеры не подхватывают.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.projects import Project
from src.db.models.users import User
from src.exceptions.board_stickers import BoardStickerNotFoundError, FreeBoardTaskLinksError
from src.exceptions.boards import BoardNotFoundError
from src.repositories.board_stickers import BoardStickersRepository
from src.repositories.users import UsersRepository
from src.schemas.board_shapes import (
    BoardBatchSchema,
    BoardShapeCreateSchema,
    BoardShapeToStickerSchema,
    BoardShapeUpdateSchema,
)
from src.schemas.board_stickers import (
    BoardStickerCreateSchema,
    BoardStickerPositionUpdateSchema,
    BoardStickerUpdateSchema,
)
from src.schemas.boards import BoardCreateSchema, BoardUpdateSchema
from tests.integration.repositories.test_boards_service import RECT, services


async def stranger(session: AsyncSession) -> int:
    other = await UsersRepository(session).save(
        data={
            "username": "stranger",
            "password_hash": "test-hash",
            "last_name": "Чужой",
            "first_name": "Человек",
            "is_active": True,
        }
    )
    return other.id


async def free_sticker(svc, board_id: int, user_id: int, **fields):
    return await svc.stickers.create_sticker(
        project_id=None,
        board_id=board_id,
        data=BoardStickerCreateSchema(body=fields.pop("body", "Идея"), **fields),
        author_id=user_id,
        author_username="owner",
        author_display_name="Владельцев Виктор",
    )


async def test_free_board_lifecycle_belongs_to_owner(db_session: AsyncSession, user: User) -> None:
    user_id = user.id
    svc = services(db_session)

    # Свободных досок нет — «Основная» не заводится (страховка только у проектов).
    assert await svc.boards.list_free_boards(user_id=user_id) == []
    first = await svc.boards.create_free_board(
        data=BoardCreateSchema(title="Схема"), user_id=user_id
    )
    second = await svc.boards.create_free_board(
        data=BoardCreateSchema(title="Черновик"), user_id=user_id
    )
    assert (first.project_id, first.position, second.position) == (None, 0, 1)

    renamed = await svc.boards.update_free_board(
        board_id=first.id,
        data=BoardUpdateSchema(revision=1, title="Процесс"),
        user_id=user_id,
    )
    assert (renamed.title, renamed.revision) == ("Процесс", 2)

    # Единственную оставшуюся доску удалить можно.
    await svc.boards.delete_free_board(board_id=second.id, revision=1, user_id=user_id)
    await svc.boards.delete_free_board(board_id=first.id, revision=2, user_id=user_id)
    assert await svc.boards.list_free_boards(user_id=user_id) == []


async def test_stranger_cannot_touch_free_board(db_session: AsyncSession, user: User) -> None:
    owner_id = user.id
    other_id = await stranger(db_session)
    svc = services(db_session)
    board = await svc.boards.create_free_board(
        data=BoardCreateSchema(title="Личное"), user_id=owner_id
    )
    sticker = await free_sticker(svc, board.id, owner_id)
    shape = await svc.shapes.create_shape(
        project_id=None,
        board_id=board.id,
        data=BoardShapeCreateSchema.model_validate(RECT),
        author_id=owner_id,
    )

    before = await svc.boards.get_free_content(board_id=board.id, user_id=owner_id)
    assert await svc.boards.list_free_boards(user_id=other_id) == []
    attempts = [
        svc.boards.get_free_content(board_id=board.id, user_id=other_id),
        svc.boards.update_free_board(
            board_id=board.id,
            data=BoardUpdateSchema(revision=1, title="Моё"),
            user_id=other_id,
        ),
        svc.boards.delete_free_board(board_id=board.id, revision=1, user_id=other_id),
        free_sticker(svc, board.id, other_id),
        svc.stickers.update_sticker(
            project_id=None,
            board_id=board.id,
            sticker_id=sticker.id,
            data=BoardStickerUpdateSchema(revision=sticker.revision, body="Взлом"),
            user_id=other_id,
        ),
        svc.stickers.move_sticker(
            project_id=None,
            board_id=board.id,
            sticker_id=sticker.id,
            data=BoardStickerPositionUpdateSchema(canvas_x=1, canvas_y=1),
            user_id=other_id,
        ),
        svc.stickers.delete_sticker(
            project_id=None,
            board_id=board.id,
            sticker_id=sticker.id,
            revision=sticker.revision,
            user_id=other_id,
        ),
        svc.shapes.create_shape(
            project_id=None,
            board_id=board.id,
            data=BoardShapeCreateSchema.model_validate(RECT),
            author_id=other_id,
        ),
        svc.shapes.update_shape(
            project_id=None,
            board_id=board.id,
            shape_id=shape.id,
            data=BoardShapeUpdateSchema.model_validate({"x": 5}),
            user_id=other_id,
        ),
        svc.shapes.delete_shape(
            project_id=None, board_id=board.id, shape_id=shape.id, revision=None, user_id=other_id
        ),
        svc.shapes.apply_batch(
            project_id=None,
            board_id=board.id,
            data=BoardBatchSchema.model_validate({"ops": [{"op": "delete_shape", "id": shape.id}]}),
            author_id=other_id,
        ),
        svc.shapes.shape_to_sticker(
            project_id=None,
            board_id=board.id,
            shape_id=shape.id,
            data=BoardShapeToStickerSchema(),
            author_id=other_id,
            author_username="stranger",
            author_display_name="Чужой Человек",
        ),
    ]
    for attempt in attempts:
        with pytest.raises(BoardNotFoundError):
            await attempt

    # Владелец по-прежнему видит всё нетронутым.
    content = await svc.boards.get_free_content(board_id=board.id, user_id=owner_id)
    assert content.board.title == "Личное" and content.board.revision == before.board.revision
    assert [(item.id, item.body) for item in content.stickers] == [(sticker.id, "Идея")]
    assert [(item.id, item.x) for item in content.shapes] == [(shape.id, 0)]


async def test_free_board_content_works_and_rejects_task_links(
    db_session: AsyncSession, user: User
) -> None:
    user_id = user.id
    svc = services(db_session)
    board = await svc.boards.create_free_board(
        data=BoardCreateSchema(title="Схема"), user_id=user_id
    )

    with pytest.raises(FreeBoardTaskLinksError):
        await free_sticker(svc, board.id, user_id, task_ids=[1])

    sticker = await free_sticker(svc, board.id, user_id, body="Шаг 1")
    assert (sticker.project_id, sticker.board_id) == (None, board.id)
    with pytest.raises(FreeBoardTaskLinksError):
        await svc.stickers.update_sticker(
            project_id=None,
            board_id=board.id,
            sticker_id=sticker.id,
            data=BoardStickerUpdateSchema(revision=sticker.revision, task_ids=[1]),
            user_id=user_id,
        )
    moved = await svc.stickers.move_sticker(
        project_id=None,
        board_id=board.id,
        sticker_id=sticker.id,
        data=BoardStickerPositionUpdateSchema(canvas_x=40, canvas_y=50),
        user_id=user_id,
    )
    assert (moved.canvas_x, moved.canvas_y) == (40, 50)

    shape = await svc.shapes.create_shape(
        project_id=None,
        board_id=board.id,
        data=BoardShapeCreateSchema.model_validate({**RECT, "label": "Узел"}),
        author_id=user_id,
    )
    replaced = await svc.shapes.shape_to_sticker(
        project_id=None,
        board_id=board.id,
        shape_id=shape.id,
        data=BoardShapeToStickerSchema(),
        author_id=user_id,
        author_username="owner",
        author_display_name="Владельцев Виктор",
    )
    assert (replaced.sticker.project_id, replaced.sticker.body) == (None, "Узел")

    await svc.stickers.delete_sticker(
        project_id=None,
        board_id=board.id,
        sticker_id=sticker.id,
        revision=moved.revision,
        user_id=user_id,
    )
    with pytest.raises(BoardStickerNotFoundError):
        await svc.stickers.delete_sticker(
            project_id=None,
            board_id=board.id,
            sticker_id=sticker.id,
            revision=moved.revision,
            user_id=user_id,
        )
    content = await svc.boards.get_free_content(board_id=board.id, user_id=user_id)
    assert [item.body for item in content.stickers] == ["Узел"]
    assert content.shapes == []


async def test_project_scope_never_sees_free_boards(
    db_session: AsyncSession, project: Project, user: User
) -> None:
    project_id, user_id = project.id, user.id
    svc = services(db_session)
    free = await svc.boards.create_free_board(
        data=BoardCreateSchema(title="Личное"), user_id=user_id
    )
    free_item = await free_sticker(svc, free.id, user_id, body="Личный")
    project_boards = await svc.boards.list_boards(project_id, user_id=user_id)
    project_item = await svc.stickers.create_sticker(
        project_id=project_id,
        board_id=project_boards[0].id,
        data=BoardStickerCreateSchema(body="Проектный"),
        author_id=user_id,
        author_username="owner",
        author_display_name="Владельцев Виктор",
    )

    assert free.id not in {item.id for item in project_boards}
    project_stickers = await BoardStickersRepository(db_session).list_by_project_id(project_id)
    assert [item.id for item in project_stickers] == [project_item.id]
    # Свободная доска не открывается через маршруты проекта.
    with pytest.raises(BoardNotFoundError):
        await svc.boards.get_content(project_id=project_id, board_id=free.id)
    with pytest.raises(BoardNotFoundError):
        await svc.stickers.create_sticker(
            project_id=project_id,
            board_id=free.id,
            data=BoardStickerCreateSchema(body="Подлог"),
            author_id=user_id,
            author_username="owner",
            author_display_name="Владельцев Виктор",
        )
    # Проектная доска не открывается как свободная.
    with pytest.raises(BoardNotFoundError):
        await svc.boards.get_free_content(board_id=project_boards[0].id, user_id=user_id)
    with pytest.raises(BoardStickerNotFoundError):
        await svc.stickers.delete_sticker(
            project_id=None,
            board_id=free.id,
            sticker_id=project_item.id,
            revision=project_item.revision,
            user_id=user_id,
        )
    assert free_item.project_id is None
