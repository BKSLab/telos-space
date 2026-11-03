"""Живое сравнение Ариса в отдельном PostgreSQL/Redis/Qdrant, без изменения .env.

Из backend: python -m scripts.aris_model_compare --live --output-dir <внешний путь>
Каждый профиль запускается в отдельном процессе с настоящими API, очередью и LLM.
Секреты остаются в памяти; временный пароль БД передаётся только через окружение.
"""

import argparse
import asyncio
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import asyncpg
import httpx

from scripts import aris_benchmark
from scripts.aris_model_profiles import PROFILES
from scripts.aris_usage import MeteredAsyncClient, UsageLedger


def save(path: Path, payload: dict) -> None:
    """Сохраняет публичные параметры eval и результаты на синтетических данных."""
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def docker(*arguments: str, env: dict | None = None, required: bool = True) -> str:
    """Вызывает Docker без shell и без значений секретов в argv."""
    result = subprocess.run(["docker", *arguments], env=env, capture_output=True, text=True)
    if required and result.returncode:
        raise RuntimeError(f"Docker {arguments[0]} failed (exit {result.returncode})")
    return result.stdout.strip()


def start_stack() -> tuple[list[str], dict[str, str], dict]:
    """Создаёт временные контейнеры, не подключая существующие данные и тома."""
    created: list[str] = []
    marker = "vera-eval-" + secrets.token_hex(5)
    child_env = os.environ.copy()
    child_env["POSTGRES_PASSWORD"] = secrets.token_hex(24)
    ports: dict[str, int] = {}
    try:
        for service, image, internal, extra in [
            (
                "postgres",
                "postgres:16-alpine",
                5432,
                [
                    "--env",
                    "POSTGRES_USER=vera_eval",
                    "--env",
                    "POSTGRES_DB=postgres",
                    "--env",
                    "POSTGRES_PASSWORD",
                ],
            ),
            ("redis", "redis:7.4-alpine", 6379, []),
            ("qdrant", "qdrant/qdrant:v1.12.6", 6333, []),
        ]:
            container = docker(
                "run",
                "--detach",
                "--rm",
                "--name",
                marker + "-" + service,
                "--publish",
                f"127.0.0.1::{internal}",
                *extra,
                image,
                env=child_env,
            )
            created.append(container)
            address = docker("port", container, str(internal)).splitlines()[0]
            if not address.startswith("127.0.0.1:"):
                raise RuntimeError("Eval service must bind only to loopback")
            ports[service] = int(address.rsplit(":", 1)[1])
        for _ in range(60):
            ready = docker(
                "exec",
                created[0],
                "pg_isready",
                "-U",
                "vera_eval",
                "-d",
                "postgres",
                required=False,
            )
            redis_ready = docker("exec", created[1], "redis-cli", "ping", required=False) == "PONG"
            try:
                qdrant_ready = (
                    httpx.get(f"http://127.0.0.1:{ports['qdrant']}/readyz", timeout=1).status_code
                    == 200
                )
            except httpx.HTTPError:
                qdrant_ready = False
            if "accepting connections" in ready and redis_ready and qdrant_ready:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Eval dependencies did not become ready")
        child_env.update(
            POSTGRES_HOST="127.0.0.1",
            POSTGRES_PORT=str(ports["postgres"]),
            POSTGRES_USER="vera_eval",
            CHAT_REDIS_URL=f"redis://127.0.0.1:{ports['redis']}/0",
            QDRANT_URL=f"http://127.0.0.1:{ports['qdrant']}",
            QDRANT_COLLECTION_PREFIX="vera_eval",
            PYTHONUNBUFFERED="1",
            PYTHONUTF8="1",
            PYDANTIC_AI_NO_BANNER="1",
            VERA_EVAL_STACK_MARKER=marker,
            VERA_EVAL_CONTAINERS=json.dumps(
                dict(zip(("postgres", "redis", "qdrant"), created, strict=True))
            ),
        )
        return created, child_env, {"containers": created, "ports": ports, "marker": marker}
    except BaseException:
        for container in reversed(created):
            docker("stop", container, required=False)
        raise


async def create_database() -> None:
    """Создаёт пустую БД только во временном PostgreSQL этого eval."""
    from src.core.settings import get_settings

    settings = get_settings().db
    name = settings.postgres_name
    if not name.startswith("vera_eval_") or not name.replace("_", "").isalnum():
        raise ValueError("Refusing to create an unexpected eval database")
    connection = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        database="postgres",
    )
    try:
        await connection.execute(f'CREATE DATABASE "{name}"')
    finally:
        await connection.close()


