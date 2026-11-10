"""Ограничители контракта репозиториев.

Правило простое: обычный публичный метод — один SQL statement. Разбор AST
не доказывает это для любой конструкции SQLAlchemy, но надёжно не даёт
вернуться уже найденным формам нарушения: дочитыванию после DML и
нескольким запросам в одном публичном методе.

Реальное поведение `RETURNING`, видимости и отката подтверждают
integration-тесты на PostgreSQL.

Каждое правило проверяется одним тестом по всему пакету репозиториев:
сообщение перечисляет все нарушения сразу, а добавление репозитория не
добавляет новых тестов.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
REPOSITORIES = SRC / "repositories"

# Методы, которые намеренно работают с набором строк. Каждый из них
# выполняет одну массовую операцию, а не N операций по строке.
BATCH_METHODS = {
    ("project_stages.py", "save_many"),
    ("knowledge_index_jobs.py", "add_many"),
    ("knowledge_index_jobs.py", "get_pending"),
    ("knowledge_index_jobs.py", "reset_processing"),
    ("knowledge_index_jobs.py", "delete_succeeded_before"),
    ("task_participants.py", "save_many"),
}

# Методы, которым по существу нужно несколько запросов: атомарный захват
# заданий очереди и замена набора связей. Каждый из них является одной
# неделимой операцией и покрыт integration-тестом.
MULTI_STATEMENT_ALLOWLIST = {
    ("knowledge_index_jobs.py", "claim_next_batch"),
    ("board_stickers.py", "replace_task_links"),
}

QUERY_CALLS = {"execute", "scalar", "scalars", "get", "flush"}


def repository_files() -> list[Path]:
    """Возвращает модули репозиториев без служебных файлов."""
    return [
        path
        for path in sorted(REPOSITORIES.glob("*.py"))
        if path.name not in {"__init__.py", "unit_of_work.py"}
    ]


def public_methods(path: Path):
    """Перечисляет публичные методы репозитория в файле."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.AsyncFunctionDef) and not item.name.startswith("_"):
                yield item


def calls_in(node: ast.AST, names: set[str]) -> list[tuple[str, int]]:
    """Возвращает вызовы сессии с указанными именами."""
    found: list[tuple[str, int]] = []
    for item in ast.walk(node):
        if not isinstance(item, ast.Call) or not isinstance(item.func, ast.Attribute):
            continue
        if item.func.attr not in names:
            continue
        target = item.func.value
        if isinstance(target, ast.Attribute) and target.attr == "db_session":
            found.append((item.func.attr, item.lineno))
    return found


def test_no_refresh_after_dml() -> None:
    """После записи нет дочитывания: серверные значения приходят из RETURNING.

    Отдельный `refresh()` превращал каждую вставку в два обращения к базе
    ради `created_at` и `updated_at`.
    """
    offenders = [
        f"{path.name}:{method.name}:{lineno}"
        for path in repository_files()
        for method in public_methods(path)
        for _, lineno in calls_in(method, {"refresh"})
    ]

    assert not offenders, (
        "Результат дочитывается после DML: " + ", ".join(offenders) + ". "
        "Серверные значения должны приходить из самого запроса."
    )


def test_ordinary_public_method_issues_one_statement() -> None:
    """Обычный публичный метод не выполняет несколько запросов подряд.

    Несколько запросов в одном методе — это оркестрация, и принадлежит она
    сервису: только он владеет транзакцией и знает бизнес-смысл порядка.
    """
    offenders: list[str] = []
    for path in repository_files():
        for method in public_methods(path):
            key = (path.name, method.name)
            if key in BATCH_METHODS or key in MULTI_STATEMENT_ALLOWLIST:
                continue
            statements = [
                (name, lineno) for name, lineno in calls_in(method, QUERY_CALLS) if name != "flush"
            ]
            if len(statements) > 1:
                offenders.append(f"{path.name}:{method.name}: {statements}")

    assert not offenders, "Несколько запросов в одном публичном методе:\n  " + "\n  ".join(
        offenders
    )


def test_public_method_does_not_call_another_public_method() -> None:
    """Публичный метод не собирается из других публичных методов.

    Такая сборка прячет второй запрос за вызовом, который выглядит как
    один, и делает число обращений к базе невидимым в месте вызова.
    """
    offenders: list[str] = []
    for path in repository_files():
        names = {method.name for method in public_methods(path)}
        for method in public_methods(path):
            if (path.name, method.name) in MULTI_STATEMENT_ALLOWLIST:
                continue
            offenders.extend(
                f"{path.name}:{method.name} -> {item.func.attr}:{item.lineno}"
                for item in ast.walk(method)
                if isinstance(item, ast.Call)
                and isinstance(item.func, ast.Attribute)
                and isinstance(item.func.value, ast.Name)
                and item.func.value.id == "self"
                and item.func.attr in names
                and item.func.attr != method.name
            )

    assert not offenders, "Публичный метод собран из других публичных: " + ", ".join(offenders)


def test_batch_methods_do_not_loop_over_queries() -> None:
    """Массовая операция не превращается в запрос на каждую строку."""
    offenders: list[str] = []
    for path in repository_files():
        for method in public_methods(path):
            if (path.name, method.name) not in BATCH_METHODS:
                continue
            for loop in ast.walk(method):
                if not isinstance(loop, ast.For | ast.AsyncFor):
                    continue
                inside = calls_in(loop, QUERY_CALLS)
                if inside:
                    offenders.append(f"{path.name}:{method.name}: {inside}")

    assert not offenders, "Массовая операция выполняет запрос внутри цикла: " + ", ".join(offenders)


def test_self_committing_methods_declare_the_commit_flag() -> None:
    """Метод, который может закоммитить сам, объявляет это в сигнатуре.

    Без явного флага вызывающий код не может отличить самостоятельную
    запись от участия в составном сценарии.
    """
    offenders: list[str] = []
    for path in repository_files():
        for method in public_methods(path):
            commits = calls_in(method, {"commit"})
            if not commits:
                continue
            declared = {argument.arg for argument in method.args.kwonlyargs}
            declared |= {argument.arg for argument in method.args.args}
            if "commit" not in declared:
                offenders.append(f"{path.name}:{method.name}")

    assert not offenders, "Методы коммитят сами, но не объявляют флаг commit: " + ", ".join(
        offenders
    )
