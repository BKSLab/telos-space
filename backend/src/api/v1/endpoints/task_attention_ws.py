"""Персональный WebSocket без request-scoped сессии или HTTP-клиента."""

import asyncio
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.dependencies.settings import SettingsDep
from src.dependencies.task_attention import TaskAttentionRuntimeDep
from src.exceptions.auth import AuthServiceError
from src.exceptions.task_attention import TaskAttentionServiceError

router = APIRouter()


@router.websocket("/users/me/task-attention/ws", name="taskAttentionWebSocket")
async def task_attention_socket(
    websocket: WebSocket, runtime: TaskAttentionRuntimeDep, settings: SettingsDep
):
    """Проверяет локальную сессию, отправляет снимок и восстанавливает его после каждого подключения."""
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host", "")
    allowed = {*settings.app.cors_origins, f"http://{host}", f"https://{host}"}
    if origin not in allowed or not runtime.ready:
        await websocket.close(code=4403 if origin not in allowed else 1013)
        return
    credentials = dict(
        session_token=websocket.cookies.get(settings.auth.session_cookie_name), bearer_secret=None
    )
    try:
        principal = await runtime.authenticate(**credentials)
        if principal.via_api_token:
            await websocket.close(code=4403)
            return
        listener = runtime.subscribe()
    except AuthServiceError:
        await websocket.close(code=4403)
        return
    except RuntimeError:
        await websocket.close(code=1013)
        return

    async def receive():
        while True:
            message = await asyncio.wait_for(
                websocket.receive_text(), runtime.config.attention_heartbeat_seconds * 3
            )
            if len(message) > 128:
                await websocket.close(code=1009)
                return

    async def send():
        last = None
        refresh = True
        while True:
            generation = runtime.generation
            # Отзыв доступа проверяется также на heartbeat; БД освобождена до wait/send.
            checked = await runtime.authenticate(**credentials)
            if checked.user_id != principal.user_id or not runtime.ready:
                await websocket.close(code=4403 if checked.user_id != principal.user_id else 1013)
                return
            if refresh:
                snapshot = (await runtime.snapshot(principal.user_id)).model_dump(mode="json")
                if snapshot != last:
                    await asyncio.wait_for(
                        websocket.send_json({"type": "task-attention", "data": snapshot}),
                        runtime.config.attention_send_timeout,
                    )
                    last = snapshot
            await asyncio.wait_for(
                websocket.send_json({"type": "ping"}), runtime.config.attention_send_timeout
            )
            refresh = await runtime.wait(listener, generation)

    pending = []
    close_code = 1013
    try:
        await websocket.accept()
        pending = [asyncio.create_task(receive()), asyncio.create_task(send())]
        done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except AuthServiceError as error:
        close_code = 4403 if error.status_code in (401, 403) else 1013
    except (WebSocketDisconnect, TimeoutError, OSError, TaskAttentionServiceError):
        pass
    finally:
        runtime.unsubscribe(listener)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        with suppress(RuntimeError, OSError):
            await websocket.close(code=close_code)
