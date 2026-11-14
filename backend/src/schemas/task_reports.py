"""Контракт ручного отчёта и публикации комментария как отчёта."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TaskReportSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    task_id: int = Field(description="ID задачи.", examples=[5])
    revision: int = Field(default=0, description="Версия; 0 — отчёта ещё нет.", examples=[1])
    body_md: str = Field(
        default="", description="Сохранённый текст результата.", examples=["Результат проверен."]
    )
    author_name: str | None = Field(
        default=None, description="Автор публикации.", examples=["Кирилл Барабанщиков"]
    )
    author_id: int | None = Field(
        default=None, description="ID публикатора; null после удаления пользователя.", examples=[2]
    )
    created_at: datetime | None = Field(
        default=None, description="Время публикации.", examples=["2026-10-07T13:00:00Z"]
    )
    source_comment_id: int | None = Field(
        default=None, description="Ссылка на комментарий, если он существует.", examples=[3]
    )
    source_comment_original_id: int | None = Field(
        default=None, description="Первоначальный ID источника.", examples=[3]
    )
    source_author_name: str | None = Field(
        default=None, description="Снимок автора источника.", examples=["Кирилл"]
    )


class TaskReportWriteSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body_md: str = Field(
        max_length=50000,
        description="Новый текст. Пустой текст очищает текущий отчёт, сохраняя историю.",
        examples=["Что сделано…"],
    )
    expected_revision: int = Field(
        ge=0,
        strict=True,
        description="Прочитанная версия для защиты от потери правок.",
        examples=[0],
    )


class TaskReportFromCommentSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comment_id: int = Field(gt=0, description="Комментарий этой задачи.", examples=[3])
    expected_revision: int = Field(
        ge=0, strict=True, description="Прочитанная версия отчёта.", examples=[0]
    )
    expected_comment_body_md: str = Field(
        max_length=50000, description="Прочитанный текст источника.", examples=["Работа завершена."]
    )


class ProjectTaskReportSchema(BaseModel):
    """Актуальный результат одной задачи проекта."""

    task_id: int
    task_key: str
    task_title: str
    report: TaskReportSchema


class ProjectTaskReportsSchema(BaseModel):
    """Полнота перечня и явные границы страницы."""

    project_id: int
    total_tasks: int
    total_reports: int
    without_report: int
    items: list[ProjectTaskReportSchema]
    offset: int
    next_offset: int | None
