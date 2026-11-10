"""Подготовка проверяемых AI-предложений без изменения реестра."""

import json
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date

from src.clients.llm import LlmClient
from src.exceptions.ai import AiProviderError
from src.exceptions.api_keys import ApiKeyRouteNotAllowedError
from src.exceptions.base import RepositoryError
from src.exceptions.clients import ClientError
from src.exceptions.project_risks import ProjectRiskServiceError
from src.exceptions.projects import ProjectNotFoundError
from src.prompts.risk_suggestions import RISK_SUGGESTIONS_SYSTEM_PROMPT
from src.repositories.documents import DocumentsRepository
from src.repositories.milestones import MilestonesRepository
from src.repositories.project_risks import ProjectRiskRepository
from src.repositories.project_stages import ProjectStagesRepository
from src.repositories.projects import ProjectsRepository
from src.repositories.task_activity import TaskActivityRepository
from src.repositories.task_comments import TaskCommentsRepository
from src.repositories.task_dependencies import TaskDependenciesRepository
from src.repositories.tasks import TasksRepository
from src.repositories.wbs_nodes import WbsNodesRepository
from src.schemas.risk_suggestions import (
    RiskSuggestionDraftSetSchema,
    RiskSuggestionSchema,
    RiskSuggestionsSchema,
)
from src.services.access import AccessService
from src.services.auth import AuthService
from src.services.tasks import build_task_key
from src.utils.checklists import checklist_text

logger = logging.getLogger(__name__)
MAX_CONTEXT_CHARS = 90_000


@dataclass(frozen=True, slots=True)
class RiskSuggestionScope:
    """Короткая область аутентификации, проверки доступа и чтения источников."""

    auth: AuthService
    access: AccessService
    projects: ProjectsRepository
    tasks: TasksRepository
    stages: ProjectStagesRepository
    comments: TaskCommentsRepository
    documents: DocumentsRepository
    nodes: WbsNodesRepository
    risks: ProjectRiskRepository
    activity: TaskActivityRepository
    milestones: MilestonesRepository
    dependencies: TaskDependenciesRepository


RiskSuggestionScopeFactory = Callable[[], AbstractAsyncContextManager[RiskSuggestionScope]]


