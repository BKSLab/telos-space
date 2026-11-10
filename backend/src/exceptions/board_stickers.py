from fastapi import status

from src.exceptions.base import RepositoryError, ServiceError


class BoardStickersRepositoryError(RepositoryError):
    """Ошибка доступа к стикерам Project Board."""

    detail = "Ошибка базы данных при обработке стикеров проекта."


class BoardStickersServiceError(ServiceError):
    """Ошибка бизнес-операции со стикерами Project Board."""

    detail = "Не удалось выполнить операцию со стикерами проекта."


class BoardStickerNotFoundError(BoardStickersServiceError):
    """Стикер не существует внутри указанного проекта."""

    status_code = status.HTTP_404_NOT_FOUND
    detail = "Стикер не найден."

    def __init__(self, sticker_id: int):
        self.sticker_id = sticker_id
        super().__init__(error_details=f"Стикер id={sticker_id} не найден.")


class BoardStickerRevisionConflictError(BoardStickersServiceError):
    """Стикер успел изменить другой участник."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Стикер уже изменён другим участником. Обновите доску и повторите."

    def __init__(self, sticker_id: int, revision: int):
        self.sticker_id = sticker_id
        self.revision = revision
        super().__init__(
            error_details=f"Ревизия {revision} стикера id={sticker_id} больше не актуальна."
        )


class BoardStickerTaskMismatchError(BoardStickersServiceError):
    """Одна из связанных задач отсутствует в проекте стикера."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "Связать стикер можно только с существующими задачами этого проекта."

    def __init__(self, task_ids: set[int]):
        self.task_ids = task_ids
        super().__init__(
            error_details=f"Задачи не принадлежат проекту стикера: {sorted(task_ids)}."
        )


class FreeBoardTaskLinksError(BoardStickersServiceError):
    """Связь стикера с задачами запрошена на доске без проекта."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "Привязка к задачам доступна только на доске проекта."

    def __init__(self):
        super().__init__(error_details="У свободной доски нет проекта, задачи недоступны.")


class BoardStickerLockedError(BoardStickersServiceError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "Сначала разблокируйте стикер или связанные с ним стрелки."

    def __init__(self):
        super().__init__(error_details=self.detail)
