"""Политика безопасного статического Git-индекса и точные Unicode-фрагменты."""

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from src.exceptions.git_sources import GitCredentialUnavailableError
from src.services.git_credentials import GitCredentialCodec
from src.services.git_index import (
    analyze_file,
    policy_hash,
    prepare_text,
    text_fragments,
)


def test_text_names_contents_and_encodings_do_not_filter_tracked_content():
    texts = [
        "TOKEN = 'synthetic-long-value'", "-----BEGIN PRIVATE KEY-----\nsynthetic",
        "ghp_" + "a" * 30, "# Пример 🧭", "binary-extension-but-text",
    ]
    for text in texts:
        for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-32"):
            assert prepare_text(text.encode(encoding)) == (text, None)
    assert policy_hash(ref="heads/main") != policy_hash(ref="heads/feature")
    assert policy_hash(ref="heads/main") == policy_hash(ref="heads/main")


def test_actual_nontext_and_lfs_have_metadata_reasons():
    for content, expected in (
        (b"binary\0text", "binary_file"),
        (b"\xff", "unsupported_encoding"),
        (b"version https://git-lfs.github.com/spec/v1\noid sha256:1", "lfs_pointer"),
    ):
        assert prepare_text(content) == (None, expected)


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
    assert [(item["name"], item["kind"], item["line"]) for item in analysis["symbols"][1:]] == [
        ("App", "class", 3),
        ("start", "method", 4),
    ]
    assert analysis["imports"] == [{"name": "os", "line": 1}, {"name": ".api", "line": 2}]
    assert analyze_file("bad.py", "class App:\n  !")["partial"]
    limited = analyze_file("many.py", "\n".join(f"def fn{i}(): pass" for i in range(201)))
    assert not limited["partial"] and len(limited["symbols"]) == 202
    parsed = analyze_file("sample.ts", "// import 'example'; function commentOnly() {}\n")
    assert not parsed["partial"] and parsed["coverage"]["parser"] == "tree_sitter"
    assert len(parsed["symbols"]) == 1 and parsed["imports"] == []


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