class RiskSuggestionService:
    """Возвращает черновики с основаниями; сохранение остаётся отдельным действием."""

    def __init__(self, *, scope: RiskSuggestionScopeFactory, llm_client: LlmClient) -> None:
        """Получает короткую область базы и готовый LLM-клиент.

        Args:
            scope: Фабрика, закрывающая также сессию авторизации до вызова модели.
            llm_client: Клиент с настроенными таймаутами и повторами.
        """
        self.scope = scope
        self.llm_client = llm_client

    async def suggest(
        self, *, project_id: int, session_token: str | None, bearer_secret: str | None
    ) -> RiskSuggestionsSchema:
        """Готовит предложения по доступному проекту без записи рисков.

        Args:
            project_id: Проект для анализа.
            session_token: Подписанная сессия пользователя.
            bearer_secret: Секрет API-токена, если запрос выполнен внешним клиентом.

        Returns:
            Предложения с серверными основаниями и проверенными связями.

        Raises:
            AuthServiceError: Пользователь не авторизован.
            AccessServiceError: Проект недоступен.
            ProjectNotFoundError: Проект отсутствует.
            ProjectRiskServiceError: Ошибка подготовки или нормализации черновика.
            AiProviderError: Модель недоступна.
        """
        logger.info("🚀 Подготовка AI-предложений рисков проекта id=%s.", project_id)
        try:
            async with self.scope() as db:
                principal = await db.auth.resolve_principal(
                    session_token=session_token, bearer_secret=bearer_secret
                )
                if getattr(principal, "api_key", None) is not None:
                    raise ApiKeyRouteNotAllowedError('risk suggestions')
                await db.access.ensure_project_access(
                    project_id=project_id, user_id=principal.user_id
                )
                project = await db.projects.get_by_id(project_id)
                if project is None:
                    raise ProjectNotFoundError(project_id)
                tasks = (await db.tasks.get_by_project(project_id))[:100]
                stages = {
                    stage.id: stage.name for stage in await db.stages.get_by_project(project_id)
                }
                comments = (await db.comments.get_for_tasks({task.id for task in tasks}))[-40:]
                documents = (await db.documents.get_by_project(project_id))[:12]
                nodes = (await db.nodes.get_by_project(project_id))[:60]
                activity = await db.activity.get_recent_by_project(project_id, limit=30)
                milestones = (await db.milestones.get_by_project(project_id))[:30]
                dependencies = (await db.dependencies.get_by_project(project_id))[:100]
                existing = await db.risks.get_by_project(project_id)
                # Снимок содержит только значения: ORM не выходит во внешнюю фазу.
                sources = [
                    f"{project.key} · {project.name}: {(project.description_md or '')[:1800]}"
                ]
                task_ids = {}
                for task in tasks:
                    key = build_task_key(project.key, task.number)
                    task_ids[key] = task.id
                    sources.append(
                        f"{key}: {task.title}; стадия={stages.get(task.stage_id, '')}; срок={task.due_date}; {(task.description_md or '')[:500]}\n"
                        f"{checklist_text(getattr(task, 'checklist', None))}"
                    )
                task_keys = {value: key for key, value in task_ids.items()}
                sources.extend(
                    f"Комментарий к {task_keys[comment.task_id]}: {comment.body_md[:500]}"
                    for comment in comments
                    if comment.task_id in task_keys
                )
                sources.extend(
                    f"Документ {document.title}: {document.content_md[:1500]}"
                    for document in documents
                )
                sources.extend(f"Раздел ИСР: {node.title}" for node in nodes)
                sources.extend(
                    f"Веха {item.title}: срок={item.due_date}, состояние={item.status.value}"
                    for item in milestones
                )
                sources.extend(
                    f"История {task_keys[item.task_id]}: {item.event_type.value}, {str(item.from_value)[:150]} → {str(item.to_value)[:150]}, {item.created_at.isoformat()}"
                    for item in activity
                    if item.task_id in task_keys
                )
                sources.extend(
                    f"Зависимость: {task_keys[item.predecessor_task_id]} → {task_keys[item.successor_task_id]}, задержка={item.lag_days} дней"
                    for item in dependencies
                    if item.predecessor_task_id in task_keys and item.successor_task_id in task_keys
                )
                evidence = {f"S{index}": text for index, text in enumerate(sources, 1)}
                existing_titles = {risk.title.strip().casefold() for risk in existing}
                snapshot = {
                    "today": str(date.today()),
                    "sources": evidence,
                    "task_keys": list(task_ids),
                    "existing_risks": [risk.title for risk in existing][:100],
                    "context_sampled": True,
                }
                content = json.dumps(snapshot, ensure_ascii=False)
                # Сохраняем основания каждого вида, уменьшая фрагменты текста,
                # если большой проект не помещается в контекст модели.
                fragment_limit = 1000
                while len(content) > MAX_CONTEXT_CHARS:
                    fragment_limit //= 2
                    evidence = {key: value[:fragment_limit] for key, value in evidence.items()}
                    snapshot["sources"] = evidence
                    content = json.dumps(snapshot, ensure_ascii=False)
        except RepositoryError as error:
            logger.exception("❌ Ошибка источников рисков проекта id=%s.", project_id)
            raise ProjectRiskServiceError(str(error)) from error
        try:
            output = await self.llm_client.get_structured_response(
                system_prompt=RISK_SUGGESTIONS_SYSTEM_PROMPT,
                content=content,
                schema=RiskSuggestionDraftSetSchema,
                max_completion_tokens=6000,
            )
            suggestions = []
            seen = set(existing_titles)
            for draft in output.suggestions:
                refs = list(dict.fromkeys(ref for ref in draft.evidence_refs if ref in evidence))
                title = draft.title.casefold()
                if not refs or title in seen:
                    continue
                seen.add(title)
                suggestions.append(
                    RiskSuggestionSchema(
                        **draft.model_dump(exclude={"evidence_refs", "task_key"}),
                        task_id=task_ids.get(draft.task_key),
                        evidence=[evidence[ref] for ref in refs],
                    )
                )
        except ClientError as error:
            logger.exception("❌ Модель недоступна для предложения рисков.")
            raise AiProviderError(str(error)) from error
        except Exception as error:
            logger.exception("❌ Непригодное AI-предложение рисков.")
            raise ProjectRiskServiceError(str(error)) from error
        logger.info(
            "✅ Подготовлено предложений рисков: %s, проект id=%s.", len(suggestions), project_id
        )
        return RiskSuggestionsSchema(suggestions=suggestions)
