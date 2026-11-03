"""Сводки Ариса вместо предзагрузки планировщика: статистика, просрочка, история."""

# ruff: noqa: F811 -- pytest внедряет импортированные фикстуры по имени параметра.

from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.agent.action_catalog import PROJECT_ACTIONS
from src.agent.action_scopes import ACTION_SCOPES, tools_for_scopes
from src.exceptions.agent_tools import AgentToolAccessError, AgentToolArgumentsError
from src.mcp_server.project_tools import action_allowed
from src.utils.api_scopes import SCOPE_PROJECTS_READ, SCOPE_TASKS_READ
from tests.integration.repositories.test_agent_project_actions import (
    actions_env as actions_env,
)
from tests.integration.repositories.test_agent_project_actions import (
    agent_env as agent_env,
)
from tests.integration.repositories.test_agent_project_actions import invoke, result

SUMMARY_TOOLS = ("get_task_statistics", "list_overdue_tasks", "list_recent_activity")


async def seed(env):
    """Три стадии и задачи: просроченная, просроченная, но готовая, будущая и без срока."""
    today = date.today()
    stages = {}
    for name, done in (("Бэклог", False), ("В работе", False), ("Готово", True)):
        created = await invoke(env, "create_stage", {"name": name, "is_done_stage": done})
        stages[name] = result(created)["stage"]["id"]

    async def make(title, stage, **fields):
        created = await invoke(
            env, "create_task", {"title": title, "stage_id": stages[stage], **fields}
        )
        return result(created)["task"]

    late = await make(
        "Сверить акты",
        "Бэклог",
        priority="HIGH",
        due_date=str(today - timedelta(days=3)),
        executor_id=2,
    )
    later = await make("Обновить договор", "В работе", due_date=str(today - timedelta(days=1)))
    finished = await make("Закрыть поставку", "Готово", due_date=str(today - timedelta(days=5)))
    future = await make("Подготовить отчёт", "В работе", due_date=str(today + timedelta(days=4)))
    undated = await make("Разобрать почту", "Бэклог")
    return stages, late, later, finished, future, undated


async def test_statistics_break_down_by_stage_names_priority_and_assignee(actions_env):
    env = actions_env
    _, late, *_ = await seed(env)

    stats = await invoke(env, "get_task_statistics")

    assert stats["today"] == date.today().isoformat()
    assert stats["total"] == 5
    # Просрочка — только незавершённые: задача в «Готово» не считается.
    assert stats["overdue"] == 2
    assert stats["by_stage"] == {"Бэклог": 2, "В работе": 2, "Готово": 1}
    assert stats["by_priority"]["HIGH"] == 1
    assert sum(stats["by_priority"].values()) == 5
    assert stats["by_assignee"][late["assignee"]] == 1
    assert stats["by_assignee"]["не назначен"] == 4
    assert stats["source_id"] == "project:1"


async def test_overdue_lists_open_late_tasks_oldest_first_with_sources(actions_env):
    env = actions_env
    _, late, later, *_ = await seed(env)

    overdue = await invoke(env, "list_overdue_tasks")

    assert [item["task_key"] for item in overdue["items"]] == [late["key"], later["key"]]
    first = overdue["items"][0]
    assert first["task_id"] == late["id"]
    assert first["source_id"] == f"task:{late['id']}"
    assert first["stage"] == "Бэклог" and first["is_done"] is False
    assert first["priority"] == "HIGH"
    assert first["due_date"] == str(date.today() - timedelta(days=3))
    assert first["title"] == "Сверить акты"
    assert overdue["truncated"] is False

    capped = await invoke(env, "list_overdue_tasks", {"limit": 1})
    assert [item["task_key"] for item in capped["items"]] == [late["key"]]
    assert capped["truncated"] is True
    with pytest.raises(AgentToolArgumentsError):
        await invoke(env, "list_overdue_tasks", {"limit": 31})


async def test_recent_activity_names_tasks_by_key_and_title(actions_env):
    env = actions_env
    stages, late, later, *_ = await seed(env)
    await invoke(env, "move_task", {"task_id": late["id"], "stage_id": stages["В работе"]})
    await invoke(env, "add_comment", {"task_id": later["id"], "body_md": "Жду подпись"})

    activity = await invoke(env, "list_recent_activity")

    items = activity["items"]
    assert items, "история проекта пуста"
    newest = items[0]
    assert newest["event_type"] == "COMMENT_ADDED"
    assert newest["task_key"] == later["key"] and newest["title"] == "Обновить договор"
    assert newest["source_id"] == f"task:{later['id']}"
    moved = next(item for item in items if item["event_type"] == "STAGE_CHANGED")
    assert moved["task_key"] == late["key"] and moved["title"] == "Сверить акты"
    assert moved["from_value"] and moved["to_value"]
    stamps = [item["created_at"] for item in items]
    assert stamps == sorted(stamps, reverse=True)

    assert len((await invoke(env, "list_recent_activity", {"limit": 1}))["items"]) == 1
    with pytest.raises(AgentToolArgumentsError):
        await invoke(env, "list_recent_activity", {"limit": 51})


