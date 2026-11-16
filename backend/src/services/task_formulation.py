"""Детерминированное оформление предложений; запись выполняет обычный task service."""

from src.schemas.task_formulation import TaskDraftSchema, TaskFormulationInput


def draft_arguments(draft: TaskDraftSchema) -> dict:
    """Возвращает обычные поля создания без назначенных людей, сроков или успеха."""
    sections = []
    for label, value in (
        ("Контекст", draft.context),
        ("Цель", draft.goal),
        ("Ожидаемый результат", draft.expected_result),
        ("Границы", draft.boundaries),
        ("Ограничения", draft.constraints),
    ):
        if value:
            sections.append(f"## {label}\n\n{value}")
    for label, values in (
        ("Критерии приёмки", draft.acceptance_criteria),
        ("Предположения для обсуждения", draft.assumptions),
        ("Открытые вопросы", draft.open_questions),
    ):
        if values:
            sections.append(f"## {label}\n\n" + "\n".join(f"- {value}" for value in values))
    result = {"title": draft.title, "description_md": "\n\n".join(sections) or None}
    if draft.checklist:
        result["checklist"] = {
            "title": "Проверка выполнения",
            "items": [{"text": value, "is_completed": False} for value in draft.checklist],
        }
    return result


def prepare_task_formulation(request: TaskFormulationInput) -> dict:
    """Готовит понятный текст и допустимые аргументы, не вызывая хранилище или LLM."""
    if request.kind == "clarify":
        return {"kind": "clarify", "question": request.question, "text": request.question}
    drafts = [request.draft] if request.kind == "draft" else request.parts
    proposals = [draft_arguments(draft) for draft in drafts]
    heading = "Черновик задачи" if request.kind == "draft" else "Предложение декомпозиции"
    rendered = []
    for number, proposal in enumerate(proposals, 1):
        title = proposal["title"] if request.kind == "draft" else f"{number}. {proposal['title']}"
        parts = [f"### {title}"]
        if proposal["description_md"]:
            parts.append(proposal["description_md"])
        if "checklist" in proposal:
            parts.append(
                "## Чек-лист\n\n"
                + "\n".join(f"- [ ] {item['text']}" for item in proposal["checklist"]["items"])
            )
        rendered.append("\n\n".join(parts))
    return {
        "kind": request.kind,
        "text": f"{heading}\n\n" + "\n\n".join(rendered),
        "proposals": proposals,
        "requires_explicit_creation": True,
    }
