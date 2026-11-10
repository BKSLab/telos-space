"""Живой статус ответа Ариса: что он делает прямо сейчас («Читаю задачу EVL-1…»).

Пока Арис готовит ответ (10–30 с), участник видит не безликое «думает», а шаг
цикла. Фраза строится по имени инструмента и ключевому аргументу; технических
имён в ней нет. Запись статуса — побочная: она идёт фоном, держит только
последнюю фразу и никогда не роняет и не задерживает ответ — сбой только
логируется.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

#: Запись статуса: фраза для показа; хранилище само решает, куда её положить.
ProgressSink = Callable[[str], Awaitable[None]]

#: Предел длины фразы (колонка ``progress`` — 200 символов).
MAX_PROGRESS_CHARS = 200
#: Сколько ждать одной записи статуса, прежде чем бросить её.
PROGRESS_WRITE_TIMEOUT = 3.0
#: Сколько ответ ждёт недописанную запись статуса при завершении.
PROGRESS_CLOSE_WAIT = 0.5
#: Сколько символов поисковой фразы показывать в статусе.
QUERY_CHARS = 40

#: Поиск по материалам проекта перед первым шагом модели.
SEARCH_PROJECT = "Ищу в материалах проекта…"
#: Начало цикла модели: вопрос разобран, первый шаг ещё не выбран.
THINKING = "Разбираюсь в вопросе…"
#: Изменяющий инструмент ждёт проверки, поручал ли участник изменение.
CHECK_WRITE = "Проверяю, просили ли вы что-то изменить…"
#: Инструмент, которого нет в таблице ниже.
GENERIC = "Смотрю проекты и задачи…"

#: Фраза по имени инструмента; ``{task}`` — ключ задачи, если он есть в аргументах.
#: Действия с подтверждением Арис только предлагает карточкой — их фраза начинается
#: с «Готовлю» и не обещает, что изменение уже выполняется.
PHRASES: dict[str, str] = {
    # Знания проекта.
    "telos_list_sources": "Просматриваю материалы проекта…",
    "telos_read_source": "Читаю материал проекта…",
    "telos_list_related_sources": "Смотрю, что связано с найденным…",
    "telos_search_sources": "Ищу в материалах проекта…",
    # Проект и задачи.
    "telos_get_project": "Смотрю проект…",
    "telos_get_latest_project_summary": "Читаю последний сохранённый обзор проекта…",
    "telos_update_project": "Обновляю проект…",
    "telos_list_tasks": "Смотрю задачи проекта…",
    "telos_list_task_reports": "Смотрю опубликованные отчёты проекта…",
    "telos_get_task_report": "Читаю отчёт задачи{task}…",
    "telos_publish_task_report": "Публикую отчёт задачи{task}…",
    "telos_get_task_statistics": "Считаю статистику задач…",
    "telos_list_overdue_tasks": "Ищу просроченные задачи…",
    "telos_list_recent_activity": "Смотрю последние изменения…",
    "telos_get_task": "Читаю задачу{task}…",
    "telos_get_any_task": "Читаю задачу{task}…",
    "telos_create_task": "Создаю задачу…",
    "telos_create_personal_task": "Создаю задачу…",
    "telos_update_task": "Обновляю задачу{task}…",
    "telos_set_task_status": "Меняю статус задачи{task}…",
    "telos_move_task": "Переношу задачу{task}…",
    "telos_delete_task": "Готовлю удаление задачи{task} — спрошу подтверждение…",
    "telos_set_checklist": "Обновляю чек-лист{task}…",
    "telos_fix_task_baseline": "Готовлю фиксацию плановых сроков задачи{task} — спрошу подтверждение…",
    "telos_attach_uploaded_file": "Прикрепляю файл к задаче{task}…",
    # Время и комментарии.
    "telos_list_comments": "Читаю комментарии{task}…",
    "telos_add_comment": "Пишу комментарий{task}…",
    "telos_update_comment": "Правлю комментарий…",
    "telos_delete_comment": "Готовлю удаление комментария — спрошу подтверждение…",
    # Команда.
    "telos_list_members": "Смотрю участников проекта…",
    "telos_add_member": "Добавляю участника…",
    "telos_remove_member": "Готовлю исключение участника — спрошу подтверждение…",
    "telos_transfer_project_ownership": "Готовлю передачу проекта — спрошу подтверждение…",
    # Доски.
    "telos_list_boards": "Смотрю доски…",
    "telos_get_board_scene": "Читаю схему доски…",
    "telos_draw_board_diagram": "Рисую схему…",
    "telos_update_board_shapes": "Обновляю схему…",
    "telos_arrange_board_objects": "Упорядочиваю объекты доски…",
    "telos_list_personal_boards": "Смотрю личные доски…",
    "telos_get_personal_board_scene": "Читаю личную схему…",
    "telos_draw_personal_board_diagram": "Рисую личную схему…",
    "telos_update_personal_board_shapes": "Обновляю личную схему…",
    "telos_arrange_personal_board_objects": "Упорядочиваю личную доску…",
    "telos_list_stickers": "Смотрю стикеры на доске…",
    "telos_create_sticker": "Добавляю стикер…",
    "telos_update_sticker": "Правлю стикер…",
    "telos_move_sticker": "Двигаю стикер…",
    "telos_delete_sticker": "Готовлю удаление стикера — спрошу подтверждение…",
    # Документы и вложения.
    "telos_list_documents": "Смотрю документы проекта…",
    "telos_get_document": "Читаю документ…",
    "telos_create_document": "Создаю документ…",
    "telos_update_document": "Правлю документ…",
    "telos_delete_document": "Готовлю удаление документа — спрошу подтверждение…",
    "telos_link_document": "Связываю документ с задачей…",
    "telos_unlink_document": "Отвязываю документ…",
    "telos_list_attachments": "Смотрю вложения{task}…",
    "telos_copy_attachment": "Копирую вложение…",
    "telos_create_text_attachment": "Прикладываю файл{task}…",
    "telos_delete_attachment": "Готовлю удаление вложения — спрошу подтверждение…",
    # Этапы, вехи, зависимости, календарь.
    "telos_list_stages": "Смотрю этапы доски…",
    "telos_create_stage": "Добавляю этап…",
    "telos_update_stage": "Правлю этап…",
    "telos_delete_stage": "Готовлю удаление этапа — спрошу подтверждение…",
    "telos_list_milestones": "Смотрю вехи…",
    "telos_create_milestone": "Добавляю веху…",
    "telos_update_milestone": "Правлю веху…",
    "telos_delete_milestone": "Готовлю удаление вехи — спрошу подтверждение…",
    "telos_list_dependencies": "Смотрю зависимости задач…",
    "telos_create_dependency": "Связываю задачи…",
    "telos_delete_dependency": "Готовлю удаление связи задач — спрошу подтверждение…",
    "telos_get_calendar": "Смотрю календарь…",
    "telos_preview_schedule_change": "Прикидываю новый график…",
    "telos_apply_schedule_change": "Готовлю перенос сроков — спрошу подтверждение…",
    "telos_get_structure": "Смотрю ИСР проекта…",
    "telos_create_wbs_node": "Добавляю раздел ИСР…",
    "telos_update_wbs_node": "Обновляю раздел ИСР…",
    "telos_move_wbs_node": "Перемещаю раздел ИСР…",
    "telos_delete_wbs_node": "Готовлю удаление раздела ИСР…",
    "telos_place_task": "Размещаю задачу в ИСР…",
    # Риски.
    "telos_list_risks": "Смотрю риски…",
    "telos_get_risk": "Читаю риск…",
    "telos_create_risk": "Добавляю риск…",
    "telos_update_risk": "Правлю риск…",
    "telos_delete_risk": "Готовлю удаление риска — спрошу подтверждение…",
    # Личное: мои задачи, проекты, потоки.
    "telos_list_my_tasks": "Смотрю ваши задачи…",
    "telos_list_my_projects": "Смотрю ваши проекты…",
    "telos_search_tasks": "Ищу задачи…",
    "telos_get_tool_schemas": "Выбираю, как это сделать…",
}

#: Поиск со словами: «Ищу задачи: „отчёт“…».
SEARCH_PHRASES: dict[str, str] = {
    "telos_search_tasks": "Ищу задачи",
    "telos_list_tasks": "Ищу задачи",
    "telos_list_my_tasks": "Ищу среди ваших задач",
    "telos_search_sources": "Ищу в материалах проекта",
    "telos_list_risks": "Ищу риски",
}

#: Обёртка проектного действия по ключу проекта (личный виджет).
PROJECT_ACTION_TOOL = "telos_run_project_action"


def describe_call(name: str, arguments: dict[str, Any] | None) -> str:
    """Человеческая фраза о вызове инструмента.

    Args:
        name: Имя инструмента из реестра.
        arguments: Аргументы вызова модели; любые, включая мусорные.
    Returns:
        Короткая фраза по-русски, не длиннее ``MAX_PROGRESS_CHARS``.
    """
    arguments = arguments if isinstance(arguments, dict) else {}
    if name == PROJECT_ACTION_TOOL:
        inner = arguments.get("arguments")
        return describe_call(
            str(arguments.get("name") or ""), inner if isinstance(inner, dict) else {}
        )
    query = _text(arguments.get("search") or arguments.get("query"))
    if query and name in SEARCH_PHRASES:
        return _limit(f"{SEARCH_PHRASES[name]}: «{_short(query)}»…")
    template = PHRASES.get(name)
    if template is None:
        return GENERIC
    key = _text(arguments.get("task_key"))
    return _limit(template.format(task=f" {key.upper()}" if key else ""))


def _text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _short(value: str) -> str:
    return value if len(value) <= QUERY_CHARS else value[: QUERY_CHARS - 1].rstrip() + "…"


def _limit(value: str) -> str:
    return value[:MAX_PROGRESS_CHARS]


class ProgressReporter:
    """Пишет последнюю фразу статуса фоном; сбой записи только логируется.

    ``report`` не ждёт записи: если предыдущая ещё идёт, новая фраза заменит
    ожидающую, и в хранилище уйдёт только самая свежая. Одна запись одновременно —
    поэтому поздняя фраза не перезапишет более новую.
    """

    def __init__(self, sink: ProgressSink | None) -> None:
        self._sink = sink
        self._latest: str | None = None
        self._written: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    def report(self, text: str) -> None:
        """Ставит фразу на запись; повтор той же фразы ничего не пишет."""
        if self._sink is None or self._closed or not text or text == self._latest:
            return
        self._latest = _limit(text)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._drain(), name="tim-progress")

    async def _drain(self) -> None:
        sink = self._sink
        while sink is not None and not self._closed and self._latest != self._written:
            text = self._latest
            try:
                async with asyncio.timeout(PROGRESS_WRITE_TIMEOUT):
                    await sink(text)  # type: ignore[arg-type]
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("⚠️ Статус ответа Ариса не записан: %s", text, exc_info=True)
            self._written = text

    async def close(self) -> None:
        """Завершает запись статуса: новых фраз нет, итог ответа очистит статус сам.

        Идущей записи даётся ``PROGRESS_CLOSE_WAIT``: прерывать её посреди
        запроса к БД хуже, чем подождать миллисекунды. Дольше — отменяется.
        """
        self._closed = True
        task, self._task = self._task, None
        if task is None or task.done():
            return
        await asyncio.wait({task}, timeout=PROGRESS_CLOSE_WAIT)
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


__all__ = [
    "CHECK_WRITE",
    "GENERIC",
    "MAX_PROGRESS_CHARS",
    "PHRASES",
    "SEARCH_PROJECT",
    "THINKING",
    "ProgressReporter",
    "ProgressSink",
    "describe_call",
]