async def test_summaries_stay_inside_accessible_project(actions_env):
    """Участник чужого проекта не получает его сводки."""
    env = actions_env
    await seed(env)
    outsider = replace(env.context, project_id=2, user_id=2)
    for name in SUMMARY_TOOLS:
        with pytest.raises(AgentToolAccessError):
            await invoke(env, name, context=outsider)
    # В своём проекте без задач — пустые сводки, а не данные проекта 1.
    empty = replace(env.context, project_id=2)
    assert (await invoke(env, "get_task_statistics", context=empty))["total"] == 0
    assert (await invoke(env, "list_overdue_tasks", context=empty))["items"] == []
    assert (await invoke(env, "list_recent_activity", context=empty))["items"] == []


def test_summaries_are_read_only_and_open_only_with_tasks_read_scope():
    definitions = {item.name: item for item in PROJECT_ACTIONS}
    for name in SUMMARY_TOOLS:
        assert definitions[name].mutating is False
        assert ACTION_SCOPES[name] == ACTION_SCOPES["list_tasks"] == SCOPE_TASKS_READ

    def key(*scopes):
        return SimpleNamespace(api_key=object(), allows=lambda scope: scope in scopes)

    # Ключ SD без tasks:read не видит и не исполняет сводки через MCP.
    projects_only = key(SCOPE_PROJECTS_READ)
    assert not any(action_allowed(projects_only, name) for name in SUMMARY_TOOLS)
    assert not set(SUMMARY_TOOLS) & tools_for_scopes([SCOPE_PROJECTS_READ])
    reader = key(SCOPE_TASKS_READ)
    assert all(action_allowed(reader, name) for name in SUMMARY_TOOLS)
    assert set(SUMMARY_TOOLS) <= tools_for_scopes([SCOPE_TASKS_READ])
    # Личный токен (без сервисного ключа) — по уровню токена, как list_tasks.
    person = SimpleNamespace(api_key=None, allows=lambda scope: False)
    assert all(action_allowed(person, name) for name in SUMMARY_TOOLS)


async def test_get_task_by_key_returns_the_task_with_its_stage_name(actions_env):
    """Ключ вместо ID и название стадии: «что с задачей KEY-12» — одним шагом."""
    env = actions_env
    stages, late, *_ = await seed(env)

    by_key = await invoke(env, "get_task", {"task_key": late["key"]})
    by_id = await invoke(env, "get_task", {"task_id": late["id"]})

    assert by_key["task"]["id"] == late["id"] and by_key["task"] == by_id["task"]
    assert by_key["stage"] == {"id": stages["Бэклог"], "name": "Бэклог", "is_done_stage": False}
    assert by_key["source_id"] == f"task:{late['id']}"
    assert (await invoke(env, "get_task", {"task_key": late["key"].lower()}))["task"]["id"] == (
        late["id"]
    )
    number = late["key"].rpartition("-")[2]
    for wrong in (f"ZZ-{number}", f"{late['key'].rpartition('-')[0]}-99999"):
        with pytest.raises(AgentToolArgumentsError):
            await invoke(env, "get_task", {"task_key": wrong})
    for arguments in ({}, {"task_id": late["id"], "task_key": late["key"]}):
        with pytest.raises(AgentToolArgumentsError):
            await invoke(env, "get_task", arguments)


async def test_task_list_rows_are_light_enough_to_fit_a_full_page(actions_env):
    """30 задач страницы влезают в бюджет результата без обрезки (раньше — 11 из 22)."""
    from src.agent.context import encode, estimate_tokens

    env = actions_env
    stages, late, *_ = await seed(env)
    for index in range(25):
        await invoke(
            env,
            "create_task",
            {
                "title": f"Задача с довольно длинным названием номер {index}",
                "stage_id": stages["Бэклог"],
            },
        )

    listed = await invoke(env, "list_tasks", {"limit": 30})

    assert len(listed["items"]) == 30 and listed["total"] == 30
    assert estimate_tokens(encode(listed)) < 12000 // 3
    row = next(item for item in listed["items"] if item["id"] == late["id"])
    assert row["key"] == late["key"] and row["stage"] == "Бэклог" and row["is_done"] is False
    assert row["priority"] == "HIGH" and row["due_date"] and row["assignee"] == late["assignee"]
    assert {"description_md", "checklist", "position", "search_title"}.isdisjoint(row)
    undated = next(item for item in listed["items"] if item["title"] == "Разобрать почту")
    # Пустые исполнитель и срок — тоже ответ, поэтому остаются явными.
    assert undated["assignee"] is None and undated["due_date"] is None
