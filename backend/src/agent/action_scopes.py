"""Какое право сервисного ключа открывает действие проектного агента.

Одна таблица на MCP-инструменты действий (``mcp_server/project_tools.py``) и на
агента трекера, которого зовёт внешний клиент
(``tt_projects_ask``): ключ = доступ владельца ∩ права ключа.
"""

from src.utils.api_scopes import (
    SCOPE_BOARDS_READ,
    SCOPE_BOARDS_WRITE,
    SCOPE_COMMENTS_WRITE,
    SCOPE_PROJECTS_READ,
    SCOPE_STRUCTURE_READ,
    SCOPE_STRUCTURE_WRITE,
    SCOPE_TASKS_DELETE,
    SCOPE_TASKS_READ,
    SCOPE_TASKS_WRITE,
)

#: Какое право ключа нужно действию через MCP. Во внешнем agent:ask таблица
#: ограничивает запись; само agent:ask открывает чтение в пределах владельца.
#: Действия вне таблицы закрыты для новых ключей. Старому tt_ они доступны
#: по его уровню READ/WRITE; доступ к самому объекту проверяется отдельно.
ACTION_SCOPES: dict[str, str] = {
    "get_structure": SCOPE_STRUCTURE_READ,
    "create_wbs_node": SCOPE_STRUCTURE_WRITE,
    "update_wbs_node": SCOPE_STRUCTURE_WRITE,
    "move_wbs_node": SCOPE_STRUCTURE_WRITE,
    "delete_wbs_node": SCOPE_STRUCTURE_WRITE,
    "place_task": SCOPE_STRUCTURE_WRITE,
    "create_personal_task": SCOPE_TASKS_WRITE,
    "set_task_status": SCOPE_TASKS_WRITE,
    "list_my_tasks": SCOPE_TASKS_READ,
    "list_my_projects": SCOPE_PROJECTS_READ,
    "search_tasks": SCOPE_TASKS_READ,
    "get_any_task": SCOPE_TASKS_READ,
    "list_boards": SCOPE_BOARDS_READ,
    "get_board_scene": SCOPE_BOARDS_READ,
    "draw_board_diagram": SCOPE_BOARDS_WRITE,
    "update_board_shapes": SCOPE_BOARDS_WRITE,
    "arrange_board_objects": SCOPE_BOARDS_WRITE,
    "list_personal_boards": SCOPE_BOARDS_READ,
    "get_personal_board_scene": SCOPE_BOARDS_READ,
    "draw_personal_board_diagram": SCOPE_BOARDS_WRITE,
    "update_personal_board_shapes": SCOPE_BOARDS_WRITE,
    "arrange_personal_board_objects": SCOPE_BOARDS_WRITE,
    "get_task_report": SCOPE_TASKS_READ,
    "list_task_reports": SCOPE_TASKS_READ,
    "publish_task_report": SCOPE_TASKS_WRITE,
    "get_project": SCOPE_PROJECTS_READ,
    "get_latest_project_summary": SCOPE_PROJECTS_READ,
    "list_members": SCOPE_PROJECTS_READ,
    "list_stages": SCOPE_PROJECTS_READ,
    "list_milestones": SCOPE_PROJECTS_READ,
    "get_calendar": SCOPE_PROJECTS_READ,
    "list_risks": SCOPE_PROJECTS_READ,
    "get_risk": SCOPE_PROJECTS_READ,
    "list_tasks": SCOPE_TASKS_READ,
    "get_task_statistics": SCOPE_TASKS_READ,
    "list_overdue_tasks": SCOPE_TASKS_READ,
    "list_recent_activity": SCOPE_TASKS_READ,
    "get_task": SCOPE_TASKS_READ,
    "list_comments": SCOPE_TASKS_READ,
    "list_dependencies": SCOPE_TASKS_READ,
    "list_attachments": SCOPE_TASKS_READ,
    "create_task": SCOPE_TASKS_WRITE,
    "update_task": SCOPE_TASKS_WRITE,
    "move_task": SCOPE_TASKS_WRITE,
    "set_checklist": SCOPE_TASKS_WRITE,
    "fix_task_baseline": SCOPE_TASKS_WRITE,
    "create_dependency": SCOPE_TASKS_WRITE,
    "delete_dependency": SCOPE_TASKS_WRITE,
    "create_milestone": SCOPE_TASKS_WRITE,
    "update_milestone": SCOPE_TASKS_WRITE,
    "create_risk": SCOPE_TASKS_WRITE,
    "update_risk": SCOPE_TASKS_WRITE,
    "delete_task": SCOPE_TASKS_DELETE,
    "add_comment": SCOPE_COMMENTS_WRITE,
    "update_comment": SCOPE_COMMENTS_WRITE,
    "delete_comment": SCOPE_COMMENTS_WRITE,
}


def tools_for_scopes(scopes: list[str] | frozenset[str]) -> frozenset[str]:
    """Действия каталога, которые открывают выданные ключу права."""
    granted = set(scopes)
    return frozenset(name for name, scope in ACTION_SCOPES.items() if scope in granted)
