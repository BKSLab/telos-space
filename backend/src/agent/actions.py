"""Контракт доменной операции, общий для проектного чата и MCP."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from src.agent.aris import PROJECT_MODES, ArisMode
from src.agent.tools import AgentToolContext
from src.utils.tool_names import require_canonical_tool_name

if TYPE_CHECKING:
    from src.services.agent_tool_scope import AgentProjectToolScope


@dataclass(frozen=True, slots=True)
class ProjectAction:
    """Явно зарегистрированное действие без динамического доступа к методам."""

    name: str
    title: str
    description: str
    parameters: type[BaseModel]
    handler: Callable[
        ["AgentProjectToolScope", AgentToolContext, BaseModel], Awaitable[dict[str, Any]]
    ]
    mutating: bool = True
    requires_confirmation: bool = False
    owner_only: bool = False
    needs_project: bool = True
    """False — личное действие Ариса вне проекта (мои задачи, разовые)."""
    modes: frozenset[ArisMode] = PROJECT_MODES
    """Режимы Ариса, в которых действие доступно модели; MCP режимы не проверяет."""

    def __post_init__(self) -> None:
        require_canonical_tool_name(self.name)
