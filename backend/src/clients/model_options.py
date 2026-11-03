"""Общие параметры модели Ариса для нативных tools и JSON-клиента."""

from typing import Any

from src.core.settings import LlmSettings


def agent_request_fields(settings: LlmSettings) -> dict[str, Any]:
    """Собирает явные параметры Polza, сохраняя прежний запрос по умолчанию.

    Args:
        settings: Модель, необязательный маршрут и режим рассуждений.

    Returns:
        Дополнительные поля тела запроса без промпта, модели и авторизации.
    """
    fields: dict[str, Any] = {}
    if provider := settings.agent_provider.strip():
        fields["provider"] = {"only": [provider], "allow_fallbacks": False}
    reasoning: dict[str, Any] = {}
    if settings.agent_reasoning_enabled is not None:
        reasoning["enabled"] = settings.agent_reasoning_enabled
    if settings.agent_reasoning_effort is not None:
        reasoning["effort"] = settings.agent_reasoning_effort
    if reasoning:
        fields["reasoning"] = reasoning
    return fields
