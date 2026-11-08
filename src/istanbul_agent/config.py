"""Application settings, read once from the environment / `.env`.

Every module that needs configuration imports :func:`get_settings` rather than
reading ``os.environ`` directly, so the set of knobs stays discoverable and
mirrors ``.env.example``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["gemini", "ollama"]

# Repository root: src/istanbul_agent/config.py -> up three levels.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Values from `.env`, with defaults matching `.env.example`."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    llm_provider: Provider = "gemini"

    # --- Gemini ---
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash"
    gemini_temperature: float = 0.0

    # --- Ollama ---
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:4b"
    ollama_temperature: float = 0.0
    ollama_keep_alive: str = "24h"
    # Thinking off, context wide: measured, not guessed. With thinking on,
    # qwen3.5:4b spent 116 s on the two-tool question and returned an empty
    # completion -- no text, no tool calls. Off, it answers the same question
    # in 21-28 s with both tools. The 8192 context is the second half of the
    # fix: at the default window the model answered quickly but wrongly,
    # losing which line the question was about (decision 0006, karar 6).
    ollama_reasoning: bool = False
    ollama_num_ctx: int = 8192

    # --- Shared ---
    llm_request_timeout: float = 120.0
    llm_max_retries: int = 2

    # --- IBB open data ---
    ibb_api_base_url: str = "https://api.ibb.gov.tr"
    ibb_request_timeout: float = 15.0

    # --- Local stores (relative paths resolve against the project root) ---
    duckdb_path: Path = Field(default=Path("data/transit.duckdb"))
    qdrant_path: Path = Field(default=Path("data/qdrant"))

    def resolved_path(self, value: Path) -> Path:
        """Make a configured path absolute against the project root."""
        return value if value.is_absolute() else PROJECT_ROOT / value

    @property
    def duckdb_file(self) -> Path:
        return self.resolved_path(self.duckdb_path)

    @property
    def qdrant_dir(self) -> Path:
        return self.resolved_path(self.qdrant_path)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached)."""
    return Settings()
