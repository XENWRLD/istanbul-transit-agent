"""The only place in the project where an LLM client is constructed.

Per the project rules no other module may import a provider SDK directly:
call :func:`get_llm` instead. The provider comes from ``LLM_PROVIDER`` in
`.env` and can be overridden per call for side-by-side comparison runs
(Gemini Flash vs. the local model) without touching the environment.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from istanbul_agent.config import Provider, Settings, get_settings

__all__ = ["MissingCredentialsError", "UnknownProviderError", "get_llm"]

SUPPORTED_PROVIDERS: tuple[Provider, ...] = ("gemini", "ollama")


class UnknownProviderError(ValueError):
    """Raised when LLM_PROVIDER names a provider we do not support."""


class MissingCredentialsError(RuntimeError):
    """Raised when the selected provider needs a key that is not configured."""


def _build_gemini(settings: Settings, overrides: dict[str, Any]) -> BaseChatModel:
    from langchain_google_genai import ChatGoogleGenerativeAI

    if settings.gemini_api_key is None or not settings.gemini_api_key.get_secret_value():
        raise MissingCredentialsError(
            "GEMINI_API_KEY is not set. Add it to .env (see .env.example), or run with "
            "LLM_PROVIDER=ollama to use the local model."
        )

    kwargs: dict[str, Any] = {
        "model": settings.gemini_model,
        "google_api_key": settings.gemini_api_key.get_secret_value(),
        "temperature": settings.gemini_temperature,
        "timeout": settings.llm_request_timeout,
        "max_retries": settings.llm_max_retries,
    }
    kwargs.update(overrides)
    return ChatGoogleGenerativeAI(**kwargs)


def _build_ollama(settings: Settings, overrides: dict[str, Any]) -> BaseChatModel:
    from langchain_ollama import ChatOllama

    kwargs: dict[str, Any] = {
        "model": settings.ollama_model,
        "base_url": settings.ollama_base_url,
        "temperature": settings.ollama_temperature,
        "keep_alive": settings.ollama_keep_alive,
        "reasoning": settings.ollama_reasoning,
        "num_ctx": settings.ollama_num_ctx,
        "client_kwargs": {"timeout": settings.llm_request_timeout},
    }
    kwargs.update(overrides)
    return ChatOllama(**kwargs)


_BUILDERS = {
    "gemini": _build_gemini,
    "ollama": _build_ollama,
}


def get_llm(
    provider: Provider | str | None = None,
    *,
    settings: Settings | None = None,
    **overrides: Any,
) -> BaseChatModel:
    """Build a chat model for ``provider``.

    Args:
        provider: ``"gemini"`` or ``"ollama"``. Defaults to ``LLM_PROVIDER``
            from `.env`.
        settings: Settings to read from; defaults to the cached process
            settings. Mainly a seam for tests.
        **overrides: Passed straight to the provider's constructor, after the
            values derived from settings. Use this to vary ``model`` or
            ``temperature`` for one call.

    Raises:
        UnknownProviderError: ``provider`` is not one of SUPPORTED_PROVIDERS.
        MissingCredentialsError: the provider needs a key that is not set.
    """
    settings = settings or get_settings()
    name = (provider or settings.llm_provider).strip().lower()

    builder = _BUILDERS.get(name)
    if builder is None:
        raise UnknownProviderError(
            f"Unknown LLM provider {name!r}. Supported: {', '.join(SUPPORTED_PROVIDERS)}."
        )
    return builder(settings, overrides)
