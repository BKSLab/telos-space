"""Исходники-факты для парсеров: без исполнения, provider и БД."""

import copy
import json

import pytest

from src.services import git_structure
from src.services.git_index import analyze_file, text_fragments
from src.services.git_structure import candidate_import_paths, extract_structure, resolve_snapshot


def declarations(structure):
    return {symbol["qualified_name"]: symbol for symbol in structure["symbols"][1:]}


def test_large_text_does_not_allocate_an_integer_for_every_character():
    import tracemalloc

    text = "a" * (2 * 1024 * 1024)
    tracemalloc.start()
    try:
        result = extract_structure("README.md", text)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 12 * 1024 * 1024
    assert result["symbols"][0]["end_offset"] == len(text)


def test_utf8_coordinates_across_sparse_conversion_boundaries():
    text = "// " + "🧭ю" * 5000 + "\r\nexport function run() { return 'ю'; }\r\n"
    result = extract_structure("large.ts", text)
    symbol = declarations(result)["run"]
    assert symbol["line_start"] == 2
    assert text[symbol["offset"]:symbol["end_offset"]] == text.encode()[
        symbol["byte_start"]:symbol["byte_end"]
    ].decode()


def test_python_exact_ranges_multiline_decorators_docs_and_containment():
    text = (
        '# 🧭 e\u0301\r\n"""Модуль."""\r\nfrom .base import Base as B\r\n'
        '@registry("ю")\r\nclass Пример(B):\r\n'
        '    """Описание класса."""\r\n'
        '    @staticmethod\r\n    async def run(\r\n'
        '        арг: str = "🧭",\r\n    ) -> str:\r\n'
        '        """Описание метода."""\r\n'
        '        def nested():\r\n            return dynamic[арг]()\r\n'
        '        return nested()\r\n'
    )
    result = extract_structure("pkg/example.py", text)
    assert not result["partial"]
    symbols = declarations(result)
    assert set(symbols) == {"Пример", "Пример.run", "Пример.run.nested"}
    method = symbols["Пример.run"]
    assert method["kind"] == "method" and method["is_async"]
    assert method["line_start"] == 7 and method["line_end"] == 14
    assert method["documentation"] == "Описание метода."
    assert method["decorators"] == ["staticmethod"]
    assert 'арг: str = "🧭",\r\n' in method["signature"]
    assert method["signature"].endswith(") -> str")
    assert symbols["Пример"]["signature"] == "class Пример(B)"
    for symbol in result["symbols"]:
        original = text[symbol["offset"]:symbol["end_offset"]]
        assert text.encode()[symbol["byte_start"]:symbol["byte_end"]].decode() == original
        assert "\r\n" in original
    assert symbols["Пример.run.nested"]["parent_id"] == method["id"]
    assert all(edge["resolution"] == "exact" for edge in result["relations"] if edge["kind"] == "contains")
    assert all(edge["resolution"] == "candidate" for edge in result["relations"] if edge["kind"] in {"calls", "inherits"})
    assert any(edge["target_name"] == "dynamic[арг]" for edge in result["relations"])
    assert result == extract_structure("pkg/example.py", text)


def test_python_signature_with_decorated_first_statement_and_redefined_names():
    result = extract_structure("a.py", "class C:\n @dec\n def f(self): pass\ndef f(): pass\ndef f(): pass\n")
    assert declarations(result)["C"]["signature"] == "class C"
    functions = [symbol for symbol in result["symbols"] if symbol["name"] == "f"]
    assert len(functions) == 3 and len({symbol["id"] for symbol in functions}) == 3
    assert [symbol["qualified_name"] for symbol in functions] == ["C.f", "f", "f"]


def test_parenthesized_multiline_python_decorator_keeps_at_sign_in_source_range():
    text = "@(\r\n    factory(\"🧭\")\r\n)\r\ndef decorated():\r\n    pass\r\n"
    symbol = extract_structure("a.py", text)["symbols"][1]
    assert symbol["offset"] == 0 and symbol["line_start"] == 1
    assert symbol["decorators"] == ['factory("🧭")']


def test_python_large_file_has_no_silent_symbol_or_import_caps():
    result = analyze_file("large.py", "\n".join(
        [f"import module{i}" for i in range(130)] + [f"def fn{i}(): pass" for i in range(1200)]
    ))
    assert len(result["symbols"]) == 1201 and len(result["imports"]) == 130
    assert not result["partial"]


