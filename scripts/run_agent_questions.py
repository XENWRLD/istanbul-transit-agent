"""İlk uçtan uca agent denemesi: beş soru, iki sağlayıcı.

Her soru için çağrılan araçları ve parametrelerini, son cevabı ve süreyi
basar. Amaç modelleri karşılaştırmak: aynı araçlarla aynı soruya hangi model
doğru aracı seçiyor, hangisi veri güvenilirliğini cevaba taşıyor.

    uv run python scripts/run_agent_questions.py
    uv run python scripts/run_agent_questions.py --provider ollama
    uv run python scripts/run_agent_questions.py --question 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from istanbul_agent.agent.graph import AgentRun, answer
from istanbul_agent.console import enable_unicode_output

# İlk beş soru raylı araçları, sonraki beş soru Faz 1'in otobüs ve konum
# araçlarını sınıyor. 6-10 arası Faz 1'in "bitti" tanımındaki kabul
# sorularıdır: en az dördü doğru araçlarla doğru cevaplanmalı.
QUESTIONS = (
    "Kadıköy'den Taksim'e metroyla kaç dakika?",
    "Kadıköy'den son M4 kaçta kalkıyor?",
    "Pazar sabahı 11'de M9 ne sıklıkla geliyor?",
    "Aşiyan'dan Levent'e nasıl giderim?",
    "Üsküdar'dan Sultanbeyli'ye ne kadar sürer, şu an hangi sıklıkta tren var?",
    "Kadıköy iskelesine en yakın otobüs durakları hangileri?",
    "Kadıköy'deki otobüs durağından hangi hatlar geçiyor?",
    "500T hattı nerelerden geçiyor, kaç durak var?",
    "34 numaralı otobüs hafta içi ilk kaçta kalkıyor?",
    "Taksim'in konumu nedir, 300 metre içinde hangi duraklar var?",
)

PROVIDERS = ("gemini", "ollama")


def show(index: int, run: AgentRun) -> None:
    print("=" * 78)
    print(f"S{index}. {run.question}")
    print(f"    sağlayıcı: {run.provider} ({run.model})   süre: {run.seconds:.1f} s")
    if run.tool_calls:
        for number, call in enumerate(run.tool_calls, start=1):
            arguments = json.dumps(call.arguments, ensure_ascii=False)
            print(f"    araç {number}: {call.name}({arguments})")
    else:
        print("    araç çağrısı YOK")
    if run.error:
        print(f"    HATA: {run.error}")
        return
    print("    --- cevap ---")
    for line in (run.answer or "(boş)").splitlines():
        print(f"    {line}")


async def run_all(providers: tuple[str, ...], indices: list[int]) -> int:
    failures = 0
    for provider in providers:
        print("\n" + "#" * 78)
        print(f"#  {provider.upper()}")
        print("#" * 78)
        for index in indices:
            run = await answer(QUESTIONS[index - 1], provider)
            show(index, run)
            failures += 1 if run.error else 0
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent senaryo denemesi")
    parser.add_argument("--provider", choices=PROVIDERS, action="append", default=None)
    parser.add_argument(
        "--question",
        type=int,
        action="append",
        default=None,
        help="1-5; verilmezse hepsi",
    )
    arguments = parser.parse_args(argv)
    providers = tuple(arguments.provider) if arguments.provider else PROVIDERS
    indices = arguments.question or list(range(1, len(QUESTIONS) + 1))
    return asyncio.run(run_all(providers, indices))


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
