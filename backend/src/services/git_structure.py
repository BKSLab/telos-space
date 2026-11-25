"""Чистый структурный индекс закреплённого Git-снимка; исходники не исполняются."""

import ast
import copy
import io
import posixpath
import tokenize
from bisect import bisect_right
from pathlib import PurePosixPath
from typing import Any

ANALYZER_VERSION = "telos-structure-1"
_OFFSET_BLOCK = 4096
_SCRIPT_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"}


class _Source:
    def __init__(self, text: str) -> None:
        self.text = text
        self.data = text.encode("utf-8")
        self.lines = text.splitlines(keepends=True)
        self.line_offsets = [0]
        for line in self.lines:
            self.line_offsets.append(self.line_offsets[-1] + len(line))
        # Sparse UTF-8 checkpoints avoid one Python integer per character in a
        # transport-sized file while retaining exact Unicode/byte conversion.
        self.byte_offsets = [0]
        for start in range(0, len(text), _OFFSET_BLOCK):
            self.byte_offsets.append(
                self.byte_offsets[-1] + len(text[start:start + _OFFSET_BLOCK].encode("utf-8"))
            )

    def ast_offset(self, line: int, byte_column: int) -> int:
        start = self.line_offsets[line - 1]
        return start + len(self.lines[line - 1].encode("utf-8")[:byte_column].decode("utf-8"))

    def from_byte(self, offset: int) -> int:
        block = bisect_right(self.byte_offsets, offset) - 1
        return min(block * _OFFSET_BLOCK, len(self.text)) + len(
            self.data[self.byte_offsets[block]:offset].decode("utf-8")
        )

    def to_byte(self, offset: int) -> int:
        block = offset // _OFFSET_BLOCK
        return self.byte_offsets[block] + len(
            self.text[block * _OFFSET_BLOCK:offset].encode("utf-8")
        )

    def range(self, start: int, end: int) -> dict:
        line = bisect_right(self.line_offsets, start)
        end_line = bisect_right(self.line_offsets, max(start, end - 1))
        return {
            "offset": start, "end_offset": end,
            "byte_start": self.to_byte(start), "byte_end": self.to_byte(end),
            "line": line, "line_start": line, "line_end": end_line,
            "column_start": start - self.line_offsets[line - 1],
            "column_end": end - self.line_offsets[end_line - 1],
        }

    def ast_range(self, node: ast.AST) -> dict:
        return self.range(
            self.ast_offset(node.lineno, node.col_offset),
            self.ast_offset(node.end_lineno, node.end_col_offset),
        )


def _base(path: str, text: str, language: str, parser: str, level: str) -> dict:
    source = _Source(text)
    root = {
        "id": "$file", "name": PurePosixPath(path).name, "qualified_name": path,
        "kind": "module", "parent_id": None, "signature": None,
        "documentation": None, "decorators": [], **source.range(0, len(text)),
    }
    return {
        "path": path, "language": language, "analyzer_version": ANALYZER_VERSION,
        "symbols": [root], "relations": [], "imports": [], "partial": False,
        "coverage": {"level": level, "parser": parser, "partial": False, "reason": None},
    }


def _partial(result: dict, reason: str) -> dict:
    result.update(partial=True, reason=reason)
    result["coverage"].update(partial=True, reason=reason)
    return result


def _relation(kind: str, owner: str, target: str, span: dict, **metadata: Any) -> dict:
    return {
        "kind": kind, "source_symbol_id": owner, "target_symbol_id": None,
        "target_path": None, "target_name": target, "resolution": "candidate",
        **span, **metadata,
    }


def _python_signature(text: str) -> str:
    depth = 0
    lines = text.splitlines(keepends=True)
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type != tokenize.OP:
            continue
        if token.string in {"(", "[", "{"}:
            depth += 1
        elif token.string in {")", "]", "}"}:
            depth -= 1
        elif token.string == ":" and depth == 0:
            line, column = token.start
            return ("".join(lines[:line - 1]) + lines[line - 1][:column]).rstrip()
    return text.rstrip()


