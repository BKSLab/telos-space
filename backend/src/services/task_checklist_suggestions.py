"""AI-черновик чек-листа с короткой авторизованной фазой чтения."""

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path

from src.clients.llm import LlmClient
from src.clients.vision import VisionCapability
from src.exceptions.ai import AiProviderError
from src.exceptions.api_keys import ApiKeyRouteNotAllowedError
from src.exceptions.base import RepositoryError
from src.exceptions.clients import ClientError
from src.exceptions.projects import ProjectNotFoundError
from src.exceptions.storage import TaskAttachmentStorageError
from src.exceptions.tasks import (
    TaskChecklistGenerationError,
    TaskContextDocumentError,
    TaskContextFileError,
    TaskNotFoundError,
    TasksServiceError,
)
from src.knowledge.extract import INDEXABLE_EXTENSIONS, extract_indexable_text
from src.prompts.task_checklist import TASK_CHECKLIST_PROMPT
from src.repositories.document_links import DocumentLinksRepository
from src.repositories.documents import DocumentsRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_attachments import TaskAttachmentsRepository
from src.repositories.tasks import TasksRepository
from src.schemas.task_checklists import (
    ChecklistItemSchema,
    ChecklistSuggestionDraftSchema,
    ChecklistSuggestionRequestSchema,
    ChecklistSuggestionSchema,
    TaskChecklistSchema,
)
from src.schemas.tasks import TaskRephraseFile
from src.services.access import AccessService
from src.services.auth import AuthService
from src.storage.task_attachments import TaskAttachmentStorage
from src.utils.checklists import checklist_context

logger = logging.getLogger(__name__)
MAX_FILES = 10
MAX_CONTEXT_CHARS = 60_000


@dataclass(frozen=True, slots=True)
class ChecklistSuggestionScope:
    """Авторизация и источники задачи в одной короткой области."""

    auth: AuthService
    access: AccessService
    projects: ProjectsRepository
    tasks: TasksRepository
    documents: DocumentsRepository
    links: DocumentLinksRepository
    attachments: TaskAttachmentsRepository


ChecklistSuggestionScopeFactory = Callable[
    [], AbstractAsyncContextManager[ChecklistSuggestionScope]
]


