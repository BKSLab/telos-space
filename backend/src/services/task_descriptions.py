from __future__ import annotations

import json
import logging
from pathlib import Path

from src.clients.llm import LlmClient
from src.clients.vision import VisionCapability
from src.exceptions.ai import AiProviderError
from src.exceptions.clients import ClientError
from src.exceptions.documents import DocumentsRepositoryError
from src.exceptions.projects import ProjectNotFoundError, ProjectsRepositoryError
from src.exceptions.tasks import (
    TaskContextDocumentError,
    TaskContextFileError,
    TaskDescriptionRewriteError,
    TaskNotFoundError,
    TasksRepositoryError,
    TasksServiceError,
)
from src.knowledge.extract import INDEXABLE_EXTENSIONS, extract_indexable_text
from src.prompts.task_description import TASK_DESCRIPTION_REPHRASE_PROMPT
from src.schemas.tasks import TaskRephraseFile, TaskRephraseRequestSchema, TaskRephraseResultSchema
from src.services.db_scope import TaskDescriptionScopeFactory
from src.services.tasks import build_task_key
from src.utils.checklists import checklist_context

logger = logging.getLogger(__name__)

RELATED_TASKS_LIMIT = 12
PROJECT_DESCRIPTION_LIMIT = 3000
RELATED_TASK_DESCRIPTION_LIMIT = 900
DOCUMENT_CONTEXT_LIMIT = 5000
MAX_REPHRASE_FILES = 10


