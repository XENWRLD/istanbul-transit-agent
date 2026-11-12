"""Ölçümün kendi gürültü bandı: kod sabitken aynı senaryo kaç kez başka davranır?

Karar 0018'in açtığı boşluk. Kararlar 0014 ve 0015 bir değişikliğin
etkisini tek koşudan okudu ve ±2/±3 mertebesinde sonuç raporladı; 0017'nin
koşusu ise **kod değişmeden ilgisiz 12 senaryonun** araç dizisini
değiştirdiğini gösterdi. O yüzden bandın kendisi ölçülmeden hiçbir A/B
sonucu yorumlanamaz.

Betik tek şey yapar: seçilen senaryoları **aynı kodla N kez** koşar ve kaç
senaryonun davranışının tekrarlar arasında oynadığını sayar.

İki tasarım kararı, ikisi de sonucu geçersiz kılmamak için:

- **Önbellek hiç kullanılmıyor.** `run_agent_eval.py` cevapları
  `eval/.cache` altında saklıyor; aynı anahtarla ikinci kez sorulan senaryo
  diskten dönerdi ve varyans sıfır ölçülürdü. Burada her tekrar gerçek bir
  LLM turudur.
- **Her tekrar tamamlandıkça diske yazılıyor.** Uzun koşular bu projede
  tekrar tekrar kesildi (kararlar 0015, 0017); kesilen bir koşunun ölçtüğü
  tekrarlar kaybolmamalı.

Yığın koşu başına **bir kez** açılır (karar 0014): üç sunucuyu açmak 15 s
ve `knowledge_server` BGE-M3'ü açılışta yüklüyor.

Kullanım:

    uv run python eval/measure_variance.py                      # 9 senaryo x 3 tekrar
    uv run python eval/measure_variance.py --repeats 5
    uv run python eval/measure_variance.py --ids rail-04 near-04 --repeats 4

**Değerlendirme koşarken makinede başka iş çalıştırılmaz** (karar 0014):
çekişme tek bir isteği zaman aşımının üstüne çıkarıp senaryoyu sahte
biçimde düşürüyor.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import statistics
import sys
import time
from typing import Any

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output

EVAL_DIR = PROJECT_ROOT / "eval"
HARNESS_PATH = EVAL_DIR / "run_agent_eval.py"
RESULTS_PATH = EVAL_DIR / "results" / "variance.json"
ROWS_PATH = EVAL_DIR / "results" / "variance_rows.jsonl"

# Varsayılan altküme: her aile bir senaryo, artı karar 0017'de davranışı
# oynadığı **ölçülmüş** olanlar. Aileleri karıştırmanın sebebi bandın araç
# sayısına ve soru tipine göre değişip değişmediğini görmek; oynadığı
# bilinenleri koymanın sebebi bandın alt sınırını değil gerçek genişliğini
# ölçmek.
DEFAULT_IDS: tuple[str, ...] = (
    "rail-03",  # 0017'de iki araçtan sıfır araca düştü
    "rail-04",  # 0015: dört koşuda üç davranış; 0017'de geçti
    "rail-06",  # 0015'te nudge ile düzelmişti, 0017'de sıfır araca döndü
    "know-06",  # 0017'de search_knowledge yerine find_parking
    "know-08",  # 0017'de get_next_departures'ı altı kez çağırdı
    "near-04",  # kararı bekleyen senaryo
    "chain-05",  # kalan başarısızlık, iki araçlı
    "route-02",  # 0017'de değişmeyen bir satır (kontrol)
    "live-02",  # canlı veri ailesi
)


def load_harness() -> Any:
    """`eval/run_agent_eval.py` paket değil; dosya yolundan yüklenir.

    Senaryo okuma ve `check()` oradan alınıyor: geçme ölçütünü ikinci kez
    yazmak iki ölçütün sessizce ayrışması demekti.
    """
    spec = importlib.util.spec_from_file_location("run_agent_eval", HARNESS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"değerlendirme betiği yüklenemedi: {HARNESS_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_agent_eval"] = module
    spec.loader.exec_module(module)
    return module


def signature(row: dict[str, Any]) -> str:
    """Bir koşunun **davranış imzası**: çağrılan araçlar, sırasıyla.

    Argümanlar imzaya girmiyor. Girse "Üsküdar" yerine "Üsküdarr" yazmak da
    varyans sayılırdı; o ayrı bir kusur (karar 0015) ve araç seçimiyle
    karıştırılmamalı. Sıfır araç kendi imzasıdır — projenin asıl derdi o.
    """
    names = row.get("tools_called") or []
    return " → ".join(names) if names else "[sıfır araç]"


def append_row(payload: dict[str, Any]) -> None:
    ROWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ROWS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def summarise(
    per_scenario: dict[str, list[dict[str, Any]]], *, provider: str, repeats: int
) -> dict[str, Any]:
    """Bandı iki yönden özetle: davranış ve oran.

    Davranış: kaç senaryonun imzası tekrarlar arasında aynı kaldı.
    Oran: aynı altkümenin geçme sayısı tekrarlar arasında kaç puan oynadı —
    bir A/B sonucunun bu bandın dışında olması gerekir.
    """
    stable: list[str] = []
    unstable: list[dict[str, Any]] = []
    for scenario_id, runs in sorted(per_scenario.items()):
        signatures = [signature(run) for run in runs]
        passes = [bool(run["passed"]) for run in runs]
        if len(set(signatures)) == 1:
            stable.append(scenario_id)
        else:
            unstable.append(
                {
                    "id": scenario_id,
                    "signatures": signatures,
                    "distinct": len(set(signatures)),
                    "passed": passes,
                    "flips_pass": len(set(passes)) > 1,
                    "seconds": [run["seconds"] for run in runs],
                }
            )

    # Tekrar bazında geçme sayısı: her tekrar turunda kaç senaryo geçti.
    per_repeat_passed: list[int] = []
    for index in range(repeats):
        passed = 0
        counted = 0
        for runs in per_scenario.values():
            if index < len(runs):
                counted += 1
                passed += bool(runs[index]["passed"])
        if counted:
            per_repeat_passed.append(passed)

    times = [run["seconds"] for runs in per_scenario.values() for run in runs]
    flipping = [item for item in unstable if item["flips_pass"]]
    return {
        "provider": provider,
        "repeats": repeats,
        "scenarios": len(per_scenario),
        "stable_scenarios": len(stable),
        "unstable_scenarios": len(unstable),
        "unstable_share": round(len(unstable) / len(per_scenario), 3) if per_scenario else None,
        "scenarios_flipping_pass": len(flipping),
        "pass_count_per_repeat": per_repeat_passed,
        "pass_count_spread": (
            max(per_repeat_passed) - min(per_repeat_passed) if per_repeat_passed else None
        ),
        "latency_median_s": round(statistics.median(times), 1) if times else None,
        "latency_min_s": round(min(times), 1) if times else None,
        "latency_max_s": round(max(times), 1) if times else None,
        "stable": stable,
        "unstable": unstable,
    }


def report(summary: dict[str, Any]) -> None:
    print("\n=== gürültü bandı ===")
    print(f"  senaryo            {summary['scenarios']} x {summary['repeats']} tekrar")
    print(f"  imzası sabit       {summary['stable_scenarios']}")
    print(
        f"  imzası oynayan     {summary['unstable_scenarios']}"
        + (f" ({summary['unstable_share']:.0%})" if summary["unstable_share"] is not None else "")
    )
    print(f"  geçme durumu oynayan  {summary['scenarios_flipping_pass']}")
    print(f"  tekrar başına geçen   {summary['pass_count_per_repeat']}")
    print(f"  **oran bandı**        ±{summary['pass_count_spread']} senaryo")
    print(
        f"  gecikme            ortanca {summary['latency_median_s']} s "
        f"(en az {summary['latency_min_s']}, en çok {summary['latency_max_s']})"
    )
    if summary["unstable"]:
        print("\n  oynayan senaryolar:")
        for item in summary["unstable"]:
            flip = " [GEÇME DURUMU DEĞİŞTİ]" if item["flips_pass"] else ""
            print(f"    {item['id']:10} {item['distinct']} ayrı imza{flip}")
            for index, text in enumerate(item["signatures"]):
                mark = "✓" if item["passed"][index] else "✗"
                print(f"      {mark} {item['seconds'][index]:>6.1f}s  {text}")


async def main_async(args: argparse.Namespace) -> int:
    harness = load_harness()
    scenarios = {scenario.id: scenario for scenario in harness.load_scenarios()}

    wanted = list(args.ids) if args.ids else list(DEFAULT_IDS)
    missing = [name for name in wanted if name not in scenarios]
    if missing:
        # Boş sonuç parametre hatasını gizlemesin (proje kuralı): yanlış
        # yazılmış bir kimlik "sonuç yok" değil, adayları sayan bir hata.
        print(f"bilinmeyen senaryo kimliği: {missing}")
        print(f"bilinen kimlikler: {', '.join(sorted(scenarios))}")
        return 1

    selected = [scenarios[name] for name in wanted]
    repeats = max(2, int(args.repeats))
    print(
        f"=== {len(selected)} senaryo x {repeats} tekrar, sağlayıcı {args.provider}, "
        "önbellek kullanılmıyor ===",
        flush=True,
    )
    started = time.perf_counter()

    from istanbul_agent.agent.graph import ask, istanbul_agent, tool_surface_digest

    per_scenario: dict[str, list[dict[str, Any]]] = {name: [] for name in wanted}

    async with istanbul_agent(args.provider, max_steps=args.max_steps) as (
        agent,
        _used,
        model_name,
        tools,
    ):
        surface = tool_surface_digest(tools)
        print(f"    araç yüzeyi: {len(tools)} araç, özet {surface}", flush=True)

        for index in range(repeats):
            print(f"\n--- tekrar {index + 1}/{repeats} ---", flush=True)
            for scenario in selected:
                run = await ask(
                    agent,
                    scenario.question,
                    provider=args.provider,
                    model=str(model_name),
                )
                row = harness.check(scenario, run)
                row["repeat"] = index + 1
                row["surface"] = surface
                per_scenario[scenario.id].append(row)
                append_row(row)
                mark = "✓" if row["passed"] else "✗"
                print(
                    f"  {mark} {scenario.id:10} {row['seconds']:>6.1f}s  {signature(row)}",
                    flush=True,
                )
                # Özet her satırdan sonra tazeleniyor: koşu kesilse bile
                # o ana kadarki band dosyada duruyor.
                summary = summarise(per_scenario, provider=args.provider, repeats=repeats)
                RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
                RESULTS_PATH.write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
                )

    summary = summarise(per_scenario, provider=args.provider, repeats=repeats)
    summary["wall_clock_s"] = round(time.perf_counter() - started, 1)
    RESULTS_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report(summary)
    print(f"\n  duvar saati        {summary['wall_clock_s']:.0f} s")
    print(f"  sonuç              {RESULTS_PATH}")
    print(f"  satırlar           {ROWS_PATH}")
    return 0


def main() -> int:
    enable_unicode_output()
    parser = argparse.ArgumentParser(description="Değerlendirmenin koşular arası gürültü bandı")
    parser.add_argument("--provider", default="ollama", choices=["ollama", "gemini"])
    parser.add_argument("--repeats", type=int, default=3, help="her senaryo kaç kez koşulacak")
    parser.add_argument("--ids", nargs="*", default=None, help="senaryo kimlikleri")
    parser.add_argument("--max-steps", type=int, default=8)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
