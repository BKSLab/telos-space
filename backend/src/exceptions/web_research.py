"""Сервисные ошибки внешнего исследования без URL, тем и credentials."""

from src.exceptions.base import ServiceError


class WebResearchServiceError(ServiceError):
    status_code = 502
    detail = "Внешнее исследование временно недоступно."


class WebResearchDisabledError(WebResearchServiceError):
    status_code = 503
    detail = "Веб-поиск не настроен. Чтение разрешённых публичных страниц доступно отдельно."


class WebResearchConsentError(WebResearchServiceError):
    status_code = 403
    detail = "Разрешите точную публичную тему или адрес страницы и повторите исследование."


class WebResearchVersionError(WebResearchServiceError):
    status_code = 409
    detail = "Страница изменилась после чтения. Прочитайте её заново перед сохранением."