class TaskChecklistSuggestionService:
    """Предлагает чек-лист без изменения задачи, файлов и индекса."""

    def __init__(
        self,
        *,
        scope: ChecklistSuggestionScopeFactory,
        llm_client: LlmClient,
        vision: VisionCapability,
        storage: TaskAttachmentStorage,
        file_context_limit: int,
        max_file_size: int,
    ) -> None:
        """Получает явные зависимости сценария.

        Args:
            scope: Короткая область проверки доступа и чтения.
            llm_client: Клиент генерации.
            vision: Распознавание поддерживаемых изображений.
            storage: Хранилище существующих вложений.
            file_context_limit: Максимум символов на извлечённый файл.
            max_file_size: Максимум байт одного файла.
        """
        self.scope = scope
        self.llm_client = llm_client
        self.vision = vision
        self.storage = storage
        self.file_context_limit = file_context_limit
        self.max_file_size = max_file_size

    async def suggest(
        self,
        *,
        project_id: int,
        data: ChecklistSuggestionRequestSchema,
        files: list[TaskRephraseFile],
        session_token: str | None,
        bearer_secret: str | None,
    ) -> ChecklistSuggestionSchema:
        """Возвращает предложение по разрешённым источникам.

        Args:
            project_id: Проект задачи.
            data: Текущие значения полей формы.
            files: Новые файлы из формы, ещё не сохранённые.
            session_token: Сессия пользователя.
            bearer_secret: API-токен, если используется.

        Returns:
            Чек-лист для принятия или редактирования и границы прочитанных файлов.

        Raises:
            AuthServiceError: Нет авторизации.
            AccessServiceError: Нет доступа к проекту.
            TaskNotFoundError: Задача отсутствует или относится к другому проекту.
            TaskContextDocumentError: Документ недоступен.
            TaskContextFileError: Превышены ограничения файлов.
            TasksServiceError: Ошибка чтения.
            TaskChecklistGenerationError: Некорректный ответ модели.
            AiProviderError: AI-провайдер недоступен.
        """
        warnings: list[str] = []
        files = [TaskRephraseFile(name=file.name[:512], content=file.content) for file in files]
        try:
            async with self.scope() as db:
                principal = await db.auth.resolve_principal(
                    session_token=session_token, bearer_secret=bearer_secret
                )
                if getattr(principal, "api_key", None) is not None:
                    raise ApiKeyRouteNotAllowedError('checklist suggestions')
                await db.access.ensure_project_access(
                    project_id=project_id, user_id=principal.user_id
                )
                project = await db.projects.get_by_id(project_id)
                if project is None:
                    raise ProjectNotFoundError(project_id)
                task = await db.tasks.get_by_id(data.task_id) if data.task_id is not None else None
                if data.task_id is not None and (task is None or task.project_id != project_id):
                    raise TaskNotFoundError(data.task_id)
                links = await db.links.get_for_task(data.task_id) if task is not None else []
                document_ids = set(data.document_ids) | {link.document_id for link in links}
                documents = await db.documents.get_by_ids(document_ids)
                by_id = {document.id: document for document in documents}
                for document_id in document_ids:
                    if document_id not in by_id or by_id[document_id].project_id != project_id:
                        raise TaskContextDocumentError(document_id=document_id)
                attachments = await db.attachments.get_for_task(task.id) if task is not None else []
                existing_files = [(item.original_name, item.storage_key) for item in attachments]
                value = (
                    data.checklist
                    if "checklist" in data.model_fields_set
                    else getattr(task, "checklist", None)
                )
                description = (
                    data.description_md
                    if "description_md" in data.model_fields_set
                    else getattr(task, "description_md", None) or ""
                )
                payload = {
                    "project": {"key": project.key, "name": project.name},
                    "task": {
                        "title": data.title,
                        "description": description[:20_000],
                        "checklist": checklist_context(value, chars=200),
                    },
                    "documents": [
                        {"title": document.title, "content": document.content_md[:3000]}
                        for document in sorted(documents, key=lambda item: item.id)[:20]
                    ],
                    "files": [],
                }
                if len(documents) > 20 or any(len(item.content_md) > 3000 for item in documents):
                    warnings.append(
                        "Документы переданы ограниченными фрагментами, не более 20 документов."
                    )
                if len(description) > 20_000:
                    warnings.append("Описание задачи передано фрагментом до 20 000 символов.")
                if value and any(
                    len(item["text"]) > 200
                    for item in (
                        value.model_dump() if isinstance(value, TaskChecklistSchema) else value
                    )["items"]
                ):
                    warnings.append("Длинные пункты текущего чек-листа переданы сокращённо.")
        except RepositoryError as error:
            logger.error("❌ Не удалось собрать контекст чек-листа.", exc_info=True)
            raise TasksServiceError(str(error)) from error

        if len(files) > MAX_FILES or any(len(file.content) > self.max_file_size for file in files):
            raise TaskContextFileError(file_name="превышены ограничения файлов контекста")
        # Чтение диска и распознавание начинаются только после закрытия DB-сессии.
        selected_files = list(files)
        for name, storage_key in existing_files[: max(0, MAX_FILES - len(files))]:
            try:
                path = self.storage.resolve(storage_key)
                content = await asyncio.to_thread(_read_bounded, path, self.max_file_size)
                selected_files.append(TaskRephraseFile(name=name, content=content))
            except (TaskAttachmentStorageError, OSError, ValueError):
                warnings.append(f"Файл «{name}» недоступен или превышает ограничение размера.")
        if len(files) + len(existing_files) > MAX_FILES:
            warnings.append(
                f"Использованы только первые {MAX_FILES} файлов, начиная с выбранных в форме."
            )
        for file in selected_files:
            if Path(file.name).suffix.lower() not in INDEXABLE_EXTENSIONS:
                warnings.append(f"Файл «{file.name}»: формат не поддерживается.")
                continue
            try:
                extracted = await extract_indexable_text(
                    file.name,
                    file.content,
                    vision=self.vision,
                    max_chars=min(self.file_context_limit, 5000),
                )
            except (ValueError, OSError):
                warnings.append(f"Не удалось прочитать содержимое файла «{file.name}».")
                continue
            except ClientError as error:
                raise AiProviderError(str(error)) from error
            if not extracted:
                warnings.append(f"В файле «{file.name}» нет доступного текста.")
                continue
            payload["files"].append({"name": file.name, "content": extracted})
            if len(extracted) >= min(self.file_context_limit, 5000):
                warnings.append(f"Файл «{file.name}» передан ограниченным фрагментом.")
        payload["source_warnings"] = warnings
        content = json.dumps(payload, ensure_ascii=False)
        while len(content) > MAX_CONTEXT_CHARS:
            previous_size = len(content)
            payload["task"]["description"] = payload["task"]["description"][
                : len(payload["task"]["description"]) // 2
            ]
            for source in payload["documents"] + payload["files"]:
                source["content"] = source["content"][: len(source["content"]) // 2]
            if "Большой контекст сокращён до доступного объёма." not in warnings:
                warnings.append("Большой контекст сокращён до доступного объёма.")
            content = json.dumps(payload, ensure_ascii=False)
            if len(content) >= previous_size:
                raise TaskChecklistGenerationError(
                    "Не удалось уложить контекст в допустимый объём."
                )
        try:
            draft = await self.llm_client.get_structured_response(
                system_prompt=TASK_CHECKLIST_PROMPT,
                content=content,
                schema=ChecklistSuggestionDraftSchema,
                max_completion_tokens=1200,
            )
            # Защищает также клиентов, вернувших объект без валидации схемой.
            draft = ChecklistSuggestionDraftSchema.model_validate(draft)
        except ClientError as error:
            logger.error("❌ AI-провайдер недоступен при генерации чек-листа.", exc_info=True)
            raise AiProviderError(str(error)) from error
        except Exception as error:
            logger.error("❌ Некорректный AI-черновик чек-листа.", exc_info=True)
            raise TaskChecklistGenerationError(str(error)) from error
        return ChecklistSuggestionSchema(
            checklist=TaskChecklistSchema(
                items=[ChecklistItemSchema(text=text) for text in draft.items]
            ),
            warnings=warnings,
        )


def _read_bounded(path: Path, limit: int) -> bytes:
    """Читает сохранённое вложение с независимой проверкой размера."""
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Файл слишком большой.")
    return content
