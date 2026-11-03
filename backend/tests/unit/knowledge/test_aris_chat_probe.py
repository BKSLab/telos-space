"""Общий чат оценивается по ответу нужного запроса, а не по старому сообщению."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from scripts.aris_chat_probe import ask_project_chat


async def test_chat_probe_waits_for_its_own_completed_reply_and_sends_only_once(monkeypatch):
    monkeypatch.setattr("scripts.aris_chat_probe.asyncio.sleep", AsyncMock())
    requests = []

    def reply(request):
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/api/v1/projects/1/chat/messages"
            assert json.loads(request.content)["content"] == "Арис, привет!"
            return httpx.Response(201, json={"id": 7})
        assert request.url.params["limit"] == "100"
        own = {
            "id": 8,
            "author_type": "AGENT",
            "content": "Привет!",
            "agent": {
                "request_message_id": 7,
                "status": "processing" if len(requests) == 2 else "completed",
            },
        }
        old = {
            "id": 3,
            "author_type": "AGENT",
            "content": "Старый ответ",
            "agent": {"request_message_id": 2, "status": "completed"},
        }
        return httpx.Response(200, json={"messages": [old, own]})

    async with httpx.AsyncClient(
        base_url="http://eval.test", transport=httpx.MockTransport(reply)
    ) as http:
        message, request_id = await ask_project_chat(http, 1, "Арис, привет!", wait=2)
    assert message["id"] == 8 and request_id == 7
    assert message["agent"]["status"] == "completed"
    assert [request.method for request in requests] == ["POST", "GET", "GET"]


async def test_chat_probe_timeout_does_not_claim_a_successful_response():
    sent = []

    def reply(request):
        sent.append(request.method)
        return httpx.Response(201, json={"id": 7})

    async with httpx.AsyncClient(
        base_url="http://eval.test", transport=httpx.MockTransport(reply)
    ) as http:
        with pytest.raises(TimeoutError, match="retain the active fixture"):
            await ask_project_chat(http, 1, "Арис, привет!", wait=0)
    assert sent == ["POST"]
