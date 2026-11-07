"""Границы слоёв, проверяемые разбором импортов.

Нарушение границы не ломает ни один сценарный тест: код работает и с
репозиторием внутри эндпоинта. Поэтому единственная защита — статическая
проверка того, что каждый слой видит только соседний снизу.

Запреты на импорт собраны в одну таблицу правил: новое правило — это
строка таблицы, а не ещё один почти такой же тест. Сообщение об ошибке
перечисляет все нарушения с файлом, именем и строкой.
"""

import ast
import inspect
from dataclasses import dataclass
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
ENDPOINTS = SRC / "api" / "v1" / "endpoints"

# Слой данных: эндпоинт не должен знать о нём вообще.
DATA_LAYER_PREFIXES = ("src.repositories", "src.db", "sqlalchemy")

# Реализации внешних клиентов: эндпоинт работает с сервисом, а не с ними.
CLIENT_MODULES = (
    "src.clients.llm",
    "src.clients.embedding",
    "src.clients.qdrant",
    "src.clients.vision",
)

# Конструкторы, которые разрешено вызывать только в местах сборки графа.
COMPOSITION_MODULES = {
    SRC / "dependencies" / "services.py",
    SRC / "dependencies" / "repositories.py",
    SRC / "dependencies" / "scopes.py",
    SRC / "dependencies" / "storage.py",
    SRC / "dependencies" / "clients.py",
    SRC / "dependencies" / "board_editor.py",
    SRC / "dependencies" / "task_attention.py",
    # Короткие области извлечения, FTS, файловых операций и приватной выдачи.
    SRC / "dependencies" / "file_processing.py",
    SRC / "dependencies" / "file_search.py",
    SRC / "dependencies" / "file_comments.py",
    SRC / "dependencies" / "agent_file_content.py",
    SRC / "mcp_server" / "services.py",
    SRC / "mcp_server" / "app.py",
    SRC / "knowledge" / "composition.py",
    SRC / "knowledge" / "runtime.py",
    # Lifecycle и сборка общего чата с одним Redis transport на процесс.
    SRC / "realtime" / "runtime.py",
    SRC / "main.py",
}

# Отложенных исключений не осталось: каждый модуль, который собирал
# репозитории сам, переведён на composition root своего контура.
PENDING_REPOSITORY_BUILDERS: set[Path] = set()

# Транспорт MCP: инструмент вызывает сервис ровно так же, как эндпоинт.
MCP_TRANSPORT_MODULES = (
    SRC / "mcp_server" / "context.py",
    SRC / "mcp_server" / "server.py",
    SRC / "mcp_server" / "write_tools.py",
    SRC / "mcp_server" / "presenters.py",
)

# Перечисления, которые пока объявлены в моделях и используются как
# значения контракта. Это зафиксированное расхождение (см. проверку
# перечислений задачи ниже), а не разрешение ходить в слой данных.
MODEL_ENUM_MODULES = ("src.db.models.project_milestones",)


def endpoint_files() -> list[Path]:
    """Возвращает модули HTTP-эндпоинтов."""
    return [path for path in sorted(ENDPOINTS.glob("*.py")) if path.name != "__init__.py"]


def service_files() -> list[Path]:
    """Возвращает модули сервисного слоя."""
    return [path for path in sorted((SRC / "services").glob("*.py")) if path.name != "__init__.py"]


