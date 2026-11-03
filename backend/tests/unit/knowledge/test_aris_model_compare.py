"""Полный платный прогон не стартует с непроверенным или чужим конфигом."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import aris_model_compare
from scripts.aris_model_profiles import PROFILES
from src.clients.model_options import agent_request_fields
from src.core.settings import get_settings


def test_comparison_rejects_missing_failed_or_mismatched_preflight_before_starting_services(
    monkeypatch, tmp_path
):
    start = Mock(side_effect=AssertionError("Services must not start"))
    monkeypatch.setattr(aris_model_compare, "start_stack", start)
    profile = next(row for row in PROFILES if row.name == "qwen38-direct")
    settings = profile.apply(get_settings().llm)
    valid = {
        "profile": profile.name,
        "status": "passed",
        "json_valid": True,
        "native_tools_valid": True,
        "model": settings.agent_model,
        "request_fields": agent_request_fields(settings),
    }
    artifact = tmp_path / "preflight.json"
    output = tmp_path / "round"
    args = SimpleNamespace(
        live=True,
        one_profile=None,
        profile=[profile.name],
        preflight=[str(artifact)],
        output_dir=str(output),
    )
    for row in [
        {**valid, "status": "failed"},
        {**valid, "model": "another-model"},
        {**valid, "request_fields": {}},
        {**valid, "native_tools_valid": False},
    ]:
        artifact.write_text(json.dumps({"records": [row]}), encoding="utf-8")
        with pytest.raises(ValueError, match="matching passed preflight"):
            aris_model_compare.main(args)
        assert not output.exists()
    start.assert_not_called()
    with pytest.raises(ValueError, match="preflight artifact is required"):
        aris_model_compare.validate_preflight([profile], [])
    artifact.write_text(json.dumps({"records": [valid]}), encoding="utf-8")
    aris_model_compare.validate_preflight([profile], [str(artifact)])


def test_gemini_control_keeps_its_preflight_after_the_active_model_switches(monkeypatch, tmp_path):
    """Смена активной модели не превращает контроль Gemini в замер Luna."""
    luna = next(row for row in PROFILES if row.name == "luna-standard")
    active = luna.apply(get_settings().llm)
    monkeypatch.setattr("src.core.settings.get_settings", lambda: SimpleNamespace(llm=active))
    control = next(row for row in PROFILES if row.name == "gemini-current")
    artifact = tmp_path / "previous-gemini-preflight.json"
    artifact.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "profile": "gemini-current",
                        "status": "passed",
                        "json_valid": True,
                        "native_tools_valid": True,
                        "model": "google/gemini-3.7-flash",
                        "request_fields": {},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    aris_model_compare.validate_preflight([control], [str(artifact)])


def test_luna_requires_a_confirmed_standard_route(tmp_path):
    profile = next(row for row in PROFILES if row.name == "luna-standard")
    settings = profile.apply(get_settings().llm)
    record = {
        "profile": profile.name,
        "status": "passed",
        "json_valid": True,
        "native_tools_valid": True,
        "model": settings.agent_model,
        "request_fields": agent_request_fields(settings),
    }
    artifact = tmp_path / "luna.json"
    artifact.write_text(json.dumps({"records": [record]}), encoding="utf-8")
    with pytest.raises(ValueError, match="luna-standard"):
        aris_model_compare.validate_preflight([profile], [str(artifact)])
    record["standard_provider_confirmed"] = True
    artifact.write_text(json.dumps({"records": [record]}), encoding="utf-8")
    aris_model_compare.validate_preflight([profile], [str(artifact)])


def test_internal_child_cannot_skip_preflight_or_use_an_unowned_stack(monkeypatch):
    database = Mock(side_effect=AssertionError("Database must not be touched"))
    monkeypatch.setattr(aris_model_compare, "create_database", database)
    args = SimpleNamespace(live=True, one_profile="gemini-current", preflight=[])
    with pytest.raises(ValueError, match="preflight artifact is required"):
        aris_model_compare.main(args)
    monkeypatch.setattr(aris_model_compare, "validate_preflight", lambda *args: None)
    monkeypatch.delenv("VERA_EVAL_STACK_MARKER", raising=False)
    with pytest.raises(ValueError, match="parent's isolated eval stack"):
        aris_model_compare.main(args)
    database.assert_not_called()