async def run_child(args) -> int:
    """Один backend/worker и benchmark используют один объект настроек процесса."""
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    from src.core.settings import get_settings

    settings = get_settings()
    selected = next(profile for profile in PROFILES if profile.name == args.one_profile)
    settings.llm = selected.apply(settings.llm)
    from src import main as application

    ledger = UsageLedger()
    ledger.case_id = "fixture_setup"

    def tracked_http(config):
        limits = httpx.Limits(
            max_connections=config.http_client.http_max_connections,
            max_keepalive_connections=config.http_client.http_max_keepalive_connections,
            keepalive_expiry=config.http_client.http_keepalive_expiry,
        )
        return MeteredAsyncClient(
            ledger=ledger, timeout=float(config.llm.llm_timeout), limits=limits
        )

    application.create_http_client = tracked_http
    with socket.socket() as probe_socket:
        probe_socket.bind(("127.0.0.1", 0))
        port = probe_socket.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    config = Config()
    config.bind = [f"127.0.0.1:{port}"]
    config.accesslog = None
    stop = asyncio.Event()
    server = asyncio.create_task(serve(application.app, config, shutdown_trigger=stop.wait))

    def on_case(case_id):
        ledger.case_id = case_id

    async def quiesce():
        """Даёт фоновым summary закончить списания до удаления тестовых диалогов."""
        last_count, quiet_since = len(ledger.records), time.monotonic()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            count = len(ledger.records)
            active = any(row.get("in_flight", False) for row in ledger.records)
            if count != last_count or active:
                last_count, quiet_since = count, time.monotonic()
            if time.monotonic() - quiet_since >= 5:
                return
            await asyncio.sleep(0.25)
        raise TimeoutError("Background model calls did not settle before fixture cleanup")

    output = Path(args.child_output)
    failed = False
    try:
        async with httpx.AsyncClient(timeout=3) as health:
            for _ in range(240):
                if server.done():
                    await server
                    raise RuntimeError("Eval server stopped before readiness")
                try:
                    ready = await health.get(base_url + "/health/ready")
                    if ready.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
            else:
                raise TimeoutError("Eval backend readiness timeout")
        await aris_benchmark.run(
            SimpleNamespace(
                as_of=date.today().isoformat(),
                output=str(output),
                label=selected.name,
                wait=630,
                base_url=base_url,
                channel=getattr(args, "channel", "widget_project"),
            ),
            on_case=on_case,
            before_cleanup=quiesce,
        )
    except Exception as error:
        failed = True
        print(json.dumps({"phase": "child_error", "type": type(error).__name__}), flush=True)
    finally:
        stop.set()
        await asyncio.wait_for(server, timeout=30)
        async with httpx.AsyncClient(timeout=20) as history:
            await ledger.resolve_routes(
                history, base_url=settings.llm.base_url, headers=settings.llm.headers
            )
        usage_path = output.with_name(output.stem + "-usage.json")
        save(
            usage_path,
            {
                "profile": selected.name,
                "model": settings.llm.agent_model,
                "summary": ledger.summary(),
                "records": ledger.records,
            },
        )
        if output.exists():
            payload = json.loads(output.read_text(encoding="utf-8"))
            payload["billing"] = ledger.summary()
            observed = {
                row["requested_model"]
                for row in ledger.records
                if row["endpoint"].endswith("chat/completions")
            }
            payload["runtime_config_confirmed"] = observed == {settings.llm.agent_model}
            payload["billing_artifact"] = usage_path.name
            save(output, payload)
    return 1 if failed else 0


def validate_preflight(profiles, paths: list[str]) -> None:
    """Разрешает полный прогон только проверенных конфигураций текущего проекта."""
    from src.clients.model_options import agent_request_fields
    from src.core.settings import get_settings

    if not paths:
        raise ValueError("A passed preflight artifact is required before model comparison")
    records = {}
    for path in paths:
        for row in json.loads(Path(path).read_text(encoding="utf-8"))["records"]:
            records[row["profile"]] = row
    settings = get_settings().llm
    for profile in profiles:
        expected = profile.apply(settings)
        row = records.get(profile.name, {})
        if not (
            row.get("status") == "passed"
            and row.get("json_valid") is True
            and row.get("native_tools_valid") is True
            and row.get("model") == expected.agent_model
            and row.get("request_fields") == agent_request_fields(expected)
            and (profile.name != "luna-standard" or row.get("standard_provider_confirmed") is True)
        ):
            raise ValueError(f"No matching passed preflight for {profile.name}")


