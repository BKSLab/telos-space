from datetime import date

from scripts.aris_benchmark import cases, channel_cases, checks, mentions_date


def test_live_suite_has_repeatable_prompts_and_smalltalk_repetitions():
    suite = cases(date(2026, 10, 22))
    assert len(suite) == len({case["id"] for case in suite}) == 14
    assert suite == cases(date(2026, 10, 22))
    for first, second in zip(suite[:3], suite[3:6], strict=True):
        assert first["text"] == second["text"]
        assert first["kind"] == second["kind"] == "smalltalk"
    assert "2026-11-01" in next(case["text"] for case in suite if case["id"] == "mixed")


def test_chat_subset_addresses_aris_without_changing_widget_prompts():
    as_of = date(2026, 11, 2)
    expected = cases(as_of)
    assert channel_cases(as_of, "widget_project") == expected
    chat = channel_cases(as_of, "project_chat")
    assert [case["id"] for case in chat] == ["hello_1", "read"]
    assert all(case["text"].startswith("Арис,") for case in chat)
    assert cases(as_of) == expected


def test_identity_check_accepts_aris_and_rejects_old_names_and_word_prefixes():
    case = {"kind": "smalltalk", "id": "identity_1"}
    for text in ("Я Арис, проектный менеджер TELOS SPACE.", "Я ARIS, проектный менеджер."):
        result = checks(case, {"status": "completed", "content": text}, [], [], {})
        assert all(result.values())
    for text in ("Я Вера, проектный менеджер.", "Я Тим, проектный менеджер."):
        result = checks(case, {"status": "completed", "content": text}, [], [], {})
        assert not result["no_old_name"] and not result["aris_name"]
    for text in ("Я Аристотель.", "Я ARIS2."):
        result = checks(case, {"status": "completed", "content": text}, [], [], {})
        assert not result["aris_name"]


def test_claimed_creation_does_not_pass_without_a_real_task_and_matching_fields():
    case = {"kind": "create"}
    original = {"id": 1, "title": "Original", "due_date": None, "priority": "LOW"}
    answer = {
        "status": "completed",
        "content": "Создал.",
        "actions": [{"tool_name": "telos_create_task", "status": "completed"}],
    }
    result = checks(case, answer, [original], [original], {})
    assert not result["exactly_one_task"] and not result["requested_task"]

    created = {"id": 2, "title": "Заметки проверки", "due_date": None, "priority": "MEDIUM"}
    assert all(checks(case, answer, [original], [original, created], {}).values())
    result = checks(case, answer, [original], [{**original, "priority": "HIGH"}, created], {})
    assert not result["existing_tasks_unchanged"]
    answer["actions"].append({"tool_name": "telos_update_project", "status": "completed"})
    result = checks(case, answer, [original], [original, created], {})
    assert not result["only_requested_action"]


def test_verified_action_card_can_be_the_entire_reply_but_an_empty_reply_cannot():
    created = {"id": 2, "title": "Заметки проверки", "due_date": None, "priority": "MEDIUM"}
    answer = {
        "status": "completed",
        "content": "",
        "sources": [],
        "actions": [{"tool_name": "telos_create_task", "status": "completed"}],
    }
    assert all(checks({"kind": "create"}, answer, [], [created], {}).values())
    answer["actions"] = []
    assert not checks({"kind": "create"}, answer, [], [], {})["nonempty"]
    answer["actions"] = [
        {"tool_name": "telos_delete_task", "status": "pending", "arguments": {"task_id": 2}}
    ]
    assert all(
        checks({"kind": "delete_pending"}, answer, [created], [created], {"future_id": 2}).values()
    )


