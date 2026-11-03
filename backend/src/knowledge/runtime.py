"""Контейнер внешних клиентов AI-контура, которым владеет lifespan.

Здесь нет ленивого глобального экземпляра и нет функции-локатора: клиенты
создаёт composition root приложения, он же их закрывает. Потребители
получают либо конкретный клиент через DI, либо весь контейнер явным
аргументом — но никогда не «находят» его сами.
"""

from dataclasses import dataclass

import httpx
from qdrant_client import AsyncQdrantClient

from src.clients.embedding import EmbeddingClient
from src.clients.llm import LlmClient
from src.clients.model_options import agent_request_fields
from src.clients.qdrant import ProjectQdrantClient
from src.clients.vision import DisabledVisionCapability, VisionCapability, VisionClient
from src.core.settings import Settings


@dataclass(slots=True)
class KnowledgeRuntime:
    """Долгоживущие сетевые клиенты AI-контура.

    Attributes:
        http_client: Общий исходящий HTTP-клиент внешних API.
        embedding_client: Клиент API эмбеддингов.
        llm_client: Клиент chat completions.
        qdrant_client: Клиент векторного индекса.
        vision: Распознавание изображений либо явная выключенная реализация.
        payload_indexes_backfill_pending: Признак отложенного backfill, если
            Qdrant был недоступен на старте.
    """

    http_client: httpx.AsyncClient
    embedding_client: EmbeddingClient
    llm_client: LlmClient
    qdrant_client: ProjectQdrantClient
    vision: VisionCapability
    payload_indexes_backfill_pending: bool = False


def create_http_client(settings: Settings) -> httpx.AsyncClient:
    """Создаёт общий HTTP-клиент внешних API с явными пределами пула.

    Лимиты берутся из настроек, а не остаются умолчаниями httpx: у числа
    одновременных исходящих соединений должен быть один видимый владелец.

    Args:
        settings: Настройки приложения.

    Returns:
        Клиент, закрывать который обязан вызывающий lifespan.
    """
    limits = httpx.Limits(
        max_connections=settings.http_client.http_max_connections,
        max_keepalive_connections=settings.http_client.http_max_keepalive_connections,
        keepalive_expiry=settings.http_client.http_keepalive_expiry,
    )
    return httpx.AsyncClient(
        timeout=float(settings.llm.llm_timeout),
        limits=limits,
    )


def create_qdrant_client(settings: Settings) -> AsyncQdrantClient:
    """Создаёт transport векторной базы.

    SDK создаётся здесь, а не внутри обёртки: создание и закрытие должны
    иметь одного видимого владельца, и в тестах его проще подменить.

    Args:
        settings: Настройки приложения.

    Returns:
        Клиент SDK, закрывать который обязан вызывающий lifespan.
    """
    return AsyncQdrantClient(
        url=settings.knowledge.qdrant_url,
        api_key=settings.knowledge.qdrant_api_key.get_secret_value() or None,
        timeout=settings.knowledge.qdrant_timeout,
    )


def build_knowledge_runtime(
    *,
    settings: Settings,
    http_client: httpx.AsyncClient,
    qdrant_client: AsyncQdrantClient,
) -> KnowledgeRuntime:
    """Собирает клиентов AI-контура поверх готовых transport-ов.

    Args:
        settings: Настройки приложения.
        http_client: Общий HTTP-клиент внешних API.
        qdrant_client: Transport векторной базы.

    Returns:
        Контейнер клиентов, готовый к передаче в DI и composition-модули.
    """
    return KnowledgeRuntime(
        http_client=http_client,
        embedding_client=EmbeddingClient(
            http_client=http_client,
            url=settings.embedding.embedding_api_url,
            api_key=settings.embedding.embedding_api_key.get_secret_value(),
            model=settings.embedding.embedding_model,
            timeout=settings.embedding.embedding_timeout,
        ),
        llm_client=LlmClient(
            http_client=http_client,
            url=settings.llm.llm_api_url,
            headers=settings.llm.headers,
            model=settings.llm.agent_model,
            timeout=settings.llm.agent_timeout_seconds,
            retries=settings.llm.agent_attempts,
            request_fields=agent_request_fields(settings.llm),
        ),
        qdrant_client=ProjectQdrantClient(
            client=qdrant_client,
            collection_prefix=settings.knowledge.qdrant_collection_prefix,
            vector_dim=settings.embedding.embedding_dim,
        ),
        vision=build_vision_capability(settings=settings, http_client=http_client),
    )


def build_vision_capability(
    *,
    settings: Settings,
    http_client: httpx.AsyncClient,
) -> VisionCapability:
    """Возвращает распознавание изображений или его явную заглушку.

    Выключенный feature flag даёт no-op реализацию того же protocol, а не
    ``None``: зависимость остаётся обязательной для всех потребителей.

    Args:
        settings: Настройки приложения.
        http_client: Общий HTTP-клиент внешних API.

    Returns:
        Рабочий клиент либо выключенная реализация той же способности.
    """
    if not settings.knowledge.knowledge_vision_enabled:
        return DisabledVisionCapability()
    return VisionClient(
        http_client=http_client,
        url=settings.llm.llm_api_url,
        headers=settings.llm.headers,
        model=settings.llm.vision_model,
        timeout=settings.llm.llm_timeout,
        retries=settings.llm.llm_retries,
        max_tokens=settings.llm.vision_max_tokens,
    )


async def close_knowledge_runtime(runtime: KnowledgeRuntime) -> None:
    """Закрывает все transport-ы контейнера ровно один раз.

    Args:
        runtime: Контейнер, созданный этим же lifespan.
    """
    await runtime.qdrant_client.close()
    await runtime.http_client.aclose()
