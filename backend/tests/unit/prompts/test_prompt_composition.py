
from src.prompts.analytics import (
    ANALYTICS_PORTFOLIO_SYSTEM_PROMPT,
    ANALYTICS_PROJECT_SYSTEM_PROMPT,
)
from src.prompts.base import (
    LANGUAGE_RULE,
    PM_ROLE,
    UNTRUSTED_DATA_RULE,
    WORKSPACE_CONTEXT,
    build_system_prompt,
)
from src.prompts.project_agent import (
    PROJECT_AGENT_SYSTEM_PROMPT,
    PROJECT_AGENT_TOOL_SELECTION_PROMPT,
)
from src.prompts.task_description import TASK_DESCRIPTION_REPHRASE_PROMPT
from src.prompts.wbs_suggestion import WBS_SUGGESTION_SYSTEM_PROMPT

ALL_PROMPTS = {
    "analytics_portfolio": ANALYTICS_PORTFOLIO_SYSTEM_PROMPT,
    "analytics_project": ANALYTICS_PROJECT_SYSTEM_PROMPT,
    "wbs_suggestion": WBS_SUGGESTION_SYSTEM_PROMPT,
    "task_description": TASK_DESCRIPTION_REPHRASE_PROMPT,
    "project_agent": PROJECT_AGENT_SYSTEM_PROMPT,
    "tool_selection": PROJECT_AGENT_TOOL_SELECTION_PROMPT,
}

ROLE_PROMPTS = {name: value for name, value in ALL_PROMPTS.items() if name != "tool_selection"}


def test_every_prompt_keeps_the_shared_composition_rules() -> None:
    """Каждый prompt объявляет данные недоверенными и требует JSON.

    Правило проверяется по всему набору сразу: сообщение называет все
    prompt-ы, из которых блок выпал, а не первый попавшийся.
    """
    without_untrusted = [name for name, text in ALL_PROMPTS.items() if UNTRUSTED_DATA_RULE not in text]
    assert not without_untrusted, f"Нет правила о недоверенных данных: {without_untrusted}"

    without_json = [
        name
        for name, text in ALL_PROMPTS.items()
        if "Верни" not in text or "JSON" not in text
    ]
    assert not without_json, f"Нет объявленного JSON-контракта: {without_json}"


def test_reasoning_prompts_open_with_the_shared_role_and_context() -> None:
    """Рассуждающие prompt-ы начинаются с роли и несут контекст трекера.

    Инструмент выбора инструментов сюда не входит намеренно: роль и
    языковое правило только мешали бы ему выбирать.
    """
    problems = [
        name
        for name, text in ROLE_PROMPTS.items()
        if not text.startswith(PM_ROLE) or WORKSPACE_CONTEXT not in text
    ]
    assert not problems, f"Роль или контекст трекера потеряны: {problems}"

    assert PM_ROLE not in PROJECT_AGENT_TOOL_SELECTION_PROMPT
    assert WORKSPACE_CONTEXT not in PROJECT_AGENT_TOOL_SELECTION_PROMPT
    assert LANGUAGE_RULE not in PROJECT_AGENT_TOOL_SELECTION_PROMPT


def test_role_names_the_principles_instead_of_relying_on_a_standard_edition() -> None:
    assert "PMBOK" in PM_ROLE
    # Номер издания намеренно не зашит: модель не получит от него знаний, зато
    # получит повод уверенно сослаться на несуществующее положение.
    assert "правило 100%" in PM_ROLE
    assert "критическим путём" in PM_ROLE
    assert not any(f"PMBOK {number}" in PM_ROLE for number in ("6", "7", "8", "9"))


def test_task_description_keeps_the_ban_on_inventing_facts_under_the_pm_role() -> None:
    assert PM_ROLE in TASK_DESCRIPTION_REPHRASE_PROMPT
    assert "не добавляй факты" in TASK_DESCRIPTION_REPHRASE_PROMPT
    assert "не заполняй догадкой" in TASK_DESCRIPTION_REPHRASE_PROMPT


def test_build_system_prompt_keeps_declared_order_of_blocks() -> None:
    prompt = build_system_prompt("Инструкция операции.")

    assert prompt.index(PM_ROLE) < prompt.index(WORKSPACE_CONTEXT)
    assert prompt.index(WORKSPACE_CONTEXT) < prompt.index(UNTRUSTED_DATA_RULE)
    assert prompt.index(UNTRUSTED_DATA_RULE) < prompt.index(LANGUAGE_RULE)
    assert prompt.endswith("Инструкция операции.")


def test_build_system_prompt_without_role_keeps_untrusted_data_rule() -> None:
    prompt = build_system_prompt("Инструкция.", with_role=False, with_language=False)

    assert prompt == f"{UNTRUSTED_DATA_RULE}\n\nИнструкция."
