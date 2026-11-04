"""Реестр инструментов MCP: имя, право из ``api_scopes`` и признак записи.

Каждый инструмент объявляется декоратором ``tt_tool`` — он регистрирует
его на сервере и записывает, какое право ключа он требует. Проверка права
выполняется централизованно в ``tool_context``: декоратор кладёт описание
вызываемого инструмента в переменную контекста, а контекст инструмента
сверяет его с правами предъявленного ключа. Инструмент без записи в
реестре вызвать нельзя — ``tool_context`` откажет (закрыто по умолчанию).

Все имена начинаются с ``tt_``: агент внешнего клиента сливает инструменты
нескольких MCP-серверов, и у bitrix24-mcp есть свои ``create_task``.

По этому же реестру собирается единый каталог справки
(``python -m src.integration_docs``).
"""

import functools
import inspect
import logging
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from src.utils.api_scopes import API_SCOPES, WRITE_SCOPES

logger = logging.getLogger(__name__)

TOOL_PREFIX = "tt_"
#: Параметр «от чьего имени» у пишущих инструментов.
ACTOR_PARAM = "actor"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Описание инструмента для проверки прав и справки.

    Attributes:
        name: Имя инструмента с префиксом ``tt_``.
        title: Короткое название.
        description: Что делает инструмент (видит агент).
        scope: Право из реестра ``api_scopes``.
        write: Меняет ли данные.
    """

    name: str
    title: str
    description: str
    scope: str
    write: bool


@dataclass(slots=True)
class ToolCall:
    """Состояние одного вызова: кто вызвал — заполняет ``tool_context``."""

    spec: ToolSpec
    actor: str | None
    started: float
    started_at: datetime
    legacy: bool = False
    principal: Any = None
    session_factory: Any = None
    params: dict = field(default_factory=dict)


TOOL_REGISTRY: dict[str, ToolSpec] = {}
_current_call: ContextVar[ToolCall | None] = ContextVar("mcp_tool_call", default=None)
_legacy_call: ContextVar[bool] = ContextVar("mcp_legacy_call", default=False)


def current_tool_call() -> ToolCall | None:
    """Описание инструмента, который выполняется сейчас."""
    return _current_call.get()


def tt_tool(
    *,
    name: str,
    title: str,
    description: str,
    scope: str,
    write: bool | None = None,
) -> Callable:
    """Регистрирует инструмент на сервере вместе с его правом.

    Args:
        name: Имя инструмента, обязательно с префиксом ``tt_``.
        title: Короткое название.
        description: Описание для агента.
        scope: Требуемое право из ``api_scopes``.
        write: Меняет ли данные; по умолчанию — если право пишущее.

    Raises:
        ValueError: Ошибка объявления — без префикса, с неизвестным правом,
            повтором имени или пишущий инструмент без параметра ``actor``.
            Падает импорт модуля, а не вызов у агента.
    """
    from src.mcp_server.server import mcp_server

    if not name.startswith(TOOL_PREFIX):
        raise ValueError(f"Инструмент {name}: имя обязано начинаться с {TOOL_PREFIX}.")
    if scope not in API_SCOPES:
        raise ValueError(f"Инструмент {name}: неизвестное право {scope}.")
    if name in TOOL_REGISTRY:
        raise ValueError(f"Инструмент {name} объявлен дважды.")
    is_write = scope in WRITE_SCOPES if write is None else write
    description = f"{description} Требует право {scope}."
    spec = ToolSpec(name=name, title=title, description=description, scope=scope, write=is_write)

    def decorator(fn: Callable) -> Callable:
        parameters = inspect.signature(fn).parameters
        if is_write and ACTOR_PARAM not in parameters:
            raise ValueError(f"Инструмент {name} меняет данные, но не принимает {ACTOR_PARAM}.")

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            actor = kwargs.get(ACTOR_PARAM)
            call = ToolCall(
                spec=spec,
                legacy=_legacy_call.get(),
                actor=actor.strip() if isinstance(actor, str) and actor.strip() else None,
                started=time.perf_counter(),
                started_at=datetime.now(UTC),
                params={
                    key: value
                    for key, value in kwargs.items()
                    if key != "context" and isinstance(value, str | int | float | bool)
                },
            )
            token = _current_call.set(call)
            status = 200
            try:
                return await fn(*args, **kwargs)
            except ToolError:
                status = 400
                raise
            except Exception:
                status = 500
                raise
            finally:
                _current_call.reset(token)
                try:
                    await _record_usage(call, status)
                except Exception:
                    logger.warning("Не удалось записать статистику MCP-вызова.", exc_info=True)

        TOOL_REGISTRY[name] = spec
        mcp_server.tool(name=name, title=title, description=description)(wrapper)
        legacy_name = name.removeprefix("tt_")
        legacy_names = {
            "list_project_tools", "execute_project_tool", "list_project_risks", "get_project_risk",
            "create_project_risk", "update_project_risk", "list_projects", "get_project", "list_tasks",
            "get_task", "list_comments", "search_tasks", "search_project_knowledge", "read_project_knowledge",
            "get_calendar_range", "list_tasks_without_due_date", "list_milestones", "create_task", "update_task",
            "move_task", "delete_task", "add_comment", "set_task_dates", "create_milestone",
        }
        if legacy_name in legacy_names:
            @functools.wraps(wrapper)
            async def legacy_wrapper(*args, **kwargs):
                legacy_token = _legacy_call.set(True)
                try:
                    result = await wrapper(*args, **kwargs)
                    if legacy_name == "list_tasks" and isinstance(result, dict):
                        return result["items"]
                    if legacy_name == "list_comments" and isinstance(result, list):
                        return [{key: value for key, value in item.items() if key != "comment_id"} for item in result]
                    if legacy_name == "add_comment" and isinstance(result, dict):
                        return {key: value for key, value in result.items() if key != "comment_id"}
                    return result
                finally:
                    _legacy_call.reset(legacy_token)
            mcp_server.tool(name=legacy_name, title=title, description=description)(legacy_wrapper)
        # Наружу отдаётся обёртка: прямой вызов из кода и тестов проходит ту же
        # проверку права, что и вызов агента через транспорт.
        return wrapper

    return decorator


async def _record_usage(call: ToolCall, status: int) -> None:
    """Пишет вызов инструмента в журнал ключа, если вызов был по ключу."""
    principal = call.principal
    api_key = getattr(principal, "api_key", None)
    if api_key is None or call.session_factory is None:
        return
    from src.dependencies.scopes import build_api_key_usage_recorder

    await build_api_key_usage_recorder(call.session_factory)(
        api_key_id=api_key.key_id,
        key_prefix=api_key.key_prefix,
        at=call.started_at,
        channel="mcp",
        method="CALL",
        route=call.spec.name,
        path=call.spec.name,
        query=call.params,
        actor_login=principal.username,
        status_code=status,
        duration_ms=int((time.perf_counter() - call.started) * 1000),
        response_bytes=None,
        client_ip=None,
    )


def tool_catalog() -> list[dict]:
    """Сокращённый программный перечень основных tt_* инструментов.

    Параметры берутся из входной схемы, которую видит агент.
    Полный каталог справки с aliases и HTTP экспортируется ``integration_docs``.
    """
    from src.mcp_server.server import mcp_server

    tools = {tool.name: tool for tool in mcp_server._tool_manager.list_tools()}
    catalog = []
    for name in sorted(TOOL_REGISTRY):
        spec = TOOL_REGISTRY[name]
        schema = tools[name].parameters
        required = set(schema.get("required", []))
        defs = schema.get("$defs", {})
        params = []
        for param, body in schema.get("properties", {}).items():
            item = {
                "name": param,
                "type": _type_of(body, defs),
                "required": param in required,
                "description": body.get("description", ""),
            }
            # Значение по умолчанию — то, что подставит сервер; справка показывает его
            # рядом с типом, как в справочнике внешнего клиента.
            if body.get("default") is not None:
                item["default"] = body["default"]
            params.append(item)
        catalog.append(
            {
                "name": name,
                "scope": spec.scope,
                "write": spec.write,
                "summary": spec.title,
                "description": spec.description,
                "params": params,
            }
        )
    return catalog


def _type_of(body: dict, defs: dict | None = None) -> str:
    """Короткое имя типа параметра из JSON Schema.

    Перечисление, вынесенное в ``$defs``, раскрывается в список значений
    (``LOW | MEDIUM | HIGH``): агенту и читателю справки нужны сами значения,
    а не слово «object».
    """
    defs = defs or {}
    if "enum" in body:
        return " | ".join(str(value) for value in body["enum"])
    if "type" in body:
        kind = body["type"]
        if kind == "array":
            return f"array<{_type_of(body.get('items', {}), defs)}>"
        return str(kind)
    variants = [
        _type_of(item, defs) for item in body.get("anyOf", []) if item.get("type") != "null"
    ]
    if variants:
        return " | ".join(dict.fromkeys(variants))
    if "$ref" in body:
        target = defs.get(body["$ref"].rsplit("/", 1)[-1], {})
        return _type_of(target, defs) if "enum" in target else "object"
    return "any"
