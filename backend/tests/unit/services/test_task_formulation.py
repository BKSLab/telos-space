"""Черновик сохраняет проверяемый смысл и не имитирует запись задачи."""

import pytest
from pydantic import ValidationError

from src.schemas.project_tool_inputs import TaskCreateInput
from src.schemas.task_formulation import TaskFormulationInput
from src.services.task_formulation import prepare_task_formulation


def test_formulation_preserves_known_content_and_normal_task_arguments_without_inventions():
    request = TaskFormulationInput.model_validate(
        {
            "kind": "draft",
            "draft": {
                "title": " Исправить сохранение формы ",
                "context": "После нажатия форма теряет введённый текст.",
                "expected_result": "Введённый текст сохраняется после повторного открытия.",
                "acceptance_criteria": ["Повторное открытие показывает сохранённые значения"],
                "checklist": ["Воспроизвести потерю текста", "Проверить повторное открытие"],
                "assumptions": ["Причина пока не установлена"],
            },
        }
    )
    result = prepare_task_formulation(request)
    proposal = result["proposals"][0]
    assert proposal["title"] == "Исправить сохранение формы"
    assert "## Ожидаемый результат" in proposal["description_md"]
    assert "## Предположения для обсуждения" in proposal["description_md"]
    assert "Причина пока не установлена" in proposal["description_md"]
    assert "## Цель" not in proposal["description_md"]
    assert result["requires_explicit_creation"] is True and "source_id" not in result
    assert set(proposal) == {"title", "description_md", "checklist"}
    parsed = TaskCreateInput.model_validate(proposal)
    assert parsed.title == proposal["title"]
    assert [item.text for item in parsed.checklist.items] == request.draft.checklist
    assert all(not item.is_completed for item in parsed.checklist.items)


def test_critical_question_and_large_idea_remain_read_only_proposals():
    clarified = prepare_task_formulation(
        TaskFormulationInput(kind="clarify", question="Какое поведение должно измениться?")
    )
    assert clarified == {
        "kind": "clarify",
        "question": "Какое поведение должно измениться?",
        "text": "Какое поведение должно измениться?",
    }
    result = prepare_task_formulation(
        TaskFormulationInput.model_validate(
            {
                "kind": "decompose",
                "parts": [
                    {"title": "Исследовать текущее поведение"},
                    {"title": "Согласовать ожидаемый результат"},
                ],
            }
        )
    )
    assert [item["title"] for item in result["proposals"]] == [
        "Исследовать текущее поведение",
        "Согласовать ожидаемый результат",
    ]
    assert all(
        set(item) == {"title", "description_md"} and item["description_md"] is None
        for item in result["proposals"]
    )
    assert result["requires_explicit_creation"] is True
    for invalid in (
        {"kind": "clarify", "question": "Вопрос?", "draft": {"title": "Лишняя запись"}},
        {"kind": "decompose", "parts": [{"title": "Одна часть"}]},
        {"kind": "draft", "draft": {"title": "Задача", "due_date": "2026-11-30"}},
        {"kind": "draft", "draft": {"title": "Задача", "assignee": "invented"}},
        {"kind": "created", "draft": {"title": "Ложное создание"}},
    ):
        with pytest.raises(ValidationError):
            TaskFormulationInput.model_validate(invalid)
