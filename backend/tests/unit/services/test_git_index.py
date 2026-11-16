"""Политика безопасного статического Git-индекса и точные Unicode-фрагменты."""

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from src.exceptions.git_sources import GitCredentialUnavailableError
from src.services.git_credentials import GitCredentialCodec
from src.services.git_index import (
    analyze_file,
    exclusion_reason,
    policy_hash,
    prepare_text,
    text_fragments,
)


def test_mandatory_filters_are_not_overridden_by_user_scope():
    excluded = {
        ".env": "secret_path",
        "src/.env.local": "secret_path",
        "private.pem": "secret_path",
        "credentials.json": "secret_path",
        "node_modules/app.js": "generated_or_dependency",
        "dist/app.js": "generated_or_dependency",
        "package-lock.json": "generated_or_dependency",
        "bundle.min.js": "generated_or_dependency",
        "photo.png": "unsupported_format",
    }
    assert {path: exclusion_reason(path, [""], []) for path in excluded} == excluded
    assert exclusion_reason("src/app.py", ["src"], []) is None
    assert exclusion_reason("src2/app.py", ["src"], []) == "outside_allowed_roots"
    assert exclusion_reason("src/app.py", [""], ["src/*"]) == "excluded_pattern"
    assert exclusion_reason("src/app.py", [""], [], size=2, max_file_bytes=1) == "file_too_large"
    assert all(
        exclusion_reason("src/app.py", [""], [], mode=mode) == "symlink_or_submodule"
        for mode in ("120000", "160000")
    )
    assert policy_hash(["src", "docs"], [], max_file_bytes=5) == policy_hash(
        ["docs", "src"], [], max_file_bytes=5
    )
    assert policy_hash(["src"], [], max_file_bytes=5) != policy_hash(
        ["src"], [], max_file_bytes=5, ref="heads/main"
    )


def test_secret_binary_lfs_text_is_never_a_chunk_or_static_analysis_input():
    for content, expected in (
        (b"binary\0text", "binary_file"),
        (b"\xff", "unsupported_encoding"),
        (b"version https://git-lfs.github.com/spec/v1\noid sha256:1", "lfs_pointer"),
        (b"API_KEY = 'synthetic-long-value'", "secret_content"),
        (b"-----BEGIN PRIVATE KEY-----\nsynthetic", "secret_content"),
        (b"ghp_" + b"a" * 30, "secret_content"),
    ):
        assert prepare_text(content) == (None, expected)
    assert prepare_text("# Пример 🧭\n".encode()) == ("# Пример 🧭\n", None)
    assert prepare_text(b"API_KEY = ''\n") == ("API_KEY = ''\n", None)


def test_chunks_preserve_original_unicode_offsets_and_cover_all_text():
    text = "строка 🧭e\u0301\n" * 1000
    fragments = text_fragments(text, max_chars=600)
    assert fragments and fragments[0]["offset"] == 0
    coverage = set()
    for fragment in fragments:
        start, end = fragment["offset"], fragment["offset"] + len(fragment["text"])
        assert len(fragment["text"]) <= 600 and text[start:end] == fragment["text"]
        assert fragment["line_start"] == text[:start].count("\n") + 1
        assert fragment["line_end"] == text[:end].count("\n") + 1
        coverage.update(range(start, end))
    assert len(coverage) == len(text) and text_fragments("") == []


def test_static_map_reports_only_declarations_and_explicit_parse_limits():
    analysis = analyze_file(
        "src/app.py",
        "import os\nfrom .api import run\nclass App:\n    def start(self):\n        pass\n",
    )
    assert analysis["kind"] == "python_module" and not analysis["partial"]
    assert analysis["symbols"] == [
        {"name": "App", "kind": "class", "line": 3},
        {"name": "start", "kind": "function", "line": 4},
    ]
    assert analysis["imports"] == [{"name": "os", "line": 1}, {"name": ".api", "line": 2}]
    assert analyze_file("bad.py", "class App:\n  !")["partial"]
    limited = analyze_file("many.py", "\n".join(f"def fn{i}(): pass" for i in range(201)))
    assert limited["partial"] and len(limited["symbols"]) == 200
    lexical = analyze_file("sample.ts", "// import 'example'; function commentOnly() {}\n")
    assert lexical["partial"] and lexical["reason"] == "lexical_only"
    assert lexical["symbols"][0]["kind"] == "candidate"
    assert lexical["imports"][0]["confidence"] == "candidate"


def test_independent_credential_encryption_has_no_plaintext_or_fallback():
    secret = SecretStr("synthetic-private-credential")
    codec = GitCredentialCodec(SecretStr(Fernet.generate_key().decode()))
    ciphertext = codec.encrypt(secret)
    assert secret.get_secret_value() not in ciphertext
    assert codec.decrypt(ciphertext).get_secret_value() == secret.get_secret_value()
    for unavailable in (
        GitCredentialCodec(None),
        GitCredentialCodec(SecretStr("")),
        GitCredentialCodec(SecretStr(Fernet.generate_key().decode())),
    ):
        with pytest.raises(GitCredentialUnavailableError) as error:
            unavailable.decrypt(ciphertext)
        assert ciphertext not in str(error.value) and secret.get_secret_value() not in str(
            error.value
        )
    with pytest.raises(GitCredentialUnavailableError):
        GitCredentialCodec(None).encrypt(secret)
    with pytest.raises(GitCredentialUnavailableError):
        GitCredentialCodec(SecretStr("invalid-key"))
