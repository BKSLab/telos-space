"""Контракты и исполнение разрешённых инструментов проектного агента."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agent.aris import ALL_MODES, ArisMode
from src.exceptions.base import ServiceError
from src.utils.tool_names import require_canonical_tool_name

if TYPE_CHECKING:
    from src.services.auth import Principal

logger = logging.getLogger(__name__)

#: Сколько ошибок аргументов называть модели за один вызов.
MAX_ARGUMENT_ERRORS = 5


@dataclass(frozen=True, slots=True)
class AgentToolContext:
    """Область инструмента, заданная backend, а не аргументами модели.

    ``project_id`` пуст в личном виджете без проекта на странице. Ответ, в
    котором вызван инструмент: ``message_id`` (виджет) или ``chat_turn_id``
    (чат проекта); у MCP обе ссылки пусты. ``mode`` ограничивает набор
    доступных инструментов независимо от того, что выбрала модель.
    """

    project_id: int | None
    user_id: int | None
    can_write: bool = False
    message_id: int | None = None
    run_id: UUID | None = None
    request_id: UUID | None = None
    username: str = ""
    display_name: str = ""
    chat_turn_id: int | None = None
    mode: ArisMode = ArisMode.WIDGET_PROJECT
    # Изменяющие инструменты, открытые правами сервисного ключа; None — без
    # ограничения ключом (вопрос из интерфейса трекера).
    allowed_tools: frozenset[str] | None = None
    principal: "Principal | None" = None

    def allows(self, name: str) -> bool:
        """Открыт ли изменяющий инструмент правами ключа этого вопроса."""
        return self.allowed_tools is None or name in self.allowed_tools


class AgentToolRequest(BaseModel):
    """Имя зарегистрированного инструмента и проверяемые аргументы."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(
        min_length=1, max_length=64, description="Имя из реестра доступных инструментов."
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Аргументы по схеме выбранного инструмента."
    )


AgentToolHandler = Callable[[AgentToolContext, BaseModel], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class AgentTool:
    """Схема инструмента и его явно внедрённый обработчик."""

    name: str
    description: str
    parameters: type[BaseModel]
    handler: AgentToolHandler
    mutating: bool = False
    requires_confirmation: bool = False
    modes: frozenset[ArisMode] = ALL_MODES

    def __post_init__(self) -> None:
        require_canonical_tool_name(self.name)


class AgentToolExecutor:
    """Разрешает только зарегистрированные инструменты с валидными аргументами."""

    def __init__(self, tools: Sequence[AgentTool]) -> None:
        self.tools = {tool.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("Имена инструментов агента должны быть уникальны.")

    def describe(
        self, *, include_parameters: bool = True, mode: ArisMode | None = None
    ) -> list[dict[str, Any]]:
        """Возвращает модели доступные возможности и схемы аргументов.

        Args:
            include_parameters: Включать ли JSON-схемы аргументов.
            mode: Режим Ариса; None — все инструменты реестра.
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                **(
                    {"parameters": tool.parameters.model_json_schema()}
                    if include_parameters
                    else {}
                ),
                "mutating": tool.mutating,
                "requires_confirmation": tool.requires_confirmation,
            }
            for tool in self.tools.values()
            if mode is None or mode in tool.modes
        ]

    async def execute(self, context: AgentToolContext, request: AgentToolRequest) -> dict[str, Any]:
        """Выполняет инструмент в серверной области проекта.

        Args:
            context: Проверенный проект и инициатор разговора.
            request: Выбор модели без права переопределить область доступа.
        Returns:
            Результат инструмента либо безопасная ошибка для следующего шага модели.
        """
        tool = self.tools.get(request.name)
        if tool is None or context.mode not in tool.modes:
            return self._refuse(context, request, "Инструмент недоступен в этом запросе.")
        if tool.mutating and not context.can_write:
            return self._refuse(context, request, "Текущий запрос не разрешает изменения проекта.")
        if tool.mutating and not context.allows(tool.name):
            return self._refuse(
                context, request, "Действие не открыто правами ключа, через который задан вопрос."
            )
        try:
            arguments = tool.parameters.model_validate(request.arguments)
        except ValidationError as error:
            return self._refuse(context, request, describe_validation_error(error, tool.parameters))
        try:
            return await tool.handler(context, arguments)
        except ServiceError as error:
            return self._refuse(context, request, error.detail)

    @staticmethod
    def _refuse(
        context: AgentToolContext, request: AgentToolRequest, reason: str
    ) -> dict[str, Any]:
        """Пишет отказ в журнал и возвращает модели понятную причину."""
        logger.warning(
            "🔒 Отказ инструмента агента %s: проект=%s, участник=%s, ответ=%s, причина: %s",
            request.name,
            context.project_id,
            context.user_id,
            context.message_id,
            reason,
        )
        return {"error": reason}


def describe_validation_error(error: ValidationError, schema: type[BaseModel]) -> str:
    """Называет поле, что с ним не так и какой формат ожидается.

    Модель по фразе «не соответствует схеме» не может исправить вызов и
    повторяет его вслепую; имя поля и ожидаемый формат дают ей исправиться
    за один шаг.

    Args:
        error: Ошибка проверки аргументов.
        schema: Схема аргументов инструмента.
    Returns:
        Одна строка на поле, не больше ``MAX_ARGUMENT_ERRORS`` полей.
    """
    properties = schema.model_json_schema().get("properties", {})
    parts = []
    for item in error.errors()[:MAX_ARGUMENT_ERRORS]:
        path = ".".join(str(part) for part in item.get("loc", ())) or "arguments"
        if item.get("type") == "extra_forbidden":
            known = ", ".join(sorted(properties)) or "нет полей"
            parts.append(f"{path}: такого поля нет; допустимые поля: {known}")
            continue
        if item.get("type") == "missing":
            reason = "обязательное поле не передано"
        else:
            reason = str(item.get("msg", "неверное значение")).rstrip(".")
        # Ошибка правила модели целиком (model_validator) приходит без поля: loc пуст.
        field_name = str((item.get("loc") or ("",))[0])
        expected = _expected_format(properties.get(field_name, {}))
        parts.append(f"{path}: {reason}" + (f"; ожидается {expected}" if expected else ""))
    hidden = len(error.errors()) - len(parts)
    if hidden > 0:
        parts.append(f"и ещё полей с ошибками: {hidden}")
    return "Аргументы не приняты. " + "; ".join(parts) + "."


def _expected_format(body: dict[str, Any]) -> str:
    """Короткое описание ожидаемого значения поля по JSON Schema."""
    variants = [body, *body.get("anyOf", [])]
    kinds = []
    for variant in variants:
        if "enum" in variant:
            kinds.append("одно из: " + ", ".join(str(value) for value in variant["enum"]))
        elif variant.get("format") == "date":
            kinds.append("дата ГГГГ-ММ-ДД")
        elif variant.get("format") == "date-time":
            kinds.append("дата и время ISO 8601")
        elif variant.get("format") == "uuid":
            kinds.append("UUID")
        elif variant.get("type") in {"integer", "number", "string", "boolean", "array", "object"}:
            kinds.append(
                {
                    "integer": "целое число",
                    "number": "число",
                    "string": "строка",
                    "boolean": "true или false",
                    "array": "список",
                    "object": "объект",
                }[variant["type"]]
            )
        elif variant.get("type") == "null":
            kinds.append("null")
    description = body.get("description")
    text = " или ".join(dict.fromkeys(kinds))
    if description:
        text = f"{text} ({description})" if text else description
    return text
