"""Бюджет каждого раунда, продолжения страниц и сохранность квитанций."""

import json
from copy import deepcopy

import pytest

from src.agent.context import AgentContextBudget, compact_dialogue_history, encode, estimate_tokens


def test_repeated_reads_replace_same_page_but_keep_other_pages_and_current_receipt():
    budget = AgentContextBudget(system_prompt="Инструкция", max_tokens=1800, history_tokens=200)
    content = {
        "question": "Сравни условия",
        "read_results": [],
        "retrieval_context": [{"source_id": "document:1", "text": "Описание"}],
    }
    budget.prepare(content)
    for _ in range(3):
        budget.add_result(
            content,
            request={"name": "telos_read_source", "arguments": {"source_id": "document:1", "offset": 0}},
            result={"source_id": "document:1", "source_handle": "SRC_1", "text": "Первая страница"},
        )
    assert len(content["read_results"]) == 1
    budget.add_result(
        content,
        request={"name": "telos_read_source", "arguments": {"offset": 0, "source_id": "document:1"}},
        result={
            "source_id": "document:1",
            "source_handle": "SRC_1",
            "text": "Актуальная страница",
            "offset": 0,
        },
    )
    assert len(content["read_results"]) == 1
    assert content["read_results"][0]["result"]["text"] == "Актуальная страница"
    assert content["retrieval_context"] == []
    budget.add_result(
        content,
        request={"name": "telos_read_source", "arguments": {"source_id": "document:1", "offset": 4000}},
        result={"source_id": "document:1", "source_handle": "SRC_1", "text": "Вторая страница"},
    )
    assert len(content["read_results"]) == 2
    for i in range(6):
        budget.add_result(
            content,
            request={"name": "telos_read_source", "arguments": {"source_id": f"document:{i + 2}"}},
            result={
                "source_id": f"document:{i + 2}",
                "source_handle": f"SRC_{i + 2}",
                "text": "Длинный текст. " * 1000,
                "offset": 0,
                "total_chars": 14000,
                "next_offset": None,
            },
        )
        output = budget.fit(content)
        assert budget.tokens(content) <= 1800
        assert json.loads(output)["question"] == "Сравни условия"
    action = {
        "id": 42,
        "tool_name": "telos_create_document",
        "title": "Документ создан",
        "status": "completed",
        "result": {
            "source_id": "document:99",
            "document": {"id": 99, "title": "Новый", "content_md": "Огромный текст" * 10000},
        },
    }
    budget.add_result(
        content,
        request={"name": "telos_create_document", "arguments": {"content_md": "Огромный текст" * 10000}},
        result={"action": action},
    )
    budget.fit(content)
    receipt = content["read_results"][-1]["result"]["action"]
    assert receipt["status"] == "completed"
    assert receipt["id"] == 42
    assert receipt["result"]["document"]["id"] == 99
    assert "content_md" in receipt["result"]["document"]["omitted_fields"]
    assert budget.tokens(content) <= 1800
    # Сохранённый результат для интерфейса не изменяется сокращением prompt.
    assert len(action["result"]["document"]["content_md"]) > 100000


def test_budget_shrinks_large_page_without_skipping_unseen_items_or_text():
    budget = AgentContextBudget(system_prompt="Инструкция", max_tokens=1700, history_tokens=200)
    result = {
        "items": [{"source_id": f"task:{i}", "text": "Подробности. " * 100} for i in range(20, 50)],
        "offset": 20,
        "next_offset": 50,
        "total": 80,
    }
    content = {
        "question": "Покажи задачи",
        "read_results": [{"request": {"name": "telos_list_sources"}, "result": result}],
    }
    budget.fit(content)
    assert 0 < len(result["items"]) < 30
    assert result["next_offset"] == 20 + len(result["items"])
    assert len(result["items"]) + result["items_omitted"] == 30
    assert result["total"] == 80
    page = {
        "source_id": "document:1",
        "text": "Содержимое. " * 2000,
        "offset": 500,
        "total_chars": 22500,
        "next_offset": None,
    }
    content["read_results"] = [{"request": {"name": "telos_read_source"}, "result": page}]
    budget.fit(content)
    assert page["next_offset"] == 500 + len(page["text"])
    assert page["text_truncated"]


