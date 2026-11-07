"""Общие SQL-проекции состояний без загрузки полного извлечённого текста."""

from sqlalchemy import Select, case, func, select

from src.db.models.file_processing_jobs import FileProcessingJob
from src.db.models.knowledge_attachment_texts import KnowledgeAttachmentText
from src.db.models.task_attachments import TaskAttachment
from src.db.models.tasks import Task


def fts_state_columns(job_status, cache_status, job_detail) -> tuple:
    """Выводит состояние атомарного PostgreSQL FTS по очереди и кешу."""
    return (
        case(
            (job_status.in_(("pending", "processing")), "pending"),
            (job_status == "failed", "failed"),
            (job_status == "ready", "ready"),
            (cache_status.in_(("ready", "empty", "unsupported", "disabled")), "ready"),
            else_="pending",
        ).label("fts_status"),
        job_detail.label("fts_detail"),
    )


def attachment_processing_query() -> Select:
    """Возвращает одну строку на оригинал с ограниченными метаданными стадий."""
    cache, job = KnowledgeAttachmentText, FileProcessingJob
    return (
        select(
            TaskAttachment.id,
            func.coalesce(cache.status, "pending").label("extraction_status"),
            cache.detail.label("extraction_detail"),
            *fts_state_columns(job.status, cache.status, job.detail),
            case((Task.project_id.is_(None), "disabled"), else_="pending").label("index_status"),
            case(
                (
                    Task.project_id.is_not(None),
                    "Состояние семантической индексации доступно в знаниях проекта.",
                ),
                else_=None,
            ).label("index_detail"),
        )
        .join(Task, Task.id == TaskAttachment.task_id)
        .outerjoin(cache, cache.attachment_id == TaskAttachment.id)
        .outerjoin(job, job.attachment_id == TaskAttachment.id)
    )
