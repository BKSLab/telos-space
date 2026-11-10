"""Фильтры списка задач проекта — одни и те же для REST и MCP.

Список задач проекта отдают два сценария: доска в REST
(``TasksService.get_task_list``) и инструмент агента ``telos_list_tasks``
(``ProjectQueryService.list_tasks``). Правило «что значит открытая задача»
или «срок до даты» не должно существовать в двух копиях — оно здесь.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class TaskFilters:
    """Необязательные фильтры списка задач.

    Attributes:
        assignee: Исполнитель — имя целиком, без учёта регистра.
        priority: Приоритет (LOW, MEDIUM, HIGH, URGENT).
        is_done: ``True`` — только завершённые, ``False`` — только открытые.
        due_from: Срок не раньше даты.
        due_to: Срок не позже даты.
        without_due_date: Только задачи без срока.
    """

    assignee: str | None = None
    priority: str | None = None
    is_done: bool | None = None
    due_from: date | None = None
    due_to: date | None = None
    without_due_date: bool = False

    @property
    def empty(self) -> bool:
        """Не задан ни один фильтр."""
        return (
            self.assignee is None
            and self.priority is None
            and self.is_done is None
            and self.due_from is None
            and self.due_to is None
            and not self.without_due_date
        )


def task_matches(task, filters: TaskFilters, *, done_stage_ids: set[int]) -> bool:
    """Проверяет задачу проекта по фильтрам.

    Args:
        task: Задача (ORM или объект с теми же полями).
        filters: Фильтры.
        done_stage_ids: Стадии проекта с признаком «завершено».

    Returns:
        Подходит ли задача.
    """
    if filters.assignee is not None:
        if (task.assignee or "").casefold() != filters.assignee.strip().casefold():
            return False
    if filters.priority is not None:
        value = getattr(task.priority, "value", task.priority)
        if str(value).upper() != filters.priority.strip().upper():
            return False
    if filters.is_done is not None and (task.stage_id in done_stage_ids) != filters.is_done:
        return False
    if filters.without_due_date and task.due_date is not None:
        return False
    if filters.due_from is not None and (task.due_date is None or task.due_date < filters.due_from):
        return False
    if filters.due_to is not None and (task.due_date is None or task.due_date > filters.due_to):
        return False
    return True
