"""Цикл Ариса на PydanticAI: нативный вызов инструментов из реестра действий.

Модель вызывает инструменты штатным tool calling, а не JSON-полем ответа.
Инструменты строятся из того же реестра ``AgentToolExecutor``, что и раньше:
имя, описание и Pydantic-схема аргументов берутся из ``AgentTool``, вызов
идёт через ``AgentToolExecutor.execute`` — права, журнал действий, pending и
подтверждение остаются в коде действий и не дублируются здесь.

Что решает этот модуль:

- **какие инструменты модель видит на шаге** (``prepare_tools``): только режима
  Ариса; изменяющие — только когда роль и ключ разрешают запись и проверка
  намерения (``WriteGate``) ещё не ответила «нет»; на последнем шаге
  инструментов нет — модель обязана ответить;
- **право записи** (``WriteGate``): поручил ли участник изменение, решает
  отдельный вызов модели; он запускается перед первой записью, изменяющий
  инструмент ждёт его ответа и без «да» не выполняется;
- **ошибки аргументов** — ``ModelRetry`` с полем и ожидаемым форматом;
- **бюджет контекста** (history processor): входной JSON плюс результаты
  инструментов не выходят за ``context_tokens`` — старые большие результаты
  сворачиваются в квитанции, затем сокращается стартовый контекст. Схемы
  инструментов в бюджет не входят. В production предметные правила и схемы
  раскрываются небольшими наборами через нативный ``telos_load_capability``;
- **лимиты**: шагов модели и вызовов инструментов — ``UsageLimits``, не
  больше восьми вызовов за шаг.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import (
    Agent,
    ModelRetry,
    RunContext,
    Tool,
    ToolOutput,
    UsageLimits,
)
from pydantic_ai.capabilities import Capability, CapabilityOrdering, PrepareTools, ProcessHistory
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestContext
from pydantic_ai.profiles import InlineDefsJsonSchemaTransformer
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset, PreparedToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.renamed import RenamedToolset
from pydantic_ai.usage import RunUsage

from src.agent.aris import ArisMode
from src.agent.capabilities import WORKFLOWS, Workflow, workflow_for_tool
from src.agent.context import AgentContextBudget, compact_action, encode, estimate_tokens
from src.agent.progress import CHECK_WRITE, THINKING, ProgressReporter, describe_call
from src.agent.tools import (
    AgentTool,
    AgentToolContext,
    AgentToolExecutor,
    AgentToolRequest,
    describe_validation_error,
)
from src.clients.agent_model import record_requests
from src.prompts.project_agent import (
    ARIS_PERSONAL_WORKFLOW_RULES,
    ARIS_WORKFLOW_COMMON,
    ARIS_WORKFLOW_INSTRUCTIONS,
)

#: Сколько вызовов инструментов модель может сделать за один шаг.
MAX_CALLS_PER_STEP = 8
#: Предел длины ответа модели на одном шаге.
MAX_OUTPUT_TOKENS = 4000
#: Сколько раз модель может исправить аргументы одного инструмента.
TOOL_RETRIES = 2
#: Имя инструмента, которым личный режим выполняет действие в проекте по ключу.
PROJECT_ACTION_TOOL = "telos_run_project_action"
#: Имя инструмента окончательного ответа.
OUTPUT_TOOL = "telos_submit_answer"
#: Результат инструмента дороже этого числа токенов сворачивается первым.
RECEIPT_THRESHOLD_TOKENS = 160
#: Нижняя граница бюджета стартового контекста при сокращении.
MIN_PROMPT_TOKENS = 3000
#: С какого остатка шагов модель видит счётчик. Раньше счётчик менял системную
#: инструкцию на каждом шаге, и кэш префикса у провайдера не срабатывал ни разу.
STEPS_NOTICE_FROM = 2
#: Не более двух дополнительных шагов на выбор наборов; предметный бюджет прежний.
MAX_WORKFLOW_LOAD_ROUNDS = 2
LOAD_CAPABILITY_TOOL = "telos_load_capability"


class ArisAnswer(BaseModel):
    """Окончательный ответ Ариса: текст и подтверждающие его источники."""

    answer: str = Field(
        default="",
        max_length=20000,
        description="Markdown-ответ участнику; пустая строка, если достаточно карточек.",
    )
    source_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Непрозрачные source_handle из результатов, подтверждающие ответ.",
    )


ResultHook = Callable[[AgentTool, dict[str, Any]], Awaitable[None]]

#: Ответ модели, когда проверка намерения не подтвердила поручение изменить данные.
WRITE_NOT_REQUESTED = (
    "Изменение не выполнено: в текущем сообщении участник не поручал менять данные. "
    "Не повторяй попытку другим инструментом; ответь по прочитанному и, если изменение "
    "нужно, попроси участника прямо поручить его."
)


class WriteGate:
    """Решение отдельной проверки поручения; начинается при создании объекта.

    Production передаёт фабрику, чтобы чтение и small talk не запускали проверку.
    Запись выполняется, только если и агент, и отдельная проверка видят поручение.
    """

    def __init__(self, decision: Awaitable[bool]) -> None:
        self._task: asyncio.Task[bool] = asyncio.ensure_future(decision)

    def known(self) -> bool | None:
        """Решение, если оно уже есть; None — проверка ещё идёт."""
        if not self._task.done() or self._task.cancelled():
            return None
        return self._task.exception() is None and self._task.result()

    async def allows(self) -> bool:
        """Ждёт решения; сбой проверки означает «нет»."""
        try:
            return await asyncio.shield(self._task)
        except asyncio.CancelledError:
            if self._task.cancelled():
                return False
            raise
        except Exception:
            return False

    def close(self) -> None:
        """Отменяет проверку, если ответ уже не нужен."""
        self._task.cancel()


@dataclass(slots=True)
class ArisDeps:
    """Всё, что знает о текущем вопросе цикл Ариса; задаёт только сервер.

    Attributes:
        execution: Проект, участник, режим, право записи и права ключа.
        executor: Реестр инструментов и их исполнение.
        system_prompt: Системная инструкция режима.
        content: Стартовый JSON-контекст; history processor сокращает его на месте.
        budget: Бюджет контекста (``context_tokens``).
        tool_rounds: Сколько шагов модели могут вызывать инструменты.
        on_result: Обработка результата (регистрация источников) до передачи модели.
        write_gate: Запущенная проверка поручения на запись; None — её ещё не
            создала фабрика либо право уже решено в ``execution.can_write``.
        calls: Журнал вызовов для метрик: имя, длительность, отказ.
        progress: Живой статус ответа для участника; None — статус не показывается.
        defer_workflows: Раскрывать предметные инструменты и правила по запросу.
        load_context: Ленивая серверная загрузка данных выбранного предметного набора.
        write_gate_factory: Создаёт проверку непосредственно перед первой записью.
    """

    execution: AgentToolContext
    executor: AgentToolExecutor
    system_prompt: str
    content: dict[str, Any]
    budget: AgentContextBudget
    tool_rounds: int
    on_result: ResultHook | None = None
    write_gate: WriteGate | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    progress: ProgressReporter | None = None
    defer_workflows: bool = False
    load_context: Callable[[str], Awaitable[None]] | None = None
    write_gate_factory: Callable[[], WriteGate] | None = None
    _step_calls: dict[int, int] = field(default_factory=dict)
    _progress_steps: set[int] = field(default_factory=set)
    _load_steps: set[int] = field(default_factory=set)
    _domain_steps: set[int] = field(default_factory=set)
    _context_groups: set[str] = field(default_factory=set)
    _context_load_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def tool_step_limit(self) -> int:
        """Загрузки без предметных действий дают ограниченную добавку к шагам."""
        extra = min(MAX_WORKFLOW_LOAD_ROUNDS, len(self._load_steps - self._domain_steps))
        return self.tool_rounds + (extra if self.defer_workflows else 0)

    def allowed(self, name: str) -> bool:
        """Можно ли вызывать инструмент в этом вопросе: режим, запись, права ключа."""
        tool = self.executor.tools.get(name)
        if tool is None or self.execution.mode not in tool.modes:
            return False
        if tool.mutating:
            return (
                self.execution.can_write
                and self.execution.allows(name)
                and (self.write_gate is None or self.write_gate.known() is not False)
            )
        return True

    def may_write(self, tool: AgentTool, arguments: dict[str, Any]) -> bool:
        """Может ли вызов изменить данные — прямо или через проектное действие по ключу."""
        if tool.mutating:
            return True
        if tool.name != PROJECT_ACTION_TOOL:
            return False
        inner = self.executor.tools.get(str(arguments.get("name") or ""))
        # Неизвестное действие считаем изменяющим: обёртка откажет сама, а право
        # записи не должно зависеть от того, узнали ли мы имя.
        return inner is None or inner.mutating

    def offered(self) -> list[str]:
        """Допустимые предметные инструменты, включая ещё не раскрытые наборы."""
        return [name for name in self.executor.tools if self.allowed(name)]


@dataclass(frozen=True, slots=True)
class ArisRunResult:
    """Итог цикла: ответ модели (или None, если лимит шагов исчерпан) и метрики."""

    output: ArisAnswer | None
    requests: int
    tool_calls: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    prompt_chars: int
    model_requests: list[dict[str, Any]] = field(default_factory=list)


class _PrepareArisTools(PrepareTools[ArisDeps]):
    """Применяет тот же фильтр снаружи нативного loader, включая последний шаг."""

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost")

    def get_wrapper_toolset(self, toolset: AbstractToolset[ArisDeps]) -> AbstractToolset[ArisDeps]:
        # The framework owns the native loader implementation. Its one public
        # registration uses our name; the original name is never in get_tools().
        return PreparedToolset(
            _CanonicalLoaderToolset(toolset, name_map={LOAD_CAPABILITY_TOOL: "load_capability"}),
            self.prepare_func,
        )

    async def before_model_request(
        self, ctx: RunContext[ArisDeps], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        parameters = request_context.model_request_parameters
        if not parameters.instruction_parts:
            return request_context
        parts = [
            dataclass_replace(part, content=_loader_instructions(part.content))
            if str(part.id) == "capability:deferred_capabilities"
            else part
            for part in parameters.instruction_parts
        ]
        return dataclass_replace(
            request_context,
            model_request_parameters=dataclass_replace(parameters, instruction_parts=parts),
        )


def _loader_instructions(text: str) -> str:
    """Имя нативной реализации не попадает в инструкции и ошибки для модели."""
    return text.replace("`load_capability`", f"`{LOAD_CAPABILITY_TOOL}`")


class _CanonicalLoaderToolset(RenamedToolset[ArisDeps]):
    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[ArisDeps],
        tool: ToolsetTool[ArisDeps],
    ) -> Any:
        try:
            return await super().call_tool(name, tool_args, ctx, tool)
        except ModelRetry as error:
            if name != LOAD_CAPABILITY_TOOL:
                raise
            raise ModelRetry(_loader_instructions(error.message)) from error


class ArisRuntime:
    """Один агент PydanticAI на реестр инструментов; состояние вопроса — в deps."""

    def __init__(self, *, model: Model, executor: AgentToolExecutor) -> None:
        """Строит агента и инструменты из реестра.

        Args:
            model: Модель PydanticAI (в тестах — TestModel/FunctionModel).
            executor: Реестр инструментов с их обработчиками и правами.
        """
        self.executor = executor
        tools = {tool.name: build_tool(tool) for tool in executor.tools.values()}
        self._tools = FunctionToolset(list(tools.values()))
        self._workflow_tools = tools
        self.agent: Agent[ArisDeps, ArisAnswer | str] = Agent(
            model,
            name="aris-pm",
            deps_type=ArisDeps,
            # Текстовый ответ разрешён рядом с telos_submit_answer: иначе PydanticAI шлёт
            # tool_choice=required, а Gemini через шлюз отвечает 400 «Request
            # contains an invalid argument», если инструментов больше ~20
            # (замер 30.09.2026 на стенде). Текст становится ответом без source_ids.
            output_type=[
                ToolOutput(
                    ArisAnswer,
                    name=OUTPUT_TOOL,
                    description="Окончательный ответ участнику и source_ids, подтверждающие его.",
                ),
                str,
            ],
            instructions=self._instructions,
            capabilities=[_PrepareArisTools(self._prepare_tools), ProcessHistory(self._fit_history)],
            model_settings=ModelSettings(max_tokens=MAX_OUTPUT_TOKENS),
            retries=TOOL_RETRIES,
            # Все вызовы шага выполняются, даже если в том же ответе есть итог:
            # иначе ответ мог бы сообщить о записи, которую никто не выполнил.
            end_strategy="exhaustive",
        )
        # Не наследуем включение экспорта телеметрии через Agent.instrument_all().
        self.agent.instrument = False

    @staticmethod
    def usage_limits(tool_rounds: int, *, defer_workflows: bool = False) -> UsageLimits:
        """Не мягче прежнего цикла: ``tool_rounds`` шагов с инструментами плюс ответ.

        Один запрос сверху — на случай, если модель на последнем шаге ответила
        не инструментом ответа и её попросили повторить.
        """
        rounds = tool_rounds + (MAX_WORKFLOW_LOAD_ROUNDS if defer_workflows else 0)
        return UsageLimits(
            request_limit=rounds + 2,
            tool_calls_limit=MAX_CALLS_PER_STEP * rounds,
        )

    def _workflow_capabilities(self, deps: ArisDeps) -> list[Capability[ArisDeps]]:
        """Каталог состоит только из наборов с допустимыми для запроса инструментами."""
        groups = list(WORKFLOWS)
        known = {name for workflow in groups for name in workflow.tools}
        groups.extend(
            Workflow(workflow_for_tool(name), tool.description, frozenset({name}))
            for name, tool in self.executor.tools.items()
            if name not in known
        )
        result = []
        for workflow in groups:
            tools = [
                tool
                for name, tool in self._workflow_tools.items()
                if name in workflow.tools and deps.allowed(name)
            ]
            if not tools:
                continue
            result.append(
                Capability[ArisDeps](
                    id=workflow.id,
                    description=workflow.description,
                    instructions=self._workflow_instructions(workflow.id),
                    tools=tools,
                    defer_loading=True,
                )
            )
        return result

    @staticmethod
    def _workflow_instructions(group_id: str) -> Callable[[RunContext[ArisDeps]], Awaitable[str]]:
        async def load(ctx: RunContext[ArisDeps]) -> str:
            deps = ctx.deps
            step = ctx.run_step
            deps._load_steps.add(step)
            deps._step_calls[step] = deps._step_calls.get(step, 0) + 1
            record: dict[str, Any] = {"tool": LOAD_CAPABILITY_TOOL, "group": group_id, "step": step}
            started = perf_counter()
            try:
                if deps._step_calls[step] > MAX_CALLS_PER_STEP:
                    record["refused"] = "step_limit"
                    raise ModelRetry(f"За один шаг — не более {MAX_CALLS_PER_STEP} инструментов.")
                # Одна SQLAlchemy-сессия не выполняет параллельные чтения разных наборов.
                async with deps._context_load_lock:
                    rules = ARIS_WORKFLOW_INSTRUCTIONS.get(group_id, "")
                    if group_id not in deps._context_groups:
                        if deps.load_context is not None:
                            await deps.load_context(group_id)
                        if not deps._context_groups:
                            # Common safety/receipt rules are delivered once per run,
                            # then retained in the protected native loader history.
                            rules = ARIS_WORKFLOW_COMMON + "\n\n" + rules
                            if deps.execution.mode == ArisMode.WIDGET_PERSONAL:
                                rules += "\n\n" + ARIS_PERSONAL_WORKFLOW_RULES
                        deps._context_groups.add(group_id)
                return rules
            finally:
                record["ms"] = round((perf_counter() - started) * 1000, 3)
                deps.calls.append(record)

        return load

    async def run(self, deps: ArisDeps, *, metadata: dict[str, Any]) -> ArisRunResult:
        """Выполняет вопрос до ответа или исчерпания лимита шагов.

        Args:
            deps: Состояние вопроса; ``content`` становится первым сообщением.
            metadata: Несекретные атрибуты трассы (канал, режим, проект, диалог).
        Returns:
            Ответ модели и метрики; ``output=None`` — лимит шагов исчерпан.
        Raises:
            pydantic_ai.exceptions.AgentRunError: Ошибка провайдера или поведения модели.
        """
        from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded

        # Native loading state belongs to this run's message history. Deps may be reused
        # by a low-level caller; context callbacks must start fresh as well.
        deps._context_groups.clear()
        deps._load_steps.clear()
        deps._domain_steps.clear()
        prompt = deps.budget.fit(deps.content)
        run_usage = RunUsage()
        if deps.progress is not None:
            deps.progress.report(THINKING)
        with record_requests() as journal:
            try:
                result = await self.agent.run(
                    prompt,
                    deps=deps,
                    usage=run_usage,
                    usage_limits=self.usage_limits(
                        deps.tool_rounds, defer_workflows=deps.defer_workflows
                    ),
                    metadata=metadata,
                    toolsets=None if deps.defer_workflows else [self._tools],
                    capabilities=self._workflow_capabilities(deps)
                    if deps.defer_workflows
                    else None,
                )
            except ModelHTTPError as error:
                # Gemini через шлюз на шаге без инструментов иногда всё равно зовёт
                # инструмент, и polza отвечает 502 «некорректный вызов инструмента».
                # Шаги к этому моменту исчерпаны: честно «проверка неполная», а не сбой.
                if error.status_code != 502 or len(deps._step_calls) < deps.tool_rounds:
                    raise
                return ArisRunResult(
                    output=None,
                    requests=len(deps._step_calls) + 1,
                    tool_calls=deps.calls,
                    input_tokens=sum(item["input_tokens"] for item in journal),
                    output_tokens=sum(item["output_tokens"] for item in journal),
                    prompt_chars=len(encode(deps.content)),
                    model_requests=journal,
                )
            except UsageLimitExceeded:
                return ArisRunResult(
                    output=None,
                    requests=run_usage.requests,
                    tool_calls=deps.calls,
                    input_tokens=sum(item["input_tokens"] for item in journal),
                    output_tokens=sum(item["output_tokens"] for item in journal),
                    prompt_chars=len(encode(deps.content)),
                    model_requests=journal,
                )
        usage = result.usage
        output = result.output
        if isinstance(output, str):
            output = ArisAnswer(answer=output)
        return ArisRunResult(
            output=output,
            requests=usage.requests,
            tool_calls=deps.calls,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            prompt_chars=len(encode(deps.content)),
            model_requests=journal,
        )

    @staticmethod
    def _instructions(ctx: RunContext[ArisDeps]) -> str:
        # Инструкция — начало каждого запроса: пока она не меняется, провайдер
        # переиспользует кэш префикса (инструкция, схемы, стартовый контекст).
        remaining = max(0, ctx.deps.tool_step_limit - ctx.usage.requests)
        if remaining > STEPS_NOTICE_FROM:
            return ctx.deps.system_prompt
        tail = (
            f"\n\nОсталось шагов с инструментами: {remaining}."
            if remaining
            else "\n\nИнструменты больше недоступны: ответь по уже прочитанному через "
            f"{OUTPUT_TOOL} и назови, что осталось непроверенным."
        )
        return ctx.deps.system_prompt + tail

    @staticmethod
    async def _prepare_tools(
        ctx: RunContext[ArisDeps], tool_defs: list[ToolDefinition]
    ) -> list[ToolDefinition]:
        if ctx.usage.requests >= ctx.deps.tool_step_limit:
            return []
        return [
            item
            for item in tool_defs
            if ctx.deps.allowed(item.name)
            or (ctx.deps.defer_workflows and item.name == LOAD_CAPABILITY_TOOL)
        ]

    async def _fit_history(
        self, ctx: RunContext[ArisDeps], messages: list[ModelMessage]
    ) -> list[ModelMessage]:
        """Держит вход шага в пределах ``context_tokens``.

        Сначала старые большие результаты (кроме последнего шага и записей)
        сворачиваются в квитанции, затем сокращается стартовый JSON — тем же
        алгоритмом, что и раньше. Пары вызов/результат не удаляются: провайдер
        требует их целиком.
        """
        deps = ctx.deps
        if deps.defer_workflows and ctx.usage.requests >= deps.tool_step_limit + 2:
            # Reserve only the loader-only rounds actually used. Otherwise the
            # unused discovery reserve permits a third unavailable-tool retry,
            # which the framework treats as a fatal tool error instead of an
            # incomplete answer within the original two final-answer attempts.
            from pydantic_ai.exceptions import UsageLimitExceeded

            raise UsageLimitExceeded("Исчерпан бюджет предметных шагов и итогового ответа.")
        limit = deps.budget.max_tokens
        last_request = max(
            (index for index, item in enumerate(messages) if isinstance(item, ModelRequest)),
            default=-1,
        )
        while True:
            traffic = _traffic_tokens(messages)
            prompt = deps.budget.tokens(deps.content)
            if prompt + traffic <= limit:
                break
            if _compact_oldest_result(messages, keep_from=last_request):
                continue
            try:
                shrunk = AgentContextBudget(
                    system_prompt=deps.system_prompt,
                    max_tokens=max(MIN_PROMPT_TOKENS, limit - traffic),
                    history_tokens=deps.budget.history_tokens,
                )
                before = encode(deps.content)
                shrunk.fit(deps.content)
            except ValueError:
                break
            if encode(deps.content) == before:
                break
        _replace_prompt(messages, encode(deps.content))
        return messages


def tool_json_schema(tool: AgentTool) -> dict[str, Any]:
    """JSON Schema для шлюзов: inline refs и типизированные массивы координат."""
    schema = InlineDefsJsonSchemaTransformer(tool.parameters.model_json_schema()).walk()

    def portable(value):
        if isinstance(value, dict):
            # OpenAPI discriminator.mapping retains references after $defs inlining.
            # The inline oneOf alternatives retain the literal type validation.
            result = {key: portable(item) for key, item in value.items() if key != "discriminator"}
            prefix = result.get("prefixItems")
            if (
                result.get("type") == "array"
                and isinstance(prefix, list)
                and prefix
                and result.get("minItems") == result.get("maxItems") == len(prefix)
                and all(item == prefix[0] for item in prefix)
            ):
                # Gemini via OpenAI gateways ignores prefixItems and requires items.
                # A fixed tuple with identical element schemas is equivalent to this
                # homogeneous array; preserve its exact length and coordinate bounds.
                result["items"] = prefix[0]
                del result["prefixItems"]
            return result
        if isinstance(value, list):
            return [portable(item) for item in value]
        return value

    return portable(schema)


def build_tool(tool: AgentTool) -> Tool[ArisDeps]:
    """Инструмент PydanticAI из записи реестра: схема и описание — из неё же."""

    async def call(ctx: RunContext[ArisDeps], **arguments: Any) -> dict[str, Any]:
        return await invoke(ctx, tool, arguments)

    # from_schema оставляет проверку аргументов invoke: ошибка должна стать
    # ModelRetry с полем и форматом, а не общим отказом валидатора.
    result = Tool.from_schema(
        call,
        takes_ctx=True,
        name=tool.name,
        description=tool.description,
        json_schema=tool_json_schema(tool),
    )
    result.max_retries = TOOL_RETRIES
    return result


async def invoke(
    ctx: RunContext[ArisDeps], tool: AgentTool, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Проверяет доступ и аргументы, исполняет инструмент реестра, готовит результат.

    Raises:
        ModelRetry: Аргументы не проходят схему — модель исправит вызов.
    """
    deps = ctx.deps
    started = perf_counter()
    step = ctx.run_step
    deps._domain_steps.add(step)
    deps._step_calls[step] = deps._step_calls.get(step, 0) + 1
    record: dict[str, Any] = {"tool": tool.name, "step": step}
    try:
        if deps._step_calls[step] > MAX_CALLS_PER_STEP:
            record["refused"] = "step_limit"
            return {
                "error": f"За один шаг — не более {MAX_CALLS_PER_STEP} инструментов. "
                "Повтори нужный вызов следующим шагом."
            }
        # Второй рубеж после prepare_tools: скрытый инструмент по имени не выполняется.
        if not deps.allowed(tool.name) or (
            deps.defer_workflows and not ctx.is_tool_available(tool.name)
        ):
            record["refused"] = "hidden"
            return {"error": "Инструмент недоступен в этом запросе."}
        try:
            tool.parameters.model_validate(arguments)
        except ValidationError as error:
            record["refused"] = "arguments"
            reason = describe_validation_error(error, tool.parameters)
            if ctx.retry >= TOOL_RETRIES:
                # Повторы исчерпаны: не роняем весь ответ, а сообщаем модели
                # причину — дальше она отвечает по уже прочитанному.
                return {"error": reason}
            raise ModelRetry(reason) from error
        execution = deps.execution
        if (
            deps.write_gate is None
            and deps.write_gate_factory is not None
            and execution.can_write
            and deps.may_write(tool, arguments)
        ):
            inner = deps.executor.tools.get(str(arguments.get("name") or ""))
            # Обёртка сама откажет неизвестному действию или записи вне scopes.
            # Для такого отказа дополнительная модель не нужна.
            if tool.mutating or (
                inner is not None and inner.mutating and execution.allows(inner.name)
            ):
                deps.write_gate = deps.write_gate_factory()
        if deps.progress is not None and step not in deps._progress_steps:
            # Одна фраза на шаг модели: первый допустимый вызов шага.
            deps._progress_steps.add(step)
            waits_gate = (
                deps.write_gate is not None
                and execution.can_write
                and deps.may_write(tool, arguments)
                and deps.write_gate.known() is None
            )
            deps.progress.report(CHECK_WRITE if waits_gate else describe_call(tool.name, arguments))
        if (
            deps.write_gate is not None
            and execution.can_write
            and deps.may_write(tool, arguments)
            and not await deps.write_gate.allows()
        ):
            if tool.mutating:
                record["refused"] = "write_intent"
                return {"error": WRITE_NOT_REQUESTED}
            # Проектное действие по ключу: обёртка откажет в записи сама.
            execution = dataclass_replace(execution, can_write=False)
        result = await deps.executor.execute(
            execution, AgentToolRequest(name=tool.name, arguments=arguments)
        )
        if deps.on_result is not None:
            await deps.on_result(tool, result)
        if isinstance(result.get("action"), dict):
            # Квитанция записи: факт и ID, без тел, которые уже есть в карточке.
            result = {**result, "action": compact_action(result["action"])}
        if "error" in result:
            record["refused"] = "error"
        return _fit_result(result, deps.budget.max_tokens // 3)
    finally:
        record["ms"] = round((perf_counter() - started) * 1000, 3)
        deps.calls.append(record)


def _fit_result(result: dict[str, Any], max_tokens: int) -> dict[str, Any]:
    """Один результат не съедает бюджет целиком: длинные списки и тексты сокращаются."""
    if estimate_tokens(encode(result)) <= max_tokens:
        return result
    value = deepcopy(result)
    while estimate_tokens(encode(value)) > max_tokens:
        if AgentContextBudget._shrink_lists(value):
            continue
        if AgentContextBudget._shrink_text(value):
            continue
        break
    return value


def _traffic_tokens(messages: list[ModelMessage]) -> int:
    total = 0
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                total += estimate_tokens(encode(part.content))
            elif isinstance(part, ToolCallPart):
                total += estimate_tokens(
                    part.args if isinstance(part.args, str) else encode(part.args)
                )
            elif isinstance(message, ModelResponse):
                total += estimate_tokens(getattr(part, "content", "") or "")
    return total


def _compact_oldest_result(messages: list[ModelMessage], *, keep_from: int) -> bool:
    for index, message in enumerate(messages[:keep_from]):
        if not isinstance(message, ModelRequest):
            continue
        for position, part in enumerate(message.parts):
            if not isinstance(part, ToolReturnPart) or not isinstance(part.content, dict):
                continue
            content = part.content
            if (
                part.tool_name == LOAD_CAPABILITY_TOOL
                or "action" in content
                or content.get("context_compacted")
                or estimate_tokens(encode(content)) <= RECEIPT_THRESHOLD_TOKENS
            ):
                continue
            parts = list(message.parts)
            parts[position] = dataclass_replace(part, content=AgentContextBudget._receipt(content))
            messages[index] = dataclass_replace(message, parts=parts)
            return True
    return False


def _replace_prompt(messages: list[ModelMessage], prompt: str) -> None:
    for index, message in enumerate(messages):
        if not isinstance(message, ModelRequest):
            continue
        for position, part in enumerate(message.parts):
            if isinstance(part, UserPromptPart):
                if part.content != prompt:
                    parts = list(message.parts)
                    parts[position] = dataclass_replace(part, content=prompt)
                    messages[index] = dataclass_replace(message, parts=parts)
                return


def metrics(result: ArisRunResult) -> dict[str, Any]:
    """Компактные метрики цикла для журнала."""
    return {
        "requests": result.requests,
        "tool_calls": len(result.tool_calls),
        "tools": [item["tool"] for item in result.tool_calls],
        "refused": [item for item in result.tool_calls if "refused" in item],
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "exhausted": result.output is None,
        "model_requests": result.model_requests,
    }


__all__ = [
    "MAX_CALLS_PER_STEP",
    "WRITE_NOT_REQUESTED",
    "WriteGate",
    "ArisAnswer",
    "ArisDeps",
    "ArisRunResult",
    "ArisRuntime",
    "build_tool",
    "metrics",
]
