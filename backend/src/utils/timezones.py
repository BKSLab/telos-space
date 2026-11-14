"""Проверка IANA timezone в контексте вопроса; часы хоста не задают день пользователя."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_timezone(value: str) -> str:
    """Возвращает проверенное имя часового пояса или ошибку входных данных."""
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("Укажите существующий часовой пояс IANA.") from error
    return value
