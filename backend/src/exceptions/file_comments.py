"""Ошибки атомарной отправки комментариев с локальными файлами."""

from src.exceptions.base import RepositoryError, ServiceError


class FileCommentsRepositoryError(RepositoryError):
    """Не удалось прочитать или записать связи/квитанцию."""

    detail = "Не удалось сохранить файлы комментария."


class FileCommentsServiceError(ServiceError):
    """Сбой составной операции с комментарием и файлами."""

    detail = "Не удалось отправить комментарий с файлами."


class FileCommentSubmissionRepositoryConflictError(FileCommentsRepositoryError):
    """Уникальный ключ квитанции уже существует."""


class FileCommentValidationError(FileCommentsServiceError):
    """Текст, файлы или подписи не прошли проверку."""

    status_code = 422

    def __init__(self, detail: str) -> None:
        self.validation_detail = detail
        super().__init__(detail)

    @property
    def detail(self) -> str:
        return self.validation_detail


class FileCommentSubmissionConflictError(FileCommentsServiceError):
    """UUID отправки уже использован для другого содержимого."""

    status_code = 409
    detail = "Этот request_id уже использован с другими аргументами."

    def __init__(self, error_details: str = "UUID отправки использован с другими аргументами.") -> None:
        super().__init__(error_details)
