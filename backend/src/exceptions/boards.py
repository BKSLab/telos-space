from typing import Any

from fastapi import status

from src.exceptions.base import RepositoryError, ServiceError


class BoardsRepositoryError(RepositoryError):
    """Ошибка доступа к доскам проекта."""

    detail = "Ошибка базы данных при обработке досок проекта."


class BoardsServiceError(ServiceError):
    """Ошибка бизнес-операции с доской, её стикерами или фигурами."""

    detail: Any = "Не удалось выполнить операцию с доской проекта."


class BoardNotFoundError(BoardsServiceError):
    """Доски нет в проекте или она удалена."""

    status_code = status.HTTP_404_NOT_FOUND
    detail = "Доска не найдена."

    def __init__(self, board_id: int):
        self.board_id = board_id
        super().__init__(error_details=f"Доска id={board_id} не найдена.")


class BoardRevisionConflictError(BoardsServiceError):
    """Доску успел изменить другой участник."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Доска уже изменена другим участником. Обновите страницу и повторите."

    def __init__(self, board_id: int, revision: int):
        self.board_id = board_id
        self.revision = revision
        super().__init__(
            error_details=f"Ревизия {revision} доски id={board_id} больше не актуальна."
        )


class ProjectLastBoardError(BoardsServiceError):
    """Последнюю активную доску проекта удалить нельзя."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Нельзя удалить последнюю доску проекта."

    def __init__(self, board_id: int):
        self.board_id = board_id
        super().__init__(error_details=f"Доска id={board_id} — последняя в проекте.")
