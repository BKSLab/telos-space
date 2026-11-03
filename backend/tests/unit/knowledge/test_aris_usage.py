"""Расход eval включает повторы и embeddings, неизвестное списание не обнуляется."""

import json

import httpx
import pytest

from scripts.aris_usage import MeteredAsyncClient, UsageLedger


async def test_ledger_accounts_for_charged_failures_retries_and_unknown_transport_cost():
    ledger = UsageLedger()
    responses = [
        (200, 0),
        (503, 0.02),
        (200, 0.04),
        (200, 0.03),
    ]

    def reply(request):
        if len(ledger.records) == 5:
            raise httpx.ConnectError("synthetic transport error", request=request)
        status, cost = responses[len(ledger.records) - 1]
        return httpx.Response(
            status,
            json={
                "id": f"gen-{len(ledger.records)}",
                "model": "qwen/qwen3.8-flash",
                "provider": "alibaba",
                "choices": [{"message": {"content": "Готово", "tool_calls": None}}],
                "usage": {
                    "cost_rub": cost,
                    "prompt_tokens": 20,
                    "completion_tokens": 5,
                    "total_tokens": 25,
                    "prompt_tokens_details": {"cached_tokens": 10},
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
            },
        )

    async with MeteredAsyncClient(
        ledger=ledger,
        transport=httpx.MockTransport(reply),
    ) as http:
        for index in range(5):
            ledger.case_id = "case-1"
            request = http.build_request(
                "POST",
                "https://polza.ai/api/v1/embeddings"
                if index == 3
                else "https://polza.ai/api/v1/chat/completions",
                headers={"Authorization": "Bearer PRIVATE_KEY"},
                json={
                    "model": "qwen/qwen3.8-flash",
                    "messages": [{"role": "user", "content": "PRIVATE_PROMPT"}],
                },
            )
            if index == 4:
                with pytest.raises(httpx.ConnectError):
                    await http.send(request)
            else:
                await http.send(request)
    assert ledger.summary() == {
        "http_attempts": 5,
        "known_cost_rub": 0.09,
        "unknown_cost_attempts": 1,
        "cost_complete": False,
    }
    assert ledger.records[0]["cost_rub"] == 0
    assert ledger.records[1]["status_code"] == 503
    assert ledger.records[-1]["status_code"] is None
    assert ledger.records[-1]["in_flight"] is False
    assert ledger.records[-1]["transport_error"] == "ConnectError"
    assert not any(row["in_flight"] for row in ledger.records)
    assert ledger.records[0]["completion_tokens"] == 5
    assert ledger.records[0]["reasoning_tokens"] == 3
    assert ledger.records[0]["cached_tokens"] == 10
    serialized = json.dumps(ledger.records)
    assert "PRIVATE_KEY" not in serialized and "PRIVATE_PROMPT" not in serialized
    assert all(record["case_id"] == "case-1" for record in ledger.records)


async def test_routes_are_resolved_only_for_recorded_generation_ids_without_account_data():
    ledger = UsageLedger()
    ledger.records = [
        {"id": "gen-own", "provider": None, "cost_rub": 0.01},
        {"id": None, "provider": None, "cost_rub": None},
    ]
    requested = []

    def reply(request):
        requested.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "finalEndpointSlug": "OpenAI",
                "organizationId": "PRIVATE_ORG",
                "apiKeyId": "PRIVATE_KEY_ID",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        await ledger.resolve_routes(
            http, base_url="https://polza.ai/api/v1", headers={"Authorization": "Bearer test"}
        )
    assert requested == ["https://polza.ai/api/v1/history/generations/gen-own"]
    assert ledger.records[0]["provider"] == "OpenAI"
    assert ledger.records[0]["provider_source"] == "generation_history"
    assert "PRIVATE_" not in json.dumps(ledger.records)
