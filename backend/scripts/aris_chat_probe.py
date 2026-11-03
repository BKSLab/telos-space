"""Ожидание ответа Ариса через настоящий контракт общего проектного чата."""

import asyncio
from time import monotonic
from uuid import uuid4

import httpx

from scripts.aris_benchmark import api


async def ask_project_chat(
    http: httpx.AsyncClient, project_id: int, question: str, *, wait: float
) -> tuple[dict, int]:
    """Отправляет одно обращение и ждёт именно связанный с ним ответ агента.

    Args:
        http: Клиент с сессией владельца синтетического проекта benchmark.
        project_id: ID проекта, созданного текущим eval.
        question: Обращение с префиксом «Арис, …».
        wait: Предельное число секунд ожидания.

    Returns:
        Итоговое сообщение агента и ID сообщения участника.
    """
    path = f"/projects/{project_id}/chat/messages"
    sent = await api(
        http, "POST", path, json={"content": question, "client_message_id": str(uuid4())}
    )
    deadline = monotonic() + wait
    while monotonic() < deadline:
        page = await api(http, "GET", path + "?limit=100")
        reply = next(
            (
                message
                for message in page["messages"]
                if message.get("author_type") == "AGENT"
                and (message.get("agent") or {}).get("request_message_id") == sent["id"]
            ),
            None,
        )
        if reply is not None and reply["agent"]["status"] in {"completed", "failed"}:
            return reply, sent["id"]
        await asyncio.sleep(0.25)
    raise TimeoutError("Project chat turn did not finish; retain the active fixture")
