"""Сигнал исполнителям очереди Ариса: появился вопрос — не ждать конца опроса.

Очередь хранится в PostgreSQL, исполнители опрашивают её раз в
``AGENT_POLL_SECONDS``. Вопрос, поставленный этим же процессом, будит их сразу
после commit — ответ не стоит в очереди до секунды впустую. Сигнал только
подсказка: пропущенный сигнал (другой процесс, сбой) подхватит обычный опрос.
"""

import asyncio

_listeners: set[asyncio.Event] = set()


def wake_agent_worker() -> None:
    """Будит ждущих исполнителей; вызывать после commit постановки в очередь."""
    for event in list(_listeners):
        event.set()


def listen() -> asyncio.Event:
    """Регистрирует сигнал одного исполнителя; снять — ``forget``."""
    event = asyncio.Event()
    _listeners.add(event)
    return event


def forget(event: asyncio.Event) -> None:
    """Снимает сигнал остановленного исполнителя."""
    _listeners.discard(event)
