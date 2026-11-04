"""Какие методы REST открыты сервисным API-ключам и под каким правом.

Список закрытый: метода нет в таблице — ключу он недоступен (403), даже если
у владельца ключа есть права в проекте. Так новый эндпоинт не откроется
ключам молча: его надо осознанно вписать сюда.

Сюда попадают только эндпоинты, которые получают принципала через
``get_principal``: там и проверяется право. Эндпоинты с собственной короткой
областью аутентификации (стриминг, LLM-подсказки, чат) ключам закрыты —
это проверяет тест ``test_api_key_routes``.

Операции MCP — в ``src/mcp_server/registry.py``; права — ``src/utils/api_scopes.py``.
"""

from typing import Final

from src.utils.api_scopes import (
    SCOPE_AGENT_ASK,
    SCOPE_BOARDS_READ,
    SCOPE_BOARDS_WRITE,
    SCOPE_COMMENTS_WRITE,
    SCOPE_KNOWLEDGE_READ,
    SCOPE_PROJECTS_READ,
    SCOPE_PROJECTS_WRITE,
    SCOPE_STRUCTURE_READ,
    SCOPE_STRUCTURE_WRITE,
    SCOPE_TASKS_DELETE,
    SCOPE_TASKS_READ,
    SCOPE_TASKS_WRITE,
)

_P = "/api/v1"