class TaskDescriptionService:
    """Создаёт улучшенный черновик описания без изменения данных задачи."""

    def __init__(
        self,
        *,
        scope: TaskDescriptionScopeFactory,
        llm_client: LlmClient,
        vision: VisionCapability,
        file_context_limit: int,
        max_file_size: int,
    ) -> None:
        """Создаёт сервис переформулирования черновика задачи.

        Args:
            scope: Фабрика короткой области работы с базой. Сессия не
                передаётся: она не должна оставаться открытой во время
                вызова модели.
            llm_client: Клиент chat completions.
            vision: Способность распознавать изображения.
            file_context_limit: Предел длины текста из приложенных файлов.
            max_file_size: Предел размера одного файла контекста. Значение
                нужно транспорту, чтобы читать файл с ограничением.
        """
        self.scope = scope
        self.llm_client = llm_client
        self.vision = vision
        self.file_context_limit = file_context_limit
        self.max_file_size = max_file_size

    async def rephrase(
        self,
        *,
        project_id: int,
        data: TaskRephraseRequestSchema,
        files: list[TaskRephraseFile],
    ) -> TaskRephraseResultSchema:
        """Возвращает новый текст, оставляя пользовательский черновик несохранённым.

        Args:
            project_id: Проект, в контексте которого переписывается черновик.
            data: Черновик задачи и выбранные источники контекста.
            files: Приложенные к запросу файлы контекста.

        Returns:
            Переписанное описание задачи.

        Raises:
            ProjectNotFoundError: Если проект не найден.
            TaskNotFoundError: Если задача не принадлежит проекту.
            TaskContextDocumentError: Если выбранный документ недоступен.
            TaskContextFileError: Если файл контекста не разобран.
            TaskDescriptionRewriteError: Если модель вернула непригодный текст.
            AiProviderError: Если LLM-сервис недоступен.
            TasksServiceError: Если собрать контекст задачи не удалось.
        """
        project, current_task, selected_documents, related_tasks = await self._load_context(
            project_id=project_id,
            data=data,
        )
        extracted_files = await self._extract_files(files)

        payload = {
            "project": {
                "key": project.key,
                "name": project.name,
                "description": _clip(project.description_md, PROJECT_DESCRIPTION_LIMIT),
            },
            "task": {
                "key": (
                    build_task_key(project.key, current_task.number)
                    if current_task is not None
                    else None
                ),
                "title": data.title.strip(),
                "draft_description": data.description_md.strip(),
                "checklist": checklist_context(
                    data.checklist
                    if "checklist" in data.model_fields_set
                    else getattr(current_task, "checklist", None)
                ),
            },
            "related_task_wording": [
                {
                    "key": build_task_key(project.key, task.number),
                    "title": task.title,
                    "checklist": checklist_context(getattr(task, "checklist", None)),
                    "description": _clip(
                        task.description_md,
                        RELATED_TASK_DESCRIPTION_LIMIT,
                    ),
                }
                for task in related_tasks[:RELATED_TASKS_LIMIT]
            ],
            "selected_documents": [
                {
                    "title": document.title,
                    "content": _clip(document.content_md, DOCUMENT_CONTEXT_LIMIT),
                }
                for document in selected_documents
            ],
            "new_files": extracted_files,
            "length_guidance": _length_guidance(data.description_md),
        }

        # Перехват охватывает только вызов клиента: собственная доменная
        # проверка результата ниже не должна попадать под этот except.
        try:
            result = await self.llm_client.get_structured_response(
                system_prompt=TASK_DESCRIPTION_REPHRASE_PROMPT,
                content=json.dumps(payload, ensure_ascii=False),
                schema=TaskRephraseResultSchema,
                max_completion_tokens=1800,
            )
        except ClientError as error:
            logger.error("❌ LLM недоступен при переформулировании задачи.", exc_info=True)
            raise AiProviderError(str(error)) from error

        description = result.description_md.strip()
        if not description or len(description) > _maximum_result_length(data.description_md):
            raise TaskDescriptionRewriteError("LLM вернула пустое или чрезмерно длинное описание.")
        return TaskRephraseResultSchema(description_md=description)

    async def _load_context(
        self,
        *,
        project_id: int,
        data: TaskRephraseRequestSchema,
    ) -> tuple:
        """Читает проект, задачу, документы и соседние задачи одним срезом.

        Перехват ошибок репозитория охватывает только чтение: доменные
        проверки ниже выполняются уже вне него, поэтому собственную ошибку
        сервиса не приходится пропускать через `except ... : raise`.
        """
        # Короткая DB-фаза: снимок собирается и область закрывается до
        # обращения к модели.
        try:
            async with self.scope() as db:
                project = await db.projects.get_by_id(project_id=project_id)
                current_task = (
                    await db.tasks.get_by_id(task_id=data.task_id)
                    if data.task_id is not None
                    else None
                )
                documents = await db.documents.get_by_ids(set(dict.fromkeys(data.document_ids)))
                related_tasks = await db.tasks.get_by_project(project_id=project_id)
        except (ProjectsRepositoryError, TasksRepositoryError, DocumentsRepositoryError) as error:
            logger.error(
                "❌ Не удалось собрать контекст задачи в проекте id=%s.",
                project_id,
                exc_info=True,
            )
            raise TasksServiceError(str(error)) from error

        if project is None:
            raise ProjectNotFoundError(project_id=project_id)
        if data.task_id is not None and (
            current_task is None or current_task.project_id != project_id
        ):
            raise TaskNotFoundError(task_id=data.task_id)

        selected_documents = self._select_documents(
            project_id=project_id,
            document_ids=data.document_ids,
            documents=documents,
        )
        related_tasks = [task for task in related_tasks if task.id != data.task_id]
        related_tasks.sort(key=lambda task: task.updated_at, reverse=True)
        return project, current_task, selected_documents, related_tasks

    @staticmethod
    def _select_documents(*, project_id: int, document_ids: list[int], documents: list) -> list:
        """Проверяет, что каждый выбранный документ принадлежит проекту."""
        by_id = {document.id: document for document in documents}
        selected = []
        for document_id in dict.fromkeys(document_ids):
            document = by_id.get(document_id)
            if document is None or document.project_id != project_id:
                raise TaskContextDocumentError(document_id=document_id)
            selected.append(document)
        return selected

    async def _extract_files(self, files: list[TaskRephraseFile]) -> list[dict[str, str]]:
        if len(files) > MAX_REPHRASE_FILES:
            raise TaskContextFileError(file_name=f"более {MAX_REPHRASE_FILES} файлов")
        result: list[dict[str, str]] = []
        for file in files:
            if not file.content:
                raise TaskContextFileError(file_name=file.name or "без имени")
            if Path(file.name).suffix.lower() not in INDEXABLE_EXTENSIONS:
                raise TaskContextFileError(file_name=file.name)
            try:
                extracted = await extract_indexable_text(
                    file.name,
                    file.content,
                    vision=self.vision,
                    max_chars=self.file_context_limit,
                )
            except ValueError as error:
                raise TaskContextFileError(file_name=file.name) from error
            except ClientError as error:
                logger.error("❌ Vision недоступен при разборе файла контекста.", exc_info=True)
                raise AiProviderError(str(error)) from error
            if not extracted:
                raise TaskContextFileError(file_name=file.name)
            result.append({"name": file.name, "content": extracted})
        return result


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized if len(normalized) <= limit else f"{normalized[:limit].rstrip()}…"


def _maximum_result_length(source: str) -> int:
    """Не даёт модели превратить короткий черновик в длинную спецификацию."""
    return min(12_000, max(600, int(len(source.strip()) * 1.8)))


def _length_guidance(source: str) -> str:
    maximum = _maximum_result_length(source)
    return f"Не более {maximum} символов; предпочтительно близко к длине исходного текста."
