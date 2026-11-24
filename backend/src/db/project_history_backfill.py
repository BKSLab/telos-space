"""Явно сохраняет только доказуемые прежние факты истории, без реконструкции правок.

Запуск после миграции при остановленных writers:
``python -m src.db.project_history_backfill``. Повтор безопасен по public_id.
Схемная миграция этот перенос данных автоматически не выполняет.
"""

import asyncio
import json

from sqlalchemy import func, select, text

from src.db.models.project_history_events import ProjectHistoryEvent


async def backfill_history(connection) -> dict[str, int]:
    before = await connection.scalar(select(func.count()).select_from(ProjectHistoryEvent))
    await connection.execute(text(
        "SELECT project_history_snapshot_task(to_jsonb(t)) FROM tasks t "
        "WHERE t.project_id IS NOT NULL"
    ))
    for table in (
        "project_context_history", "project_deadline_changes", "project_risks",
        "documents", "project_files",
    ):
        # Table identifiers are the fixed allowlist above, never external input.
        await connection.execute(text(
            f"SELECT project_history_snapshot_source('{table}', to_jsonb(s), s.project_id) "
            f"FROM {table} s WHERE s.project_id IS NOT NULL"
        ))
    after = await connection.scalar(select(func.count()).select_from(ProjectHistoryEvent))
    return {"before": before, "after": after, "created": after - before}


async def main() -> None:
    from src.db.session import engine

    try:
        async with engine.begin() as connection:
            result = await backfill_history(connection)
        print(json.dumps(result))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
