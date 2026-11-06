"""Manual smoke test: actually talk to each configured LLM provider.

Not a pytest test -- unit tests never call a real LLM. Run it by hand to
satisfy the Faz 0 exit criterion ("llm.py can reach both Gemini and Ollama"):

    uv run python scripts/smoke_llm.py
    uv run python scripts/smoke_llm.py --provider ollama

A provider that is not configured (no Gemini key, Ollama not running) is
reported as SKIP, not as a failure.
"""

from __future__ import annotations

import argparse
import sys
import time

from istanbul_agent.config import get_settings
from istanbul_agent.llm import MissingCredentialsError, get_llm

PROMPT = "Kadıköy hangi şehirdedir? Tek kelimeyle cevapla."


def probe(provider: str) -> tuple[str, str]:
    """Return (status, detail) for one provider. Never raises."""
    try:
        llm = get_llm(provider)
    except MissingCredentialsError as exc:
        return "SKIP", str(exc).split(".")[0]
    except Exception as exc:
        return "FAIL", f"{type(exc).__name__}: {exc}"

    started = time.perf_counter()
    try:
        reply = llm.invoke(PROMPT)
    except Exception as exc:
        return "FAIL", f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter() - started) * 1000

    text = getattr(reply, "text", None) or reply.content or ""
    return "OK", f"{elapsed_ms:.0f} ms | {' '.join(text.split())[:120]!r}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["gemini", "ollama"], action="append")
    args = parser.parse_args()
    providers = args.provider or ["gemini", "ollama"]

    settings = get_settings()
    print(f"LLM_PROVIDER={settings.llm_provider}  prompt={PROMPT!r}\n")

    statuses = []
    for name in providers:
        model = settings.gemini_model if name == "gemini" else settings.ollama_model
        status, detail = probe(name)
        statuses.append(status)
        print(f"  {status:4}  {name:7} {model:22} {detail}")

    print()
    if "FAIL" in statuses:
        return 1
    if "OK" not in statuses:
        print("No provider was reachable -- nothing was verified.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
