"""Ошибки локальной обработки и полнотекстового поиска файлов."""

from src.exceptions.base import RepositoryError, ServiceError


class FileProcessingRepositoryError(RepositoryError):
    detail = "Не удалось обработать очередь файлов."


class FileSearchRepositoryError(RepositoryError):
    detail = "Не удалось выполнить поиск файлов."


class FileSearchServiceError(ServiceError):
    detail = "Поиск файлов временно недоступен."