def validate_child_isolation() -> None:
    """Проверяет имена и loopback-порты именно временных контейнеров родителя."""
    from src.core.settings import get_settings

    marker = os.environ.get("VERA_EVAL_STACK_MARKER", "")
    if not re.fullmatch(r"vera-eval-[0-9a-f]{10}", marker):
        raise ValueError("Child requires its parent's isolated eval stack")
    containers = json.loads(os.environ.get("VERA_EVAL_CONTAINERS", "{}"))
    settings = get_settings()
    redis = urlsplit(settings.chat.chat_redis_url)
    qdrant = urlsplit(settings.knowledge.qdrant_url)
    if (
        settings.db.postgres_host != "127.0.0.1"
        or redis.hostname != "127.0.0.1"
        or qdrant.hostname != "127.0.0.1"
    ):
        raise ValueError("Eval dependencies must use loopback")
    if (
        settings.knowledge.qdrant_collection_prefix != settings.db.postgres_name
        or not settings.db.postgres_name.startswith("vera_eval_")
    ):
        raise ValueError("Eval database and collection namespace must match")
    for service, internal, port in [
        ("postgres", 5432, settings.db.postgres_port),
        ("redis", 6379, redis.port),
        ("qdrant", 6333, qdrant.port),
    ]:
        container = containers.get(service, "")
        if not re.fullmatch(r"[0-9a-f]{64}", container):
            raise ValueError("Missing owned eval container")
        if docker("inspect", "--format", "{{.Name}}", container) != "/" + marker + "-" + service:
            raise ValueError("Unexpected eval container ownership")
        if docker("port", container, str(internal)) != f"127.0.0.1:{port}":
            raise ValueError("Unexpected eval container port")


def main(args) -> int:
    """Выполняет заданные повторы; при техническом сбое сохраняет отдельный eval stack."""
    if not args.live:
        raise ValueError("--live is required for paid model comparison")
    if args.one_profile:
        from alembic import command
        from alembic.config import Config

        selected = [profile for profile in PROFILES if profile.name == args.one_profile]
        validate_preflight(selected, args.preflight or [])
        validate_child_isolation()
        asyncio.run(create_database())
        command.upgrade(Config("alembic.ini"), "head")
        return asyncio.run(run_child(args))
    selected = [profile for profile in PROFILES if not args.profile or profile.name in args.profile]
    validate_preflight(selected, args.preflight or [])
    output = Path(args.output_dir).resolve()
    if output.exists():
        raise ValueError("Output directory already exists; choose a fresh round directory")
    output.mkdir(parents=True)
    containers, environment, stack = start_stack()
    payload = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "preflight_artifacts": args.preflight,
        "channel": args.channel,
        "stack": stack,
        "runs": [],
        "stack_retained": False,
    }
    retain = False
    try:
        for repetition in range(1, args.repeat + 1):
            for profile in selected:
                stem = f"{profile.name}-attempt{repetition:02d}"
                run_env = {
                    **environment,
                    "POSTGRES_NAME": "vera_eval_"
                    + profile.name.replace("-", "_")
                    + f"_{repetition}",
                }
                run_env["QDRANT_COLLECTION_PREFIX"] = run_env["POSTGRES_NAME"]
                files = output / (stem + "-files")
                files.mkdir()
                run_env.update(UPLOADS_PATH=str(files), AVATARS_PATH=str(files / "avatars"))
                print(
                    json.dumps(
                        {"profile": profile.name, "repetition": repetition, "phase": "started"}
                    ),
                    flush=True,
                )
                with (output / (stem + ".log")).open("w", encoding="utf-8") as log:
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "scripts.aris_model_compare",
                            "--live",
                            "--one-profile",
                            profile.name,
                            "--child-output",
                            str(output / (stem + ".json")),
                            "--channel",
                            args.channel,
                            *[
                                part
                                for path in args.preflight
                                for part in ("--preflight", str(Path(path).resolve()))
                            ],
                        ],
                        env=run_env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                retain = retain or result.returncode != 0
                record = {
                    "profile": profile.name,
                    "repetition": repetition,
                    "exit_code": result.returncode,
                    "artifact": stem + ".json",
                }
                payload["runs"].append(record)
                save(output / "round.json", payload)
                print(json.dumps(record), flush=True)
    finally:
        payload["stack_retained"] = retain
        if not retain:
            for container in reversed(containers):
                docker("stop", container, required=False)
        payload["finished_at"] = datetime.now(timezone.utc).isoformat()
        save(output / "round.json", payload)
    return 1 if retain else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output-dir")
    parser.add_argument(
        "--profile", action="append", choices=[profile.name for profile in PROFILES]
    )
    parser.add_argument("--repeat", type=int, default=1, choices=range(1, 4))
    parser.add_argument("--preflight", action="append")
    parser.add_argument(
        "--channel", choices=["widget_project", "project_chat"], default="widget_project"
    )
    parser.add_argument(
        "--one-profile", choices=[profile.name for profile in PROFILES], help=argparse.SUPPRESS
    )
    parser.add_argument("--child-output", help=argparse.SUPPRESS)
    raise SystemExit(main(parser.parse_args()))
