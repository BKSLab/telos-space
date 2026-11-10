"""Вопрос агенту трекера от внешнего агента (менеджерский агент SD, #40793)."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.agent_conversations import TimPageContextSchema

#: Ключ беседы внешней системы: sd:chat:123 — одна беседа, один диалог агента.
EXTERNAL_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,254}$"


class AgentAskSchema(BaseModel):
    """Вопрос агенту трекера от имени сотрудника."""

    model_config = ConfigDict(extra="forbid")
    request_id: UUID | None = Field(None, description="Стабильный UUID запроса для безопасного повтора отправки.")
    text: str = Field(
        min_length=1,
        max_length=8000,
        description=(
            "Вопрос или поручение своими словами вызывающего агента. По нему агент ищет, "
            "но менять данные по нему не будет."
        ),
        examples=["Что просрочено у сотрудника по проекту CRM?"],
    )
    user_text: str | None = Field(
        default=None,
        max_length=8000,
        description=(
            "Дословная реплика сотрудника. Только она разрешает агенту изменения; "
            "без неё вопрос выполняется на чтение."
        ),
        examples=["Перенеси CRM-12 на пятницу"],
    )
    conversation_id: str | None = Field(
        default=None,
        pattern=EXTERNAL_KEY_PATTERN,
        description=(
            "Ключ беседы вызывающей системы, например sd:chat:123. Одна беседа — один диалог "
            "с памятью; без ключа каждый вопрос — новый диалог."
        ),
        examples=["sd:chat:4512"],
    )
    page_context: TimPageContextSchema | None = Field(
        default=None,
        description=(
            "Экран трекера, открытый у сотрудника (из сообщения встройки tracker:route): "
            "route, project_key, task_id, board_id. «Эта задача» — task_id. Прав не даёт: "
            "недоступный проект или задача молча игнорируются; ключ задачи в тексте главнее."
        ),
    )


class AgentActionLinkSchema(BaseModel):
    """Действие агента в ответе: выполненное или ждущее решения сотрудника."""

    id: UUID = Field(description="ID действия.")
    tool: str = Field(description="Имя инструмента.", examples=["telos_update_task"])
    title: str = Field(description="Что сделано или предложено.", examples=["Изменить задачу"])
    status: Literal["completed", "pending", "rejected", "failed"] = Field(
        description="Состояние действия."
    )
    task_key: str | None = Field(default=None, description="Ключ задачи, если действие о задаче.")
    url: str | None = Field(
        default=None, description="Ссылка на задачу в трекере; нет APP_PUBLIC_URL — нет ссылки."
    )
    confirm_url: str | None = Field(
        default=None,
        description=(
            "Только у pending: страница, где сотрудник сам подтверждает или отклоняет действие."
        ),
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Параметры предложенного действия (только у pending)."
    )


class AgentTaskLinkSchema(BaseModel):
    """Задача, на которую опирается ответ."""

    key: str = Field(description="Ключ задачи.", examples=["CRM-12"])
    title: str = Field(description="Название задачи.")
    url: str | None = Field(default=None, description="Ссылка на задачу в трекере.")


class AgentRequestResultSchema(BaseModel):
    """Состояние и результат вопроса агенту трекера."""

    request_id: UUID = Field(description="ID вопроса; по нему результат запрашивают повторно.")
    status: Literal["queued", "processing", "completed", "failed"] = Field(
        description="queued/processing — ответ готовится; completed — готов; failed — ошибка."
    )
    conversation_id: str | None = Field(default=None, description="Ключ беседы, если был задан.")
    answer: str | None = Field(
        default=None, description="Полный текст ответа для сотрудника (Markdown со ссылками)."
    )
    error: str | None = Field(default=None, description="Причина ошибки при status=failed.")
    progress: str | None = Field(
        default=None,
        description=(
            "Что агент делает сейчас, пока ответ готовится (queued/processing), например "
            "«Читаю задачу CRM-12…»; null — статуса ещё нет или ответ завершён."
        ),
        examples=["Ищу задачи: «отчёт»…"],
    )
    project_key: str | None = Field(
        default=None, description="Проект, в котором агент ответил; null — обзор или уточнение."
    )
    write_allowed: bool = Field(
        description="Была ли у вопроса дословная реплика сотрудника, разрешающая изменения."
    )
    links: list[AgentTaskLinkSchema] = Field(
        default_factory=list, description="Задачи, на которые опирается ответ."
    )
    done: list[AgentActionLinkSchema] = Field(
        default_factory=list, description="Выполненные действия."
    )
    pending: list[AgentActionLinkSchema] = Field(
        default_factory=list,
        description="Действия, которые ждут решения сотрудника по ссылке confirm_url.",
    )
    rejected: list[AgentActionLinkSchema] = Field(
        default_factory=list, description="Отклонённые сотрудником или не выполненные действия."
    )
