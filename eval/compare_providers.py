"""İki sağlayıcıyı **aynı senaryolarda** yan yana koy.

`eval/results/agent_eval.json` içinde her sağlayıcının ölçülen satırları
duruyor ama kapsamları farklı: yerel model 49 senaryonun tamamında
koşuyor, Gemini ücretsiz katmanı günde 20 istek kabul ettiği için (karar
0006) yalnızca temsilî bir alt kümede. Bu yüzden karşılaştırma **kesişim**
üzerinden yapılır; ortalama almak iki farklı seti kıyaslamak olurdu.

Ölçülen: senaryo geçme, araç doğruluğu, adım sayısı (araç çağrısı),
gecikme ve kaynaksız cevap.

    uv run python eval/compare_providers.py
    uv run python eval/compare_providers.py --base ollama --other gemini
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from typing import Any

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output

RESULTS_PATH = PROJECT_ROOT / "eval" / "results" / "agent_eval.json"
REPORT_PATH = PROJECT_ROOT / "eval" / "results" / "provider_comparison.json"


def load_rows() -> dict[str, dict[str, dict[str, Any]]]:
    payload = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    return {
        str(run["provider"]): {row["id"]: row for row in run.get("rows", [])}
        for run in payload.get("runs", [])
    }


def metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    steps = [len(row.get("tools_called") or []) for row in rows]
    times = [row["seconds"] for row in rows]
    return {
        "scenarios": len(rows),
        "passed": sum(1 for row in rows if row["passed"]),
        "tools_ok": sum(1 for row in rows if row["tools_ok"]),
        "unverified": sum(
            1 for row in rows if row.get("unverified") and not row.get("expects_no_tool")
        ),
        "nudged": sum(1 for row in rows if row.get("nudged")),
        "steps_median": statistics.median(steps),
        "steps_total": sum(steps),
        "latency_median_s": round(statistics.median(times), 1),
        "latency_total_s": round(sum(times), 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="ollama")
    parser.add_argument("--other", default="gemini")
    args = parser.parse_args(argv)

    if not RESULTS_PATH.exists():
        print(f"sonuç dosyası yok: {RESULTS_PATH}")
        return 1
    stored = load_rows()
    missing = [name for name in (args.base, args.other) if name not in stored]
    if missing:
        print(f"bu sağlayıcıların ölçümü yok: {missing} (kayıtlı: {sorted(stored)})")
        return 1

    shared = sorted(set(stored[args.base]) & set(stored[args.other]))
    if not shared:
        print("iki sağlayıcının ortak senaryosu yok")
        return 1

    # Hata taşıyan satır davranış sonucu değildir. Gemini'nin ücretsiz
    # katmanı günde 20 istek kabul ediyor ve kota dolduğunda senaryo
    # `GoogleRateLimitError` ile düşüyor; bunu "geçemedi" diye saymak, kota
    # sınırını model başarısızlığı gibi göstermek olurdu. Karar 0011 ayrı
    # kapsamları yan yana koymayı yasaklıyordu, bu onun alt basamağı:
    # **ölçülemeyen senaryo karşılaştırmanın dışında kalır.**
    broken = sorted(
        key
        for key in shared
        if stored[args.base][key].get("error") or stored[args.other][key].get("error")
    )
    if broken:
        print(f"  ! hata taşıyan senaryolar karşılaştırmadan çıkarıldı: {broken}")
        for key in broken:
            for name in (args.base, args.other):
                problem = stored[name][key].get("error")
                if problem:
                    print(f"      {key} / {name}: {str(problem)[:70]}")
        shared = [key for key in shared if key not in set(broken)]
        if not shared:
            print()
            print("  ölçülebilir ortak senaryo kalmadı; kota yenilendiğinde yeniden koş")
            return 1
        print()

    base_rows = [stored[args.base][key] for key in shared]
    other_rows = [stored[args.other][key] for key in shared]
    base = metrics(base_rows)
    other = metrics(other_rows)

    print(f"=== ortak {len(shared)} senaryo: {args.base} vs {args.other} ===\n")
    print(f"  {'senaryo':12} {args.base:>26} {args.other:>26}")
    for key in shared:
        left, right = stored[args.base][key], stored[args.other][key]

        def cell(row: dict[str, Any]) -> str:
            mark = "✓" if row["passed"] else "✗"
            return f"{mark} {len(row.get('tools_called') or [])} adım {row['seconds']:>6.1f}s"

        print(f"  {key:12} {cell(left):>26} {cell(right):>26}")

    print(f"\n  {'ölçüt':20} {args.base:>12} {args.other:>12}")
    rows_out = [
        (
            "geçen senaryo",
            f"{base['passed']}/{base['scenarios']}",
            f"{other['passed']}/{other['scenarios']}",
        ),
        (
            "araç doğruluğu",
            f"{base['tools_ok']}/{base['scenarios']}",
            f"{other['tools_ok']}/{other['scenarios']}",
        ),
        ("kaynaksız cevap", base["unverified"], other["unverified"]),
        ("uyarılan tur", base["nudged"], other["nudged"]),
        ("adım ortanca", base["steps_median"], other["steps_median"]),
        ("adım toplam", base["steps_total"], other["steps_total"]),
        ("gecikme ortanca", f"{base['latency_median_s']} s", f"{other['latency_median_s']} s"),
        ("gecikme toplam", f"{base['latency_total_s']} s", f"{other['latency_total_s']} s"),
    ]
    for label, left, right in rows_out:
        print(f"  {label:20} {left!s:>12} {right!s:>12}")

    only_other = [
        key
        for key in shared
        if stored[args.other][key]["passed"] and not stored[args.base][key]["passed"]
    ]
    only_base = [
        key
        for key in shared
        if stored[args.base][key]["passed"] and not stored[args.other][key]["passed"]
    ]
    if only_other:
        print(f"\n  yalnızca {args.other} geçti: {only_other}")
    if only_base:
        print(f"  yalnızca {args.base} geçti: {only_base}")

    REPORT_PATH.write_text(
        json.dumps(
            {
                "shared_scenarios": shared,
                "excluded_for_errors": broken,
                args.base: base,
                args.other: other,
                f"only_{args.other}_passed": only_other,
                f"only_{args.base}_passed": only_base,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n  rapor: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
