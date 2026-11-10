"""Доменные ошибки редактора досок."""

from src.exceptions.boards import BoardsServiceError


class BoardEditorValidationError(BoardsServiceError):
    status_code = 422
    detail = "Объекты metadata должны принадлежать этой доске."


class BoardAlreadyPromotedError(BoardsServiceError):
    status_code = 409
    detail = "Доска уже привязана к проекту."


class BoardObjectLockedError(BoardsServiceError):
    status_code = 409
    detail = "Объект заблокирован в редакторе. Сначала снимите блокировку."
