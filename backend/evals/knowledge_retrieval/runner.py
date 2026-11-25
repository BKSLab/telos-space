from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

RETRIEVAL_KINDS = frozenset({"semantic", "hybrid", "exact", "textual"})
RUNNABLE_APPROVALS = frozenset({"APPROVED", "TEST_FIXTURE"})


@dataclass(frozen=True, slots=True)
class RetrievalExample:
    """Один утверждённый вопрос с релевантными source_id."""

    example_id: str
    question: str
    gold_source_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    """Агрегированные метрики retrieval-набора."""

    examples_count: int
    recall_at_k: dict[str, float]
    mrr: float


def load_retrieval_examples(path: Path) -> list[RetrievalExample]:
    """Читает утверждённые примеры, включая точный/текстовый Git-поиск."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    examples: list[RetrievalExample] = []
    for item in payload.get("examples", []):
        if item.get("kind") not in RETRIEVAL_KINDS:
            continue
        if item.get("approval_status") not in RUNNABLE_APPROVALS:
            raise ValueError(f"Пример {item.get('id')!r} не утверждён владельцем проекта.")
        gold_source_ids = frozenset(str(value) for value in item.get("gold_source_ids", []))
        if not gold_source_ids:
            raise ValueError(f"У примера {item.get('id')!r} отсутствует ground truth.")
        examples.append(
            RetrievalExample(
                example_id=str(item["id"]),
                question=str(item["question"]),
                gold_source_ids=gold_source_ids,
            )
        )
    if not examples:
        raise ValueError("В наборе нет runnable retrieval-примеров.")
    return examples


def load_predictions(path: Path) -> dict[str, list[str]]:
    """Читает ранжированные source_id, полученные проверяемым retrieval."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(item["id"]): [str(source_id) for source_id in item.get("source_ids", [])]
        for item in payload.get("predictions", [])
    }


def calculate_metrics(
    examples: list[RetrievalExample],
    predictions: dict[str, list[str]],
    *,
    k_values: tuple[int, ...] = (1, 5, 10),
) -> RetrievalMetrics:
    """Считает средние Recall@k и MRR по ранжированным source_id."""
    if not examples:
        raise ValueError("Нельзя считать метрики по пустому набору.")
    normalized_k = tuple(sorted(set(k_values)))
    if not normalized_k or any(k < 1 for k in normalized_k):
        raise ValueError("Все значения k должны быть положительными.")

    recall_sums = {k: 0.0 for k in normalized_k}
    reciprocal_rank_sum = 0.0
    for example in examples:
        ranked = list(dict.fromkeys(predictions.get(example.example_id, [])))
        for k in normalized_k:
            relevant = example.gold_source_ids.intersection(ranked[:k])
            recall_sums[k] += len(relevant) / len(example.gold_source_ids)
        first_relevant_rank = next(
            (
                rank
                for rank, source_id in enumerate(ranked, start=1)
                if source_id in example.gold_source_ids
            ),
            None,
        )
        if first_relevant_rank is not None:
            reciprocal_rank_sum += 1 / first_relevant_rank

    count = len(examples)
    return RetrievalMetrics(
        examples_count=count,
        recall_at_k={str(k): recall_sums[k] / count for k in normalized_k},
        mrr=reciprocal_rank_sum / count,
    )


def run(dataset_path: Path, predictions_path: Path, k_values: tuple[int, ...]) -> dict[str, Any]:
    """Выполняет оценку файлов и возвращает JSON-совместимый результат."""
    metrics = calculate_metrics(
        load_retrieval_examples(dataset_path),
        load_predictions(predictions_path),
        k_values=k_values,
    )
    return asdict(metrics)


def main() -> None:
    """Запускает CLI runner для сохранённых retrieval-предсказаний."""
    parser = argparse.ArgumentParser(description="Recall@k и MRR для Project Agent retrieval")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--k", type=int, nargs="+", default=[1, 5, 10])
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.dataset, args.predictions, tuple(args.k)),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