@pytest.mark.parametrize("path", ["a.ts", "a.tsx", "a.js", "a.jsx", "a.mjs", "a.cjs", "a.mts", "a.cts"])
def test_tree_sitter_script_declarations_not_comments_or_strings(path):
    text = (
        '// function phantom() {}\r\nconst sample = "class Fake {}";\r\n'
        '/** Документ 🧭 */\r\nexport class Service extends Base {\r\n'
        '  start(a) { return helper(a); }\r\n}\r\n'
        'export const process = (a) => helper(a);\r\n'
    )
    result = extract_structure(path, text)
    assert not result["partial"] and result["coverage"]["parser"] == "tree_sitter"
    assert set(declarations(result)) == {"Service", "Service.start", "process"}
    assert declarations(result)["Service"]["documentation"] == "/** Документ 🧭 */"
    for symbol in result["symbols"]:
        assert text[symbol["offset"]:symbol["end_offset"]].encode() == text.encode()[symbol["byte_start"]:symbol["byte_end"]]
    calls = [edge for edge in result["relations"] if edge["kind"] == "calls"]
    assert len(calls) == 2 and {edge["target_name"] for edge in calls} == {"helper"}
    assert all(edge["resolution"] == "candidate" for edge in calls)


def test_typescript_interfaces_types_import_alias_and_implements():
    result = extract_structure("src/a.ts", (
        'import { Base as B, helper } from "./base";\n'
        'import Hidden from "@app/hidden";\n'
        'interface Contract extends Parent { run(x: number): string; }\n'
        'type Result = string | number;\n'
        'class Worker extends B implements Contract { run(x: number): string { return helper(x); } }\n'
    ))
    assert not result["partial"]
    assert declarations(result)["Contract"]["kind"] == "interface"
    assert declarations(result)["Result"]["kind"] == "type"
    imports = [edge for edge in result["relations"] if edge["kind"] == "imports"]
    assert {(edge["local_name"], edge["imported_name"]) for edge in imports} == {("B", "Base"), ("helper", "helper"), ("Hidden", "default")}
    assert {edge["target_name"] for edge in result["relations"] if edge["kind"] == "implements"} == {"Contract"}
    assert {edge["target_name"] for edge in result["relations"] if edge["kind"] == "inherits"} == {"B", "Parent"}


def test_commonjs_dynamic_literal_imports_and_reexports_use_parser_nodes():
    result = extract_structure("a.js", (
        'const x = require("./x");\nconst y = import("./y");\n'
        'export {thing} from "./z";\nconst d = import(dynamic);\n'
    ))
    assert {item["name"] for item in result["imports"]} == {"./x", "./y", "./z"}
    assert all(edge["resolution"] != "exact" for edge in result["relations"] if edge["kind"] == "imports")


@pytest.mark.parametrize("path,text", [("a.py", "def broken(:\n"), ("a.ts", "export class Broken { f( {\n")])
def test_syntax_error_remains_readable_with_honest_coverage(path, text):
    result = extract_structure(path, text)
    assert result["partial"] and result["coverage"]["reason"] == "syntax_not_parsed"
    assert result["symbols"][0]["id"] == "$file"
    assert text_fragments(text)[0]["text"] == text


def test_missing_parser_falls_back_to_original_text(monkeypatch):
    def missing(_):
        raise ImportError("unavailable")

    monkeypatch.setattr(git_structure, "_script_parser", missing)
    result = extract_structure("a.ts", "function hello() {}")
    assert result["partial"] and result["coverage"]["level"] == "text"
    assert result["reason"] == "parser_unavailable" and len(result["symbols"]) == 1


@pytest.mark.parametrize("path", ["main.rs", "a.go", "a.vue", "a.svelte", "data.bin"])
def test_unsupported_language_keeps_file_root_and_does_not_invent_structure(path):
    result = extract_structure(path, "fn main() { }\n")
    assert result["coverage"]["reason"] == "structural_parser_unsupported"
    assert result["coverage"]["level"] == "text"
    assert len(result["symbols"]) == 1 and result["relations"] == []


def test_snapshot_resolver_relative_python_imports_and_ambiguous_calls():
    corpus = {
        "pkg/a.py": extract_structure("pkg/a.py", "from .base import Base as B\nclass C(B): pass\ndef same(): pass\ndef same(): pass\nsame()\n"),
        "pkg/base.py": extract_structure("pkg/base.py", "class Base: pass\n"),
    }
    before = copy.deepcopy(corpus)
    result = resolve_snapshot(corpus)
    assert corpus == before
    edges = result["pkg/a.py"]["relations"]
    imported = next(edge for edge in edges if edge["kind"] == "imports")
    assert imported["resolution"] == "exact" and imported["target_path"] == "pkg/base.py"
    assert imported["target_symbol_id"].startswith("Base@")
    inherited = next(edge for edge in edges if edge["kind"] == "inherits")
    assert inherited["resolution"] == "candidate" and inherited["target_path"] == "pkg/base.py"
    call = next(edge for edge in edges if edge["kind"] == "calls")
    assert call["resolution"] == "candidate" and len(call["target_candidates"]) == 2
    assert call["target_symbol_id"] is None


