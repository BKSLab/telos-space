"""Безопасные ошибки протокола GitHub, без текста ответа и credentials."""

from src.exceptions.clients import ClientError


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


class GitHubRedirectError(GitHubClientError):
    """Переход по upstream Location запрещён, включая переименования repo."""

    detail = "GitHub запросил запрещённое перенаправление."
