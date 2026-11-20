"""Безопасные ошибки общих AI-сценариев приложения."""

from fastapi import status

from src.exceptions.base import ServiceError


class AiProviderError(ServiceError):
    """Внешний AI-провайдер или семантическое хранилище недоступны."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    detail = "AI-сервис временно недоступен. Повторите попытку позже."


class AgentAnswerError(ServiceError):
    """Арис не смог сформировать ответ в доступном ему контексте."""

    status_code = status.HTTP_502_BAD_GATEWAY
    detail = "Арис не смог сформировать ответ."


class AgentContextChangedError(AgentAnswerError):
    """Проверенные знания изменились во время подготовки ответа."""

    status_code = status.HTTP_409_CONFLICT
    detail = "Данные проекта обновились во время подготовки ответа"

    def __init__(self) -> None:
        super().__init__(self.detail)
