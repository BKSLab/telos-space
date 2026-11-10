"""Доски, стикеры на досках и фигуры на настоящем PostgreSQL.

Проверяются правила, которые держатся на связке сервисов и базы: страховка
«у проекта всегда есть доска», запрет удалить последнюю, стикеры и фигуры
строго своей доски, привязки стрелок, отцепление при удалении, пакет «всё
или ничего», замена фигуры стикером с перецепкой стрелок.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.board_shapes import BoardShape
from src.db.models.boards import Board
from src.db.models.projects import Project
from src.db.models.users import User
from src.exceptions.board_shapes import (
    BoardBatchConflictError,
    BoardShapeInvalidError,
    BoardShapeNotFoundError,
    BoardShapeRevisionConflictError,
)
from src.exceptions.board_stickers import BoardStickerNotFoundError
from src.exceptions.boards import (
    BoardNotFoundError,
    BoardRevisionConflictError,
    ProjectLastBoardError,
)
from src.repositories.board_editor import BoardEditorRepository
from src.repositories.board_shapes import BoardShapesRepository
from src.repositories.board_stickers import BoardStickersRepository
from src.repositories.boards import BoardsRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
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
from src.services.board_shapes import BoardShapesService
from src.services.board_stickers import BoardStickersService
from src.services.boards import BoardsService


def services(session: AsyncSession) -> SimpleNamespace:
    """Сервисы доски поверх настоящих репозиториев одной сессии."""
    boards = BoardsRepository(session)
    stickers = BoardStickersRepository(session)
    shapes = BoardShapesRepository(session)
    unit_of_work = UnitOfWork(session)
    return SimpleNamespace(
        boards=BoardsService(
            boards_repository=boards,
            stickers_repository=stickers,
            shapes_repository=shapes,
            unit_of_work=unit_of_work,
        ),
        stickers=BoardStickersService(
            editor_repository=BoardEditorRepository(session),
            stickers_repository=stickers,
            tasks_repository=TasksRepository(session),
            boards_repository=boards,
            shapes_repository=shapes,
            unit_of_work=unit_of_work,
        ),
        shapes=BoardShapesService(
            editor_repository=BoardEditorRepository(session),
            boards_repository=boards,
            shapes_repository=shapes,
            stickers_repository=stickers,
            unit_of_work=unit_of_work,
        ),
    )


async def other_project(session: AsyncSession, owner_id: int) -> int:
    project = await ProjectsRepository(session).save(
        data={
            "owner_id": owner_id,
            "key": "OTHER",
            "name": "Чужой проект",
            "status": "PLANNING",
            "color": "#58a6ff",
            "order_index": 1,
        }
    )
    return project.id


async def add_sticker(svc, project_id: int, board_id: int | None, user_id: int, **fields):
    return await svc.stickers.create_sticker(
        project_id=project_id,
        board_id=board_id,
        data=BoardStickerCreateSchema(body=fields.pop("body", "Идея"), **fields),
        author_id=user_id,
        author_username="owner",
        author_display_name="Владельцев Виктор",
    )


async def add_shape(svc, project_id: int, board_id: int, user_id: int, **data):
    return await svc.shapes.create_shape(
        project_id=project_id,
        board_id=board_id,
        data=BoardShapeCreateSchema.model_validate(data),
        author_id=user_id,
    )


async def shape_count(session: AsyncSession, board_id: int) -> int:
    return await session.scalar(
        select(func.count(BoardShape.id)).where(BoardShape.board_id == board_id)
    )


RECT = {"kind": "rect", "x": 0, "y": 0, "w": 100, "h": 60}


async def test_board_lifecycle_default_board_and_last_board_guard(
    db_session: AsyncSession, project: Project, user: User
) -> None:
    project_id, user_id = project.id, user.id
    svc = services(db_session)

    # Проекту без досок GET заводит «Основную» (страховка), повторный — не дублирует.
    first = await svc.boards.list_boards(project_id, user_id=user_id)
    again = await svc.boards.list_boards(project_id, user_id=user_id)
    assert [(item.title, item.position) for item in first] == [("Основная", 0)]
    assert [item.id for item in again] == [first[0].id]

    second = await svc.boards.create_board(
        project_id=project_id, data=BoardCreateSchema(title="  Схема  "), user_id=user_id
    )
    assert (second.title, second.position, second.revision) == ("Схема", 1, 1)

    renamed = await svc.boards.update_board(
        project_id=project_id,
        board_id=second.id,
        data=BoardUpdateSchema(revision=1, title="Процесс", position=5),
    )
    assert (renamed.title, renamed.position, renamed.revision) == ("Процесс", 5, 2)
    with pytest.raises(BoardRevisionConflictError):
        await svc.boards.update_board(
            project_id=project_id,
            board_id=second.id,
            data=BoardUpdateSchema(revision=1, title="Поздно"),
        )

    # Чужой проект не видит доску: 404, а не конфликт.
    stranger = await other_project(db_session, user_id)
    with pytest.raises(BoardNotFoundError):
        await svc.boards.get_content(project_id=stranger, board_id=second.id)
    with pytest.raises(BoardNotFoundError):
        await svc.boards.update_board(
            project_id=stranger,
            board_id=second.id,
            data=BoardUpdateSchema(revision=2, title="Чужое"),
        )

    with pytest.raises(BoardRevisionConflictError):
        await svc.boards.delete_board(project_id=project_id, board_id=second.id, revision=1)
    await svc.boards.delete_board(project_id=project_id, board_id=second.id, revision=2)
    assert [item.id for item in await svc.boards.list_boards(project_id, user_id=user_id)] == [
        first[0].id
    ]
    archived = await db_session.get(Board, second.id)
    assert archived is not None and archived.archived_at is not None
    with pytest.raises(BoardNotFoundError):
        await svc.boards.get_content(project_id=project_id, board_id=second.id)

    with pytest.raises(ProjectLastBoardError):
        await svc.boards.delete_board(project_id=project_id, board_id=first[0].id, revision=1)


async def test_stickers_live_on_their_board_only(
    db_session: AsyncSession, project: Project, user: User, board: Board
) -> None:
    project_id, user_id, main_id = project.id, user.id, board.id
    svc = services(db_session)
    scheme = await svc.boards.create_board(
        project_id=project_id, data=BoardCreateSchema(title="Схема"), user_id=user_id
    )

    on_main = await add_sticker(svc, project_id, main_id, user_id, z_index=4, width=300)
    on_scheme = await add_sticker(svc, project_id, scheme.id, user_id, body="Схемная")
    # Без доски (так зовёт агент) — первая доска проекта.
    by_default = await add_sticker(svc, project_id, None, user_id, body="Агент")

    assert (on_main.board_id, on_main.z_index, on_main.width) == (main_id, 4, 300.0)
    assert by_default.board_id == main_id
    listed = await svc.stickers.list_stickers(project_id, scheme.id)
    assert [item.id for item in listed] == [on_scheme.id]

    # Стикер чужой доски — 404 на любой записи.
    with pytest.raises(BoardStickerNotFoundError):
        await svc.stickers.update_sticker(
            project_id=project_id,
            board_id=scheme.id,
            sticker_id=on_main.id,
            data=BoardStickerUpdateSchema(revision=1, body="Чужое"),
        )
    with pytest.raises(BoardStickerNotFoundError):
        await svc.stickers.move_sticker(
            project_id=project_id,
            board_id=scheme.id,
            sticker_id=on_main.id,
            data=BoardStickerPositionUpdateSchema(canvas_x=1, canvas_y=1),
        )
    with pytest.raises(BoardStickerNotFoundError):
        await svc.stickers.delete_sticker(
            project_id=project_id, board_id=scheme.id, sticker_id=on_main.id, revision=1
        )

    moved = await svc.stickers.move_sticker(
        project_id=project_id,
        board_id=main_id,
        sticker_id=on_main.id,
        data=BoardStickerPositionUpdateSchema(canvas_x=10, canvas_y=20, z_index=9),
    )
    assert (moved.canvas_x, moved.z_index, moved.revision) == (10, 9, 1)

    content = await svc.boards.get_content(project_id=project_id, board_id=main_id)
    assert [item.id for item in content.stickers] == [by_default.id, on_main.id]

    # Стикеры удалённой доски пропадают из выдачи проекта (аналитика, агент).
    scheme_content = await svc.boards.get_content(project_id=project_id, board_id=scheme.id)
    await svc.boards.delete_board(
        project_id=project_id, board_id=scheme.id, revision=scheme_content.board.revision
    )
    remaining = await BoardStickersRepository(db_session).list_by_project_id(project_id)
    assert on_scheme.id not in {item.id for item in remaining}


async def test_shape_rules_style_geometry_and_refs(
    db_session: AsyncSession, project: Project, user: User, board: Board
) -> None:
    project_id, user_id, board_id = project.id, user.id, board.id
    svc = services(db_session)
    rect = await add_shape(svc, project_id, board_id, user_id, **RECT, label="Старт")
    assert rect.style.model_dump() == {
        "stroke": "neutral",
        "fill": "none",
        "stroke_width": 2,
        "dash": "solid",
        "route": "straight",
        "start_head": "none",
        "end_head": "none",
        "font_size": "m",
        "text_align": "center",
    }
    sticker = await add_sticker(svc, project_id, board_id, user_id)
    arrow = await add_shape(
        svc,
        project_id,
        board_id,
        user_id,
        kind="arrow",
        points=[[100, 30], [180, -20], [260, 40]],
        start_ref={"type": "shape", "id": rect.id, "anchor": "right"},
        end_ref={"type": "sticker", "id": sticker.id},
        style={"dash": "dashed"},
    )
    # Рамку стрелки считает сервер; у стрелки наконечник на конце по умолчанию.
    assert (arrow.x, arrow.y, arrow.w, arrow.h) == (100, -20, 160, 60)
    assert (arrow.style.end_head, arrow.style.dash) == ("arrow", "dashed")
    assert arrow.end_ref.model_dump() == {"type": "sticker", "id": sticker.id, "anchor": "center"}

    # Привязки — только к фигурам и стикерам этой доски, не к стрелке и не к себе.
    other = await svc.boards.create_board(
        project_id=project_id, data=BoardCreateSchema(title="Другая"), user_id=user_id
    )
    foreign = await add_sticker(svc, project_id, other.id, user_id)
    for ref in (
        {"type": "sticker", "id": foreign.id},
        {"type": "shape", "id": arrow.id},
        {"type": "shape", "id": 10**9},
    ):
        with pytest.raises(BoardShapeInvalidError):
            await add_shape(
                svc,
                project_id,
                board_id,
                user_id,
                kind="line",
                points=[[0, 0], [5, 5]],
                end_ref=ref,
            )
    with pytest.raises(BoardShapeInvalidError):
        await svc.shapes.update_shape(
            project_id=project_id,
            board_id=board_id,
            shape_id=arrow.id,
            data=BoardShapeUpdateSchema(start_ref={"type": "shape", "id": arrow.id}),
        )
    # client_id бывает только в пакете.
    with pytest.raises(BoardShapeInvalidError):
        await add_shape(
            svc,
            project_id,
            board_id,
            user_id,
            kind="arrow",
            points=[[0, 0], [5, 5]],
            end_ref={"type": "shape", "client_id": "tmp-1"},
        )

    # Форма отсекается ещё в схеме.
    for bad in (
        {**RECT, "points": [[0, 0], [1, 1]]},
        {"kind": "rect", "x": 0, "y": 0, "w": 0.5, "h": 10},
        {"kind": "arrow", "points": [[0, 0]]},
        {"kind": "arrow", "points": [[0, 0], [2_000_000, 0]]},
        {**RECT, "style": {"stroke": "#ff0000"}},
        {**RECT, "style": {"stroke_width": 3}},
        {**RECT, "label": "x" * 2001},
    ):
        with pytest.raises(ValidationError):
            BoardShapeCreateSchema.model_validate(bad)

    # Правка: стиль сливается с текущим; сдвиг стрелки переносит все точки.
    restyled = await svc.shapes.update_shape(
        project_id=project_id,
        board_id=board_id,
        shape_id=rect.id,
        data=BoardShapeUpdateSchema(style={"fill": "yellow"}, revision=1),
    )
    assert (restyled.style.fill, restyled.style.stroke, restyled.revision) == (
        "yellow",
        "neutral",
        2,
    )
    with pytest.raises(BoardShapeRevisionConflictError):
        await svc.shapes.update_shape(
            project_id=project_id,
            board_id=board_id,
            shape_id=rect.id,
            data=BoardShapeUpdateSchema(label="Поздно", revision=1),
        )
    shifted = await svc.shapes.update_shape(
        project_id=project_id,
        board_id=board_id,
        shape_id=arrow.id,
        data=BoardShapeUpdateSchema(x=110, y=0),
    )
    assert shifted.points == [[110, 50], [190, 0], [270, 60]]
    assert (shifted.x, shifted.y, shifted.revision) == (110, 0, 2)
    with pytest.raises(BoardShapeInvalidError):
        await svc.shapes.update_shape(
            project_id=project_id,
            board_id=board_id,
            shape_id=arrow.id,
            data=BoardShapeUpdateSchema(w=10),
        )
    with pytest.raises(BoardShapeNotFoundError):
        await svc.shapes.update_shape(
            project_id=project_id,
            board_id=other.id,
            shape_id=rect.id,
            data=BoardShapeUpdateSchema(label="Чужая доска"),
        )


async def test_deleting_target_detaches_arrows_but_keeps_them(
    db_session: AsyncSession, project: Project, user: User, board: Board
) -> None:
    project_id, user_id, board_id = project.id, user.id, board.id
    svc = services(db_session)
    rect = await add_shape(svc, project_id, board_id, user_id, **RECT)
    sticker = await add_sticker(svc, project_id, board_id, user_id)
    arrow = await add_shape(
        svc,
        project_id,
        board_id,
        user_id,
        kind="arrow",
        points=[[0, 0], [300, 300]],
        start_ref={"type": "shape", "id": rect.id},
        end_ref={"type": "sticker", "id": sticker.id},
    )

    await svc.shapes.delete_shape(
        project_id=project_id, board_id=board_id, shape_id=rect.id, revision=None
    )
    await svc.stickers.delete_sticker(
        project_id=project_id, board_id=board_id, sticker_id=sticker.id, revision=1
    )

    content = await svc.boards.get_content(project_id=project_id, board_id=board_id)
    [left] = content.shapes
    assert left.id == arrow.id
    assert (left.start_ref, left.end_ref) == (None, None)
    assert left.points == [[0, 0], [300, 300]]
    assert left.revision == 3


async def test_batch_is_all_or_nothing_with_client_ids_and_conflicts(
    db_session: AsyncSession, project: Project, user: User, board: Board
) -> None:
    project_id, user_id, board_id = project.id, user.id, board.id
    svc = services(db_session)
    sticker = await add_sticker(svc, project_id, board_id, user_id)
    old = await add_shape(svc, project_id, board_id, user_id, **RECT)
    victim = await add_shape(svc, project_id, board_id, user_id, **RECT)

    result = await svc.shapes.apply_batch(
        project_id=project_id,
        board_id=board_id,
        author_id=user_id,
        data=BoardBatchSchema.model_validate(
            {
                "ops": [
                    {"op": "create_shape", "client_id": "a", "data": RECT},
                    {
                        "op": "create_shape",
                        "client_id": "arrow",
                        "data": {
                            "kind": "arrow",
                            "points": [[0, 0], [50, 50]],
                            "start_ref": {"type": "shape", "client_id": "a", "anchor": "left"},
                            "end_ref": {"type": "sticker", "id": sticker.id},
                        },
                    },
                    {"op": "update_shape", "id": old.id, "revision": 1, "data": {"label": "A"}},
                    {"op": "update_shape", "id": old.id, "revision": 2, "data": {"z_index": 5}},
                    {"op": "delete_shape", "id": victim.id},
                    {
                        "op": "move_sticker",
                        "id": sticker.id,
                        "data": {"canvas_x": 500, "canvas_y": 5, "z_index": 2},
                    },
                ]
            }
        ),
    )
    created_id, arrow_id = result.id_map["a"], result.id_map["arrow"]
    by_id = {item.id: item for item in result.shapes}
    assert set(by_id) == {created_id, arrow_id, old.id}
    assert by_id[arrow_id].start_ref.model_dump() == {
        "type": "shape",
        "id": created_id,
        "anchor": "left",
    }
    assert (by_id[old.id].label, by_id[old.id].z_index, by_id[old.id].revision) == ("A", 5, 3)
    assert result.deleted_shape_ids == [victim.id]
    assert [(item.id, item.canvas_x, item.z_index) for item in result.stickers] == [
        (sticker.id, 500, 2)
    ]

    before = await shape_count(db_session, board_id)
    # Устаревшая ревизия и исчезнувшая фигура — 409 со списком, ничего не применено.
    with pytest.raises(BoardBatchConflictError) as conflict:
        await svc.shapes.apply_batch(
            project_id=project_id,
            board_id=board_id,
            author_id=user_id,
            data=BoardBatchSchema.model_validate(
                {
                    "ops": [
                        {"op": "create_shape", "data": RECT},
                        {"op": "update_shape", "id": old.id, "revision": 1, "data": {"x": 9}},
                        {"op": "delete_shape", "id": victim.id},
                        {"op": "move_sticker", "id": 10**9, "data": {"canvas_x": 0, "canvas_y": 0}},
                    ]
                }
            ),
        )
    assert conflict.value.detail["conflicts"] == [
        {"kind": "shape", "id": old.id},
        {"kind": "shape", "id": victim.id},
        {"kind": "sticker", "id": 10**9},
    ]
    assert conflict.value.status_code == 409
    assert await shape_count(db_session, board_id) == before

    # Ошибка посреди пакета откатывает и уже выполненные операции.
    with pytest.raises(BoardShapeInvalidError):
        await svc.shapes.apply_batch(
            project_id=project_id,
            board_id=board_id,
            author_id=user_id,
            data=BoardBatchSchema.model_validate(
                {
                    "ops": [
                        {"op": "create_shape", "client_id": "x", "data": RECT},
                        {"op": "update_shape", "id": old.id, "data": {"label": "Откатится"}},
                        {
                            "op": "create_shape",
                            "data": {
                                "kind": "line",
                                "points": [[0, 0], [1, 1]],
                                "end_ref": {"type": "shape", "client_id": "later"},
                            },
                        },
                        {"op": "create_shape", "client_id": "later", "data": RECT},
                    ]
                }
            ),
        )
    assert await shape_count(db_session, board_id) == before
    content = await svc.boards.get_content(project_id=project_id, board_id=board_id)
    assert {item.id: item.label for item in content.shapes}[old.id] == "A"

    with pytest.raises(ValidationError):
        BoardBatchSchema.model_validate(
            {"ops": [{"op": "create_shape", "client_id": "d", "data": RECT}] * 2}
        )
    with pytest.raises(ValidationError):
        BoardBatchSchema.model_validate({"ops": [{"op": "delete_shape", "id": 1}] * 501})


async def test_shape_limit_per_board(
    db_session: AsyncSession, project: Project, user: User, board: Board, monkeypatch
) -> None:
    monkeypatch.setattr("src.services.board_shapes.MAX_SHAPES_PER_BOARD", 2)
    project_id, user_id, board_id = project.id, user.id, board.id
    svc = services(db_session)
    await add_shape(svc, project_id, board_id, user_id, **RECT)
    with pytest.raises(BoardShapeInvalidError):
        await svc.shapes.apply_batch(
            project_id=project_id,
            board_id=board_id,
            author_id=user_id,
            data=BoardBatchSchema.model_validate(
                {"ops": [{"op": "create_shape", "data": RECT}] * 2}
            ),
        )
    await add_shape(svc, project_id, board_id, user_id, **RECT)
    with pytest.raises(BoardShapeInvalidError):
        await add_shape(svc, project_id, board_id, user_id, **RECT)


async def test_shape_to_sticker_keeps_arrows_attached(
    db_session: AsyncSession, project: Project, user: User, board: Board
) -> None:
    project_id, user_id, board_id = project.id, user.id, board.id
    svc = services(db_session)
    rect = await add_shape(
        svc,
        project_id,
        board_id,
        user_id,
        **{**RECT, "w": 100, "h": 900},
        label=" Идея ",
        z_index=7,
    )
    blank = await add_shape(svc, project_id, board_id, user_id, **RECT)
    arrow = await add_shape(
        svc,
        project_id,
        board_id,
        user_id,
        kind="arrow",
        points=[[0, 0], [10, 10]],
        start_ref={"type": "shape", "id": blank.id},
        end_ref={"type": "shape", "id": rect.id, "anchor": "top"},
    )

    with pytest.raises(BoardShapeRevisionConflictError):
        await svc.shapes.shape_to_sticker(
            project_id=project_id,
            board_id=board_id,
            shape_id=rect.id,
            data=BoardShapeToStickerSchema(revision=5),
            author_id=user_id,
            author_username="owner",
            author_display_name="Владельцев Виктор",
        )
    with pytest.raises(BoardShapeInvalidError):
        await svc.shapes.shape_to_sticker(
            project_id=project_id,
            board_id=board_id,
            shape_id=arrow.id,
            data=BoardShapeToStickerSchema(),
            author_id=user_id,
            author_username="owner",
            author_display_name="Владельцев Виктор",
        )

    result = await svc.shapes.shape_to_sticker(
        project_id=project_id,
        board_id=board_id,
        shape_id=rect.id,
        data=BoardShapeToStickerSchema(revision=1, color="#c9edff"),
        author_id=user_id,
        author_username="owner",
        author_display_name="Владельцев Виктор",
    )
    sticker = result.sticker
    assert (sticker.body, sticker.color, sticker.z_index) == ("Идея", "#c9edff", 7)
    # Размер зажат в пределы стикера 160..520.
    assert (sticker.width, sticker.height) == (160, 520)
    [updated] = result.updated_shapes
    assert updated.id == arrow.id
    assert updated.end_ref.model_dump() == {"type": "sticker", "id": sticker.id, "anchor": "top"}
    assert updated.start_ref.model_dump()["id"] == blank.id
    content = await svc.boards.get_content(project_id=project_id, board_id=board_id)
    assert rect.id not in {item.id for item in content.shapes}

    plain = await svc.shapes.shape_to_sticker(
        project_id=project_id,
        board_id=board_id,
        shape_id=blank.id,
        data=BoardShapeToStickerSchema(),
        author_id=user_id,
        author_username="owner",
        author_display_name="Владельцев Виктор",
    )
    assert (plain.sticker.body, plain.sticker.color) == ("Новый стикер", "#fff19a")
