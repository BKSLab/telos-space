"""Ошибки данных и сценариев файлов проекта."""

from src.exceptions.base import RepositoryError, ServiceError


class ProjectFilesRepositoryError(RepositoryError):
    detail = "Не удалось сохранить или прочитать файл проекта."


class ProjectFilesServiceError(ServiceError):
    detail = "Не удалось выполнить операцию с файлом проекта."


class ProjectFileNotFoundError(ProjectFilesServiceError):
    status_code = 404
    detail = "Файл проекта не найден."


class ProjectFileValidationError(ProjectFilesServiceError):
    status_code = 422
    detail = "Файл пуст или имя файла некорректно."


class ProjectFileTooLargeError(ProjectFilesServiceError):
    status_code = 413
    detail = "Размер файла превышает установленный лимит."


class ProjectFileUnsupportedError(ProjectFilesServiceError):
    status_code = 415
    detail = "Поддерживаются текст, Markdown, CSV, PDF, DOCX, Excel и изображения."
