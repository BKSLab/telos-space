"""Страховка «у проекта всегда есть доска» — общая для сервисов доски."""

import logging

from src.db.models.boards import DEFAULT_BOARD_TITLE, Board
from src.repositories.boards import BoardsRepository

logger = logging.getLogger(__name__)


async def ensure_default_board(
    boards_repository: BoardsRepository,
    project_id: int,
    *,
    created_by_user_id: int | None,
) -> Board:
    """Возвращает первую активную доску, создавая «Основную», если досок нет.

    Проверка повторяется под блокировкой досок проекта: два параллельных
    первых запроса не создадут две «Основные». Фиксирует транзакцию вызывающий.
    """
    board = await boards_repository.get_first_active(project_id)
    if board is not None:
        return board
    await boards_repository.lock_project_boards(project_id)
    board = await boards_repository.get_first_active(project_id)
    if board is not None:
        return board
    logger.warning("⚠️ У проекта id=%s нет досок — создаётся «Основная».", project_id)
    return await boards_repository.insert(
        data={
            "project_id": project_id,
            "title": DEFAULT_BOARD_TITLE,
            "position": 0,
            "created_by_user_id": created_by_user_id,
        }
    )