def test_snapshot_resolution_retains_ambiguity_and_never_guesses_ts_aliases():
    corpus = {path: extract_structure(path, text) for path, text in {
        "src/a.ts": 'import {foo as f} from "./other"; import {bar} from "@app/other"; f();',
        "src/other.ts": "export function foo() {}",
        "src/other.js": "export function foo() {}",
        "@app/other.ts": "export function bar() {}",
    }.items()}
    result = resolve_snapshot(corpus)
    imports = [edge for edge in result["src/a.ts"]["relations"] if edge["kind"] == "imports"]
    assert imports[0]["resolution"] == "candidate" and len(imports[0]["target_candidates"]) == 2
    assert imports[1]["resolution"] == "unresolved" and imports[1]["target_candidates"] == []
    assert candidate_import_paths("src/a.ts", {"module": "../../../outside"}, "typescript") == []


def test_resolver_empty_multiple_repository_and_snapshot_isolation_large_manifest():
    assert resolve_snapshot({}) == {}
    source = extract_structure("a.py", "from target import run\nrun()")
    first = {"a.py": source, "target.py": extract_structure("target.py", "def run(): pass")}
    second = {"a.py": source}
    third = {"a.py": source, "different/target.py": first["target.py"]}
    for index in range(1100):
        first[f"pkg/file{index}.txt"] = extract_structure(f"pkg/file{index}.txt", "text")
    repositories = [resolve_snapshot(repo) for repo in [first, second, third]]
    assert len(repositories[0]) == 1102
    assert repositories[0]["a.py"]["relations"][0]["resolution"] == "exact"
    assert repositories[1]["a.py"]["relations"][0]["resolution"] == "unresolved"
    assert repositories[2]["a.py"]["relations"][0]["resolution"] == "unresolved"
    # Re-resolving the same unchanged source after deleting its target clears stale edges.
    next_snapshot = resolve_snapshot({"a.py": repositories[0]["a.py"]})
    assert next_snapshot["a.py"]["relations"][0]["target_path"] is None
    assert next_snapshot["a.py"]["relations"][-1]["target_symbol_id"] is None


def test_small_functions_are_whole_long_functions_split_exactly_at_lines():
    small = 'def small():\r\n    return "🧭"\r\n'
    long = "def long():\r\n" + '    value = "текст 🧭"\r\n' * 130 + "    return value\r\n"
    text = "# intro\r\n" * 50 + small + long + small.replace("small", "last")
    structure = extract_structure("a.py", text)
    fragments = text_fragments(text, max_chars=600, structure=structure)
    assert "".join(fragment["text"] for fragment in fragments) == text
    for symbol in structure["symbols"][1:]:
        if symbol["end_offset"] - symbol["offset"] <= 600:
            assert any(fragment["offset"] <= symbol["offset"] and fragment["end_offset"] >= symbol["end_offset"] for fragment in fragments)
    assert any(fragment["symbol_id"] and fragment["context"] for fragment in fragments)
    for fragment in fragments:
        assert len(fragment["text"]) <= 600
        assert text[fragment["offset"]:fragment["end_offset"]] == fragment["text"]
        assert fragment["line_start"] == text[:fragment["offset"]].count("\n") + 1
    assert json.loads(json.dumps(structure)) == structure


@pytest.mark.parametrize("path,text", [
    ("doc.md", "# First\r\n" + "text\r\n" * 90 + "# Second\r\n" + "text\r\n" * 90),
    ("config.toml", "[first]\n" + "value = 1\n" * 90 + "[second]\n" + "value = 2\n" * 90),
    ("schema.sql", "CREATE TABLE first (id int);\n" * 30 + "SELECT * FROM first;\n" * 30),
])
def test_sections_keep_original_offsets(path, text):
    fragments = text_fragments(text, max_chars=600, path=path)
    assert "".join(fragment["text"] for fragment in fragments) == text
    assert all(len(fragment["text"]) <= 600 for fragment in fragments)


def test_parser_does_not_execute_source_or_imported_project_code(tmp_path):
    sentinel = tmp_path / "must-not-exist"
    result = extract_structure("hostile.py", f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\nraise RuntimeError('no execution')\n")
    assert not sentinel.exists()
    assert any(edge["kind"] == "calls" for edge in result["relations"])


@pytest.mark.parametrize("question,qualified,start,end", [
    ("Где объявлен класс Service?", "Service", 1, 3),
    ("Где находится метод Service.run?", "Service.run", 2, 3),
    ("Где объявлена функция standalone?", "standalone", 4, 5),
])
def test_labelled_exact_symbol_range_corpus(question, qualified, start, end):
    # Labels are source truth for future retrieval evaluation, not model predictions.
    text = "class Service:\n    def run(self):\n        return 1\ndef standalone():\n    return 2\n"
    symbols = declarations(extract_structure("src/service.py", text))
    assert question and (symbols[qualified]["line_start"], symbols[qualified]["line_end"]) == (start, end)
