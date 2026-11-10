"""Komut satırı arayüzü.

    uv run python -m istanbul_agent.cli "Kadıköy'den Taksim'e metroyla kaç dakika?"
    uv run python -m istanbul_agent.cli --provider ollama "Kadıköy'den son M4 kaçta?"
    uv run python -m istanbul_agent.cli --quiet "..."      # yalnız cevap

Varsayılan olarak çağrılan araçları ve parametrelerini de basar: agent'ın
doğru aracı seçip seçmediği cevaba bakarak anlaşılmıyor.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from istanbul_agent.agent.graph import AgentRun, answer


def render(run: AgentRun, *, quiet: bool = False) -> None:
    if not quiet:
        print(f"sağlayıcı: {run.provider} ({run.model})")
        for index, call in enumerate(run.tool_calls, start=1):
            arguments = json.dumps(call.arguments, ensure_ascii=False)
            print(f"  araç {index}: {call.name}({arguments})")
        if not run.tool_calls:
            print("  araç çağrısı yok")
        if run.nudged:
            print("  (araç çağrılmadığı için model bir kez uyarıldı)")
    if run.error:
        print(f"HATA: {run.error}", file=sys.stderr)
        return
    if run.unverified:
        print(
            "UYARI: bu cevap hiçbir araç çağrısına dayanmıyor, yani kaynaksız. "
            "Doğrulanmamış bilgi olarak değerlendir.",
            file=sys.stderr,
        )
    print(run.answer.strip())
    if not quiet:
        print(f"\n({run.seconds:.1f} s)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="istanbul_agent.cli", description="İstanbul toplu taşıma asistanı"
    )
    parser.add_argument("question", help="Soru, tırnak içinde")
    parser.add_argument(
        "--provider",
        choices=("gemini", "ollama"),
        default=None,
        help="LLM sağlayıcısı; verilmezse .env içindeki LLM_PROVIDER",
    )
    parser.add_argument("--quiet", action="store_true", help="Yalnızca cevabı yaz")
    parser.add_argument("--max-steps", type=int, default=8, help="Agent'ın en fazla adım sayısı")
    arguments = parser.parse_args(argv)

    run = asyncio.run(answer(arguments.question, arguments.provider, max_steps=arguments.max_steps))
    render(run, quiet=arguments.quiet)
    return 1 if run.error else 0


if __name__ == "__main__":
    sys.exit(main())
