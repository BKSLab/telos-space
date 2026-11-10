"""Какое право сервисного ключа открывает действие проектного агента.

Одна таблица на MCP-инструменты действий (``mcp_server/project_tools.py``) и на
агента трекера, которого зовёт внешний клиент
(``telos_ask_aris``): ключ = доступ владельца ∩ права ключа.
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
#: Действия вне таблицы закрыты для новых ключей. Токену telos_token_ они доступны
#: по его уровню READ/WRITE; доступ к самому объекту проверяется отдельно.
ACTION_SCOPES: dict[str, str] = {
    "telos_get_structure": SCOPE_STRUCTURE_READ,
    "telos_create_wbs_node": SCOPE_STRUCTURE_WRITE,
    "telos_update_wbs_node": SCOPE_STRUCTURE_WRITE,
    "telos_move_wbs_node": SCOPE_STRUCTURE_WRITE,
    "telos_delete_wbs_node": SCOPE_STRUCTURE_WRITE,
    "telos_place_task": SCOPE_STRUCTURE_WRITE,
    "telos_create_personal_task": SCOPE_TASKS_WRITE,
    "telos_set_task_status": SCOPE_TASKS_WRITE,
    "telos_list_my_tasks": SCOPE_TASKS_READ,
    "telos_list_my_projects": SCOPE_PROJECTS_READ,
    "telos_search_tasks": SCOPE_TASKS_READ,
    "telos_get_any_task": SCOPE_TASKS_READ,
    "telos_list_boards": SCOPE_BOARDS_READ,
    "telos_get_board_scene": SCOPE_BOARDS_READ,
    "telos_draw_board_diagram": SCOPE_BOARDS_WRITE,
    "telos_update_board_shapes": SCOPE_BOARDS_WRITE,
    "telos_arrange_board_objects": SCOPE_BOARDS_WRITE,
    "telos_list_personal_boards": SCOPE_BOARDS_READ,
    "telos_get_personal_board_scene": SCOPE_BOARDS_READ,
    "telos_draw_personal_board_diagram": SCOPE_BOARDS_WRITE,
    "telos_update_personal_board_shapes": SCOPE_BOARDS_WRITE,
    "telos_arrange_personal_board_objects": SCOPE_BOARDS_WRITE,
    "telos_get_task_report": SCOPE_TASKS_READ,
    "telos_list_task_reports": SCOPE_TASKS_READ,
    "telos_publish_task_report": SCOPE_TASKS_WRITE,
    "telos_get_project": SCOPE_PROJECTS_READ,
    "telos_get_latest_project_summary": SCOPE_PROJECTS_READ,
    "telos_list_members": SCOPE_PROJECTS_READ,
    "telos_list_stages": SCOPE_PROJECTS_READ,
    "telos_list_milestones": SCOPE_PROJECTS_READ,
    "telos_get_calendar": SCOPE_PROJECTS_READ,
    "telos_list_risks": SCOPE_PROJECTS_READ,
    "telos_get_risk": SCOPE_PROJECTS_READ,
    "telos_list_tasks": SCOPE_TASKS_READ,
    "telos_get_task_statistics": SCOPE_TASKS_READ,
    "telos_list_overdue_tasks": SCOPE_TASKS_READ,
    "telos_list_recent_activity": SCOPE_TASKS_READ,
    "telos_get_task": SCOPE_TASKS_READ,
    "telos_list_comments": SCOPE_TASKS_READ,
    "telos_list_dependencies": SCOPE_TASKS_READ,
    "telos_list_attachments": SCOPE_TASKS_READ,
    "telos_create_task": SCOPE_TASKS_WRITE,
    "telos_update_task": SCOPE_TASKS_WRITE,
    "telos_move_task": SCOPE_TASKS_WRITE,
    "telos_set_checklist": SCOPE_TASKS_WRITE,
    "telos_fix_task_baseline": SCOPE_TASKS_WRITE,
    "telos_create_dependency": SCOPE_TASKS_WRITE,
    "telos_delete_dependency": SCOPE_TASKS_WRITE,
    "telos_create_milestone": SCOPE_TASKS_WRITE,
    "telos_update_milestone": SCOPE_TASKS_WRITE,
    "telos_create_risk": SCOPE_TASKS_WRITE,
    "telos_update_risk": SCOPE_TASKS_WRITE,
    "telos_delete_task": SCOPE_TASKS_DELETE,
    "telos_add_comment": SCOPE_COMMENTS_WRITE,
    "telos_update_comment": SCOPE_COMMENTS_WRITE,
    "telos_delete_comment": SCOPE_COMMENTS_WRITE,
}


def tools_for_scopes(scopes: list[str] | frozenset[str]) -> frozenset[str]:
    """Действия каталога, которые открывают выданные ключу права."""
    granted = set(scopes)
    return frozenset(name for name, scope in ACTION_SCOPES.items() if scope in granted)
