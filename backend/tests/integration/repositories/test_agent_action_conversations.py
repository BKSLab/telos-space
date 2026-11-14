"""Файлы, подтверждения и восстановление действий через постоянную очередь."""

# ruff: noqa: F811 -- pytest внедряет импортированные фикстуры по имени параметра.

from dataclasses import replace
from uuid import uuid4

import pytest

from src.exceptions.agent_conversations import AgentConversationNotFoundError
from src.exceptions.agent_files import (
    AgentFileConflictError,
    AgentFileNotFoundError,
    AgentFileValidationError,
)
from src.exceptions.agent_tools import (
    AgentToolAccessError,
    AgentToolConflictError,
    AgentToolOperationError,
)
from src.schemas.agent_conversations import AgentMessageCreateSchema
from src.schemas.knowledge import KnowledgeAnswerSchema
from src.services.agent_files import AgentFilesService
from tests.integration.repositories.test_agent_conversations_repository import (
    agent_env as agent_env,
)
from tests.integration.repositories.test_agent_conversations_repository import read, send, start
from tests.integration.repositories.test_agent_project_actions import actions_env as actions_env
from tests.integration.repositories.test_agent_project_actions import invoke, result, task


async def test_file_is_private_until_sent_then_attaches_original_bytes_once(actions_env):
    env = actions_env
    files = AgentFilesService(scope=env.actions.scope)
    conversation = await start(env)
    arguments = dict(project_id=1, user_id=1, conversation_id=conversation.id)
    binary = b"%PDF-1.4\x00\xff\xfe file bytes"
    uploaded = await files.upload(
        **arguments, file_name="plan.pdf", content_type="application/pdf", content=binary
    )
    assert uploaded.size == len(binary) and "storage_key" not in uploaded.model_dump()
    original = await task(env)
    call = {"file_id": str(uploaded.id), "task_id": original["id"]}
    with pytest.raises(AgentToolOperationError):
        await invoke(env, "telos_attach_uploaded_file", call)
    with pytest.raises(AgentFileNotFoundError):
        await files.delete_draft(**{**arguments, "user_id": 2}, file_id=uploaded.id)
    accepted = await env.service.send_message(
        **arguments,
        data=AgentMessageCreateSchema(
            request_id=uuid4(), content="Прикрепи файл к задаче", file_ids=[uploaded.id]
        ),
    )
    assert accepted.user_message.files[0].id == uploaded.id
    with pytest.raises(AgentFileConflictError):
        await files.delete_draft(**arguments, file_id=uploaded.id)
    request_id = uuid4()
    first = await invoke(env, "telos_attach_uploaded_file", call, request_id=request_id)
    assert first == await invoke(env, "telos_attach_uploaded_file", call, request_id=request_id)
    assert result(first)["document"]["original"]["size"] == len(binary)
    copied_files = list((env.files / "tasks").rglob("*.pdf"))
    assert len(copied_files) == 1 and copied_files[0].read_bytes() == binary
    with pytest.raises(AgentToolOperationError):
        await invoke(env, "telos_attach_uploaded_file", call, context=replace(env.context, user_id=2))
    await env.service.process_next()
    assert env.responder.ask.await_args.kwargs["uploaded_files"][0]["id"] == str(uploaded.id)
    assert (await read(env, conversation.id)).items[0].files[0].id == uploaded.id
    other = await start(env)
    with pytest.raises(AgentConversationNotFoundError):
        await env.service.send_message(
            **{**arguments, "conversation_id": other.id},
            data=AgentMessageCreateSchema(
                request_id=uuid4(), content="Чужой диалог", file_ids=[uploaded.id]
            ),
        )
    for name, content in [
        ("bad.exe", b"123"),
        ("empty.txt", b""),
        ("big.txt", b"a" * (files.max_file_size + 1)),
    ]:
        with pytest.raises(AgentFileValidationError):
            await files.upload(**arguments, file_name=name, content_type=None, content=content)
    draft = await files.upload(
        **arguments, file_name="draft.txt", content_type=None, content=b"draft"
    )
    await files.delete_draft(**arguments, file_id=draft.id)
    assert not list((env.files / "agent").rglob("*.txt"))


async def test_confirmed_action_resumes_chat_and_old_worker_cannot_write(actions_env):
    env = actions_env
    original = await task(env)
    conversation = await start(env)
    seen = []

    async def respond(**kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            await env.actions.execute(
                context=kwargs["execution"],
                name="telos_delete_task",
                arguments={
                    "task_id": original["id"],
                    "expected_updated_at": original["updated_at"],
                },
            )
            return KnowledgeAnswerSchema(answer="Подтвердите удаление в карточке.", sources=[])
        assert kwargs["action_history"][0]["status"] == "completed"
        return KnowledgeAnswerSchema(answer="Задача удалена.", sources=[])

    env.responder.ask.side_effect = respond
    await send(env, conversation.id, "Удали задачу")
    await env.service.process_next()
    messages = await read(env, conversation.id)
    action = messages.items[-1].actions[0]
    assert (
        action.status == "pending"
        and action.result["preview"]["task"]["title"] == original["title"]
    )
    assert (await invoke(env, "telos_list_tasks"))["total"] == 1
    with pytest.raises(AgentToolAccessError):
        await env.actions.decide(
            context=replace(env.context, user_id=2),
            conversation_id=conversation.id,
            action_id=action.id,
            decision="approve",
        )
    with pytest.raises(AgentToolAccessError):
        await env.actions.decide(context=env.context, action_id=action.id, decision="approve")
    done = await env.actions.decide(
        context=env.context,
        conversation_id=conversation.id,
        action_id=action.id,
        decision="approve",
    )
    assert done.status == "completed"
    assert (await read(env, conversation.id)).items[-1].status == "queued"
    with pytest.raises(AgentToolConflictError):
        await env.actions.execute(
            context=seen[0]["execution"],
            name="telos_create_task",
            arguments={"title": "Устаревшая попытка"},
        )
    await env.service.process_next()
    assert (await read(env, conversation.id)).items[-1].content == "Задача удалена."
    assert not (await invoke(env, "telos_list_tasks"))["items"]


async def test_new_question_rejects_pending_action_and_changed_arguments_cannot_approve(
    actions_env,
):
    env = actions_env
    original = await task(env)
    conversation = await start(env)

    async def respond(**kwargs):
        await env.actions.execute(
            context=kwargs["execution"],
            name="telos_delete_task",
            arguments={"task_id": original["id"], "expected_updated_at": original["updated_at"]},
        )
        return KnowledgeAnswerSchema(answer="Ожидаю решения.", sources=[])

    env.responder.ask.side_effect = respond
    await send(env, conversation.id, "Удали задачу")
    await env.service.process_next()
    action = (await read(env, conversation.id)).items[-1].actions[0]
    await send(env, conversation.id, "Передумал, давай обсудим требования")
    with pytest.raises(AgentToolConflictError):
        await env.actions.decide(
            context=env.context,
            conversation_id=conversation.id,
            action_id=action.id,
            decision="approve",
        )
    assert (await read(env, conversation.id)).items[1].actions[0].status == "rejected"
    assert (await invoke(env, "telos_list_tasks"))["total"] == 1
