"""Создание проекта через общий доменный сервис с durable-квитанцией."""

from uuid import UUID, uuid4

from src.exceptions.agent_tools import (
    AgentToolAccessError,
    AgentToolRequestConflictError,
    AgentToolsServiceError,
)
from src.exceptions.base import RepositoryError
from src.schemas.projects import ProjectCreateSchema
from src.services.access import AccessService
from src.services.agent_tool_scope import AgentProjectToolScopeFactory
from src.utils.api_scopes import SCOPE_PROJECTS_WRITE
from src.utils.request_actor import reset_request_actor, set_request_actor

CREATE_PROJECT_TOOL = "telos_create_project"


class ProjectCreationService:
    """Объединяет создание REST-проекта и результат повтора одной транзакцией."""

    def __init__(self, *, scope: AgentProjectToolScopeFactory) -> None:
        self.scope = scope

    async def create(
        self,
        *,
        actor_user_id: int,
        bearer_secret: str | None,
        data: ProjectCreateSchema,
        request_id: UUID,
    ) -> dict:
        """Создаёт проект или возвращает исходный результат UUID-запроса.

        Args:
            actor_user_id: Пользователь, проверенный транспортом до ожидания lock.
            bearer_secret: Текущие учётные данные; не сохраняются в квитанции.
            data: Тот же проверенный паспорт, который принимает REST.
            request_id: Стабильный UUID вне контекста существующего проекта.
        Returns:
            Исходные идентификатор, ключ, название и статус проекта.
        Raises:
            AgentToolRequestConflictError: UUID уже использован с другими аргументами.
            AuthServiceError: Ключ или пользователь больше не действуют либо scope отозван.
            AccessServiceError: Созданный ранее проект больше недоступен.
            ProjectsServiceError: Общий сценарий создания отклонён.
            AgentToolsServiceError: Проект и квитанцию сохранить не удалось.
        """
        arguments = data.model_dump(mode="json")
        try:
            async with self.scope() as db:
                # Общий namespace личных действий намеренно обнаруживает повтор UUID
                # с другой операцией. NULL-project квитанция переживает удаление проекта.
                await db.runs.lock_request(None, actor_user_id, request_id)
                # Credentials в этой сессии ещё не читались: после lock они свежие.
                # Commit диагностики AuthService завершает только SAVEPOINT,
                # поэтому достаточно одного соединения без раннего outer commit.
                principal = await db.auth.resolve_principal(
                    session_token=None, bearer_secret=bearer_secret
                )
                if principal.user_id != actor_user_id:
                    raise AgentToolAccessError("Пользователь вызова изменился.")
                principal.require(SCOPE_PROJECTS_WRITE)
                previous = await db.runs.get_request(None, principal.user_id, request_id)
                if previous is not None:
                    if (
                        previous.tool_name != CREATE_PROJECT_TOOL
                        or previous.arguments != arguments
                        or previous.status != "completed"
                    ):
                        raise AgentToolRequestConflictError(AgentToolRequestConflictError.detail)
                    grant = await db.access.ensure_project_access(
                        project_id=previous.result["project_id"], user_id=principal.user_id
                    )
                    AccessService.require_write(grant)
                    return dict(previous.result)
                actor_token = set_request_actor(principal.request_actor())
                try:
                    project = await db.projects.create_project(
                        data=data.model_dump(), owner_id=principal.user_id
                    )
                finally:
                    reset_request_actor(actor_token)
                result = {
                    "project_id": project.id,
                    "project_key": project.key,
                    "name": project.name,
                    "status": project.status.value,
                    "created": True,
                }
                await db.runs.save({
                    "id": uuid4(),
                    "project_id": None,
                    "user_id": principal.user_id,
                    "request_id": request_id,
                    "tool_name": CREATE_PROJECT_TOOL,
                    "title": "Создать проект",
                    "arguments": arguments,
                    "status": "completed",
                    "result": result,
                })
                await db.unit_of_work.commit()
                return result
        except RepositoryError as error:
            raise AgentToolsServiceError("Не удалось сохранить проект и квитанцию.") from error
