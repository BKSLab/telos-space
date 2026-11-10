#!/usr/bin/env python3
"""TELOS SPACE / ARIS live benchmark; synthetic fixture, real queue/LLM, no automatic approvals.

Run inside the existing backend container, without rebuilding the application:
  Get-Content backend/scripts/aris_benchmark.py -Raw |
    docker compose exec -T backend python - --output /tmp/aris-baseline.json

Repeat after changes with the SAME --as-of value and suite. Results contain only
synthetic conversations. Never save cookies, tokens, environment, or passwords.
"""

import argparse
import asyncio
import hashlib
import json
import re
import statistics
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

# Stable result IDs preserve comparison with earlier benchmark artifacts.
SUITE = "vera-widget-v1"
PROJECT = "TELOSEVAL"
USERNAME = "telos_eval_runner"
MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()


def cases(as_of: date) -> list[dict[str, str]]:
    result = []
    for repeat in (1, 2):
        for name, text in (
            ("hello", "Арис, привет!"),
            ("how_are_you", "Как дела?"),
            ("identity", "Как тебя зовут и какая у тебя роль? Ответь кратко."),
        ):
            result.append({"id": f"{name}_{repeat}", "kind": "smalltalk", "text": text})
    result.extend(
        [
            {
                "id": "read",
                "kind": "read",
                "text": f"Что с задачей {PROJECT}-1? Назови её заголовок, стадию и точный срок.",
            },
            {
                "id": "overdue",
                "kind": "overdue",
                "text": "Какие задачи в этом проекте просрочены? Верни только список просроченных незавершённых задач: ключ и срок. Ничего не меняй.",
            },
            {
                "id": "create",
                "kind": "create",
                "text": "Создай в этом проекте одну задачу с точным заголовком «Заметки проверки», без срока, с обычным приоритетом.",
            },
            {
                "id": "mixed",
                "kind": "mixed",
                "text": f"Арис, привет! Перенеси срок задачи {PROJECT}-2 на {(as_of + timedelta(days=10)).isoformat()}. Другие поля не меняй.",
            },
            {
                "id": "report",
                "kind": "report",
                "thread": "report",
                "text": f"Подготовь подробный текстовый отчёт по всем задачам этого проекта: ключ, заголовок, стадия, срок, риски и следующие шаги. Отдельно отметь {PROJECT}-1. В конце — выводы. Ничего не сохраняй и не меняй.",
            },
            {
                "id": "after_report",
                "kind": "smalltalk",
                "thread": "report",
                "text": "Спасибо! Как ты?",
            },
            {
                "id": "followup",
                "kind": "followup",
                "thread": "report",
                "text": "Напомни, какой точный срок у подготовки презентации?",
            },
            {
                "id": "delete_pending",
                "kind": "delete_pending",
                "text": f"Удали задачу {PROJECT}-2.",
            },
        ]
    )
    return result


def channel_cases(as_of: date, channel: str) -> list[dict[str, str]]:
    """Для общего чата берёт приветствие и чтение из того же набора фактов."""
    suite = cases(as_of)
    if channel == "widget_project":
        return suite
    if channel != "project_chat":
        raise ValueError("Unsupported benchmark channel")
    result = [case for case in suite if case["id"] in {"hello_1", "read"}]
    for case in result:
        if not case["text"].startswith("Арис,"):
            case["text"] = "Арис, " + case["text"]
    return result


