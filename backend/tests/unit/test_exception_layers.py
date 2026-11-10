"""Границы слоёв исключений.

Каждый слой имеет собственную иерархию, и вышестоящий слой обязан её
преобразовать. Смысл правила практический: `except AiProviderError`
в эндпоинте не должен ловить сбой httpx, а `except ServiceError` в сервисе
— чужую доменную ошибку.
"""

import inspect
import pkgutil

import pytest

import src.exceptions as exceptions_package
from src.exceptions.access import AccessServiceError, ResourceNotAvailableError
from src.exceptions.base import ApplicationError, RepositoryError, ServiceError
from src.exceptions.clients import (
    ClientError,
    EmbeddingClientError,
    LlmClientError,
    VectorStoreClientError,
    VisionClientError,
)
from src.exceptions.storage import (
    AvatarStorageError,
    StorageError,
    TaskAttachmentStorageError,
)
from src.exceptions.task_documents import (
    TaskDocumentImportServiceError,
    TaskDocumentStepFailedError,
)

CLIENT_ERRORS = [
    LlmClientError,
    VisionClientError,
    EmbeddingClientError,
    VectorStoreClientError,
]
STORAGE_ERRORS = [AvatarStorageError, TaskAttachmentStorageError]


@pytest.mark.parametrize("error_type", CLIENT_ERRORS, ids=lambda t: t.__name__)
def test_client_error_is_not_a_service_error(error_type: type) -> None:
    """Ошибка клиента не притворяется ошибкой сервисного слоя.

    Иначе сбой внешнего API проходил бы через `except ServiceError`
    вышестоящего кода как своя доменная ошибка.
    """
    assert issubclass(error_type, ClientError)
    assert issubclass(error_type, ApplicationError)
    assert not issubclass(error_type, ServiceError)
    assert not issubclass(error_type, RepositoryError)


@pytest.mark.parametrize("error_type", STORAGE_ERRORS, ids=lambda t: t.__name__)
def test_storage_error_is_not_a_service_error(error_type: type) -> None:
    """Ошибка хранилища тоже принадлежит своему слою."""
    assert issubclass(error_type, StorageError)
    assert not issubclass(error_type, ServiceError)
    assert not issubclass(error_type, ClientError)


def test_client_error_chain_keeps_the_original_cause() -> None:
    """Преобразование сохраняет исходную причину через `raise ... from`."""
    original = ValueError("некорректный ответ")

    try:
        try:
            raise original
        except ValueError as error:
            raise LlmClientError(str(error)) from error
    except LlmClientError as wrapped:
        assert wrapped.__cause__ is original


def test_step_failure_reports_the_cause_status_and_detail() -> None:
    """Обёртка составного сценария переносит наружу причину отказа."""
    cause = ResourceNotAvailableError(resource="Документ", resource_id=5)

    wrapped = TaskDocumentStepFailedError(cause)

    assert isinstance(wrapped, TaskDocumentImportServiceError)
    assert wrapped.status_code == cause.status_code
    assert wrapped.detail == cause.detail


def test_layer_hierarchies_do_not_overlap() -> None:
    """Слои клиентов и хранилищ не пересекаются между собой и с сервисным; иерархия ошибок доступа принадлежит сервисному слою."""
    # Слои клиентов и хранилищ независимы.
    assert not issubclass(ClientError, StorageError)
    assert not issubclass(StorageError, ClientError)
    # Все модули исключений следуют одному правилу именования слоёв. Класс с суффиксом `ServiceError` обязан быть в сервисном слое: иначе `except ServiceError` в транспорте перестанет означать то, что читается.
    misplaced: list[str] = []
    for module_info in pkgutil.iter_modules(exceptions_package.__path__):
        module = __import__(
            f"src.exceptions.{module_info.name}",
            fromlist=["*"],
        )
        for name, value in inspect.getmembers(module, inspect.isclass):
            if not issubclass(value, ApplicationError) or value.__module__ != module.__name__:
                continue
            if name.endswith("ServiceError") and not issubclass(value, ServiceError):
                misplaced.append(f"{module.__name__}.{name}")
            if name.endswith("RepositoryError") and not issubclass(value, RepositoryError):
                misplaced.append(f"{module.__name__}.{name}")

    assert not misplaced, f"Класс не соответствует своему слою: {misplaced}"
    # Ошибки доступа принадлежат сервисному слою.
    assert issubclass(AccessServiceError, ServiceError)
    assert issubclass(ResourceNotAvailableError, AccessServiceError)
    assert ResourceNotAvailableError(resource="Проект", resource_id=1).status_code == 404
