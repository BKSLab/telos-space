"""Черновик постановки: форматирование не создаёт задачу и не задаёт людей/сроки."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DraftItem = Annotated[str, Field(min_length=1, max_length=500)]


class TaskDraftSchema(BaseModel):
    """Необязательные смысловые разделы обычного описания задачи."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(
        min_length=1, max_length=512, description="Конкретное действие и предмет задачи."
    )
    context: str = Field(
        default="", max_length=2400, description="Подтверждённый контекст и текущее поведение."
    )
    goal: str = Field(
        default="", max_length=1500, description="Зачем нужна задача, если это известно."
    )
    expected_result: str = Field(
        default="",
        max_length=2400,
        description="Ожидаемое изменение или результат без выдуманных метрик.",
    )
    boundaries: str = Field(default="", max_length=1500, description="Известные границы работы.")
    constraints: str = Field(default="", max_length=1500, description="Подтверждённые ограничения.")
    acceptance_criteria: list[DraftItem] = Field(
        default_factory=list,
        max_length=12,
        description="Проверяемые условия принятия, если они известны.",
    )
    checklist: list[DraftItem] = Field(
        default_factory=list,
        max_length=20,
        description="Предложенные шаги; отметки выполнения отсутствуют.",
    )
    assumptions: list[DraftItem] = Field(
        default_factory=list,
        max_length=8,
        description="Явно помеченные предположения, не требования.",
    )
    open_questions: list[DraftItem] = Field(
        default_factory=list,
        max_length=8,
        description="Оставшиеся вопросы; не блокируют понятную простую задачу автоматически.",
    )


class TaskFormulationInput(BaseModel):
    """Ровно один сценарий подготовки, без бизнес-записи и самоподтверждения."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["draft", "clarify", "decompose"] = Field(
        description="Черновик, один существенный вопрос или предложение декомпозиции."
    )
    draft: TaskDraftSchema | None = Field(
        default=None, description="Одна предлагаемая задача для kind=draft."
    )
    question: str | None = Field(
        default=None,
        min_length=1,
        max_length=700,
        description="Один короткий вопрос, ответ на который меняет результат; только для kind=clarify.",
    )
    parts: list[TaskDraftSchema] = Field(
        default_factory=list,
        max_length=12,
        description="Предлагаемые задачи большой идеи; только для kind=decompose.",
    )

    @model_validator(mode="after")
    def one_formulation(self) -> "TaskFormulationInput":
        if self.kind == "draft" and (self.draft is None or self.question is not None or self.parts):
            raise ValueError("Черновик содержит только draft.")
        if self.kind == "clarify" and (not self.question or self.draft is not None or self.parts):
            raise ValueError("Уточнение содержит один question без черновиков.")
        if self.kind == "decompose" and (
            len(self.parts) < 2 or self.draft is not None or self.question is not None
        ):
            raise ValueError("Декомпозиция содержит от двух до двенадцати parts.")
        return self
