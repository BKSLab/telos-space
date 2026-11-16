"""Ошибки производной карты и проверки использованных версий."""

from src.exceptions.base import RepositoryError, ServiceError


class ProjectKnowledgeRepositoryError(RepositoryError):
    """Сохранённые версии или утверждения прочитать/сохранить не удалось."""

    detail = "Не удалось обратиться к карте знаний проекта."


class ProjectKnowledgeServiceError(ServiceError):
    """Сценарий карты не завершён."""

    detail = "Не удалось получить карту знаний проекта."


class ProjectKnowledgeNotFoundError(ProjectKnowledgeServiceError):
    """Проект/утверждение не существует в доступной области."""

    status_code = 404
    detail = "Знания не найдены в доступном проекте."

    def __init__(self) -> None:
        super().__init__(self.detail)


class ProjectKnowledgeAccessError(ProjectKnowledgeServiceError):
    """Сохраняет доменный 401/403/404 во всех каналах карты."""

    def __init__(self, *, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class ProjectKnowledgeStaleError(ProjectKnowledgeServiceError):
    """Прочитанное поколение или evidence изменилось до публикации."""

    status_code = 409
    detail = "Источники изменились после чтения. Повторите запрос."

    def __init__(self) -> None:
        super().__init__(self.detail)


class ProjectKnowledgeValidationError(ProjectKnowledgeServiceError):
    """Параметры чтения карты непригодны."""

    status_code = 422
    detail = "Непригодные параметры карты знаний."

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or type(self).detail
        super().__init__(self.detail)
