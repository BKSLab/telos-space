"""Контракт доменной операции, общий для проектного чата и MCP."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from src.agent.aris import PROJECT_MODES, ArisMode
from src.agent.tools import AgentToolContext
from src.utils.tool_names import require_canonical_tool_name

if TYPE_CHECKING:
    from src.services.agent_chat_files import AgentChatFilesService
    from src.services.agent_chat_history import AgentChatHistoryService
    from src.services.agent_context_tools import AgentContextToolsService
    from src.services.agent_tool_scope import AgentProjectToolScope


@dataclass(frozen=True, slots=True)
class ProjectAction:
    """Явно зарегистрированное действие без динамического доступа к методам."""

    name: str
    title: str
    description: str
    parameters: type[BaseModel]
    handler: (
        Callable[["AgentProjectToolScope", AgentToolContext, BaseModel], Awaitable[dict[str, Any]]]
        | None
    ) = None
    mutating: bool = True
    requires_confirmation: bool = False
    owner_only: bool = False
    needs_project: bool = True
    """False — личное действие Ариса вне проекта (мои задачи, разовые)."""
    modes: frozenset[ArisMode] = PROJECT_MODES
    """Режимы Ариса, в которых действие доступно модели; MCP режимы не проверяет."""
    project_optional: bool = False
    required_scopes: frozenset[str] = frozenset()
    external_effect: bool = False
    """Передача точной темы/URL вовне: pending без права бизнес-записи."""
    context_handler: (
        Callable[
            ["AgentContextToolsService", AgentToolContext, BaseModel], Awaitable[dict[str, Any]]
        ]
        | None
    ) = None
    """Один документный сценарий работает и в личной задаче, сохраняя текущий контекст."""
    chat_file_handler: (
        Callable[["AgentChatFilesService", AgentToolContext, BaseModel], Awaitable[dict[str, Any]]]
        | None
    ) = None
    """Читает через собственные короткие области, без внешней транзакции инструмента."""
    chat_history_handler: (
        Callable[
            ["AgentChatHistoryService", AgentToolContext, BaseModel], Awaitable[dict[str, Any]]
        ]
        | None
    ) = None

    def __post_init__(self) -> None:
        require_canonical_tool_name(self.name)
        if (
            sum(
                handler is not None
                for handler in (
                    self.handler,
                    self.chat_file_handler,
                    self.chat_history_handler,
                    self.context_handler,
                )
            )
            != 1
        ):
            raise ValueError("Нужен ровно один обработчик действия.")
        if (
            self.chat_file_handler is not None or self.chat_history_handler is not None
        ) and self.mutating:
            raise ValueError("Файловый обработчик доступен только для чтения.")
        if self.external_effect and (
            self.mutating or not self.requires_confirmation or self.context_handler is None
        ):
            raise ValueError("Внешняя передача требует отдельного подтверждения и context handler.")
