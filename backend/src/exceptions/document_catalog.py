"""Ошибки общего домена документов."""

from src.exceptions.base import RepositoryError, ServiceError


class DocumentCatalogRepositoryError(RepositoryError):
    detail = "Не удалось обработать данные документов."


class DocumentCatalogServiceError(ServiceError):
    detail = "Не удалось выполнить действие с документом."

    def __init__(self, error_details: str | None = None):
        super().__init__(error_details or self.detail)


class CatalogDocumentNotFoundError(DocumentCatalogServiceError):
    status_code = 404
    detail = "Документ недоступен или не найден."


class CatalogDocumentConflictError(DocumentCatalogServiceError):
    status_code = 409
    detail = "Документ изменился. Обновите его и повторите действие."


class CatalogDocumentValidationError(DocumentCatalogServiceError):
    status_code = 422

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)
