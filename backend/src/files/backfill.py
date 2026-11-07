"""Идемпотентно заполняет поиск из имеющихся текстов и метаданных.

python -m src.files.backfill --batch-size 200
--enqueue-missing явно разрешает последующую обработку прежних оригиналов
worker-ом, включая настроенный vision. Без флага очередь не изменяется.
"""

import argparse
import asyncio

from src.db.session import async_session_factory
from src.dependencies.file_processing import build_file_search_backfill


async def run(*, batch_size: int, enqueue_missing: bool) -> None:
    """Выполняет bounded keyset-проход, сообщая только обезличенные счётчики."""
    service = build_file_search_backfill(session_factory=async_session_factory)
    await service.run(
        batch_size=batch_size,
        enqueue_missing=enqueue_missing,
        progress=lambda kind, count: print(f"{kind}: {count}", flush=True),
    )


def main() -> None:
    """Разбирает явные операционные параметры команды."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument(
        "--enqueue-missing",
        action="store_true",
        help="Поставить оригиналы без готового кеша в очередь worker-а (может вызвать vision).",
    )
    args = parser.parse_args()
    asyncio.run(run(batch_size=args.batch_size, enqueue_missing=args.enqueue_missing))


if __name__ == "__main__":
    main()
