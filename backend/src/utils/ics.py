"""Сборка iCalendar (RFC 5545) для ленты подписки: только события на весь день."""

from dataclasses import dataclass
from datetime import UTC, date, datetime

PRODID = "-//TELOS SPACE//Calendar//RU"
LINE_LIMIT_OCTETS = 75


@dataclass(frozen=True, slots=True)
class IcsEvent:
    """Событие на весь день: ``date_to`` включительно."""

    uid: str
    summary: str
    date_from: date
    date_to: date
    stamp: datetime
    url: str | None = None
    description: str | None = None


def escape_text(value: str) -> str:
    """Экранирует значение TEXT: обратная косая, ``;``, ``,`` и переводы строк."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold_line(line: str) -> str:
    """Переносит строку длиннее 75 октетов, не разрывая символ UTF-8."""
    parts: list[str] = []
    current = ""
    size = 0
    limit = LINE_LIMIT_OCTETS
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > limit:
            parts.append(current)
            current = ""
            size = 0
            limit = LINE_LIMIT_OCTETS - 1  # продолжение начинается с пробела
        current += char
        size += width
    parts.append(current)
    return "\r\n ".join(parts)


def _utc(value: datetime) -> str:
    """Момент в формате UTC ``YYYYMMDDTHHMMSSZ``."""
    moment = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def render_calendar(*, name: str, events: list[IcsEvent]) -> str:
    """Собирает VCALENDAR с CRLF-переводами строк.

    Args:
        name: Название календаря у подписчика.
        events: События на весь день.

    Returns:
        Текст ``text/calendar``.
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(name)}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for event in events:
        end_exclusive = date.fromordinal(event.date_to.toordinal() + 1)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{event.uid}",
            f"DTSTAMP:{_utc(event.stamp)}",
            f"DTSTART;VALUE=DATE:{event.date_from.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{end_exclusive.strftime('%Y%m%d')}",
            f"SUMMARY:{escape_text(event.summary)}",
            "TRANSP:TRANSPARENT",
        ]
        if event.description:
            lines.append(f"DESCRIPTION:{escape_text(event.description)}")
        if event.url:
            lines.append(f"URL:{event.url}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"
