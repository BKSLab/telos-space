"""Локальный учёт usage всех HTTP-попыток eval Ариса, без заголовков и промптов."""

import asyncio
import json
import math
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx


class UsageLedger:
    """Hooks общего HTTP-клиента: включая JSON, embeddings, фоновые вызовы и retries."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.case_id: str | None = None

    async def on_request(self, request: httpx.Request) -> None:
        """Регистрирует попытку до HTTP; payload и credentials не сохраняются."""
        try:
            payload = json.loads(request.content)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("model"), str):
            return
        record = {
            "index": len(self.records),
            "case_id": self.case_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "requested_model": payload["model"],
            "endpoint": request.url.path,
            "options": {key: payload[key] for key in ("provider", "reasoning") if key in payload},
            "status_code": None,
            "in_flight": True,
            "cost_rub": None,
            "tools_offered": len(payload.get("tools") or []),
            "request_kind": "embeddings"
            if request.url.path.endswith("embeddings")
            else "structured"
            if payload.get("response_format")
            else "runtime",
        }
        self.records.append(record)
        request.extensions["vera_usage"] = (record, time.monotonic())

    async def on_response(self, response: httpx.Response) -> None:
        """Сохраняет только usage и идентификаторы из ответа провайдера."""
        attached = response.request.extensions.get("vera_usage")
        if attached is None:
            return
        record, started = attached
        await response.aread()
        record.update(
            status_code=response.status_code,
            in_flight=False,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        if response.status_code == 429:
            record["rate_limit_headers"] = {
                key: value
                for key, value in response.headers.items()
                if key == "retry-after" or key.startswith("x-ratelimit-")
            }
        try:
            data = response.json()
        except ValueError:
            record["response_json_valid"] = False
            return
        if not isinstance(data, dict):
            return
        choices = data.get("choices") or []
        message = (
            (choices[0].get("message") or {}) if choices and isinstance(choices[0], dict) else {}
        )
        if not isinstance(message, dict):
            message = {}
        record["tool_calls"] = [
            call.get("function", {}).get("name")
            for call in (message.get("tool_calls") or [])
            if isinstance(call, dict) and isinstance(call.get("function"), dict)
        ]
        for name in ("id", "model", "provider"):
            value = data.get(name)
            record[name] = value[:200] if isinstance(value, str) else None
        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(name)
            record[name] = value if type(value) is int and value >= 0 else None
        for group, name in (
            ("prompt_tokens_details", "cached_tokens"),
            ("completion_tokens_details", "reasoning_tokens"),
        ):
            details = usage.get(group) or {}
            value = details.get(name) if isinstance(details, dict) else None
            record[name] = value if type(value) is int and value >= 0 else None
        cost = usage.get("cost_rub", usage.get("cost"))
        try:
            parsed = float(cost) if not isinstance(cost, bool) else math.nan
            record["cost_rub"] = parsed if math.isfinite(parsed) and parsed >= 0 else None
        except (TypeError, ValueError):
            pass
        error = data.get("error") or {}
        if isinstance(error, dict):
            record["error_code"] = error.get("code")
            metadata = error.get("metadata") or {}
            if isinstance(metadata, dict):
                record["error_reason"] = metadata.get("reason")
                record["error_provider"] = metadata.get("provider_name")

    def summary(self) -> dict[str, Any]:
        """Суммирует уже списанные расходы; неизвестная стоимость не считается нулём."""
        known = [row["cost_rub"] for row in self.records if row["cost_rub"] is not None]
        return {
            "http_attempts": len(self.records),
            "known_cost_rub": round(sum(known), 8),
            "unknown_cost_attempts": len(self.records) - len(known),
            "cost_complete": len(known) == len(self.records),
        }

    def on_transport_error(self, request: httpx.Request, error: BaseException) -> None:
        """Завершает попытку без HTTP-ответа, оставляя её списание неизвестным."""
        attached = request.extensions.get("vera_usage")
        if attached is None:
            return
        record, started = attached
        record.update(
            in_flight=False,
            transport_error=type(error).__name__,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )

    async def resolve_routes(
        self, http: httpx.AsyncClient, *, base_url: str, headers: dict[str, str]
    ) -> None:
        """Подтверждает маршрут по истории только собственных generation IDs.

        Polza может не возвращать provider в chat completion. Запрос истории
        выполняется после замера, без сохранения данных аккаунта и промптов.
        """
        for record in self.records:
            if record.get("provider") or not record.get("id"):
                continue
            try:
                response = await http.get(
                    base_url.rstrip("/") + "/history/generations/" + quote(record["id"], safe=""),
                    headers=headers,
                )
                if response.status_code != 200:
                    record["route_lookup_status"] = response.status_code
                    continue
                value = response.json().get("finalEndpointSlug")
                if isinstance(value, str):
                    record["provider"] = value
                    record["provider_source"] = "generation_history"
            except (httpx.HTTPError, ValueError):
                record["route_lookup_status"] = "unavailable"


class MeteredAsyncClient(httpx.AsyncClient):
    """Общий eval-клиент отмечает также завершение ошибки транспорта и отмены."""

    def __init__(self, *, ledger: UsageLedger, **kwargs) -> None:
        self.ledger = ledger
        hooks = kwargs.pop("event_hooks", {})
        hooks["request"] = [*hooks.get("request", []), ledger.on_request]
        hooks["response"] = [*hooks.get("response", []), ledger.on_response]
        super().__init__(event_hooks=hooks, **kwargs)

    async def send(self, request: httpx.Request, *args, **kwargs) -> httpx.Response:
        """Сохраняет семантику HTTPX, включая передачу отмены вызывающей стороне."""
        try:
            return await super().send(request, *args, **kwargs)
        except (httpx.RequestError, asyncio.CancelledError) as error:
            self.ledger.on_transport_error(request, error)
            raise
