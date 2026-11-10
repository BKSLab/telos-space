"""Компактное представление сущностей трекера для MCP-инструментов.

Наружу отдаются отображаемые ключи (``PROJ-142``), а не числовые
идентификаторы: агент оперирует тем же, что видит человек в интерфейсе.
Превью длинных текстов обрезаются с явной пометкой и метаданными;
отдельное чтение возвращает исходный текст без потерь.

На вход принимаются DTO сервисного слоя, а не ORM-модели: представление
не должно зависеть от того, как данные хранятся.
"""

from src.services.project_query import (
    CommentDto,
    ProjectOverviewDto,
    ProjectSummaryDto,
    TaskDetailsDto,
    TaskSummaryDto,
)

TEXT_LIMIT = 2000
TRUNCATION_NOTE = "… (текст обрезан)"


def shorten(text: str | None, limit: int = TEXT_LIMIT) -> str | None:
    """Обрезает длинный текст и честно помечает обрезку.

    Args:
        text: Исходный текст или ``None``.
        limit: Предельная длина.

    Returns:
        Текст в пределах лимита либо ``None``.
    """
    if not text:
        return None
    if len(text) <= limit:
        return text
    return f"{text[:limit]}{TRUNCATION_NOTE}"


def project_summary(project: ProjectSummaryDto) -> dict:
    """Возвращает краткую карточку проекта для списка."""
    return {
        "project_key": project.project_key,
        "name": project.name,
        "status": project.status,
        "start_date": project.start_date.isoformat() if project.start_date else None,
        "due_date": project.due_date.isoformat() if project.due_date else None,
    }


def project_detail(
    overview: ProjectOverviewDto, *, full_text: bool = False
) -> dict:
    """Возвращает карточку проекта с превью или полным описанием."""
    result = {
        **project_summary(overview.summary),
        "description": overview.description if full_text else shorten(overview.description),
        "total_tasks": overview.total_tasks,
        "stages": [
            {
                "name": stage.name,
                "is_done_stage": stage.is_done_stage,
                "task_count": stage.task_count,
            }
            for stage in overview.stages
        ],
    }
    result["description_meta"] = _text_metadata(
        overview.description,
        full_text=full_text,
        read_full={
            "tool": "telos_get_project",
            "arguments": {"project_key": overview.summary.project_key, "full_text": True},
        },
    )
    return result


def task_summary(task: TaskSummaryDto) -> dict:
    """Возвращает краткую карточку задачи для списка."""
    return {
        "task_key": task.task_key,
        "checklist": task.checklist,
        "title": task.title,
        "stage": task.stage,
        "is_done": task.is_done,
        "priority": task.priority,
        "assignee": task.assignee,
        "due_date": task.due_date.isoformat() if task.due_date else None,
    }


def task_detail(
    details: TaskDetailsDto, *, full_text: bool = False
) -> dict:
    """Возвращает карточку задачи с превью или полным описанием."""
    result = {
        **task_summary(details.summary),
        "role": details.role,
        "wbs_path": details.wbs_path,
        "description": details.description if full_text else shorten(details.description),
        "comment_count": details.comment_count,
    }
    result["description_meta"] = _text_metadata(
        details.description,
        full_text=full_text,
        read_full={
            "tool": "telos_get_task",
            "arguments": {"task_key": details.summary.task_key, "full_text": True},
        },
    )
    return result


def comment_item(
    comment: CommentDto, *, full_text: bool = False
) -> dict:
    """Возвращает комментарий с превью или полным текстом."""
    result = {
        "task_key": comment.task_key,
        "author": comment.author,
        "created_at": comment.created_at.isoformat(),
        "body": comment.body if full_text else shorten(comment.body),
    }
    result["body_meta"] = _text_metadata(
        comment.body,
        full_text=full_text,
        read_full=(
            {
                "tool": "telos_get_comment",
                "arguments": {"task_key": comment.task_key, "comment_id": comment.comment_id},
            }
            if comment.comment_id is not None else None
        ),
    )
    return result


def _text_metadata(text: str | None, *, full_text: bool, read_full: dict | None) -> dict:
    """Описывает обрезку по исходному тексту, независимо от его содержимого."""
    length = len(text) if text is not None else 0
    truncated = not full_text and length > TEXT_LIMIT
    return {
        "truncated": truncated,
        "length": length,
        "length_unit": "unicode_code_points",
        "read_full": read_full if truncated else None,
    }