class _PythonExtractor(ast.NodeVisitor):
    def __init__(self, result: dict, source: _Source) -> None:
        self.result = result
        self.source = source
        self.stack = [result["symbols"][0]]
        self.decorator_offsets = [
            source.line_offsets[token.start[0] - 1] + token.start[1]
            for token in tokenize.generate_tokens(io.StringIO(source.text).readline)
            if token.type == tokenize.OP and token.string == "@"
            and not source.lines[token.start[0] - 1][:token.start[1]].strip()
        ]

    def _definition(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        parent = self.stack[-1]
        qualified = node.name if parent["id"] == "$file" else f"{parent['qualified_name']}.{node.name}"
        span = self.source.ast_range(node)
        if node.decorator_list:
            first = node.decorator_list[0]
            expression_start = self.source.ast_offset(first.lineno, first.col_offset)
            start = self.decorator_offsets[bisect_right(self.decorator_offsets, expression_start) - 1]
            span = self.source.range(start, span["end_offset"])
        is_class = isinstance(node, ast.ClassDef)
        start_header = self.source.ast_offset(node.lineno, node.col_offset)
        signature = _python_signature(self.source.text[start_header:span["end_offset"]])
        symbol = {
            "id": f"{qualified}@{node.lineno}:{node.col_offset}", "name": node.name,
            "qualified_name": qualified,
            "kind": "class" if is_class else "method" if parent["kind"] == "class" else "function",
            "is_async": isinstance(node, ast.AsyncFunctionDef), "parent_id": parent["id"],
            "signature": signature, "documentation": ast.get_docstring(node, clean=False),
            "decorators": [ast.get_source_segment(self.source.text, item) for item in node.decorator_list],
            **span,
        }
        self.result["symbols"].append(symbol)
        self.result["relations"].append(_relation(
            "contains", parent["id"], qualified, span,
            target_symbol_id=symbol["id"], target_path=self.result["path"], resolution="exact",
        ))
        if is_class:
            for base in node.bases:
                self.result["relations"].append(_relation(
                    "inherits", symbol["id"], ast.unparse(base), self.source.ast_range(base),
                ))
        # Decorators/defaults/bases execute in the surrounding scope, not the function body.
        for field, value in ast.iter_fields(node):
            if field != "body":
                for child in value if isinstance(value, list) else [value]:
                    if isinstance(child, ast.AST):
                        self.visit(child)
        self.stack.append(symbol)
        for statement in node.body:
            self.visit(statement)
        self.stack.pop()

    visit_ClassDef = _definition
    visit_FunctionDef = _definition
    visit_AsyncFunctionDef = _definition

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            self._import(node, item.name, item.asname or item.name.split(".")[0], None)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        for item in node.names:
            self._import(node, module, item.asname or item.name, item.name)

    def _import(self, node: ast.AST, module: str, local_name: str, imported_name: str | None) -> None:
        self.result["imports"].append({"name": module, "line": node.lineno})
        self.result["relations"].append(_relation(
            "imports", self.stack[-1]["id"], module, self.source.ast_range(node),
            module=module, local_name=local_name, imported_name=imported_name, resolution="unresolved",
        ))

    def visit_Call(self, node: ast.Call) -> None:
        self.result["relations"].append(_relation(
            "calls", self.stack[-1]["id"], ast.unparse(node.func), self.source.ast_range(node),
        ))
        self.generic_visit(node)


def _python(path: str, text: str) -> dict:
    result = _base(path, text, "python", "python_ast", "structural")
    try:
        tree = ast.parse(text)
        result["symbols"][0]["documentation"] = ast.get_docstring(tree, clean=False)
        _PythonExtractor(result, _Source(text)).visit(tree)
    except (SyntaxError, ValueError, RecursionError):
        return _partial(result, "syntax_not_parsed")
    return result


def _script_parser(suffix: str) -> Any:
    from tree_sitter import Language, Parser

    if suffix in {".ts", ".tsx", ".mts", ".cts"}:
        import tree_sitter_typescript

        capsule = tree_sitter_typescript.language_tsx() if suffix == ".tsx" else tree_sitter_typescript.language_typescript()
    else:
        import tree_sitter_javascript

        capsule = tree_sitter_javascript.language()
    return Parser(Language(capsule))


def _script(path: str, text: str, suffix: str) -> dict:
    result = _base(path, text, "typescript" if suffix in {".ts", ".tsx", ".mts", ".cts"} else "javascript", "tree_sitter", "structural")
    source = _Source(text)
    try:
        tree = _script_parser(suffix).parse(source.data)
    except (ImportError, ValueError, RuntimeError):
        result["coverage"].update(level="text", parser="text")
        return _partial(result, "parser_unavailable")

    def value(node: Any) -> str:
        return source.data[node.start_byte:node.end_byte].decode("utf-8")

    def span(node: Any) -> dict:
        return source.range(source.from_byte(node.start_byte), source.from_byte(node.end_byte))

    declarations = {
        "function_declaration": "function", "generator_function_declaration": "function",
        "class_declaration": "class", "abstract_class_declaration": "class",
        "method_definition": "method", "method_signature": "method",
        "interface_declaration": "interface", "type_alias_declaration": "type",
        "enum_declaration": "enum", "internal_module": "namespace",
        "function_signature": "function",
    }

    def walk(node: Any, parent: dict) -> None:
        current = parent
        kind = declarations.get(node.type)
        name_node = node.child_by_field_name("name")
        definition = node
        if node.type == "variable_declarator":
            initializer = node.child_by_field_name("value")
            if initializer and initializer.type in {"arrow_function", "function_expression", "generator_function", "class"}:
                kind = "class" if initializer.type == "class" else "function"
                definition = initializer
        if kind and name_node:
            name = value(name_node)
            qualified = name if parent["id"] == "$file" else f"{parent['qualified_name']}.{name}"
            location = span(node)
            body = definition.child_by_field_name("body")
            signature_end = source.from_byte(body.start_byte) if body else location["end_offset"]
            previous = node.prev_named_sibling
            if node.parent and node.parent.type == "export_statement":
                previous = node.parent.prev_named_sibling
            documentation = value(previous) if previous and previous.type == "comment" and value(previous).startswith("/**") else None
            current = {
                "id": f"{qualified}@{location['line_start']}:{location['column_start']}",
                "name": name, "qualified_name": qualified, "kind": kind,
                "parent_id": parent["id"], "signature": text[location["offset"]:signature_end].rstrip(),
                "documentation": documentation,
                "decorators": [value(child) for child in node.named_children if child.type == "decorator"],
                **location,
            }
            result["symbols"].append(current)
            result["relations"].append(_relation(
                "contains", parent["id"], qualified, location, target_symbol_id=current["id"],
                target_path=path, resolution="exact",
            ))
        if node.type in {"import_statement", "export_statement"}:
            module_node = node.child_by_field_name("source")
            if module_node:
                module = value(module_node)[1:-1]
                result["imports"].append({"name": module, "line": span(node)["line_start"]})
                bindings = []
                pending = list(node.named_children)
                while pending:
                    child = pending.pop(0)
                    if child.type == "import_specifier":
                        imported = child.child_by_field_name("name")
                        alias = child.child_by_field_name("alias")
                        bindings.append((value(alias or imported), value(imported)))
                    elif child.type == "namespace_import":
                        bindings.append((value(child.named_children[-1]), "*"))
                    elif child.type == "identifier" and child.parent.type == "import_clause":
                        bindings.append((value(child), "default"))
                    else:
                        pending.extend(child.named_children)
                for local, imported in bindings or [(None, None)]:
                    result["relations"].append(_relation(
                        "imports", parent["id"], module, span(node), module=module,
                        local_name=local, imported_name=imported, resolution="unresolved",
                    ))
        if node.type in {"call_expression", "new_expression"}:
            target = node.child_by_field_name("function") or node.child_by_field_name("constructor")
            if target:
                result["relations"].append(_relation("calls", current["id"], value(target), span(node)))
                arguments = node.child_by_field_name("arguments")
                if value(target) in {"require", "import"} and arguments and len(arguments.named_children) == 1:
                    argument = arguments.named_children[0]
                    if argument.type == "string":
                        module = value(argument)[1:-1]
                        result["imports"].append({"name": module, "line": span(node)["line_start"]})
                        result["relations"].append(_relation(
                            "imports", current["id"], module, span(node), module=module,
                            imported_name=None, local_name=None, resolution="unresolved",
                        ))
        if node.type in {"extends_clause", "implements_clause", "extends_type_clause"}:
            for child in node.named_children:
                result["relations"].append(_relation(
                    "implements" if node.type == "implements_clause" else "inherits",
                    current["id"], value(child), span(child),
                ))
        for child in node.named_children:
            walk(child, current)

    try:
        walk(tree.root_node, result["symbols"][0])
    except RecursionError:
        return _partial(result, "parser_recursion_limit")
    return _partial(result, "syntax_not_parsed") if tree.root_node.has_error else result


def extract_structure(path: str, text: str) -> dict:
    """Извлекает декларации и синтаксические свидетельства без исполнения и сети.

    Args:
        path: POSIX-путь внутри одного репозитория.
        text: Исходный текст без нормализации переносов и Unicode.

    Returns:
        Символы, отношения и честный уровень покрытия. Offset — Unicode chars,
        byte_start/end — UTF-8; границы полуоткрытые, номера строк с единицы.
    """
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in {".py", ".pyi"}:
        return _python(path, text)
    if suffix in _SCRIPT_SUFFIXES:
        return _script(path, text, suffix)
    result = _base(path, text, suffix.lstrip(".") or "text", "text", "text")
    result["coverage"]["reason"] = "structural_parser_unsupported"
    return result


def candidate_import_paths(path: str, relation: dict, language: str) -> list[str]:
    """Возвращает bounded пути для поиска только внутри выбранного snapshot.

    Args:
        path: Исходный POSIX-путь.
        relation: Отношение imports с module/imported_name.
        language: Язык исходника.

    Returns:
        Детерминированные кандидаты без проверки диска и без suffix-match.
        Корни source packages и TS aliases требуют отдельного адаптера.
    """
    module = relation["module"]
    if language == "python":
        level = len(module) - len(module.lstrip("."))
        tail = module[level:].replace(".", "/")
        if level:
            package = path.split("/")[:-1]
            if level > len(package):
                return []
            base = "/".join(package[:len(package) - level + 1])
            stem = posixpath.join(base, tail) if tail else base
        else:
            stem = tail
        options = [stem + ".py", stem + ".pyi", stem + "/__init__.py", stem + "/__init__.pyi"]
        imported = relation.get("imported_name")
        if imported and imported != "*":
            child = posixpath.join(stem, imported)
            options += [child + ".py", child + ".pyi", child + "/__init__.py"]
    elif module.startswith("."):
        stem = posixpath.normpath(posixpath.join(posixpath.dirname(path), module))
        if stem == ".." or stem.startswith("../"):
            return []
        options = [stem] if PurePosixPath(stem).suffix else []
        # Extension substitution is deliberately a candidate if several sources exist.
        if stem.endswith((".js", ".jsx", ".mjs", ".cjs")):
            stem = stem.rsplit(".", 1)[0]
        options += [stem + ext for ext in sorted(_SCRIPT_SUFFIXES)]
        options += [stem + "/index" + ext for ext in sorted(_SCRIPT_SUFFIXES)]
    else:
        return []
    return sorted(set(options))


def resolve_snapshot(structures: dict[str, dict]) -> dict[str, dict]:
    """Разрешает только переданный снимок; неоднозначность сохраняется явно.

    Args:
        structures: Полный доступный набор path → extract_structure для одного SHA/repo.

    Returns:
        Копия структур с exact импортами и candidate calls/inherits. Конфигурации
        aliases, модули и package scripts не загружаются и не исполняются.
    """
    resolved = copy.deepcopy(structures)
    paths = set(structures)
    for path, structure in resolved.items():
        bindings: dict[str, list[dict]] = {}
        for relation in structure["relations"]:
            if relation["kind"] != "imports":
                continue
            relation.update(target_path=None, target_symbol_id=None, resolution="unresolved")
            candidates = []
            for target_path in sorted(set(candidate_import_paths(path, relation, structure["language"])) & paths):
                imported = relation.get("imported_name")
                matching = [symbol for symbol in structures[target_path]["symbols"] if
                            symbol["parent_id"] == "$file" and symbol["name"] == imported]
                if imported in {None, "*"} or not matching:
                    matching = [{"id": "$file"}]
                candidates.extend({"path": target_path, "symbol_id": symbol["id"]} for symbol in matching)
            relation["target_candidates"] = candidates
            if len(candidates) == 1:
                relation.update(target_path=candidates[0]["path"], target_symbol_id=candidates[0]["symbol_id"], resolution="exact")
            elif candidates:
                relation["resolution"] = "candidate"
            if relation.get("local_name"):
                bindings.setdefault(relation["local_name"], []).extend(candidates)
        for relation in structure["relations"]:
            if relation["kind"] not in {"calls", "inherits", "implements"}:
                continue
            relation.update(target_path=None, target_symbol_id=None, resolution="candidate")
            target = relation["target_name"]
            candidates = list(bindings.get(target, []))
            candidates.extend({"path": path, "symbol_id": symbol["id"]} for symbol in structure["symbols"] if symbol["name"] == target)
            relation["target_candidates"] = sorted(
                { (item["path"], item["symbol_id"]): item for item in candidates }.values(),
                key=lambda item: (item["path"], item["symbol_id"]),
            )
            if len(relation["target_candidates"]) == 1:
                candidate = relation["target_candidates"][0]
                relation.update(target_path=candidate["path"], target_symbol_id=candidate["symbol_id"])
    return resolved
