"""Архитектурные ограничители графа зависимостей.

Правила проверяются разбором исходников: забытый `get_settings()` в
сервисе или вернувшийся глобальный runtime не ломают ни один сценарный
тест, поэтому единственная защита от их возвращения — статический guard.

Один тест — одно правило по всему дереву. Проверка перечисляет все
нарушения сразу: разбитая по файлам параметризация показывала только
первый упавший файл и заставляла узнавать про остальные следующим
прогоном.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

# Чтение глобальных настроек допустимо только в точке сборки приложения.
SETTINGS_ALLOWLIST = {
    SRC / "core" / "settings.py",
    SRC / "db" / "session.py",
    SRC / "main.py",
    SRC / "mcp_server" / "worker.py",
    SRC / "dependencies" / "settings.py",
    SRC / "dependencies" / "storage.py",
    # Имя cookie входит в сигнатуру маршрута и должно быть известно в
    # момент сборки приложения, а не при обработке запроса.
    SRC / "dependencies" / "auth.py",
    SRC / "core" / "config_logger.py",
    SRC / "db" / "alembic" / "env.py",
    SRC / "utils" / "tokens.py",
    SRC / "utils" / "check_db.py",
}

# Отложенных исключений не осталось: каждая точка чтения настроек либо
# перечислена в allowlist выше, либо переведена на конструкторную
# передачу значения.
PENDING_SETTINGS_READERS: set[Path] = set()

DEPENDENCIES = SRC / "dependencies"

# Сценарии с медленным внешним вызовом. Каждый обязан работать через
# короткую область базы, а не через репозитории, живущие вместе с ним.
EXTERNAL_CALL_SERVICES = {
    "agent_conversations.py": "AgentConversationsService",
    "aris_chat.py": "ArisChatService",
    "risk_suggestions.py": "RiskSuggestionService",
    "task_checklist_suggestions.py": "TaskChecklistSuggestionService",
    "analytics.py": "AnalyticsService",
    "project_agent.py": "ProjectAgentService",
    "task_descriptions.py": "TaskDescriptionService",
    "task_documents.py": "TaskDocumentImportService",
    "wbs_suggestion.py": "WbsSuggestionService",
    "attachment_download.py": "AttachmentDownloadService",
}


def iter_python_files(*relative: str) -> list[Path]:
    """Возвращает исходные файлы указанных пакетов без кеша интерпретатора."""
    files: list[Path] = []
    for part in relative:
        root = SRC / part
        target = [root] if root.is_file() else sorted(root.rglob("*.py"))
        files.extend(path for path in target if "__pycache__" not in path.parts)
    return files


def parse(path: Path) -> ast.AST:
    """Разбирает исходный файл в синтаксическое дерево."""
    return ast.parse(path.read_text(encoding="utf-8"))


def where(path: Path, line: int) -> str:
    """Возвращает адрес нарушения в виде `путь:строка`."""
    return f"{path.relative_to(SRC.parent)}:{line}"


def calls_named(tree: ast.AST, name: str) -> list[int]:
    """Возвращает номера строк, где вызывается функция с данным именем."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == name:
            lines.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == name:
            lines.append(node.lineno)
    return lines


