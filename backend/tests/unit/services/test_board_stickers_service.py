from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.db.models.users import User
from src.exceptions.board_stickers import (
    BoardStickerNotFoundError,
    BoardStickerRevisionConflictError,
    BoardStickersRepositoryError,
    BoardStickersServiceError,
    BoardStickerTaskMismatchError,
)
from src.exceptions.boards import BoardNotFoundError
from src.repositories.board_shapes import BoardShapesRepository
from src.repositories.board_stickers import BoardStickersRepository
from src.repositories.boards import BoardsRepository
from src.repositories.tasks import TasksRepository
from src.repositories.unit_of_work import UnitOfWork
from src.schemas.board_stickers import (
    BoardStickerCreateSchema,
    BoardStickerPositionUpdateSchema,
    BoardStickerUpdateSchema,
)
from src.services.board_stickers import BoardStickersService
from tests.board_support import local_user


def sticker(*, revision: int = 1, task_ids: list[int] | None = None) -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=4,
        project_id=1,
        board_id=3,
        z_index=0,
        body="Согласовать API",
        color="#fff19a",
        canvas_x=40.0,
        canvas_y=40.0,
        created_by_user_id=7,
        created_by_username_snapshot="i.ivanov",
        created_by_display_name_snapshot="Иванов Иван",
        task_links=[SimpleNamespace(task_id=task_id) for task_id in (task_ids or [])],
        revision=revision,
        created_at=now,
        updated_at=now,
    )


def user() -> User:
    return local_user(
        id=7,
        username="i.ivanov",
        display_name="Иванов Иван Петрович",
        is_active=True,
    )


def boards() -> AsyncMock:
    """Доски проекта: активная доска id=3 — она же первая."""
    repository = AsyncMock(spec=BoardsRepository)
    repository.get_active.return_value = SimpleNamespace(id=3, project_id=1)
    repository.get_first_active.return_value = SimpleNamespace(id=3, project_id=1)
    return repository


def service(
    repository: AsyncMock | None = None,
    tasks_repository: AsyncMock | None = None,
    unit_of_work: AsyncMock | None = None,
    *,
    boards_repository: AsyncMock | None = None,
    shapes_repository: AsyncMock | None = None,
) -> BoardStickersService:
    return BoardStickersService(
        stickers_repository=repository or AsyncMock(spec=BoardStickersRepository),
        tasks_repository=tasks_repository or AsyncMock(spec=TasksRepository),
        boards_repository=boards_repository or boards(),
        shapes_repository=shapes_repository or AsyncMock(spec=BoardShapesRepository),
        unit_of_work=unit_of_work or AsyncMock(spec=UnitOfWork),
    )


@pytest.mark.asyncio
async def test_list_returns_transport_contract() -> None:
    repository = AsyncMock(spec=BoardStickersRepository)
    repository.list_by_board_id.return_value = [sticker(task_ids=[11, 12])]
    boards_repository = boards()

    result = await service(repository, boards_repository=boards_repository).list_stickers(1, 3)

    assert result[0].task_ids == [11, 12]
    assert (result[0].board_id, result[0].z_index) == (3, 0)
    repository.list_by_board_id.assert_awaited_once_with(3)
    boards_repository.get_active.assert_awaited_once_with(project_id=1, board_id=3)

    # Доска чужого проекта или удалённая — 404, стикеры не читаются.
    repository = AsyncMock(spec=BoardStickersRepository)
    boards_repository = boards()
    boards_repository.get_active.return_value = None
    with pytest.raises(BoardNotFoundError):
        await service(repository, boards_repository=boards_repository).list_stickers(1, 99)
    repository.list_by_board_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_revision_never_overwrites_someone_elses_change() -> None:
    """Устаревшая ревизия отклоняется и до записи, и по её результату.

    Проверка перед записью ловит явно старую версию, а нулевое число
    затронутых строк — гонку двух одновременных правок. Оба случая для
    клиента одинаковы: его версия больше не актуальна.
    """
    repository = AsyncMock(spec=BoardStickersRepository)
    repository.get_by_id.return_value = sticker(revision=5)
    with pytest.raises(BoardStickerRevisionConflictError):
        await service(repository).update_sticker(
            project_id=1,
            sticker_id=4,
            data=BoardStickerUpdateSchema(revision=4, body="Поздняя версия"),
        )
    repository.update_fields.assert_not_awaited()

    repository = AsyncMock(spec=BoardStickersRepository)
    repository.get_by_id.return_value = sticker(revision=2)
    repository.update_fields.return_value = False
    with pytest.raises(BoardStickerRevisionConflictError):
        await service(repository).update_sticker(
            project_id=1,
            sticker_id=4,
            data=BoardStickerUpdateSchema(revision=2, color="#E3F6D5"),
        )

    repository = AsyncMock(spec=BoardStickersRepository)
    repository.get_by_id.return_value = sticker(revision=3)
    repository.delete.return_value = False
    with pytest.raises(BoardStickerRevisionConflictError):
        await service(repository).delete_sticker(project_id=1, sticker_id=4, revision=3)


