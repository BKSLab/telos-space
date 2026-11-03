"""Живая проверка протокола Polza на синтетических данных, без БД и записей.

Из backend: python -m scripts.aris_model_preflight --live --output <внешний путь.json>
Используется только текущий ключ проекта; каждый профиль делает платные вызовы.
"""

import argparse
import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

from pydantic import BaseModel

from scripts.aris_benchmark import mentions_date
from scripts.aris_model_profiles import PROFILES, ModelProfile
from scripts.aris_usage import MeteredAsyncClient, UsageLedger
from src.agent.context import AgentContextBudget
from src.agent.runtime import TimDeps, TimRuntime
from src.agent.tim import TimMode
from src.agent.tools import AgentTool, AgentToolContext, AgentToolExecutor
from src.clients.agent_model import build_agent_model
from src.clients.llm import LlmClient
from src.clients.model_options import agent_request_fields
from src.core.settings import LlmSettings, get_settings
from src.prompts.project_agent import TIM_BASE_PROMPTS
from src.schemas.project_tool_inputs import TaskInput


class JsonProbe(BaseModel):
    task_id: int
    can_write: bool
    due_date: date


async def probe(profile: ModelProfile, base: LlmSettings) -> dict:
    """Проверяет настоящий JSON-клиент и runtime с отложенным чтением задачи."""
    settings = profile.apply(base).model_copy(update={"llm_timeout": 60})
    ledger = UsageLedger()
    result = {
        "profile": profile.name,
        "model": settings.agent_model,
        "request_fields": agent_request_fields(settings),
        "timeout_seconds": settings.agent_timeout_seconds,
        "attempts": settings.agent_attempts,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    started = monotonic()
    try:
        async with asyncio.timeout(180):
            async with MeteredAsyncClient(ledger=ledger, timeout=60) as http:
                ledger.case_id = "json"
                llm = LlmClient(
                    http_client=http,
                    url=settings.llm_api_url,
                    headers=settings.headers,
                    model=settings.agent_model,
                    timeout=settings.agent_timeout_seconds,
                    retries=settings.agent_attempts,
                    request_fields=agent_request_fields(settings),
                )
                answer = await llm.get_structured_response(
                    system_prompt="Верни только JSON с полями task_id (int), can_write (bool), due_date (YYYY-MM-DD).",
                    content="ID задачи 7. Срок 2026-11-12. Изменения не разрешены.",
                    schema=JsonProbe,
                )
                result["json_valid"] = answer == JsonProbe(
                    task_id=7, can_write=False, due_date=date(2026, 11, 12)
                )
                if not result["json_valid"]:
                    raise ValueError("JSON probe returned incorrect facts")
                reads = []

                async def read_task(context, arguments):
                    reads.append(arguments.task_id)
                    if arguments.task_id != 7:
                        return {"error": "Доступна только синтетическая задача 7."}
                    return {
                        "task": {
                            "id": 7,
                            "key": "COMPAT-7",
                            "title": "Совместимость Ариса",
                            "stage": "В работе",
                            "due_date": "2026-11-12",
                        }
                    }

                executor = AgentToolExecutor(
                    [
                        AgentTool(
                            "get_task",
                            "Прочитать синтетическую задачу по ID.",
                            TaskInput,
                            read_task,
                        ),
                    ]
                )
                mode = TimMode.WIDGET_PROJECT
                system = TIM_BASE_PROMPTS[mode]
                deps = TimDeps(
                    execution=AgentToolContext(project_id=1, user_id=1, can_write=False, mode=mode),
                    executor=executor,
                    system_prompt=system,
                    content={
                        "question": "Прочитай задачу COMPAT-7 с ID 7 инструментом get_task и назови её точный заголовок, стадию и срок. Сначала загрузи набор tasks, затем прочитай задачу. Ответ оформи через final_result.",
                        "current_date": date.today().isoformat(),
                    },
                    budget=AgentContextBudget(
                        system_prompt=system, max_tokens=12000, history_tokens=1200
                    ),
                    tool_rounds=3,
                    defer_workflows=True,
                )
                ledger.case_id = "native_tools"
                runtime = TimRuntime(model=build_agent_model(settings, http), executor=executor)
                native = await runtime.run(deps, metadata={})
                text = native.output.answer if native.output else ""
                result["tool_reads"] = reads
                result["runtime_requests"] = native.model_requests
                result["answer"] = text
                result["native_tools_valid"] = (
                    bool(reads)
                    and all(value == 7 for value in reads)
                    and mentions_date(text, "2026-11-12")
                    and "совместимость ариса" in text.casefold()
                    and "в работе" in text.casefold()
                )
                if not result["native_tools_valid"]:
                    raise ValueError("Native tools probe did not return the fixture facts")
                await ledger.resolve_routes(
                    http, base_url=settings.base_url, headers=settings.headers
                )
                if profile.name == "luna-standard":
                    result["standard_provider_confirmed"] = all(
                        str(row.get("provider") or "").lower() == "openai" for row in ledger.records
                    )
                    if not result["standard_provider_confirmed"]:
                        raise ValueError("Standard provider could not be confirmed")
                result["status"] = "passed"
    except Exception as error:
        result["status"] = "failed"
        result["error_type"] = type(error).__name__
        status_code = getattr(error, "status_code", None)
        if status_code is not None:
            result["status_code"] = status_code
    result["seconds"] = round(monotonic() - started, 3)
    result["billing"] = ledger.summary()
    result["http_requests"] = ledger.records
    return result


async def main(args) -> int:
    """Запускает выбранные профили и сохраняет только безопасные eval-артефакты."""
    if not args.live:
        raise ValueError("--live is required for paid provider calls")
    base = get_settings().llm
    if (
        urlsplit(base.llm_api_url).hostname != "polza.ai"
        or urlsplit(base.base_url).hostname != "polza.ai"
    ):
        raise ValueError("Preflight requires this project's Polza configuration")
    output = Path(args.output)
    if output.exists():
        raise ValueError("Output already exists; choose a fresh artifact path")
    output.parent.mkdir(parents=True, exist_ok=True)
    profiles = [profile for profile in PROFILES if not args.profile or profile.name in args.profile]
    payload = {"started_at": datetime.now(timezone.utc).isoformat(), "records": []}
    for profile in profiles:
        record = await probe(profile, base)
        payload["records"].append(record)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    key: record.get(key)
                    for key in ("profile", "status", "error_type", "seconds", "billing")
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return 0 if all(record["status"] == "passed" for record in payload["records"]) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--profile", action="append", choices=[profile.name for profile in PROFILES]
    )
    raise SystemExit(asyncio.run(main(parser.parse_args())))
