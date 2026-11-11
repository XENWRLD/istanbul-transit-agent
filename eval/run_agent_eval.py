"""Agent senaryoları: doğru araç, doğru sıra, kaynaksız cevap var mı?

`eval/agent_scenarios.jsonl` içindeki her senaryoda ne beklendiği yazılı:

- `expect_tools` — çağrılması gereken araçlar. `expect_any: true` ise
  **biri** yeterli; yoksa hepsi gerekli.
- `expect_order` — bu araçlar bu **sırayla** çağrılmış olmalı (araya başka
  çağrı girebilir, sıra bozulmasın).
- `must_mention` — cevapta bu ifadelerden **biri** geçmeli. Tazelik
  uyarısı, hata payı ve "elimde yok" gibi şeyler burada ölçülüyor:
  doğru aracı çağırıp sonucu yanlış aktarmak da hatadır.
- `expect_tools: []` — araç çağrılmaması beklenen senaryolar (Faz 2'de
  olmayan araçlar). Burada ölçülen şey, modelin uydurmak yerine bilginin
  olmadığını söylemesi.

Ölçülen üç oran:

| Oran | Ne demek |
|---|---|
| araç doğruluğu | beklenen araçlar çağrıldı mı |
| sıra doğruluğu | `expect_order` verilen senaryolarda sıra korundu mu |
| ifade doğruluğu | `must_mention` ifadelerinden biri cevapta var mı |

Ayrıca **kaynaksız cevap** sayılıyor: `unverified` (hiç araç çağrılmamış)
olan senaryolar — `expect_tools: []` olanlar hariç, çünkü orada araç
çağırmamak doğru davranış.

Kota: Gemini ücretsiz katmanı günde 20 istek (karar 0006), bu yüzden
varsayılan sağlayıcı **ollama**. Cevaplar `eval/.cache/` altında soru +
sağlayıcı + model + prompt özetine göre önbelleğe alınır; aynı set
yeniden çalıştırıldığında LLM'e gidilmez.

    uv run python eval/run_agent_eval.py                      # ollama, tüm set
    uv run python eval/run_agent_eval.py --limit 5            # ilk 5 senaryo
    uv run python eval/run_agent_eval.py --id know-01 --id chain-01
    uv run python eval/run_agent_eval.py --provider gemini --limit 5
    uv run python eval/run_agent_eval.py --refresh            # önbelleği yok say
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from istanbul_agent.agent.prompt import SYSTEM_PROMPT
from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output
from istanbul_agent.store.station_names import fold_case

SCENARIOS_PATH = PROJECT_ROOT / "eval" / "agent_scenarios.jsonl"
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"
RESULTS_PATH = RESULTS_DIR / "agent_eval.json"
CACHE_DIR = PROJECT_ROOT / "eval" / ".cache"


@dataclass
class Scenario:
    id: str
    question: str
    expect_tools: list[str] = field(default_factory=list)
    expect_order: list[str] = field(default_factory=list)
    expect_any: bool = False
    forbid_tools: list[str] = field(default_factory=list)
    must_mention: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def expects_no_tool(self) -> bool:
        """Araç çağrılmaması beklenen senaryo (henüz o araç yok)."""
        return not self.expect_tools


def load_scenarios() -> list[Scenario]:
    scenarios: list[Scenario] = []
    for line in SCENARIOS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        scenarios.append(
            Scenario(
                id=row["id"],
                question=row["question"],
                expect_tools=list(row.get("expect_tools") or []),
                expect_order=list(row.get("expect_order") or []),
                expect_any=bool(row.get("expect_any")),
                forbid_tools=list(row.get("forbid_tools") or []),
                must_mention=list(row.get("must_mention") or []),
                note=row.get("note", ""),
            )
        )
    return scenarios


def cache_key(scenario: Scenario, provider: str, model: str) -> str:
    """Soru + sağlayıcı + model + prompt özeti.

    Prompt özeti anahtarda: sistem prompt'u değiştiğinde davranış değişir
    (CLAUDE.md kuralı), eski cevabı yeniden kullanmak ölçümü yalanlar.
    """
    digest = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]
    stem = f"{provider}-{model}-{digest}-{scenario.id}"
    return stem.replace("/", "_").replace(":", "_")


def is_subsequence(wanted: list[str], actual: list[str]) -> bool:
    """`wanted` sırası `actual` içinde korunuyor mu?"""
    position = 0
    for name in actual:
        if position < len(wanted) and name == wanted[position]:
            position += 1
    return position == len(wanted)


def check(scenario: Scenario, run: Any) -> dict[str, Any]:
    """Bir senaryonun sonucunu değerlendir."""
    names = list(run.tool_names)
    # Türkçe duyarlı katlama: `casefold()` "YAKLAŞIK"ı "yaklaşik" yapıyor ve
    # "yaklaşık" ifadesini kaçırıyor.
    answer = fold_case(run.answer or "")

    if scenario.expects_no_tool:
        # Araç yok: model uydurmak yerine bilginin olmadığını söylemeli.
        # Hangi araçları çağırdığı serbest (arayıp bulamamak da kabul).
        tools_ok = True
    elif scenario.expect_any:
        tools_ok = any(name in names for name in scenario.expect_tools)
    else:
        tools_ok = all(name in names for name in scenario.expect_tools)

    order_ok: bool | None = None
    if scenario.expect_order:
        order_ok = is_subsequence(scenario.expect_order, names)

    mention_ok: bool | None = None
    if scenario.must_mention:
        mention_ok = any(fold_case(term) in answer for term in scenario.must_mention)

    forbidden = [name for name in scenario.forbid_tools if name in names]

    return {
        "id": scenario.id,
        "question": scenario.question,
        "expects_no_tool": scenario.expects_no_tool,
        "tools_called": names,
        "tools_ok": tools_ok,
        "order_ok": order_ok,
        "mention_ok": mention_ok,
        "forbidden_called": forbidden,
        "unverified": bool(run.unverified),
        "nudged": bool(run.nudged),
        "seconds": round(run.seconds, 1),
        "error": run.error,
        "answer": (run.answer or "")[:400],
        "passed": bool(
            tools_ok
            and (order_ok is not False)
            and (mention_ok is not False)
            and not forbidden
            and not run.error
            # Kaynaksız cevap, araç beklenmeyen senaryolar dışında hatadır.
            and (scenario.expects_no_tool or not run.unverified)
        ),
    }


def summarise(rows: list[dict[str, Any]], *, provider: str) -> dict[str, Any]:
    """Satır listesinden oranları hesapla.

    Hem canlı koşu için hem de birleştirilmiş kayıt için aynı fonksiyon:
    kısmi bir koşudan sonra özetin yalnızca o koşuyu anlatması, dosyada
    duran satırlarla çelişiyordu.
    """
    if not rows:
        return {"provider": provider, "scenarios": 0}
    ordered = [row for row in rows if row.get("order_ok") is not None]
    mentions = [row for row in rows if row.get("mention_ok") is not None]
    unverified = [row for row in rows if row.get("unverified") and not row.get("expects_no_tool")]
    times = [row["seconds"] for row in rows]
    steps = [len(row.get("tools_called") or []) for row in rows]
    return {
        "provider": provider,
        "scenarios": len(rows),
        "passed": sum(1 for row in rows if row["passed"]),
        "pass_rate": round(sum(1 for row in rows if row["passed"]) / len(rows), 3),
        "tool_accuracy": round(sum(1 for row in rows if row["tools_ok"]) / len(rows), 3),
        "order_accuracy": (
            round(sum(1 for row in ordered if row["order_ok"]) / len(ordered), 3)
            if ordered
            else None
        ),
        "mention_accuracy": (
            round(sum(1 for row in mentions if row["mention_ok"]) / len(mentions), 3)
            if mentions
            else None
        ),
        "unverified_answers": len(unverified),
        "nudged": sum(1 for row in rows if row.get("nudged")),
        "errors": sum(1 for row in rows if row.get("error")),
        "latency_median_s": round(statistics.median(times), 1),
        "steps_median": round(statistics.median(steps), 1),
        "steps_max": max(steps),
    }


async def run_scenario(
    scenario: Scenario, provider: str, *, refresh: bool, max_steps: int
) -> tuple[Any, bool]:
    """Senaryoyu çalıştır ya da önbellekten oku; `(run, önbellekten_mi)`."""
    from istanbul_agent.agent.graph import AgentRun, answer

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Model adını öğrenmek için ayarlara bakıyoruz; LLM kurmak gerekmiyor.
    from istanbul_agent.config import get_settings

    settings = get_settings()
    model = settings.ollama_model if provider == "ollama" else settings.gemini_model
    path = CACHE_DIR / f"{cache_key(scenario, provider, model)}.json"

    if path.exists() and not refresh:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cached = AgentRun(
            question=payload["question"],
            answer=payload["answer"],
            provider=payload["provider"],
            model=payload["model"],
            seconds=payload["seconds"],
            error=payload.get("error"),
            nudged=payload.get("nudged", False),
            unverified=payload.get("unverified", False),
        )
        from istanbul_agent.agent.graph import ToolCall

        cached.tool_calls = [
            ToolCall(name=call["name"], arguments=call.get("arguments", {}))
            for call in payload.get("tool_calls", [])
        ]
        return cached, True

    run = await answer(scenario.question, provider, max_steps=max_steps)
    path.write_text(
        json.dumps(
            {
                "question": run.question,
                "answer": run.answer,
                "provider": run.provider,
                "model": run.model,
                "seconds": run.seconds,
                "error": run.error,
                "nudged": run.nudged,
                "unverified": run.unverified,
                "tool_calls": [
                    {"name": call.name, "arguments": call.arguments} for call in run.tool_calls
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return run, False


async def main_async(args: argparse.Namespace) -> int:
    scenarios = load_scenarios()
    if args.id:
        wanted = set(args.id)
        scenarios = [scenario for scenario in scenarios if scenario.id in wanted]
    if args.limit:
        scenarios = scenarios[: args.limit]
    if not scenarios:
        print("senaryo seçilmedi")
        return 1

    print(f"=== {len(scenarios)} senaryo, sağlayıcı {args.provider} ===", flush=True)
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    from_cache = 0

    for index, scenario in enumerate(scenarios):
        run, cached = await run_scenario(
            scenario, args.provider, refresh=args.refresh, max_steps=args.max_steps
        )
        from_cache += 1 if cached else 0
        # Bekleme yalnızca gerçekten LLM'e gidildiyse ve son senaryo değilse.
        if args.delay and not cached and index < len(scenarios) - 1:
            await asyncio.sleep(args.delay)
        row = check(scenario, run)
        row["cached"] = cached
        rows.append(row)
        mark = "✓" if row["passed"] else "✗"
        extra = []
        if not row["tools_ok"]:
            extra.append(f"araç bekleniyordu: {scenario.expect_tools}")
        if row["order_ok"] is False:
            extra.append(f"sıra bozuk: {scenario.expect_order}")
        if row["mention_ok"] is False:
            extra.append(f"ifade yok: {scenario.must_mention}")
        if row["unverified"] and not scenario.expects_no_tool:
            extra.append("KAYNAKSIZ")
        if row["error"]:
            extra.append(f"hata: {row['error'][:60]}")
        print(
            f"  {mark} {scenario.id:12} {row['seconds']:>6.1f}s "
            f"{'(önbellek)' if cached else '          '} {row['tools_called']}"
            + (f"  — {'; '.join(extra)}" if extra else ""),
            flush=True,
        )

    summary = summarise(rows, provider=args.provider)

    def report(title: str, data: dict[str, Any], scope: str) -> None:
        print(f"\n=== {title} ===")
        print(
            f"  geçen senaryo      {data['passed']}/{data['scenarios']} ({data['pass_rate']:.1%})"
        )
        print(f"  araç doğruluğu     {data['tool_accuracy']:.1%}")
        if data["order_accuracy"] is not None:
            print(f"  sıra doğruluğu     {data['order_accuracy']:.1%}")
        if data["mention_accuracy"] is not None:
            print(f"  ifade doğruluğu    {data['mention_accuracy']:.1%}")
        print(f"  kaynaksız cevap    {data['unverified_answers']}")
        print(f"  uyarılan tur       {data['nudged']}")
        print(f"  hata               {data['errors']}")
        print(f"  adım ortanca       {data['steps_median']} (en çok {data['steps_max']})")
        print(f"  gecikme ortanca    {data['latency_median_s']} s")
        print(f"  kapsam             {scope}")

    report(
        "bu koşu",
        summary,
        f"{len(rows)} senaryo, {from_cache} önbellekten, {time.perf_counter() - started:.0f} s",
    )

    # Satırlar **senaryo bazında** birleştirilir: kısmi bir koşu (ör. kota
    # yüzünden 3 senaryoluk gemini turu) daha önce ölçülen satırları
    # silmemeli. Özet birleştirilmiş satırlardan yeniden hesaplanır.
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stored: dict[str, dict[str, dict[str, Any]]] = {}
    if RESULTS_PATH.exists():
        previous = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        for run_summary in previous.get("runs", []):
            provider = str(run_summary.get("provider"))
            stored[provider] = {row["id"]: row for row in run_summary.get("rows", [])}
    merged_rows = stored.setdefault(args.provider, {})
    for row in rows:
        merged_rows[row["id"]] = row

    runs = []
    for provider in sorted(stored):
        provider_rows = [stored[provider][key] for key in sorted(stored[provider])]
        runs.append(
            {
                **summarise(provider_rows, provider=provider),
                "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "rows": provider_rows,
            }
        )
    RESULTS_PATH.write_text(
        json.dumps({"runs": runs}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    total = next(run for run in runs if run["provider"] == args.provider)
    if total["scenarios"] != summary["scenarios"]:
        report("kayıttaki tüm senaryolar", total, f"{total['scenarios']} senaryo")

    print(f"  sonuç: {RESULTS_PATH}")
    return 0 if not summary["errors"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("ollama", "gemini"), default="ollama")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--id", action="append", default=None)
    parser.add_argument("--refresh", action="store_true", help="önbelleği yok say")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help=(
            "senaryolar arası bekleme (saniye). Gemini ücretsiz katmanı dakikada 5 istek "
            "kabul ediyor ve her senaryo 2-4 istek harcıyor; kota hatasına düşmemek için "
            "gemini ile 15-20 saniye ver (karar 0006)"
        ),
    )
    parser.add_argument("--max-steps", type=int, default=8)
    args = parser.parse_args(argv)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
