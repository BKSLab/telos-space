"""Изоляция cache routing Polza для независимых запросов модели."""

from urllib.parse import urlsplit
from uuid import uuid4


def request_routing_fields(url: str) -> dict[str, str]:
    """Обходит воспроизведённый 400 на повторном prompt-prefix у Polza.

    prompt_cache_key — документированная метка маршрутизации, не API-ключ.
    Метка уникальна на запрос; промпт, модель и авторизация не меняются.
    Остальным OpenAI-совместимым API поле не отправляется.
    """
    if urlsplit(url).hostname == "polza.ai":
        return {"prompt_cache_key": f"telos-{uuid4().hex}"}
    return {}
