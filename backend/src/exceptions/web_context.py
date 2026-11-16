"""Безопасные ошибки изолированного клиента публичного веб-контекста."""

from src.exceptions.clients import ClientError


class WebContextClientError(ClientError):
    """Публичный веб-контекст недоступен или его ответ непригоден для чтения."""

    detail = "Не удалось получить публичный веб-контекст."


class WebQueryPrivacyError(WebContextClientError):
    """Сервер не разрешил отправку темы или обнаружены учётные данные."""

    detail = "Отправка этого запроса во внешний поиск не разрешена."


class UnsafeWebUrlError(WebContextClientError):
    """Адрес, DNS-ответ или переход нарушает политику публичных назначений."""

    detail = "Веб-адрес не разрешён для внешнего чтения."


class WebResponseError(WebContextClientError):
    """Провайдер или сайт вернул ошибку протокола либо неверную структуру."""

    detail = "Внешний источник вернул непригодный ответ."


class WebUnsupportedContentError(WebContextClientError):
    """Представление требует неподдерживаемого декодирования или извлечения."""

    detail = "Формат внешнего источника не поддерживается."


class WebContentTooLargeError(WebContextClientError):
    """Размер ответа превышает ограничение до извлечения текста."""

    detail = "Внешний источник превышает допустимый размер."


class WebContextTimeoutError(WebContextClientError):
    """Исчерпан общий бюджет, включая DNS, переходы и получение тела."""

    detail = "Истекло время получения внешнего источника."
