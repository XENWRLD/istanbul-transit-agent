"""Settings are read from the environment, not hardcoded in call sites."""

from pathlib import Path

import pytest

from istanbul_agent.config import PROJECT_ROOT, Settings


def test_defaults_match_env_example():
    """Every default in Settings must mirror the value in .env.example."""
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = dict(
        line.split("=", 1)
        for line in (raw.strip() for raw in text.splitlines())
        if line and not line.startswith("#") and "=" in line
    )

    settings = Settings(_env_file=None)
    for key in ("LLM_PROVIDER", "GEMINI_MODEL", "OLLAMA_MODEL", "OLLAMA_KEEP_ALIVE"):
        assert key in documented, f"{key} missing from .env.example"
        assert str(getattr(settings, key.lower())) == documented[key]


def test_env_overrides_default(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "some-other-model")
    settings = Settings(_env_file=None)
    assert settings.llm_provider == "ollama"
    assert settings.ollama_model == "some-other-model"


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_api_key_is_not_leaked_by_repr(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-value")
    settings = Settings(_env_file=None)
    assert "super-secret-value" not in repr(settings)
    assert settings.gemini_api_key is not None
    assert settings.gemini_api_key.get_secret_value() == "super-secret-value"


def test_relative_store_paths_resolve_against_project_root():
    settings = Settings(_env_file=None, duckdb_path=Path("data/transit.duckdb"))
    assert settings.duckdb_file == PROJECT_ROOT / "data" / "transit.duckdb"


def test_absolute_store_paths_are_left_alone(tmp_path):
    target = tmp_path / "elsewhere.duckdb"
    settings = Settings(_env_file=None, duckdb_path=target)
    assert settings.duckdb_file == target
