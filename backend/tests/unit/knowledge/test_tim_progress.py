"""Живой статус ответа Ариса: фразы по инструментам и запись, которая не мешает ответу.

Настоящая LLM не вызывается: FunctionModel ведёт цикл PydanticAI, инструменты
исполняет реестр ``AgentToolExecutor``.
"""

import asyncio
from time import perf_counter
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from src.agent.progress import (
    CHECK_WRITE,
    GENERIC,
    MAX_PROGRESS_CHARS,
    PHRASES,
    THINKING,
    ProgressReporter,
    describe_call,
)
from src.agent.runtime import TimRuntime, WriteGate
from src.agent.tools import AgentTool, AgentToolExecutor
from src.dependencies.services import TIM_ACTIONS
from src.schemas.project_tool_inputs import TaskLookupInput
from tests.unit.knowledge.agent_models import final
from tests.unit.knowledge.test_tim_runtime import deps_for, registry_executor, small_executor


class Sink:
    """Запись статуса в память; можно замедлить или уронить."""

    def __init__(self, *, delay: float = 0.0, error: Exception | None = None) -> None:
        self.written: list[str] = []
        self.delay = delay
        self.error = error

    async def __call__(self, text: str) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        self.written.append(text)


def test_phrases_name_the_step_without_technical_names():
    assert describe_call("get_task", {"task_key": "evl-1"}) == "Читаю задачу EVL-1…"
    assert describe_call("get_task", {"task_id": 7}) == "Читаю задачу…"
    assert describe_call("search_tasks", {"search": "  отчёт   по складу "}) == (
        "Ищу задачи: «отчёт по складу»…"
    )
    assert describe_call("get_calendar", {}) == "Смотрю календарь…"
    assert describe_call("create_task", {"title": "Новая"}) == "Создаю задачу…"
    # Проектное действие по ключу описывается действием внутри.
    assert (
        describe_call(
            "run_project_action",
            {"project_key": "EVL", "name": "get_task", "arguments": {"task_key": "EVL-3"}},
        )
        == "Читаю задачу EVL-3…"
    )
    # Неизвестный инструмент и мусорные аргументы — общая фраза, без исключений.
    assert describe_call("brand_new_tool", {"x": 1}) == GENERIC
    assert describe_call("run_project_action", {"arguments": "мусор"}) == GENERIC
    assert describe_call("get_task", None) == "Читаю задачу…"  # type: ignore[arg-type]


def test_every_registered_tool_has_a_phrase_and_long_queries_are_cut():
    executor = registry_executor()
    missing = [
        name for name in executor.tools if name not in PHRASES and name != "run_project_action"
    ]
    assert missing == []
    assert {definition.name for definition in TIM_ACTIONS} <= set(PHRASES)
    phrase = describe_call("search_tasks", {"search": "слово " * 200})
    assert len(phrase) < 80 and phrase.endswith("»…")
    assert len(describe_call("get_task", {"task_key": "K" * 500})) <= MAX_PROGRESS_CHARS
    for text in PHRASES.values():
        assert "_" not in text and text.endswith("…")
    # Действие с подтверждением Арис только предлагает — фраза не обещает изменение.
    for definition in TIM_ACTIONS:
        if definition.requires_confirmation:
            assert PHRASES[definition.name].startswith("Готовлю"), definition.name


async def test_runtime_reports_start_and_one_phrase_per_model_step():
    read = AsyncMock(return_value={"task": {"id": 7, "key": "EVL-1"}})
    executor = AgentToolExecutor([AgentTool("get_task", "Прочитать задачу", TaskLookupInput, read)])
    runtime = TimRuntime(model=TestModel(), executor=executor)
    steps = 0

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        nonlocal steps
        steps += 1
        if steps == 1:
            # Два вызова в одном шаге — одна фраза, по первому вызову.
            return ModelResponse(
                parts=[
                    ToolCallPart("get_task", {"task_key": "EVL-1"}, tool_call_id="a"),
                    ToolCallPart("get_task", {"task_key": "EVL-2"}, tool_call_id="b"),
                ]
            )
        return final("Готово.")

    runtime.agent.model = FunctionModel(respond)
    sink = Sink()
    deps = deps_for(executor)
    deps.progress = ProgressReporter(sink)
    result = await runtime.run(deps, metadata={})
    await asyncio.sleep(0)
    await deps.progress.close()

    assert result.output.answer == "Готово."
    assert read.await_count == 2
    assert sink.written == [THINKING, "Читаю задачу EVL-1…"]


@pytest.mark.parametrize("lazy", [False, True])
async def test_mutating_call_waiting_for_the_write_check_says_so(lazy):
    executor, _, create = small_executor()
    runtime = TimRuntime(model=TestModel(), executor=executor)
    decision: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    seen: list[str] = []
    sink = Sink()

    async def respond(messages, info: AgentInfo) -> ModelResponse:
        if not seen:
            seen.append("call")
            return ModelResponse(
                parts=[ToolCallPart("create_task", {"title": "Новая"}, tool_call_id="c")]
            )
        return final("Создал.")

    runtime.agent.model = FunctionModel(respond)
    deps = deps_for(executor)
    if lazy:
        deps.write_gate_factory = Mock(side_effect=lambda: WriteGate(decision))
    else:
        deps.write_gate = WriteGate(decision)
    deps.progress = ProgressReporter(sink)
    running = asyncio.create_task(runtime.run(deps, metadata={}))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if CHECK_WRITE in sink.written:
            break
    decision.set_result(True)
    result = await running
    await deps.progress.close()

    assert sink.written[-1] == CHECK_WRITE
    assert result.output.answer == "Создал."
    create.assert_awaited_once()
    if lazy:
        deps.write_gate_factory.assert_called_once_with()


async def test_failing_or_slow_progress_never_breaks_or_delays_the_answer():
    for sink in (Sink(error=RuntimeError("БД недоступна")), Sink(delay=30)):
        executor, _, _ = small_executor()
        runtime = TimRuntime(model=TestModel(), executor=executor)
        calls = 0

        async def respond(messages, info: AgentInfo) -> ModelResponse:
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(
                    parts=[ToolCallPart("get_task", {"task_key": "EVL-1"}, tool_call_id="r")]
                )
            return final("Ответ.")

        runtime.agent.model = FunctionModel(respond)
        deps = deps_for(executor)
        deps.progress = ProgressReporter(sink)
        started = perf_counter()
        result = await runtime.run(deps, metadata={})
        await deps.progress.close()
        elapsed = perf_counter() - started

        assert result.output.answer == "Ответ."
        # Медленная запись не держит ответ: close ждёт её не дольше полсекунды.
        assert elapsed < 2
        assert sink.written == []


async def test_reporter_writes_only_the_latest_phrase_and_nothing_after_close():
    sink = Sink(delay=0.05)
    reporter = ProgressReporter(sink)
    reporter.report("Первый…")
    await asyncio.sleep(0.01)
    # Пока пишется первая фраза, пришли две: в хранилище уйдёт только последняя.
    reporter.report("Второй…")
    reporter.report("Третий…")
    reporter.report("Третий…")
    await asyncio.sleep(0.2)
    assert sink.written == ["Первый…", "Третий…"]
    await reporter.close()
    reporter.report("После ответа…")
    await asyncio.sleep(0.1)
    assert sink.written == ["Первый…", "Третий…"]
    # Без записи статус просто не показывается.
    silent = ProgressReporter(None)
    silent.report("Ничего…")
    await silent.close()
