from fastapi import status

from src.exceptions.base import RepositoryError
from src.exceptions.boards import BoardsServiceError


class BoardShapesRepositoryError(RepositoryError):
    """Ошибка доступа к фигурам доски."""

    detail = "Ошибка базы данных при обработке фигур доски."


class BoardShapesServiceError(BoardsServiceError):
    """Ошибка бизнес-операции с фигурами доски."""

    detail = "Не удалось выполнить операцию с фигурами доски."


class BoardShapeNotFoundError(BoardShapesServiceError):
    """Фигуры нет на указанной доске."""

    status_code = status.HTTP_404_NOT_FOUND
    detail = "Фигура не найдена."

    def __init__(self, shape_id: int):
        self.shape_id = shape_id
        super().__init__(error_details=f"Фигура id={shape_id} не найдена.")


class BoardShapeRevisionConflictError(BoardShapesServiceError):
    """Фигуру успел изменить или удалить другой участник."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Фигура уже изменена другим участником. Обновите доску и повторите."

    def __init__(self, shape_id: int, revision: int):
        self.shape_id = shape_id
        self.revision = revision
        super().__init__(
            error_details=f"Ревизия {revision} фигуры id={shape_id} больше не актуальна."
        )


class BoardBatchConflictError(BoardShapesServiceError):
    """Пакет не применён: часть объектов изменена или удалена другими."""

    status_code = status.HTTP_409_CONFLICT
    message = "Часть объектов доски уже изменена другими участниками. Пакет не применён."

    def __init__(self, conflicts: list[dict]):
        self.conflicts = conflicts
        self.detail = {"message": self.message, "conflicts": conflicts}
        super().__init__(error_details=f"Конфликты пакета: {conflicts}.")


class BoardShapeInvalidError(BoardShapesServiceError):
    """Фигура не проходит правила доски: привязка, лимит, вид фигуры."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT

    def __init__(self, reason: str):
        self.detail = reason
        super().__init__(error_details=reason)