def test_read_and_pending_checks_detect_mutation_and_unapproved_deletion():
    task = {"id": 2, "title": "Task"}
    answer = {"status": "completed", "content": "Привет!", "actions": []}
    result = checks({"kind": "smalltalk", "id": "hello"}, answer, [task], [], {})
    assert not result["tasks_unchanged"]

    pending = {"tool_name": "telos_delete_task", "status": "pending", "arguments": {"task_id": 2}}
    answer["actions"] = [pending]
    assert all(
        checks({"kind": "delete_pending"}, answer, [task], [task], {"future_id": 2}).values()
    )
    answer["actions"] = [{**pending, "status": "completed"}]
    result = checks({"kind": "delete_pending"}, answer, [task], [], {"future_id": 2})
    assert not result["task_still_exists"] and not result["no_completed_deletion"]


def test_fact_checks_reject_wrong_year_and_task_key_prefixes():
    for text in ("2026-10-19", "19.10.2026", "19 октября 2026"):
        assert mentions_date(text, "2026-10-19")
    for text in ("2027-10-19", "19.10.2027", "19 октября 2027"):
        assert not mentions_date(text, "2026-10-19")
    answer = {
        "status": "completed",
        "content": "TELOSEVAL-10, В работе, презентация, 19.10.2026",
        "actions": [],
    }
    result = checks({"kind": "read"}, answer, [], [], {"overdue_date": "2026-10-19"})
    assert not result["task_key"]
    answer["content"] = "TELOSEVAL-1, TELOSEVAL-2, TELOSEVAL-3: 19.10.2026"
    result = checks({"kind": "overdue"}, answer, [], [], {"overdue_date": "2026-10-19"})
    assert not result["exact_overdue_tasks"]


async def test_unknown_project_post_result_retains_fixture_user(monkeypatch, tmp_path):
    """Потеря ответа после commit проекта не запускает SQL cascade удаления."""
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    import httpx
    import pytest

    from scripts import aris_benchmark

    db = MagicMock()
    db.scalar = AsyncMock(side_effect=[None, None])
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock()
    db.add.side_effect = lambda user: setattr(user, "id", 991)
    scope = MagicMock()
    scope.__aenter__ = AsyncMock(return_value=db)
    scope.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("src.db.session.async_session_factory", lambda: scope)
    monkeypatch.setattr("src.db.session.engine", SimpleNamespace(dispose=AsyncMock()))
    monkeypatch.setattr(
        "src.core.settings.get_settings",
        lambda: SimpleNamespace(
            llm=SimpleNamespace(
                agent_model="test",
                base_url="http://fixture.invalid",
                agent_timeout_seconds=30,
                agent_attempts=1,
            ),
            agent=SimpleNamespace(model_dump=lambda: {}),
            auth=SimpleNamespace(session_cookie_name="session"),
        ),
    )
    monkeypatch.setattr("src.utils.tokens.create_access_token", lambda user_id: "fixture-token")
    committed = []

    def response(request):
        assert request.url.path == "/api/v1/projects"
        committed.append(True)
        raise httpx.ReadTimeout("Connection lost after server commit")

    client = httpx.AsyncClient
    monkeypatch.setattr(
        aris_benchmark.httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(response), **kwargs),
    )
    output = tmp_path / "baseline.json"
    with pytest.raises(httpx.ReadTimeout):
        await aris_benchmark.run(
            SimpleNamespace(
                as_of=date.today().isoformat(), output=str(output), label="test", wait=1
            )
        )
    assert committed
    db.execute.assert_not_awaited()
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["fixture_user_id"] == 991
    assert result["cleanup"] == "fixture_retained_after_error"
    assert result["llm_timeout"] == 30 and result["llm_retries"] == 1


async def test_stale_benchmark_date_is_rejected_before_creating_fixtures(tmp_path):
    """Старая дата меняет множество просроченных задач относительно часов сервера."""
    from datetime import timedelta
    from types import SimpleNamespace

    import pytest

    from scripts.aris_benchmark import run

    output = tmp_path / "stale.json"
    with pytest.raises(ValueError, match="must match the backend date"):
        await run(
            SimpleNamespace(
                as_of=(date.today() - timedelta(days=1)).isoformat(),
                output=str(output),
                label="stale",
                wait=1,
            )
        )
    assert not output.exists()
