"""List the Gemini models this API key can actually use.

Diagnostic helper: the free tier retires model names without notice, so the
model id in `.env` has to be checked against the live list rather than
assumed. Run it when a Gemini call returns 404 NOT_FOUND.

    uv run python scripts/list_gemini_models.py
"""

from __future__ import annotations

import sys

import httpx

from istanbul_agent.config import get_settings

LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def main() -> int:
    settings = get_settings()
    if settings.gemini_api_key is None:
        print("GEMINI_API_KEY is not set in .env")
        return 1

    response = httpx.get(
        LIST_URL,
        params={"key": settings.gemini_api_key.get_secret_value(), "pageSize": "200"},
        timeout=30.0,
    )
    print(f"HTTP {response.status_code}")
    if response.status_code != 200:
        print(response.text[:2000])
        return 1

    models = [
        m
        for m in response.json().get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    for m in sorted(models, key=lambda m: m["name"]):
        name = m["name"].removeprefix("models/")
        marker = " <- .env" if name == settings.gemini_model else ""
        print(f"  {name:45} in={m.get('inputTokenLimit')} out={m.get('outputTokenLimit')}{marker}")
    print(f"\n{len(models)} model supports generateContent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
