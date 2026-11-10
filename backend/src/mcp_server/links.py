"""Абсолютные ссылки на задачи и проекты для ответов инструментов.

Агент пересказывает ответ человеку, и ссылка должна открываться: интерфейс
чата делает кликабельным только абсолютный адрес. Поэтому адрес собирается
целиком — из ``APP_PUBLIC_URL`` и маршрутов фронта (``frontend/src/App.tsx``):
карточку любой доступной задачи открывает ``/my-tasks?task=<id>`` (тот же
адрес, что в ленте .ics), проект — ``/projects/<KEY>``. Меняется маршрут —
правится и этот модуль.

``APP_PUBLIC_URL`` не задан — поля ``url`` в ответе нет вовсе: пустая строка
или относительный путь выглядели бы как ссылка, которую можно отдать.
"""

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

#: Приписка про ссылки — часть описания инструментов для агента.
LINKS_HINT = (
    "Поле url — ссылка на задачу или проект в TELOS SPACE: давай её человеку "
    "целиком и как есть, не сокращай и не собирай адрес сам. Нет поля — нет ссылки."
)


@dataclass(frozen=True, slots=True)
class ResourceLinks:
    """Сборщик ссылок на интерфейс TELOS SPACE.

    Attributes:
        base_url: Публичный адрес без хвостового слеша; пусто — ссылок нет.
    """

    base_url: str

    def task(self, task_id: int | None) -> str | None:
        """Ссылка на карточку задачи."""
        return f"{self.base_url}/my-tasks?task={task_id}" if self.base_url and task_id else None

    def project(self, project_key: str | None) -> str | None:
        """Ссылка на проект по его ключу."""
        if not self.base_url or not project_key:
            return None
        return f"{self.base_url}/projects/{quote(project_key.strip().upper(), safe='')}"


def build_links(public_url: str) -> ResourceLinks:
    """Собирает сборщик ссылок из ``APP_PUBLIC_URL``."""
    return ResourceLinks((public_url or "").rstrip("/"))


def put_url(target: dict[str, Any], url: str | None, key: str = "url") -> dict[str, Any]:
    """Кладёт ссылку в ответ, только если она есть."""
    if url:
        target[key] = url
    return target
