"""Единый read-контракт поиска оригиналов и markdown-документов."""

from typing import Literal

from pydantic import BaseModel, Field

FileSearchScope = Literal["all", "project_files", "task_files", "documents"]
FileSearchSourceType = Literal["project_file", "task_attachment", "document"]


class FileSearchItemSchema(BaseModel):
    source_type: FileSearchSourceType = Field(
        description="Вид первичного источника.", examples=["task_attachment"]
    )
    source_id: int = Field(description="ID оригинала или документа.", examples=[12])
    project_id: int | None = Field(
        description="Текущий проект; null для личной задачи.", examples=[2, None]
    )
    task_id: int | None = Field(
        description="Задача оригинала или отфильтрованной связи документа.", examples=[7, None]
    )
    task_key: str | None = Field(
        None,
        description="Текущий отображаемый ключ задачи без дополнительного запроса.",
        examples=["PROJ-7", "T-8", None],
    )
    task_title: str | None = Field(
        None,
        description="Текущий заголовок задачи без дополнительного запроса.",
        examples=["Подготовить поставку", None],
    )
    document_slug: str | None = Field(
        None,
        description="URL-идентификатор найденного markdown-документа.",
        examples=["specification", None],
    )
    title: str = Field(
        description="Исходное имя файла или заголовок документа.", examples=["План.pdf"]
    )
    caption: str | None = Field(
        description="Пользовательская подпись файла.", examples=["План поставки", None]
    )
    content_type: str | None = Field(
        description="MIME оригинала; null для markdown-документа.",
        examples=["application/pdf", None],
    )
    preview_url: str | None = Field(
        description="Защищённый URL локальной растровой миниатюры.", examples=[None]
    )
    excerpt: str = Field(
        description="Ограниченный фрагмент с маркерами __FTS_START__/__FTS_END__, без HTML-разметки.",
        examples=["Согласовать __FTS_START__поставку__FTS_END__ оборудования."],
    )
    rank: float = Field(description="Относительная FTS-релевантность.", examples=[0.25])


class FileSearchPageSchema(BaseModel):
    items: list[FileSearchItemSchema] = Field(
        description="Источники в порядке релевантности с устойчивым порядком при равенстве.",
        examples=[[]],
    )
    total: int = Field(ge=0, description="Число совпавших первичных источников.", examples=[14])
    limit: int = Field(ge=1, description="Размер страницы.", examples=[30])
    offset: int = Field(ge=0, description="Смещение страницы.", examples=[0])
