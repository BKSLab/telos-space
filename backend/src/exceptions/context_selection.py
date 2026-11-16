from src.exceptions.base import ServiceError


class ContextSelectionError(ServiceError):
    detail = "Не удалось собрать проверяемый контекст."

    def __init__(self) -> None:
        super().__init__(self.detail)


class ContextSelectionChangedError(ContextSelectionError):
    status_code = 409
    detail = "Источники изменились во время чтения. Повторите отбор контекста."


class ContextSelectionArgumentsError(ContextSelectionError):
    status_code = 422
    detail = "Фрагмент текущего вопроса не подтверждён дословным текстом человека."