def test_history_budget_keeps_recent_messages_and_marks_omissions():
    budget = AgentContextBudget(system_prompt="Правила", max_tokens=2000, history_tokens=150)
    content = {
        "dialog_history": [
            {"role": "user", "content": str(i) + " реплика " * 20} for i in range(10)
        ],
        "dialog_memory": "Память " * 1000,
    }
    budget.prepare(content)
    assert content["dialog_history"][-1]["content"].startswith("9")
    assert content["dialog_history_omitted"] > 0
    assert "память сокращена" in content["dialog_memory"]


def test_shortened_reply_keeps_card_ids_at_the_end():
    budget = AgentContextBudget(system_prompt="Правила", max_tokens=2000, history_tokens=150)
    content = {
        "dialog_history": [
            {
                "role": "assistant",
                "content": "Вывод: "
                + "Длинное объяснение. " * 200
                + "\nИсточники: task:777 — Приёмка",
            }
        ]
    }
    budget.prepare(content)
    assert content["dialog_history"][0]["content"].startswith("Вывод:")
    assert "task:777" in content["dialog_history"][0]["content"]


def test_report_then_smalltalk_preserves_exact_facts_and_latest_request_without_mutating_history():
    fact = "Решили: срок task:777 — 2026-11-15. Осталось подтвердить удаление action:42."
    report = (
        "Сводка проекта.\n"
        + "Развёрнутые подробности текущей работы.\n" * 200
        + fact
        + "\n"
        + "Дополнительные рекомендации по проекту.\n" * 200
        + "Источники: task:777 — Приёмка"
    )
    for request in (
        "Как дела? 🙂",
        "Спасибо! И перенеси task:777 на 2026-11-16, затем покажи риски.",
    ):
        history = [
            {"role": "user", "content": "Для task:777 согласован срок 2026-11-15."},
            {"role": "assistant", "content": report},
            {"role": "user", "content": request},
        ]
        original = deepcopy(history)
        compacted = compact_dialogue_history(history, max_tokens=600)
        assert compacted[-1] == history[-1]
        assert compacted[0] == history[0]
        assert "Решили: срок task:777 — 2026-11-15." in compacted[1]["content"]
        assert "Осталось подтвердить удаление action:42." in compacted[1]["content"]
        assert "часть реплики опущена" in compacted[1]["content"]
        assert estimate_tokens(encode(compacted)) <= 600
        assert history == original
        assert compact_dialogue_history(compacted, max_tokens=600) == compacted


def test_history_budget_counts_json_utf8_and_can_compact_old_user_batches():
    history = [
        {
            "role": "user",
            "content": "Решили: task:42 завершить 2026-12-09.\n" + "Кириллица 🙂\n" * 200,
        },
        {"role": "assistant", "content": "Описание 🙂\n" * 100},
        {"role": "user", "content": "Подробное поручение 🙂\n" * 200},
    ]
    with pytest.raises(ValueError, match="поручение"):
        compact_dialogue_history(history, max_tokens=400)
    compacted = compact_dialogue_history(history, max_tokens=400, preserve_latest_user=False)
    assert "2026-12-09" in encode(compacted)
    assert "task:42" in encode(compacted)
    assert estimate_tokens(encode(compacted)) <= 400
    assert "\ufffd" not in encode(compacted)
    assert json.loads(encode(compacted)) == compacted
    # В prepare текущее поручение уже передано в question: длинный прежний
    # user сокращается как история и не блокирует ответ на новую реплику.
    budget = AgentContextBudget(system_prompt="Правила", max_tokens=1500, history_tokens=400)
    content = {"question": "Спасибо! И покажи task:42.", "dialog_history": history}
    budget.prepare(content)
    budget.fit(content)
    assert content["question"] == "Спасибо! И покажи task:42."
    assert "2026-12-09" in encode(content["dialog_history"])


