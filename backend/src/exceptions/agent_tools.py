from src.exceptions.base import RepositoryError, ServiceError


class AgentToolRunsRepositoryError(RepositoryError):
    """Ошибка журнала действий."""


class AgentToolsServiceError(ServiceError):
    """Безопасная ошибка выполнения инструмента."""

    status_code = 500
    detail = "Не удалось выполнить действие агента."


class AgentToolAccessError(AgentToolsServiceError):
    """Объект недоступен в серверной области проекта."""

    status_code = 404
    detail = "Объект или действие недоступны в этом проекте."


class AgentToolConflictError(AgentToolsServiceError):
    """Действие нельзя выполнить с указанным состоянием или ключом."""

    status_code = 409
    detail = "Состояние изменилось. Уточните действие по текущим данным."


class AgentToolRequestConflictError(AgentToolConflictError):
    """Квитанция того же ключа уже связана с другими аргументами или действием."""

    detail = "Ключ повтора уже использован для другого запроса. Повторите исходные аргументы или задайте новый request_id."


class AgentToolOperationError(AgentToolsServiceError):
    """Сохраняет безопасный контракт ошибки доменного сценария."""

    def __init__(self, error: ServiceError) -> None:
        super().__init__(str(error))
        self.status_code = error.status_code
        self.detail = error.detail


class AgentToolArgumentsError(AgentToolConflictError):
    """Аргументы действия не приняты: причина называет поле и ожидаемый формат."""

    status_code = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.detail = message


class AgentToolDecidedError(AgentToolConflictError):
    """По действию уже принято решение; повторно по ссылке его не принимают."""

    detail = "По этому действию решение уже принято."


class AgentToolExpiredError(AgentToolConflictError):
    """Срок подтверждения прошёл: параметры могли устареть."""

    status_code = 410
    detail = "Срок подтверждения истёк. Попросите агента предложить действие заново."
