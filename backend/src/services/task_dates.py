"""Общие правила изменения плановых дат задачи для всех каналов записи."""

from datetime import date

from src.exceptions.tasks import TaskDateChangeReasonError

DATE_CHANGE_REASON_MAX_LENGTH = 2000


def validate_date_change_reason(
    *,
    previous_start: date | None,
    previous_due: date | None,
    next_start: date | None,
    next_due: date | None,
    reason: str | None,
) -> str | None:
    """Проверяет причину изменения каждой уже назначенной даты.

    Первое назначение отсутствующей даты и повтор тех же значений причины
    не требуют. Вызывающий сервис передаёт состояние под блокировкой строки.

    Raises:
        TaskDateChangeReasonError: Причина отсутствует или имеет недопустимый формат.
    """
    if reason is not None and (
        not isinstance(reason, str) or len(reason) > DATE_CHANGE_REASON_MAX_LENGTH
    ):
        raise TaskDateChangeReasonError()
    normalized = reason.strip() if reason is not None else None
    needs_reason = any(
        previous is not None and previous != current
        for previous, current in ((previous_start, next_start), (previous_due, next_due))
    )
    if needs_reason and not normalized:
        raise TaskDateChangeReasonError()
    return normalized or None


def task_dates_value(start_date: date | None, due_date: date | None) -> str:
    """Возвращает читаемый снимок обеих дат для единой записи истории."""
    return f"начало: {start_date or '—'}; завершение: {due_date or '—'}"