def test_history_receipts_keep_pending_confirmation_and_latest_outcome_metadata():
    pending = {
        "id": "e0af49ce-dc22-48bf-a52f-d8534157c46f",
        "tool_name": "telos_delete_task",
        "status": "pending",
        "confirmation_id": "confirm-42",
        "confirmation_url": "/agent/pending/confirm-42",
        "result": {"task_id": 777, "expires_at": "2026-11-16T10:00:00Z"},
    }
    completed = {
        "id": "completed-43",
        "tool_name": "telos_update_task",
        "status": "completed",
        "result": {"task_id": 778, "due_date": "2026-11-15", "participant_decision": "approve"},
    }
    history = [
        {"role": "assistant", "content": "Предложение. " * 200, "action": pending},
        {"role": "assistant", "content": "Результат. " * 200, "action": completed},
        {"role": "user", "content": "Спасибо!"},
    ]
    compacted = compact_dialogue_history(history, max_tokens=600)
    assert compacted[0]["action"] == pending
    assert compacted[1]["action"] == completed
    assert compacted[-1] == history[-1]
    assert estimate_tokens(encode(compacted)) <= 600


def test_prepare_and_fit_keep_old_pending_and_latest_write_outcome_under_pressure():
    pending = {
        "id": "pending-42",
        "tool_name": "telos_delete_task",
        "title": "Удалить task:777",
        "status": "pending",
        "confirmation_id": "confirm-42",
        "confirmation_url": "/agent/pending/confirm-42",
        "confirmation": "Подтвердите удаление задачи task:777.",
        "created_at": "2026-11-15T10:00:00Z",
        "result": {"task_id": 777, "expires_at": "2026-11-16T10:00:00Z"},
    }
    completed = {
        "id": "completed-99",
        "tool_name": "telos_update_task",
        "title": "Срок обновлён",
        "status": "completed",
        "result": {
            "task_id": 778,
            "due_date": "2026-11-15",
            "participant_decision": "approve",
            "message": "Точный результат с пояснением. " * 15,
        },
    }
    actions = (
        [pending]
        + [
            {
                "id": f"old-{index}",
                "tool_name": "telos_update_task",
                "status": "completed",
                "result": {"source_id": f"task:{index}", "message": "Старый результат. " * 50},
            }
            for index in range(25)
        ]
        + [completed]
    )
    original = deepcopy(actions)
    budget = AgentContextBudget(system_prompt="Правила", max_tokens=1400, history_tokens=250)
    content = {
        "question": "Спасибо! И покажи риски task:777.",
        "dialog_history": [{"role": "user", "content": "Срок task:777 — 2026-11-15."}],
        "previous_actions": actions,
        "retrieval_context": [{"text": "Большой отчёт. " * 2000}],
        "read_results": [],
    }
    budget.prepare(content)
    prepared_pending = deepcopy(content["previous_actions"][0])
    prepared_completed = deepcopy(content["previous_actions"][-1])
    budget.fit(content)
    assert content["previous_actions"][0] == prepared_pending == pending
    assert content["previous_actions"][-1] == prepared_completed == completed
    assert content["previous_actions_omitted"] > 0
    assert content["question"] == "Спасибо! И покажи риски task:777."
    assert content["dialog_history"][-1]["content"] == "Срок task:777 — 2026-11-15."
    assert budget.tokens(content) <= 1400
    assert actions == original
    # Даже если последняя завершённая запись старше окна из 20 pending,
    # сохраняются и её результат, и каждое ещё не принятое решение.
    pending_batch = [{**pending, "id": f"pending-{index}"} for index in range(25)]
    backlog = {"previous_actions": [completed, *pending_batch]}
    budget.prepare(backlog)
    assert backlog["previous_actions"] == [completed, *pending_batch]