#: (HTTP-метод, шаблон маршрута) → требуемое право.
API_KEY_ROUTE_SCOPES: Final[dict[tuple[str, str], str]] = {
    # ── Проекты: карточка, стадии, команда, вехи, календарь, риски ──────────
    ("GET", f"{_P}/projects"): SCOPE_PROJECTS_READ,
    ("GET", f"{_P}/projects/{{project_id}}"): SCOPE_PROJECTS_READ,
    ("GET", f"{_P}/projects/{{project_id}}/stats"): SCOPE_PROJECTS_READ,
    ("GET", f"{_P}/projects/{{project_id}}/deadline-history"): SCOPE_PROJECTS_READ,
    ("GET", f"{_P}/projects/{{project_id}}/stages"): SCOPE_PROJECTS_READ,
    ("GET", f"{_P}/projects/{{project_id}}/members"): SCOPE_PROJECTS_READ,
    ("GET", f"{_P}/projects/{{project_id}}/milestones"): SCOPE_PROJECTS_READ,
    ("GET", f"{_P}/projects/{{project_id}}/calendar"): SCOPE_PROJECTS_READ,
    ("GET", f"{_P}/projects/{{project_id}}/calendar/unscheduled"): SCOPE_PROJECTS_READ,
    ("GET", f"{_P}/projects/{{project_id}}/calendar/workload"): SCOPE_PROJECTS_READ,
    ("GET", f"{_P}/projects/{{project_id}}/risks"): SCOPE_PROJECTS_READ,
    ("GET", f"{_P}/projects/{{project_id}}/risks/summary"): SCOPE_PROJECTS_READ,
    ("GET", f"{_P}/projects/{{project_id}}/risks/{{risk_id}}"): SCOPE_PROJECTS_READ,
    ("GET", f"{_P}/users"): SCOPE_PROJECTS_READ,
    # ── Задачи: чтение ──────────────────────────────────────────────────────
    ("GET", f"{_P}/projects/{{project_id}}/tasks"): SCOPE_TASKS_READ,
    ("GET", f"{_P}/tasks/by-key/{{task_key}}"): SCOPE_TASKS_READ,
    ("GET", f"{_P}/tasks/{{task_id}}"): SCOPE_TASKS_READ,
    ("GET", f"{_P}/tasks/{{task_id}}/links"): SCOPE_TASKS_READ,
    ("GET", f"{_P}/tasks/{{task_id}}/comments"): SCOPE_TASKS_READ,
    ("GET", f"{_P}/tasks/{{task_id}}/activity"): SCOPE_TASKS_READ,
    ("GET", f"{_P}/tasks/{{task_id}}/report"): SCOPE_TASKS_READ,
    ("GET", f"{_P}/projects/{{project_id}}/task-reports"): SCOPE_TASKS_READ,
    ("GET", f"{_P}/tasks/{{task_id}}/report/history"): SCOPE_TASKS_READ,
    ("GET", f"{_P}/tasks/{{task_id}}/attachments"): SCOPE_TASKS_READ,
    ("GET", f"{_P}/tasks/{{task_id}}/move-to/preview"): SCOPE_TASKS_READ,
    ("GET", f"{_P}/projects/{{project_id}}/task-dependencies"): SCOPE_TASKS_READ,
    ("GET", f"{_P}/my/tasks"): SCOPE_TASKS_READ,
    # ── Задачи: запись ──────────────────────────────────────────────────────
    ("POST", f"{_P}/projects/{{project_id}}/tasks"): SCOPE_TASKS_WRITE,
    ("PATCH", f"{_P}/tasks/{{task_id}}"): SCOPE_TASKS_WRITE,
    ("PATCH", f"{_P}/tasks/{{task_id}}/move"): SCOPE_TASKS_WRITE,
    ("PATCH", f"{_P}/tasks/{{task_id}}/status"): SCOPE_TASKS_WRITE,
    ("POST", f"{_P}/tasks/{{task_id}}/report"): SCOPE_TASKS_WRITE,
    ("POST", f"{_P}/tasks/{{task_id}}/report/from-comment"): SCOPE_TASKS_WRITE,
    ("POST", f"{_P}/tasks/{{task_id}}/move-to"): SCOPE_TASKS_WRITE,
    ("POST", f"{_P}/tasks/{{task_id}}/baseline"): SCOPE_TASKS_WRITE,
    ("POST", f"{_P}/projects/{{project_id}}/baseline"): SCOPE_TASKS_WRITE,
    ("POST", f"{_P}/projects/{{project_id}}/task-dependencies"): SCOPE_TASKS_WRITE,
    ("DELETE", f"{_P}/projects/{{project_id}}/task-dependencies/{{dependency_id}}"): (
        SCOPE_TASKS_WRITE
    ),
    ("POST", f"{_P}/projects/{{project_id}}/milestones"): SCOPE_TASKS_WRITE,
    ("PATCH", f"{_P}/projects/{{project_id}}/milestones/{{milestone_id}}"): SCOPE_TASKS_WRITE,
    ("POST", f"{_P}/projects/{{project_id}}/risks"): SCOPE_TASKS_WRITE,
    ("PATCH", f"{_P}/projects/{{project_id}}/risks/{{risk_id}}"): SCOPE_TASKS_WRITE,
    ("DELETE", f"{_P}/tasks/{{task_id}}"): SCOPE_TASKS_DELETE,
    # ── Комментарии ─────────────────────────────────────────────────────────
    ("POST", f"{_P}/tasks/{{task_id}}/comments"): SCOPE_COMMENTS_WRITE,
    ("PATCH", f"{_P}/comments/{{comment_id}}"): SCOPE_COMMENTS_WRITE,
    ("DELETE", f"{_P}/comments/{{comment_id}}"): SCOPE_COMMENTS_WRITE,
    # ── База знаний ─────────────────────────────────────────────────────────
    ("GET", f"{_P}/projects/{{project_id}}/knowledge/status"): SCOPE_KNOWLEDGE_READ,
    # ── Внешний вызов помощника владельца ключа ──────────────────────────
    ("POST", f"{_P}/agent/ask"): SCOPE_AGENT_ASK,
    ("GET", f"{_P}/agent/requests/{{request_id}}"): SCOPE_AGENT_ASK,
}

API_KEY_ROUTE_SCOPES.update(
    {
        ("POST", f"{_P}/tasks"): SCOPE_TASKS_WRITE,
        ("GET", f"{_P}/my/task-attention"): SCOPE_TASKS_READ,
        ("GET", f"{_P}/tasks/{{task_id}}/attention"): SCOPE_TASKS_READ,
        ("GET", f"{_P}/me/calendar"): SCOPE_TASKS_READ,
        ("GET", f"{_P}/me/calendar/unscheduled"): SCOPE_TASKS_READ,
        ("GET", f"{_P}/portfolio/calendar"): SCOPE_PROJECTS_READ,
        ("GET", f"{_P}/projects/defaults"): SCOPE_PROJECTS_READ,
        ("POST", f"{_P}/projects"): SCOPE_PROJECTS_WRITE,
        ("PATCH", f"{_P}/projects/{{project_id}}"): SCOPE_PROJECTS_WRITE,
        ("DELETE", f"{_P}/projects/{{project_id}}"): SCOPE_PROJECTS_WRITE,
        ("GET", f"{_P}/projects/{{project_id}}/icon-image"): SCOPE_PROJECTS_READ,
        ("POST", f"{_P}/projects/{{project_id}}/icon-image"): SCOPE_PROJECTS_WRITE,
        ("DELETE", f"{_P}/projects/{{project_id}}/icon-image"): SCOPE_PROJECTS_WRITE,
        ("DELETE", f"{_P}/projects/{{project_id}}/milestones/{{milestone_id}}"): SCOPE_TASKS_WRITE,
        ("POST", f"{_P}/projects/{{project_id}}/calendar/scenarios/preview"): SCOPE_TASKS_READ,
        ("POST", f"{_P}/projects/{{project_id}}/calendar/scenarios/apply"): SCOPE_TASKS_WRITE,
        ("GET", f"{_P}/projects/{{project_id}}/wbs"): SCOPE_STRUCTURE_READ,
        ("POST", f"{_P}/projects/{{project_id}}/wbs/suggestion"): SCOPE_STRUCTURE_READ,
    }
)

