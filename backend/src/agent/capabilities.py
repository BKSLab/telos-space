"""Небольшие предметные наборы Ариса; обработчики остаются в общем реестре.

Идентификаторы устойчивы: они используются нативным ``load_capability`` и
серверной ленивой загрузкой контекста. Здесь нет отдельного исполнителя или
классификатора вопроса.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Workflow:
    id: str
    description: str
    tools: frozenset[str]


WORKFLOWS = (
    Workflow(
        "tasks",
        "Задачи текущего проекта: найти, прочитать, создать, изменить, стадии, чек-листы; "
        "точный срок одной задачи; количества, распределение и просроченные задачи.",
        frozenset(
            {
                "list_tasks",
                "get_task_statistics",
                "list_overdue_tasks",
                "get_task",
                "create_task",
                "update_task",
                "move_task",
                "delete_task",
                "set_checklist",
                "fix_task_baseline",
            }
        ),
    ),
    Workflow(
        "comments",
        "Комментарии задачи: прочитать обсуждение, добавить или изменить комментарий.",
        frozenset({"list_comments", "add_comment", "update_comment", "delete_comment"}),
    ),
    Workflow(
        "documents",
        "Документы проекта: найти, прочитать полный текст, создать, изменить и связать с задачей.",
        frozenset(
            {
                "list_documents",
                "get_document",
                "create_document",
                "update_document",
                "delete_document",
                "link_document",
                "unlink_document",
            }
        ),
    ),
    Workflow(
        "attachments",
        "Вложения и загруженные пользователем файлы: прочитать перечень, прикрепить, "
        "скопировать или создать текстовый файл.",
        frozenset(
            {
                "list_attachments",
                "copy_attachment",
                "create_text_attachment",
                "delete_attachment",
                "attach_uploaded_file",
            }
        ),
    ),
    Workflow(
        "reports",
        "Сохранённый обзор/разбор проекта Ариса, последняя аналитическая сводка; "
        "опубликованные итоговые отчёты задач: наличие, чтение и новая версия.",
        frozenset({"get_latest_project_summary", "list_task_reports", "get_task_report", "publish_task_report"}),
    ),
    Workflow(
        "project",
        "Паспорт проекта, состав команды, стадии канбана и последние изменения проекта.",
        frozenset(
            {
                "get_project",
                "update_project",
                "list_members",
                "add_member",
                "remove_member",
                "transfer_project_ownership",
                "list_stages",
                "create_stage",
                "update_stage",
                "delete_stage",
                "list_recent_activity",
            }
        ),
    ),
    Workflow(
        "calendar",
        "Календарь, вехи, зависимости, календарные сигналы и перепланирование с preview; "
        "точная дата одной задачи без перепланирования — tasks.",
        frozenset(
            {
                "list_milestones",
                "create_milestone",
                "update_milestone",
                "delete_milestone",
                "list_dependencies",
                "create_dependency",
                "delete_dependency",
                "get_calendar",
                "preview_schedule_change",
                "apply_schedule_change",
            }
        ),
    ),
    Workflow(
        "risks",
        "Реестр зарегистрированных рисков проекта: прочитать, создать или изменить риск.",
        frozenset({"list_risks", "get_risk", "create_risk", "update_risk", "delete_risk"}),
    ),
    Workflow(
        "structure",
        "ИСР: дерево разделов, декомпозиция и размещение задач; свободный пул и холст.",
        frozenset(
            {
                "get_structure",
                "create_wbs_node",
                "update_wbs_node",
                "move_wbs_node",
                "delete_wbs_node",
                "place_task",
            }
        ),
    ),
    Workflow(
        "boards",
        "Доски проекта: стикеры, редактируемые схемы, фигуры, стрелки и упорядочивание выделения.",
        frozenset(
            {
                "list_boards",
                "get_board_scene",
                "draw_board_diagram",
                "update_board_shapes",
                "arrange_board_objects",
                "list_stickers",
                "create_sticker",
                "update_sticker",
                "move_sticker",
                "delete_sticker",
            }
        ),
    ),
    Workflow(
        "personal_boards",
        "Личные доски пользователя: найти, прочитать, нарисовать или упорядочить схему.",
        frozenset(
            {
                "list_personal_boards",
                "get_personal_board_scene",
                "draw_personal_board_diagram",
                "update_personal_board_shapes",
                "arrange_personal_board_objects",
            }
        ),
    ),
    Workflow(
        "personal",
        "Мои задачи и доступные проекты; поиск по всем проектам; создание разовой задачи "
        "и её статус. Для «мои задачи», в том числе просроченных.",
        frozenset(
            {
                "list_my_tasks",
                "list_my_projects",
                "search_tasks",
                "get_any_task",
                "create_personal_task",
                "set_task_status",
            }
        ),
    ),
    Workflow(
        "project_actions",
        "В личном диалоге: чтение или изменение другого проекта по его ключу "
        "и точные схемы проектных действий.",
        frozenset({"run_project_action", "get_tool_schemas"}),
    ),
    Workflow(
        "sources",
        "Знания проекта: поиск фактов и текста по всем источникам, чтение оригиналов "
        "и связанных объектов для анализа или ответа по документам.",
        frozenset({"list_sources", "read_source", "related_sources", "search_sources"}),
    ),
)


def workflow_for_tool(name: str) -> str:
    """Новые инструменты получают отдельный набор, не открывающий весь реестр."""
    return next((workflow.id for workflow in WORKFLOWS if name in workflow.tools), f"tool_{name}")
