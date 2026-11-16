"""Безопасные ошибки протокола GitHub, без текста ответа и credentials."""

from src.exceptions.base import RepositoryError, ServiceError
from src.exceptions.clients import ClientError


class GitSourcesRepositoryError(RepositoryError):
    """Безопасная ошибка хранения Git без SQL-параметров и ciphertext."""

    detail = "Не удалось сохранить или прочитать Git-источник."


class GitSourceServiceError(ServiceError):
    """Базовая доменная ошибка Git-источников."""

    detail = "Не удалось обработать Git-источник."

    def __init__(self, error_details: str | None = None) -> None:
        super().__init__(self.detail)


class GitSourceNotAvailableError(GitSourceServiceError):
    """Отсутствует, чужой проект, отключённый источник или отозванный доступ."""

    status_code = 404
    detail = "Git-источник отсутствует или недоступен."


class GitSourceOwnerRequiredError(GitSourceServiceError):
    """Текущий пользователь не владелец проекта."""

    status_code = 403
    detail = "Git-подключениями управляет владелец проекта."


class GitSourceSessionRequiredError(GitSourceServiceError):
    """Credentials не принимаются от модели и внешних ключей."""

    status_code = 403
    detail = "Управление Git credentials требует сессии владельца проекта."


class GitSourceConflictError(GitSourceServiceError):
    """Изменились привязка, поколение или версия источника."""

    status_code = 409
    detail = "Git-источник изменился. Перечитайте актуальную версию."


class GitSourceValidationError(GitSourceServiceError):
    """Не выполнены ограничения сценария Git."""

    status_code = 422
    detail = "Некорректные параметры Git-источника."


class GitSourceScopeRequiredError(GitSourceServiceError):
    status_code = 403
    detail = "Для Git-источников требуются projects:read и knowledge:read."


class GitSourceIncompleteError(GitSourceServiceError):
    status_code = 503
    detail = "Git-источник пока подготовлен не полностью; проверьте охват и повторите чтение."


class GitSourceBudgetError(GitSourceIncompleteError):
    """A bounded foreground read cannot complete a new remote proof."""

    detail = "Исчерпан бюджет запросов Git этого чтения. Повторите выбранное чтение отдельно."


class GitCredentialUnavailableError(GitSourceServiceError):
    """Ключ шифрования отсутствует, повреждён или не расшифровывает привязку."""

    status_code = 503
    detail = "Хранилище Git credentials не настроено или недоступно."


class GitHubClientError(ClientError):
    """Базовая ошибка чтения GitHub; перевод в доменную ошибку делает сервис."""

    detail = "Не удалось прочитать GitHub."

    def __init__(
        self,
        *,
        upstream_status: int | None = None,
        retry_after: int | None = None,
        rate_limit_reset: int | None = None,
    ) -> None:
        self.upstream_status = upstream_status
        self.retry_after = retry_after
        self.rate_limit_reset = rate_limit_reset
        super().__init__(self.detail)


class GitHubAuthenticationError(GitHubClientError):
    """Upstream отклонил аутентификацию."""

    detail = "GitHub отклонил аутентификацию."


class GitHubForbiddenError(GitHubClientError):
    """Upstream запретил чтение; это не проверка проектных прав TELOS."""

    detail = "GitHub запретил чтение ресурса."


class GitHubNotFoundError(GitHubClientError):
    """Ресурс отсутствует или скрыт GitHub из-за прав credential."""

    detail = "Ресурс GitHub отсутствует или недоступен."


class GitHubConflictError(GitHubClientError):
    """Например, репозиторий ещё не содержит коммитов."""

    detail = "Текущее состояние репозитория GitHub не допускает чтение."


class GitHubRateLimitError(GitHubClientError):
    """Очередь должна отложить чтение согласно безопасным числовым заголовкам."""

    detail = "Достигнут лимит запросов GitHub."


class GitHubRequestError(GitHubClientError):
    """Некорректный локальный аргумент или отклонённый upstream запрос."""

    detail = "Некорректные параметры запроса к GitHub."


class GitHubResponseError(GitHubClientError):
    """Неизвестный статус, невалидный JSON или неподтверждённый снимок."""

    detail = "GitHub вернул неподдерживаемый ответ."


class GitHubTransportError(GitHubClientError):
    """Сбой сети, deadline или 5xx, допускающий ограниченный повтор."""

    detail = "Временный сбой при чтении GitHub."


class GitHubLimitError(GitHubClientError):
    """Ответ или blob превышает явно заданный локальный лимит."""

    detail = "Данные GitHub превышают установленный лимит чтения."


class GitHubBudgetExceededError(GitHubLimitError):
    """Требуется сохранить checkpoint и продолжить в следующей bounded пачке."""

    detail = "Достигнут бюджет запросов GitHub этой пачки."


class GitHubRedirectError(GitHubClientError):
    """Переход по upstream Location запрещён, включая переименования repo."""

    detail = "GitHub запросил запрещённое перенаправление."
