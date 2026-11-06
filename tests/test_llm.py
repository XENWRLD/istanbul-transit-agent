"""Provider selection in llm.py. No real LLM is contacted here."""

import pytest

from istanbul_agent.config import Settings
from istanbul_agent.llm import (
    MissingCredentialsError,
    UnknownProviderError,
    get_llm,
)


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_ollama_provider_uses_configured_model():
    llm = get_llm(settings=_settings(llm_provider="ollama", ollama_model="qwen3.5:4b"))
    assert llm.model == "qwen3.5:4b"
    assert llm.keep_alive == "24h"


def test_default_provider_comes_from_settings():
    llm = get_llm(settings=_settings(llm_provider="ollama"))
    assert type(llm).__name__ == "ChatOllama"


def test_explicit_provider_overrides_settings():
    settings = _settings(llm_provider="gemini", gemini_api_key="unused")
    llm = get_llm("ollama", settings=settings)
    assert type(llm).__name__ == "ChatOllama"


def test_overrides_reach_the_constructor():
    llm = get_llm(
        "ollama",
        settings=_settings(ollama_model="qwen3.5:4b"),
        model="other-model",
        temperature=0.7,
    )
    assert llm.model == "other-model"
    assert llm.temperature == 0.7


def test_gemini_without_key_raises_actionable_error():
    with pytest.raises(MissingCredentialsError, match="GEMINI_API_KEY"):
        get_llm("gemini", settings=_settings(gemini_api_key=None))


def test_gemini_with_key_builds():
    llm = get_llm("gemini", settings=_settings(gemini_api_key="test-key"))
    assert type(llm).__name__ == "ChatGoogleGenerativeAI"
    assert llm.model.endswith("gemini-3.5-flash")


def test_unknown_provider_raises():
    with pytest.raises(UnknownProviderError, match="gpt-9"):
        get_llm("gpt-9", settings=_settings())


def test_provider_name_is_case_and_space_insensitive():
    llm = get_llm("  Ollama ", settings=_settings())
    assert type(llm).__name__ == "ChatOllama"