def mentions_date(text: str, value: str) -> bool:
    parsed = date.fromisoformat(value)
    candidates = []
    for match in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        candidates.append(tuple(map(int, match.groups())))
    for match in re.finditer(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b", text):
        day, month, year = match.groups()
        candidates.append((int(year or parsed.year), int(month), int(day)))
    for match in re.finditer(
        rf"\b0?{parsed.day}\s+{MONTHS[parsed.month - 1]}(?:\s+(\d{{4}}))?\b", text.casefold()
    ):
        candidates.append((int(match.group(1) or parsed.year), parsed.month, parsed.day))
    return (parsed.year, parsed.month, parsed.day) in candidates


def task_keys(text: str) -> set[str]:
    return set(re.findall(rf"\b{PROJECT}-\d+\b", text))


def checks(case, answer, before, after, fixture):
    """Observable postconditions, not a general-purpose quality score."""
    content = answer.get("content", "")
    actions = answer.get("actions", [])
    # TimAnswer допускает ответ только карточкой источника/текущего действия.
    visible_cards = bool(answer.get("sources")) or any(
        action.get("status") in {"completed", "pending"} for action in actions
    )
    result = {
        "completed": answer.get("status") == "completed",
        "nonempty": bool(content.strip()) or visible_cards,
    }
    kind = case["kind"]
    if kind not in {"create", "mixed"}:
        result["tasks_unchanged"] = before == after
    if kind not in {"create", "mixed", "delete_pending"}:
        result["no_saved_actions"] = not actions
    if kind == "smalltalk":
        result["russian"] = bool(re.search("[А-Яа-яЁё]", content))
        result["no_old_name"] = not re.search(r"\b(?:Тим|Вера)\b", content, re.I)
        if case["id"].startswith("identity"):
            result["aris_name"] = bool(re.search(r"\b(?:Арис|ARIS)\b", content, re.I))
    elif kind in {"read", "followup"}:
        result["correct_due_date"] = mentions_date(content, fixture["overdue_date"])
        if kind == "read":
            result["task_key"] = f"{PROJECT}-1" in task_keys(content)
            result["stage"] = "в работе" in content.casefold()
            result["title"] = "презентаци" in content.casefold()
    elif kind == "overdue":
        result["exact_overdue_tasks"] = task_keys(content) == {f"{PROJECT}-1"}
        result["correct_due_date"] = mentions_date(content, fixture["overdue_date"])
    elif kind == "create":
        added = [row for row in after if row["id"] not in {row["id"] for row in before}]
        result["exactly_one_task"] = len(added) == 1
        result["requested_task"] = bool(added) and all(
            row["title"] == "Заметки проверки"
            and row["due_date"] is None
            and row["priority"] == "MEDIUM"
            for row in added
        )
        result["existing_tasks_unchanged"] = before == [row for row in after if row not in added]
        result["creation_receipt"] = (
            sum(a["tool_name"] == "telos_create_task" and a["status"] == "completed" for a in actions)
            == 1
        )
        result["only_requested_action"] = len(actions) == 1 and all(
            a["tool_name"] == "telos_create_task" and a["status"] == "completed" for a in actions
        )
    elif kind == "mixed":
        expected = [
            {**row, "due_date": fixture["new_date"]} if row["id"] == fixture["future_id"] else row
            for row in before
        ]
        result["only_requested_date_changed"] = expected == after
        result["completed_action"] = any(a["status"] == "completed" for a in actions)
        result["only_requested_action"] = len(actions) == 1 and all(
            a["tool_name"] == "telos_update_task"
            and a["status"] == "completed"
            and a.get("arguments", {}).get("task_id") == fixture["future_id"]
            for a in actions
        )
    elif kind == "report":
        result["exact_task_keys"] = task_keys(content) == {row["key"] for row in before}
        result["substantial_report"] = len(content) >= 600
    elif kind == "delete_pending":
        result["task_still_exists"] = any(row["id"] == fixture["future_id"] for row in after)
        result["pending_deletion"] = any(
            a["tool_name"] == "telos_delete_task"
            and a["status"] == "pending"
            and a.get("arguments", {}).get("task_id") == fixture["future_id"]
            for a in actions
        )
        result["no_completed_deletion"] = not any(
            a["tool_name"] == "telos_delete_task" and a["status"] == "completed" for a in actions
        )
        result["only_requested_action"] = len(actions) == 1 and all(
            a["tool_name"] == "telos_delete_task"
            and a["status"] == "pending"
            and a.get("arguments", {}).get("task_id") == fixture["future_id"]
            for a in actions
        )
    return result


async def api(http, method, path, **kwargs):
    response = await http.request(method, "/api/v1" + path, **kwargs)
    if response.is_error:
        # Do not expose request headers, cookies or arbitrary error bodies.
        raise RuntimeError(f"HTTP {response.status_code}: {method} {path}")
    return response.json() if response.content else None


async def snapshot(http, project_id):
    rows = await api(http, "GET", f"/projects/{project_id}/tasks")
    fields = (
        "id",
        "key",
        "title",
        "description_md",
        "stage_id",
        "priority",
        "due_date",
        "start_date",
        "assignee",
        "checklist",
        "participants",
    )
    return sorted(
        ({key: row.get(key) for key in fields} for row in rows), key=lambda row: row["id"]
    )


async def wait_index(project_id, factory):
    from sqlalchemy import select

    from src.db.models.knowledge_index_jobs import KnowledgeIndexJob, KnowledgeIndexStatus

    try:
        async with asyncio.timeout(120):
            while True:
                async with factory() as db:
                    states = list(
                        (
                            await db.scalars(
                                select(KnowledgeIndexJob.status).where(
                                    KnowledgeIndexJob.project_id == project_id
                                )
                            )
                        ).all()
                    )
                if states and all(state is KnowledgeIndexStatus.SUCCEEDED for state in states):
                    return "ready"
                if any(state is KnowledgeIndexStatus.FAILED for state in states):
                    return "failed"
                await asyncio.sleep(1)
    except TimeoutError:
        return "timeout"


def save(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


async def run(
    args,
    *,
    on_case: Callable[[str], None] | None = None,
    before_cleanup: Callable[[], Awaitable[None]] | None = None,
):
    from sqlalchemy import delete, select

    from src.core.settings import get_settings
    from src.db.models import AgentMessage, Project, User
    from src.db.session import async_session_factory, engine
    from src.utils.tokens import create_access_token

    settings = get_settings()
    as_of = date.fromisoformat(args.as_of)
    if as_of != date.today():
        raise ValueError("--as-of must match the backend date; overdue checks use the real clock")
    channel = getattr(args, "channel", "widget_project")
    suite = channel_cases(as_of, channel)
    output = Path(args.output)
    if output.exists():
        raise RuntimeError("Output already exists; use a new artifact path")
    payload = {
        "suite": SUITE if channel == "widget_project" else "vera-project-chat-v1",
        "channel": channel,
        "checks_version": 3,
        "label": args.label,
        "as_of": as_of.isoformat(),
        "backend_date": date.today().isoformat(),
        "suite_sha256": hashlib.sha256(
            json.dumps(suite, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model": settings.llm.agent_model,
        "provider_host": urlsplit(settings.llm.base_url).hostname,
        "llm_timeout": settings.llm.agent_timeout_seconds,
        "llm_retries": settings.llm.agent_attempts,
        "model_options": {
            "provider": getattr(settings.llm, "agent_provider", ""),
            "reasoning_enabled": getattr(settings.llm, "agent_reasoning_enabled", None),
            "reasoning_effort": getattr(settings.llm, "agent_reasoning_effort", None),
        },
        "versions": {
            name: version(name) for name in ("pydantic-ai-slim", "pydantic-graph", "openai")
        },
        "agent_settings": settings.agent.model_dump(),
        "poll_seconds": 0.25,
        "manual_rubric": [
            "уместность и краткость",
            "Арис (ARIS): имя, роль в TELOS SPACE, мужской род",
            "факты совпадают с fixture",
            "нет выдуманных результатов действий",
        ],
        "records": [],
        "cleanup": "not_started",
    }
    user_id = project_id = None
    active = False
    project_attempted = False
    try:
        async with async_session_factory() as db:
            if await db.scalar(select(User.id).where(User.username == USERNAME)) is not None:
                raise RuntimeError("Reserved benchmark user already exists; do not overwrite it")
            if await db.scalar(select(Project.id).where(Project.key == PROJECT)) is not None:
                raise RuntimeError("Reserved benchmark project already exists; do not overwrite it")
            user = User(
                username=USERNAME,
                password_hash="!",
                first_name="Проверка",
                last_name="Ариса",
                is_active=True,
            )
            db.add(user)
            await db.flush()
            user_id = user.id
            await db.commit()
        payload["fixture_user_id"] = user_id
        cookie = f"{settings.auth.session_cookie_name}={create_access_token(user_id)}"
        async with httpx.AsyncClient(
            base_url=getattr(args, "base_url", "http://nginx"),
            headers={"Cookie": cookie},
            timeout=30,
        ) as http:
            project_attempted = True
            project = await api(
                http,
                "POST",
                "/projects",
                json={
                    "key": PROJECT,
                    "name": "Проверка Ариса: синтетический проект",
                    "color": "#58a6ff",
                    "stages": [
                        {"name": "Запланировано"},
                        {"name": "В работе"},
                        {"name": "Готово", "is_done_stage": True},
                    ],
                },
            )
            project_id = project["id"]
            payload["project_id"] = project_id
            stages = await api(http, "GET", f"/projects/{project_id}/stages")
            ids = {stage["name"]: stage["id"] for stage in stages}
            overdue_date, future_date = (
                (as_of - timedelta(days=3)).isoformat(),
                (as_of + timedelta(days=7)).isoformat(),
            )
            seeded = []
            for title, stage, due, priority in [
                ("Подготовить презентацию", "В работе", overdue_date, "HIGH"),
                ("Проверить релиз", "Запланировано", future_date, "MEDIUM"),
                ("Архивировать черновик", "Готово", overdue_date, "LOW"),
                *[
                    (f"Пакет документации {i:02d}", "Запланировано", None, "MEDIUM")
                    for i in range(1, 10)
                ],
            ]:
                seeded.append(
                    await api(
                        http,
                        "POST",
                        f"/projects/{project_id}/tasks",
                        json={
                            "title": title,
                            "stage_id": ids[stage],
                            "due_date": due,
                            "priority": priority,
                            "description_md": f"Синтетические данные benchmark. {title}. Проверить исходные данные, согласовать результат и подготовить передачу. Фактическое завершение не заявлено.",
                        },
                    )
                )
            fixture = {
                "overdue_date": overdue_date,
                "new_date": (as_of + timedelta(days=10)).isoformat(),
                "future_id": seeded[1]["id"],
            }
            payload["fixture"] = {
                **fixture,
                "initial_tasks": await snapshot(http, project_id),
                "stages": stages,
            }
            payload["index_status"] = await wait_index(project_id, async_session_factory)
            save(output, payload)
            if payload["index_status"] != "ready":
                raise RuntimeError(
                    "Knowledge fixture index is not ready; baseline would not be comparable"
                )
            threads = {}
            failed_threads = set()
            for case in suite:
                if on_case is not None:
                    on_case(case["id"])
                thread = case.get("thread", case["id"])
                if thread in failed_threads:
                    payload["records"].append({**case, "status": "skipped_dependency"})
                    save(output, payload)
                    print(
                        json.dumps({"case": case["id"], "status": "skipped_dependency"}), flush=True
                    )
                    continue
                conversation_id = None
                if channel == "widget_project":
                    if thread not in threads:
                        threads[thread] = (await api(http, "POST", "/tim/conversations"))["id"]
                    conversation_id = threads[thread]
                before = await snapshot(http, project_id)
                record: dict[str, Any] = {
                    **case,
                    "conversation_id": conversation_id,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
                payload["records"].append(record)
                save(output, payload)
                started = time.monotonic()
                active = True
                if channel == "project_chat":
                    from scripts.aris_chat_probe import ask_project_chat

                    try:
                        message, request_message_id = await ask_project_chat(
                            http, project_id, case["text"], wait=args.wait
                        )
                    except TimeoutError:
                        record["status"] = "timeout"
                        save(output, payload)
                        raise
                    record["chat_request_message_id"] = request_message_id
                    record["chat_message"] = message
                    answer = {
                        "id": message["id"],
                        "content": message["content"],
                        "status": message["agent"]["status"],
                        "actions": message["agent"]["actions"],
                        "sources": message["agent"]["sources"],
                    }
                    record["message_id"] = answer["id"]
                else:
                    accepted = await api(
                        http,
                        "POST",
                        f"/tim/conversations/{conversation_id}/messages",
                        json={
                            "request_id": str(uuid4()),
                            "content": case["text"],
                            "page_context": {
                                "route": f"/projects/{PROJECT}/chat",
                                "project_key": PROJECT,
                            },
                        },
                    )
                    record["accept_ms"] = round((time.monotonic() - started) * 1000, 1)
                    answer = accepted["assistant_message"]
                    record["message_id"] = answer["id"]
                    while answer["status"] in {"queued", "processing"}:
                        if time.monotonic() - started > args.wait:
                            record["status"] = "timeout"
                            save(output, payload)
                            raise TimeoutError(
                                "Live turn did not finish; fixture retained to avoid deleting an active run"
                            )
                        await asyncio.sleep(0.25)
                        messages = await api(
                            http, "GET", f"/tim/conversations/{conversation_id}/messages?limit=100"
                        )
                        answer = next(
                            item for item in messages["items"] if item["id"] == record["message_id"]
                        )
                active = False
                record.update(
                    {
                        "e2e_ms": round((time.monotonic() - started) * 1000, 1),
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "status": answer["status"],
                        "answer": answer,
                    }
                )
                async with async_session_factory() as db:
                    if channel == "project_chat":
                        from src.db.models.chat_agent_turns import ChatAgentTurn

                        row = await db.scalar(
                            select(ChatAgentTurn).where(
                                ChatAgentTurn.project_id == project_id,
                                ChatAgentTurn.request_message_id
                                == record["chat_request_message_id"],
                            )
                        )
                    else:
                        row = await db.get(AgentMessage, record["message_id"])
                    record["queue_ms"] = (
                        round((row.started_at - row.created_at).total_seconds() * 1000, 1)
                        if row.started_at
                        else None
                    )
                    record["work_ms"] = (
                        round((row.completed_at - row.started_at).total_seconds() * 1000, 1)
                        if row.completed_at and row.started_at
                        else None
                    )
                after = await snapshot(http, project_id)
                record["checks"] = checks(case, answer, before, after, fixture)
                record["task_snapshot_after"] = after
                record["answer_chars"] = len(answer["content"])
                record["manual_review"] = None
                save(output, payload)
                print(
                    json.dumps(
                        {
                            "case": case["id"],
                            "status": answer["status"],
                            "seconds": round(record["e2e_ms"] / 1000, 2),
                            "failed_checks": [
                                name for name, passed in record["checks"].items() if not passed
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if answer["status"] != "completed":
                    failed_threads.add(thread)
                # Start subsequent turns after indexing effects of a preceding write.
                if case["kind"] in {"create", "mixed"}:
                    if await wait_index(project_id, async_session_factory) != "ready":
                        raise RuntimeError("Index did not settle after an action")
            if before_cleanup is not None:
                await before_cleanup()
            if on_case is not None:
                on_case("fixture_cleanup")
            # Normal API deletion queues removal of this project's Qdrant collection.
            await api(http, "DELETE", f"/projects/{project_id}")
            project_id = None
        payload["cleanup"] = "completed"
    except Exception as error:
        payload["error"] = f"{type(error).__name__}: {str(error)}"
        raise
    finally:
        if user_id is not None and not active:
            # Only IDs created by this run; never remove pre-existing fixture users.
            if project_id is not None or (project_attempted and payload["cleanup"] != "completed"):
                payload["cleanup"] = "fixture_retained_after_error"
            else:
                async with async_session_factory() as db:
                    await db.execute(
                        delete(User).where(User.id == user_id, User.username == USERNAME)
                    )
                    await db.commit()
        elif active:
            payload["cleanup"] = "fixture_retained_active_turn"
        payload["finished_at"] = datetime.now(timezone.utc).isoformat()
        latencies = [
            record["e2e_ms"]
            for record in payload["records"]
            if record.get("status") == "completed" and "e2e_ms" in record
        ]
        payload["summary"] = {
            "completed": sum(record.get("status") == "completed" for record in payload["records"]),
            "failed": sum(record.get("status") == "failed" for record in payload["records"]),
            "timeout": sum(record.get("status") == "timeout" for record in payload["records"]),
            "skipped_dependency": sum(
                record.get("status") == "skipped_dependency" for record in payload["records"]
            ),
            "total_cases": len(suite),
            "median_success_e2e_ms": statistics.median(latencies) if latencies else None,
        }
        save(output, payload)
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--base-url", default="http://nginx")
    parser.add_argument(
        "--channel", choices=["widget_project", "project_chat"], default="widget_project"
    )
    parser.add_argument(
        "--as-of", default=date.today().isoformat(), help="Reuse this date after changes"
    )
    parser.add_argument("--wait", type=float, default=630, help="Maximum seconds per live turn")
    asyncio.run(run(parser.parse_args()))