@pytest.mark.asyncio
async def test_missing_sticker_is_not_found_on_update_and_move() -> None:
    """Отсутствующий стикер отвечает 404, а не конфликтом ревизии."""
    repository = AsyncMock(spec=BoardStickersRepository)
    repository.get_by_id.return_value = None
    with pytest.raises(BoardStickerNotFoundError):
        await service(repository).update_sticker(
            project_id=1,
            sticker_id=404,
            data=BoardStickerUpdateSchema(revision=1, body="Текст"),
        )

    repository = AsyncMock(spec=BoardStickersRepository)
    repository.get_by_id.return_value = None
    with pytest.raises(BoardStickerNotFoundError):
        await service(repository).move_sticker(
            project_id=1,
            sticker_id=404,
            data=BoardStickerPositionUpdateSchema(canvas_x=10.0, canvas_y=20.0),
        )
    repository.update_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_move_updates_only_canvas_position_and_commits() -> None:
    repository = AsyncMock(spec=BoardStickersRepository)
    moved = sticker(revision=3)
    moved.canvas_x = 312.5
    moved.canvas_y = -48.0
    repository.update_position.return_value = True
    repository.get_by_id.return_value = moved
    unit_of_work = AsyncMock(spec=UnitOfWork)

    result = await service(repository, unit_of_work=unit_of_work).move_sticker(
        project_id=1,
        sticker_id=4,
        data=BoardStickerPositionUpdateSchema(canvas_x=312.5, canvas_y=-48.0),
    )

    assert result.canvas_x == 312.5
    assert result.canvas_y == -48.0
    assert result.revision == 3
    repository.update_position.assert_awaited_once_with(
        project_id=1,
        sticker_id=4,
        canvas_x=312.5,
        canvas_y=-48.0,
    )
    unit_of_work.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_delete_uses_revision_detaches_arrows_and_commits() -> None:
    repository = AsyncMock(spec=BoardStickersRepository)
    repository.get_by_id.return_value = sticker(revision=3)
    repository.delete.return_value = True
    shapes_repository = AsyncMock(spec=BoardShapesRepository)
    unit_of_work = AsyncMock(spec=UnitOfWork)

    await service(
        repository, unit_of_work=unit_of_work, shapes_repository=shapes_repository
    ).delete_sticker(
        project_id=1,
        sticker_id=4,
        revision=3,
    )

    repository.delete.assert_awaited_once_with(
        project_id=1,
        sticker_id=4,
        expected_revision=3,
    )
    shapes_repository.detach_refs.assert_awaited_once_with(
        board_id=3, target_type="sticker", target_id=4
    )
    unit_of_work.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_repository_failure_is_wrapped() -> None:
    repository = AsyncMock(spec=BoardStickersRepository)
    repository.list_by_board_id.side_effect = BoardStickersRepositoryError("БД недоступна")

    with pytest.raises(BoardStickersServiceError):
        await service(repository).list_stickers(1, 3)


@pytest.mark.asyncio
async def test_sticker_create_and_update_preserve_author_and_links() -> None:
    """Автор проставляется по сессии и сохраняется при правке, связи задач заменяются, чужая задача отклоняется."""

    repository = AsyncMock(spec=BoardStickersRepository)
    repository.insert.return_value = 4
    repository.get_by_id.return_value = sticker(task_ids=[11])
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_project.return_value = [SimpleNamespace(id=11)]
    unit_of_work = AsyncMock(spec=UnitOfWork)

    result = await service(repository, tasks_repository, unit_of_work).create_sticker(
        project_id=1,
        data=BoardStickerCreateSchema(body="  Согласовать API  ", task_ids=[11]),
        author_id=user().id,
        author_username=user().username,
        author_display_name="Иванов Иван Петрович",
    )

    assert result.id == 4
    saved = repository.insert.await_args.kwargs
    assert saved["data"]["created_by_user_id"] == 7
    assert saved["data"]["created_by_username_snapshot"] == "i.ivanov"
    assert saved["data"]["created_by_display_name_snapshot"] == "Иванов Иван Петрович"
    assert saved["data"]["body"] == "Согласовать API"
    assert saved["data"]["canvas_x"] == 40.0
    assert saved["data"]["canvas_y"] == 40.0
    # Без board_id (так зовёт агент) стикер ложится на первую доску проекта.
    assert saved["data"]["board_id"] == 3
    assert (saved["data"]["width"], saved["data"]["z_index"]) == (230.0, 0)
    links = repository.replace_task_links.await_args.kwargs
    assert links["task_ids"] == [11]
    unit_of_work.commit.assert_awaited_once_with()

    repository = AsyncMock(spec=BoardStickersRepository)
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_project.return_value = []

    with pytest.raises(BoardStickerTaskMismatchError):
        await service(repository, tasks_repository).create_sticker(
            project_id=1,
            data=BoardStickerCreateSchema(body="Текст", task_ids=[99]),
            author_id=user().id,
            author_username=user().username,
            author_display_name="Иванов Иван Петрович",
        )

    repository.insert.assert_not_awaited()

    repository = AsyncMock(spec=BoardStickersRepository)
    repository.get_by_id.side_effect = [
        sticker(revision=3, task_ids=[11]),
        sticker(revision=4, task_ids=[12]),
    ]
    repository.update_fields.return_value = True
    tasks_repository = AsyncMock(spec=TasksRepository)
    tasks_repository.get_by_project.return_value = [SimpleNamespace(id=12)]
    unit_of_work = AsyncMock(spec=UnitOfWork)

    result = await service(repository, tasks_repository, unit_of_work).update_sticker(
        project_id=1,
        sticker_id=4,
        data=BoardStickerUpdateSchema(
            revision=3,
            body="Новый текст",
            color="#c9edff",
            task_ids=[12],
        ),
    )

    assert result.revision == 4
    changes = repository.update_fields.await_args.kwargs
    assert changes["expected_revision"] == 3
    assert changes["changes"] == {
        "body": "Новый текст",
        "color": "#c9edff",
    }
    assert repository.replace_task_links.await_args.kwargs["task_ids"] == [12]
    assert "created_by_user_id" not in changes["changes"]
    unit_of_work.commit.assert_awaited_once_with()