def imported_modules(path: Path) -> list[tuple[str, int]]:
    """Возвращает импортируемые модули с номерами строк."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, node.lineno))
        elif isinstance(node, ast.Import):
            found.extend((alias.name, node.lineno) for alias in node.names)
    return found


@dataclass(frozen=True)
class ImportRule:
    """Запрет на импорт для одного слоя.

    Attributes:
        layer: Название слоя в сообщении об ошибке.
        files: Модули слоя.
        forbidden: Префиксы запрещённых модулей.
        allowed: Модули-исключения, разрешённые явно.
        reason: Почему граница существует.
    """

    layer: str
    files: tuple[Path, ...]
    forbidden: tuple[str, ...]
    reason: str
    allowed: tuple[str, ...] = ()


IMPORT_RULES = (
    ImportRule(
        layer="HTTP-эндпоинт",
        files=tuple(endpoint_files()),
        forbidden=DATA_LAYER_PREFIXES,
        reason=(
            "транспорт вызывает сервис и переводит его ошибку в HTTP-ответ; "
            "прямой доступ к данным означал бы бизнес-правило в обработчике запроса"
        ),
    ),
    ImportRule(
        layer="HTTP-эндпоинт",
        files=tuple(endpoint_files()),
        forbidden=CLIENT_MODULES,
        reason="эндпоинт работает с сервисом, а не с реализацией внешнего клиента",
    ),
    ImportRule(
        layer="инструмент MCP",
        files=MCP_TRANSPORT_MODULES,
        forbidden=DATA_LAYER_PREFIXES,
        allowed=MODEL_ENUM_MODULES,
        reason=(
            "пока инструмент собирал репозитории сам, правила доступа и выборки "
            "существовали в MCP отдельной копией и молча расходились с HTTP"
        ),
    ),
    ImportRule(
        layer="сервис",
        files=tuple(service_files()),
        forbidden=("fastapi", "src.dependencies", "src.db.session", "sqlalchemy"),
        reason=(
            "импорт FastAPI означал бы бизнес-правило в терминах HTTP, недоступное "
            "из MCP и worker-а, а импорт SQLAlchemy — запрос мимо репозитория"
        ),
    ),
    ImportRule(
        layer="фоновый индексатор",
        files=(SRC / "knowledge" / "worker.py",),
        forbidden=("src.repositories", "src.db.session", "sqlalchemy"),
        reason=(
            "пока worker брал фабрику сессий из модуля, его зависимости нельзя было "
            "ни увидеть в сигнатуре, ни подменить в тесте"
        ),
    ),
)


def test_layers_import_only_what_they_are_allowed_to() -> None:
    """Ни один слой не импортирует то, что ему запрещено таблицей правил."""
    offenders: list[str] = []
    for rule in IMPORT_RULES:
        for path in rule.files:
            offenders.extend(
                f"{rule.layer} {path.name}:{lineno} → {module} ({rule.reason})"
                for module, lineno in imported_modules(path)
                if module.startswith(rule.forbidden) and module not in rule.allowed
            )

    assert not offenders, "Нарушены границы слоёв:\n  " + "\n  ".join(offenders)


def test_repository_constructors_live_only_in_composition_modules() -> None:
    """Репозитории создаются только там, где собирается граф зависимостей.

    Конструктор репозитория в другом месте означает скрытую зависимость
    от сессии: её нельзя ни увидеть в сигнатуре, ни подменить в тесте.
    Правило обходит всё дерево, поэтому отдельные проверки эндпоинтов и
    инструментов MCP не нужны — они попадают под него наравне со всеми.
    """
    allowed_dirs = (SRC / "repositories", SRC / "db")
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts or "alembic" in path.parts:
            continue
        if path in COMPOSITION_MODULES or path in PENDING_REPOSITORY_BUILDERS:
            continue
        if any(path.is_relative_to(item) for item in allowed_dirs):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders.extend(
            f"{path.relative_to(SRC.parent)}:{node.lineno} {node.func.id}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.endswith("Repository")
        )

    assert not offenders, "Репозитории создаются вне модулей сборки графа:\n  " + "\n  ".join(
        offenders
    )


def test_service_and_client_constructors_live_only_in_composition_modules() -> None:
    """Сервисы и клиентов создаёт только сборка графа.

    Сервис, собранный внутри обработчика, невозможно подменить в тесте, а
    клиент, созданный на лету, заводит собственное соединение мимо
    владельца ресурса. Как и правило выше, проверка идёт по всему дереву
    и покрывает транспорт HTTP и MCP.
    """
    allowed_dirs = (SRC / "services", SRC / "clients", SRC / "storage")
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts or "alembic" in path.parts:
            continue
        if path in COMPOSITION_MODULES or path in PENDING_REPOSITORY_BUILDERS:
            continue
        if any(path.is_relative_to(item) for item in allowed_dirs):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders.extend(
            f"{path.relative_to(SRC.parent)}:{node.lineno} {node.func.id}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.endswith(("Service", "Client"))
        )

    assert not offenders, (
        "Сервисы или клиенты создаются вне модулей сборки графа:\n  " + "\n  ".join(offenders)
    )


def test_worker_receives_its_dependencies_through_the_constructor() -> None:
    """Конструктор worker-а перечисляет всё, от чего он зависит."""
    from src.knowledge.worker import KnowledgeWorker

    parameters = set(inspect.signature(KnowledgeWorker.__init__).parameters) - {"self"}

    assert parameters == {"config", "queue", "index_service", "runtime"}


def test_mcp_tool_context_does_not_expose_a_session() -> None:
    """В контексте инструмента нет сессии базы данных.

    Наличие сессии сделало бы границу необязательной: обработчик мог бы
    обойти сервис, и проверка импортов этого бы не заметила.
    """
    from src.mcp_server.context import ToolContext

    assert "session" not in ToolContext.__annotations__, (
        "ToolContext снова отдаёт сессию: инструмент сможет ходить в базу мимо сервисов."
    )


def test_task_enums_live_in_the_transport_layer() -> None:
    """Приоритет и роль задачи объявлены в слое контракта, а не в модели.

    Эти значения эндпоинт принимает в query-параметрах и отдаёт наружу,
    поэтому брать их из модели базы он не должен. Направление
    зависимости обратное: модель ссылается на общее перечисление.

    Остальные перечисления схем пока приходят из моделей — это
    зафиксированное расхождение, а не проверяемый здесь инвариант:
    массовое переименование выходит за границы задачи.
    """
    enums_source = (SRC / "schemas" / "enums.py").read_text(encoding="utf-8")
    model_source = (SRC / "db" / "models" / "tasks.py").read_text(encoding="utf-8")

    assert "class TaskPriority" in enums_source
    assert "class TaskRole" in enums_source
    assert "class TaskPriority" not in model_source, (
        "Перечисление объявлено в двух местах: значения разойдутся."
    )
    assert "from src.schemas.enums import" in model_source, (
        "Модель должна ссылаться на общее перечисление контракта."
    )


def test_pending_repository_builders_still_need_the_exception() -> None:
    """Временное исключение снимается вместе с переносом границы.

    Если модуль перестал собирать репозитории, он обязан уйти из списка
    отложенных: иначе исключение переживёт причину своего появления и
    перестанет что-либо охранять. Список пуст — проверка охраняет от
    возврата исключений без причины.
    """
    stale: list[str] = []
    for path in PENDING_REPOSITORY_BUILDERS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        builds = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.endswith("Repository")
            for node in ast.walk(tree)
        )
        if not builds:
            stale.append(str(path.relative_to(SRC.parent)))

    assert not stale, (
        "Модули больше не собирают репозитории и должны быть убраны из "
        f"PENDING_REPOSITORY_BUILDERS: {stale}"
    )
