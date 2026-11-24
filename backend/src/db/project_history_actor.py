"""Передаёт действительного автора каждого DML транзакционным триггерам."""

from sqlalchemy import event
from sqlalchemy.engine import Engine

from src.utils.request_actor import current_request_actor


@event.listens_for(Engine, "before_cursor_execute")
def set_project_history_actor(connection, cursor, statement, parameters, context, executemany):
    """Обновляет local GUC после поздней auth и очищает автора без контекста.

    Слушатель Engine применяется и к независимым тестовым движкам. Отдельный
    cursor.execute не вызывает SQLAlchemy hooks повторно. Значения передаются
    параметрами и не попадают в SQL или диагностические сообщения.
    """
    if connection.dialect.name != "postgresql":
        return
    compiled = context.compiled
    is_dml = compiled is not None and any(
        getattr(compiled, operation, False) for operation in ("isinsert", "isupdate", "isdelete")
    )
    if not is_dml:
        is_dml = statement.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE ", "WITH "))
    if not is_dml:
        return
    actor = current_request_actor()
    values = (str(actor.user_id) if actor else "", actor.name[:350] if actor else "")
    if connection.dialect.paramstyle == "numeric_dollar":
        sql = "SELECT set_config('telos.actor_user_id', $1, true), set_config('telos.actor_name', $2, true)"
    else:
        sql = "SELECT set_config('telos.actor_user_id', %s, true), set_config('telos.actor_name', %s, true)"
    cursor.execute(sql, values)