for method, suffix in (
    ("POST", "/nodes"),
    ("PATCH", "/nodes/{node_id}"),
    ("POST", "/nodes/{node_id}/move"),
    ("DELETE", "/nodes/{node_id}"),
    ("POST", "/tasks/{task_id}/assign"),
    ("POST", "/tasks/{task_id}/placement"),
    ("DELETE", "/tasks/{task_id}/assignment"),
    ("POST", "/suggestion/apply"),
):
    API_KEY_ROUTE_SCOPES[(method, f"{_P}/projects/{{project_id}}/wbs{suffix}")] = (
        SCOPE_STRUCTURE_WRITE
    )

for base in (f"{_P}/boards", f"{_P}/projects/{{project_id}}/boards"):
    API_KEY_ROUTE_SCOPES[("GET", base)] = SCOPE_BOARDS_READ
    API_KEY_ROUTE_SCOPES[("POST", base)] = SCOPE_BOARDS_WRITE
    for method, suffix in (
        ("GET", "/{board_id}/content"),
        ("GET", "/{board_id}/editor-metadata"),
        ("GET", "/{board_id}/stickers"),
    ):
        if base.endswith("/projects/{project_id}/boards") or not suffix.endswith("/stickers"):
            API_KEY_ROUTE_SCOPES[(method, base + suffix)] = SCOPE_BOARDS_READ
    for method, suffix in (
        ("PATCH", "/{board_id}"),
        ("DELETE", "/{board_id}"),
        ("PUT", "/{board_id}/editor-metadata"),
        ("POST", "/{board_id}/batch"),
        ("POST", "/{board_id}/stickers"),
        ("PATCH", "/{board_id}/stickers/{sticker_id}"),
        ("PATCH", "/{board_id}/stickers/{sticker_id}/position"),
        ("DELETE", "/{board_id}/stickers/{sticker_id}"),
        ("POST", "/{board_id}/shapes"),
        ("PATCH", "/{board_id}/shapes/{shape_id}"),
        ("DELETE", "/{board_id}/shapes/{shape_id}"),
        ("POST", "/{board_id}/shapes/{shape_id}/to-sticker"),
    ):
        API_KEY_ROUTE_SCOPES[(method, base + suffix)] = SCOPE_BOARDS_WRITE

for method, suffix in (
    ("GET", ""),
    ("POST", ""),
    ("PATCH", "/{sticker_id}"),
    ("PATCH", "/{sticker_id}/position"),
    ("DELETE", "/{sticker_id}"),
):
    API_KEY_ROUTE_SCOPES[(method, f"{_P}/projects/{{project_id}}/board/stickers{suffix}")] = (
        SCOPE_BOARDS_READ if method == "GET" else SCOPE_BOARDS_WRITE
    )

_PROMOTION = ("POST", f"{_P}/boards/{{board_id}}/create-project")
API_KEY_ROUTE_SCOPES[_PROMOTION] = SCOPE_BOARDS_WRITE
API_KEY_EXTRA_SCOPES: Final = {_PROMOTION: (SCOPE_PROJECTS_WRITE,)}


def required_scope(method: str, route_path: str) -> str | None:
    """Возвращает право, под которым метод открыт ключам, или ``None``."""
    return API_KEY_ROUTE_SCOPES.get((method.upper(), route_path))


def extra_scopes(method: str, route_path: str) -> tuple[str, ...]:
    return API_KEY_EXTRA_SCOPES.get((method.upper(), route_path), ())