def imported_names(tree: ast.AST) -> list[tuple[str, str, int]]:
    """Возвращает импорты как тройки «модуль, имя, строка»."""
    found: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.extend((node.module, alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.Import):
            found.extend((alias.name, alias.name, node.lineno) for alias in node.names)
    return found


def caught_names(handler: ast.ExceptHandler) -> list[str]:
    """Возвращает имена исключений, перехваченных клаузой."""
    if handler.type is None:
        return []
    elements = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return [element.id for element in elements if isinstance(element, ast.Name)]


def init_of(tree: ast.AST, class_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Возвращает конструктор указанного класса."""
    service = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node
        for node in service.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "__init__"
    )


def test_settings_are_read_only_in_declared_composition_points() -> None:
    """Список мест, читающих глобальные настройки, зафиксирован явно.

    Новая точка чтения — осознанное решение: она должна попасть в
    allowlist вместе с обоснованием, а не появиться незаметно. Правило
    покрывает всё дерево, поэтому отдельные проверки по сервисам,
    транспорту и worker-у не нужны: ни один из них в allowlist не входит.
    """
    allowed = SETTINGS_ALLOWLIST | PENDING_SETTINGS_READERS
    offenders = [
        where(path, line)
        for path in iter_python_files(".")
        if path not in allowed
        for line in calls_named(parse(path), "get_settings")
    ]

    assert not offenders, "Настройки читаются вне разрешённых точек сборки: " + ", ".join(offenders)


def test_pending_settings_readers_still_need_the_exception() -> None:
    """Временное исключение снимается вместе с переносом границы.

    Если файл перестал читать настройки, он обязан уйти из списка
    отложенных: иначе исключение переживёт причину, ради которой оно
    появилось, и перестанет что-либо охранять.
    """
    stale = [
        str(path.relative_to(SRC.parent))
        for path in PENDING_SETTINGS_READERS
        if not calls_named(parse(path), "get_settings")
    ]

    assert not stale, (
        "Файлы больше не читают настройки и должны быть убраны из "
        f"PENDING_SETTINGS_READERS: {stale}"
    )


def test_no_module_finds_clients_by_itself() -> None:
    """Глобального локатора клиентов больше нет ни у кого."""
    offenders = [
        str(path.relative_to(SRC.parent))
        for path in iter_python_files("services", "mcp_server", "knowledge/worker.py", "api")
        if "get_knowledge_runtime()" in path.read_text(encoding="utf-8")
    ]

    assert not offenders, (
        "Клиентов ищут сами: " + ", ".join(offenders) + ". "
        "Клиенты создаёт lifespan и передаёт через зависимость."
    )


def test_global_runtime_locator_is_gone() -> None:
    """Ленивый глобальный runtime удалён вместе со своим состоянием."""
    source = (SRC / "knowledge" / "runtime.py").read_text(encoding="utf-8")

    assert "global _runtime" not in source
    assert "def get_knowledge_runtime(" not in source


def test_client_does_not_create_its_own_transport() -> None:
    """Клиент получает transport готовым: у сетевого ресурса один владелец."""
    offenders = [
        where(path, node.lineno)
        for path in iter_python_files("clients", "storage")
        for node in ast.walk(parse(path))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"AsyncClient", "AsyncQdrantClient"}
    ]

    assert not offenders, (
        "Сетевой клиент создаётся внутри слоя клиентов: " + ", ".join(offenders) + ". "
        "Transport создаёт lifespan и передаёт конструктором."
    )


def test_auth_and_access_adapters_do_not_touch_data() -> None:
    """Auth и access зависимости не обращаются к данным.

    Им разрешено единственное исключение — перевод ошибки сервиса в
    `HTTPException`. Всё остальное, включая правила доступа и чтение
    репозиториев, принадлежит сервисному слою.
    """
    forbidden = ("src.repositories", "src.db")
    offenders = [
        f"{where(path, line)} ({module})"
        for path in (DEPENDENCIES / "auth.py", DEPENDENCIES / "access.py")
        for module, _, line in imported_names(parse(path))
        if module.startswith(forbidden)
    ]

    assert not offenders, (
        "Адаптеры auth/access обращаются к слою данных: " + ", ".join(offenders) + ". "
        "Правила доступа принадлежат сервису."
    )


def test_required_dependencies_have_no_none_default() -> None:
    """Обязательная зависимость не может отсутствовать молча.

    `= None` у репозитория или сервиса превращает ошибку сборки графа в
    падение по `AttributeError` где-то ниже.
    """
    offenders: list[str] = []
    for path in (DEPENDENCIES / "auth.py", DEPENDENCIES / "access.py"):
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            args = node.args
            defaults = dict(
                zip(args.args[len(args.args) - len(args.defaults) :], args.defaults, strict=True)
            )
            for argument, default in defaults.items():
                annotation = ast.unparse(argument.annotation) if argument.annotation else ""
                is_none_default = isinstance(default, ast.Constant) and default.value is None
                looks_required = annotation.endswith(("ServiceDep", "RepositoryDep"))
                if is_none_default and looks_required:
                    offenders.append(f"{where(path, node.lineno)} {node.name}({argument.arg})")

    assert not offenders, "Обязательные зависимости объявлены необязательными: " + ", ".join(
        offenders
    )


def test_endpoints_never_receive_orm_models_from_access_guards() -> None:
    """Guard доступа не поднимает персистентную модель в эндпоинт."""
    source = (DEPENDENCIES / "access.py").read_text(encoding="utf-8")
    removed_aliases = (
        "AccessibleProjectDep",
        "OwnedProjectDep",
        "AccessibleTaskDep",
        "AccessibleStageDep",
        "AccessibleDocumentDep",
    )

    present = [alias for alias in removed_aliases if alias in source]
    assert not present, (
        f"Возвращены ORM-алиасы доступа: {present}. "
        "Эндпоинт работает с идентификатором пути и разрешением."
    )


def test_client_and_storage_do_not_import_service_errors() -> None:
    """Нижний слой не знает исключений вышестоящего.

    Клиент, поднимающий `*ServiceError`, фактически объявляет чужой
    контракт: вышестоящий сервис перестаёт быть местом, где решается,
    во что превращается сбой внешней системы.
    """
    own = {"src.exceptions.clients", "src.exceptions.storage"}
    offenders = [
        f"{where(path, line)} ({module}.{name})"
        for path in iter_python_files("clients", "storage")
        for module, name, line in imported_names(parse(path))
        if module.startswith("src.exceptions")
        and module not in own
        and (name.endswith("ServiceError") or "Service" in name)
    ]

    assert not offenders, (
        "Клиент или хранилище импортирует исключения сервисного слоя: "
        + ", ".join(offenders)
        + ". Преобразование принадлежит вызывающему сервису."
    )


def test_endpoints_do_not_catch_lower_layer_errors() -> None:
    """Эндпоинт не ловит ошибки клиента, хранилища или репозитория.

    Их обязан преобразовать сервис: если такая ошибка доходит до
    транспорта, значит граница слоя где-то пропущена.
    """
    forbidden_suffixes = ("ClientError", "StorageError", "RepositoryError")
    offenders = [
        f"{where(path, node.lineno)} ({name})"
        for path in iter_python_files("api")
        for node in ast.walk(parse(path))
        if isinstance(node, ast.ExceptHandler)
        for name in caught_names(node)
        if name.endswith(forbidden_suffixes)
    ]

    assert not offenders, "Эндпоинт ловит ошибку нижнего слоя: " + ", ".join(offenders)


def test_broad_application_error_is_confined_to_transport_boundaries() -> None:
    """Широкий перехват допустим только на финальной границе транспорта.

    Внутри сервиса он скрывает, какой именно контракт зависимости
    обрабатывается, и молча проглатывает новое семейство ошибок.
    """
    allowed_prefixes = (SRC / "mcp_server",)
    offenders = [
        where(path, node.lineno)
        for path in iter_python_files(".")
        if not any(path.is_relative_to(prefix) for prefix in allowed_prefixes)
        for node in ast.walk(parse(path))
        if isinstance(node, ast.ExceptHandler) and "ApplicationError" in caught_names(node)
    ]

    assert not offenders, (
        "Широкий перехват ApplicationError вне транспортной границы: " + ", ".join(offenders)
    )


def test_no_dead_reraise_of_own_errors_remains() -> None:
    """Не осталось `except OwnError: raise`, который ничего не ловит.

    Такая клауза появляется, когда `try` охватывает и вызов зависимости,
    и собственную доменную проверку. Она ничего не защищает и создаёт
    ложное впечатление, что защищает.
    """
    offenders: list[str] = []
    for path in iter_python_files("services"):
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.Try):
                continue
            for index, handler in enumerate(node.handlers):
                is_bare_raise = (
                    len(handler.body) == 1
                    and isinstance(handler.body[0], ast.Raise)
                    and handler.body[0].exc is None
                )
                if not is_bare_raise or handler.type is None:
                    continue
                later_types = [
                    ast.unparse(other.type)
                    for other in node.handlers[index + 1 :]
                    if other.type is not None
                ]
                # Пропускаем случаи с широким перехватом ниже: там клауза
                # действительно нужна, чтобы своя ошибка прошла наверх.
                if any("Exception" in item or "BaseException" in item for item in later_types):
                    continue
                offenders.append(f"{where(path, handler.lineno)} ({ast.unparse(handler.type)})")

    assert not offenders, "Мёртвые клаузы `except ...: raise`: " + ", ".join(offenders)


def test_service_with_external_call_works_through_a_short_db_scope() -> None:
    """Сервис с медленным внешним вызовом не удерживает соединение с базой.

    Три свойства проверяются вместе, потому что нарушение любого из них
    означает одно и то же: сессия живёт столько же, сколько сервис, и
    соединение занято и во время ожидания модели, и во время передачи
    файла. Хранимый репозиторий даёт длинную сессию напрямую, отсутствие
    фабрики области не оставляет короткой альтернативы, а импорт
    SQLAlchemy означает, что реализация области уехала из DI-слоя в сам
    сервис.
    """
    offenders: list[str] = []
    for file_name, class_name in sorted(EXTERNAL_CALL_SERVICES.items()):
        path = SRC / "services" / file_name
        source = path.read_text(encoding="utf-8")
        init = init_of(ast.parse(source), class_name)

        stored = [
            target.attr
            for node in ast.walk(init)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr.endswith(("_repository", "unit_of_work"))
        ]
        if stored:
            offenders.append(f"{class_name} хранит {stored}")

        arguments = {argument.arg for argument in init.args.args + init.args.kwonlyargs}
        if "scope" not in arguments:
            offenders.append(f"{class_name} не получает фабрику короткой области")

        if "sqlalchemy" in source:
            offenders.append(f"{class_name} импортирует SQLAlchemy")

    assert not offenders, (
        "Сервисы с внешним вызовом работают не через короткую область: " + "; ".join(offenders)
    )


def dependency_modules() -> list[Path]:
    """Возвращает модули слоя зависимостей."""
    return [path for path in sorted(DEPENDENCIES.glob("*.py")) if path.name != "__init__.py"]


def factories_wrapped_in_aliases(tree: ast.AST) -> set[str]:
    """Возвращает функции, обёрнутые в `Depends` внутри `...Dep`-алиаса."""
    wrapped: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id.endswith("Dep")):
            continue
        wrapped.update(
            sub.args[0].id
            for sub in ast.walk(node.value)
            if isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id == "Depends"
            and sub.args
            and isinstance(sub.args[0], ast.Name)
        )
    return wrapped


def test_every_dependency_factory_has_a_dep_alias() -> None:
    """У каждой публичной фабрики зависимости есть свой `...Dep`-алиас.

    Алиас — единственная форма, которую видят потребители: без него
    каждый вызывающий пишет `Annotated[..., Depends(...)]` заново, и
    подмена в тестах перестаёт быть однозначной.
    """
    offenders: list[str] = []
    for path in dependency_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        factories = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith(("get_", "require_"))
        }
        missing = sorted(factories - factories_wrapped_in_aliases(tree))
        offenders.extend(f"{path.name}: {name}" for name in missing)

    assert not offenders, "Фабрики без `...Dep`-алиаса: " + ", ".join(offenders)


def test_dependencies_are_declared_through_aliases() -> None:
    """Потребитель объявляет зависимость алиасом, а не `Depends` в сигнатуре.

    Повторённый вручную `Annotated[..., Depends(...)]` расходится с
    алиасом молча: тип и фабрика начинают жить отдельно друг от друга.
    """
    offenders: list[str] = []
    for path in iter_python_files("dependencies", "api"):
        for node in ast.walk(parse(path)):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            offenders.extend(
                f"{where(path, argument.lineno)} {node.name}({argument.arg})"
                for argument in arguments
                if argument.annotation is not None
                and any(
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "Depends"
                    for sub in ast.walk(argument.annotation)
                )
            )

    assert not offenders, "Зависимость объявлена в сигнатуре вместо `...Dep`-алиаса: " + ", ".join(
        offenders
    )
