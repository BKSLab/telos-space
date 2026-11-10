"""Квитанция каталога учитывает фактические схемы, defaults и описания."""

import copy
import json
from pathlib import Path

import pytest

from src.mcp_server.catalog import catalog_hash, normalize_tools, source_build_version
from src.mcp_server.catalog_probe import compare_snapshots, main


def test_catalog_fingerprint_and_comparison_preserve_the_complete_contract() -> None:
    original = {
        "name": "tt_read",
        "description": "Полное чтение",
        "inputSchema": {"type": "object", "properties": {
            "full_text": {"type": "boolean", "default": False, "description": "Читать полностью"},
        }},
        "outputSchema": {"type": "object"},
    }
    other = {"name": "tt_other", "inputSchema": {"type": "object"}}
    reordered = json.loads(json.dumps(original, sort_keys=True))
    assert catalog_hash([original, other]) == catalog_hash([other, reordered])
    assert normalize_tools([other, original])[0]["name"] == "tt_other"
    for key, replacement in (
        ("description", "Изменённое описание"),
        ("inputSchema", {"type": "object", "properties": {"full_text": {"default": True}}}),
        ("outputSchema", {"type": "string"}),
    ):
        changed = {**original, key: replacement}
        comparison = compare_snapshots({"tools": [original]}, {"tools": [changed, other]})
        assert not comparison["same_catalog"]
        assert comparison["changed"] == {"tt_read": [key]}
        assert comparison["added"] == ["tt_other"]
        assert comparison["removed"] == []
    # JSON Schema различает boolean и число; Python dict equality их смешивает.
    numeric = copy.deepcopy(original)
    numeric["inputSchema"]["properties"]["full_text"]["default"] = 0
    assert compare_snapshots({"tools": [original]}, {"tools": [numeric]})["changed"]
    assert compare_snapshots({"tools": [original]}, {"tools": [reordered]})["same_catalog"]
    for invalid in ([original, original], [{"inputSchema": {}}]):
        with pytest.raises(ValueError):
            catalog_hash(invalid)
    # Короткая выдача tool search/ALL_TOOLS не считается полным tools/list.
    with pytest.raises(ValueError, match="JSON Schema"):
        compare_snapshots({"tools": [original]}, {"tools": [{"name": "tt_read"}]})


def test_build_identity_tracks_code_and_dependencies_and_ignores_secrets(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    code = tmp_path / "src" / "example.py"
    code.write_bytes(b"x = 1\r\n")
    (tmp_path / "main.py").write_text("import src\n", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("mcp==2.1.1\n", encoding="utf-8")
    first = source_build_version(tmp_path)
    (tmp_path / ".env").write_text("SYNTHETIC_TEST_SECRET=changed\n", encoding="utf-8")
    code.write_bytes(b"x = 1\n")
    assert source_build_version(tmp_path) == first
    code.write_bytes(b"x = 2\n")
    second = source_build_version(tmp_path)
    assert second != first
    requirements.write_text("mcp==2.1.2\n", encoding="utf-8")
    assert source_build_version(tmp_path) != second


def test_probe_cli_errors_do_not_print_auth_values_or_server_payloads(monkeypatch, capsys):
    from src.mcp_server import catalog_probe

    secret = "synthetic-key-for-redaction-test"

    async def failing_snapshot(*_args, **_kwargs):
        raise RuntimeError(secret)

    monkeypatch.setenv("SYNTHETIC_PROBE_TOKEN", secret)
    monkeypatch.setattr(catalog_probe, "remote_snapshot", failing_snapshot)
    monkeypatch.setattr("sys.argv", [
        "catalog_probe", "remote", "http://127.0.0.1:1/", "--output", "unused.json",
        "--token-env", "SYNTHETIC_PROBE_TOKEN",
    ])
    assert main() == 2
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert "RuntimeError" in output.out
