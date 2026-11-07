"""Идемпотентное построение FTS без обращения к файлам, vision или RAG."""

from collections.abc import Callable

from src.services.file_processing import FileProcessingScopeFactory


class FileSearchBackfillService:
    def __init__(self, *, scope: FileProcessingScopeFactory) -> None:
        self.scope = scope

    async def run(
        self,
        *,
        batch_size: int = 100,
        enqueue_missing: bool = False,
        progress: Callable[[str, int], None] | None = None,
    ) -> dict[str, int]:
        """Заполняет только производный поиск из уже существующих локальных данных."""
        if not 1 <= batch_size <= 1000:
            raise ValueError("Размер пакета должен быть от 1 до 1000.")
        totals = {}
        for kind in ("task_attachment", "project_file"):
            cursor, total = 0, 0
            while True:
                async with self.scope() as db:
                    source_ids = await db.sources.lock_backfill_sources(
                        source_type=kind, after_id=cursor, limit=batch_size
                    )
                    count = await db.sources.backfill_search_batch(
                        source_type=kind, source_ids=source_ids
                    )
                    await db.unit_of_work.commit()
                total += count
                if progress is not None:
                    progress(kind, total)
                if count < batch_size:
                    break
                cursor = source_ids[-1]
            totals[kind] = total
            if enqueue_missing:
                cursor, enqueued = 0, 0
                while True:
                    async with self.scope() as db:
                        next_cursor, seen, added = await db.sources.enqueue_missing_batch(
                            source_type=kind, after_id=cursor, limit=batch_size
                        )
                        await db.unit_of_work.commit()
                    enqueued += added
                    if progress is not None:
                        progress(f"enqueued_{kind}", enqueued)
                    if seen < batch_size:
                        break
                    cursor = next_cursor
                totals[f"enqueued_{kind}"] = enqueued
        return totals
