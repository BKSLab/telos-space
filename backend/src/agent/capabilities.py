"""Небольшие предметные наборы Ариса; обработчики остаются в общем реестре.

Идентификаторы устойчивы: они используются нативным ``telos_load_capability`` и
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
        "точный срок одной задачи; количества, распределение и просроченные задачи. "
        "Помочь сформулировать задачу, подготовить черновик, задать существенный вопрос "
        "или предложить декомпозицию большой идеи без записи.",
        frozenset(
            {
                "telos_list_tasks",
                "telos_get_task_statistics",
                "telos_list_overdue_tasks",
                "telos_get_task",
                "telos_create_task",
                "telos_prepare_task",
                "telos_update_task",
                "telos_move_task",
                "telos_delete_task",
                "telos_set_checklist",
                "telos_fix_task_baseline",
            }
        ),
    ),
    Workflow(
        "comments",
        "Комментарии задачи: прочитать обсуждение, добавить или изменить комментарий.",
        frozenset(
            {
                "telos_list_comments",
                "telos_add_comment",
                "telos_update_comment",
                "telos_delete_comment",
            }
        ),
    ),
    Workflow(
        "documents",
        "Документы проекта и конкретной задачи: перечень по task_id, чтение текста, создание/изменение; "
        "прикрепление файла из переписки к существующей или только что созданной задаче.",
        frozenset(
            {
                "telos_list_documents",
                "telos_get_document",
                "telos_create_document",
                "telos_update_document",
                "telos_delete_document",
                "telos_link_document",
                "telos_unlink_document",
                "telos_copy_document",
                "telos_create_text_attachment",
                "telos_attach_uploaded_file",
            }
        ),
    ),
    Workflow(
        "reports",
        "Сохранённый обзор/разбор проекта Ариса, последняя аналитическая сводка; "
        "опубликованные итоговые отчёты задач: наличие, чтение и новая версия.",
        frozenset(
            {
                "telos_get_latest_project_summary",
                "telos_list_task_reports",
                "telos_get_task_report",
                "telos_publish_task_report",
            }
        ),
    ),
    Workflow(
        "project",
        "Паспорт проекта, команда, стадии канбана и история событий за день или период.",
        frozenset(
            {
                "telos_get_project",
                "telos_update_project",
                "telos_list_members",
                "telos_add_member",
                "telos_remove_member",
                "telos_transfer_project_ownership",
                "telos_list_stages",
                "telos_create_stage",
                "telos_update_stage",
                "telos_delete_stage",
                "telos_list_project_history",
            }
        ),
    ),
    Workflow(
        "calendar",
        "Календарь, вехи, зависимости, календарные сигналы и перепланирование с preview; "
        "точная дата одной задачи без перепланирования — tasks.",
        frozenset(
            {
                "telos_list_milestones",
                "telos_create_milestone",
                "telos_update_milestone",
                "telos_delete_milestone",
                "telos_list_dependencies",
                "telos_create_dependency",
                "telos_delete_dependency",
                "telos_get_calendar",
                "telos_preview_schedule_change",
                "telos_apply_schedule_change",
            }
        ),
    ),
    Workflow(
        "risks",
        "Реестр зарегистрированных рисков проекта: прочитать, создать или изменить риск.",
        frozenset(
            {
                "telos_list_risks",
                "telos_get_risk",
                "telos_create_risk",
                "telos_update_risk",
                "telos_delete_risk",
            }
        ),
    ),
    Workflow(
        "structure",
        "ИСР: дерево разделов, декомпозиция и размещение задач; свободный пул и холст.",
        frozenset(
            {
                "telos_get_structure",
                "telos_create_wbs_node",
                "telos_update_wbs_node",
                "telos_move_wbs_node",
                "telos_delete_wbs_node",
                "telos_place_task",
            }
        ),
    ),
    Workflow(
        "boards",
        "Доски проекта: стикеры, редактируемые схемы, фигуры, стрелки и упорядочивание выделения.",
        frozenset(
            {
                "telos_list_boards",
                "telos_get_board_scene",
                "telos_draw_board_diagram",
                "telos_update_board_shapes",
                "telos_arrange_board_objects",
                "telos_list_stickers",
                "telos_create_sticker",
                "telos_update_sticker",
                "telos_move_sticker",
                "telos_delete_sticker",
            }
        ),
    ),
    Workflow(
        "personal_boards",
        "Личные доски пользователя: найти, прочитать, нарисовать или упорядочить схему.",
        frozenset(
            {
                "telos_list_personal_boards",
                "telos_get_personal_board_scene",
                "telos_draw_personal_board_diagram",
                "telos_update_personal_board_shapes",
                "telos_arrange_personal_board_objects",
            }
        ),
    ),
    Workflow(
        "personal",
        "Мои задачи и доступные проекты; поиск по всем проектам; создание разовой задачи "
        "и её статус. Для «мои задачи», в том числе просроченных.",
        frozenset(
            {
                "telos_list_my_tasks",
                "telos_list_my_projects",
                "telos_search_tasks",
                "telos_get_any_task",
                "telos_create_personal_task",
                "telos_set_task_status",
            }
        ),
    ),
    Workflow(
        "project_actions",
        "В личном диалоге: чтение или изменение другого проекта по его ключу "
        "и точные схемы проектных действий.",
        frozenset({"telos_run_project_action", "telos_get_tool_schemas"}),
    ),
    Workflow(
        "chat_history",
        "Переписка текущего чата: резюме того, что мы выше обсуждали; что я/участник спрашивал или писал; последние/старые сообщения, поиск по периоду и автору, окружение, точные цитаты и вложения сообщений.",
        frozenset({"telos_list_chat_messages", "telos_get_chat_message", "telos_read_chat_file"}),
    ),
    Workflow(
        "sources",
        "Материалы трекера: задачи, документы, отчёты и вложения, чтение оригиналов "
        "и связанных объектов. Поиск здесь не охватывает код Git; для реализации используй git.",
        frozenset(
            {
                "telos_list_sources",
                "telos_read_source",
                "telos_list_related_sources",
                "telos_search_sources",
            }
        ),
    ),
    Workflow(
        "context",
        "Отобрать достаточный контекст конкретного вопроса: компактный паспорт, карта знаний, противоречия, пробелы и адресное дочитывание evidence. Не считать пустой паспорт или отсутствие задач автоматическим блокером ясного поручения.",
        frozenset({"telos_select_context", "telos_get_knowledge_map", "telos_get_knowledge_claim"}),
    ),
    Workflow(
        "git",
        "Как устроено и реализовано приложение: авторизация, API, настройки, модули и тесты. Поиск и чтение кода подключённых репозиториев, версии и связанные изменения задач. Точные repository/SHA/path/environment; PR/CI/deploy не доказывают production друг друга.",
        frozenset(
            {
                "telos_list_git_connections",
                "telos_search_git_sources",
                "telos_read_git_file",
                "telos_get_git_map",
                "telos_list_git_snapshots",
                "telos_list_git_symbols",
                "telos_get_git_graph",
                "telos_get_git_impact",
                "telos_list_task_git_links",
            }
        ),
    ),
    Workflow(
        "web",
        "Публичный веб после отдельного человеческого согласия на точную тему/URL. Читать/искать можно без права бизнес-записи; сохранение исследования — только явно порученная запись в документы.",
        frozenset(
            {
                "telos_request_web_search",
                "telos_request_web_page",
                "telos_search_web",
                "telos_read_web_page",
                "telos_save_web_research",
            }
        ),
    ),
)


def workflow_for_tool(name: str) -> str:
    """Новые инструменты получают отдельный набор, не открывающий весь реестр."""
    return next((workflow.id for workflow in WORKFLOWS if name in workflow.tools), f"tool_{name}")
