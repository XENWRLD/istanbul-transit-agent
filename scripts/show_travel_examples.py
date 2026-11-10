"""Run the hand-checkable examples for `get_rail_travel_time`.

These are meant to be compared against metro.istanbul by a person, so the set
deliberately spans the cases that behave differently: journeys entirely inside
the feed, journeys into post-2020 extensions, an interchange over Marmaray, a
ring line, and a pair that cannot be answered at all. Where
datasets/external_travel_times.csv has an independently checked figure, it is
shown alongside.

    uv run python scripts/show_travel_examples.py
"""

from __future__ import annotations

import csv
import sys

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output
from istanbul_agent.store.rail_queries import get_rail_travel_time, load_network
from istanbul_agent.store.station_names import normalize

EXTERNAL = PROJECT_ROOT / "datasets" / "external_travel_times.csv"

# (origin, destination, what this case is testing)
EXAMPLES: list[tuple[str, str, str]] = [
    # --- fully inside the feed: these should read `gtfs` and be exact ---
    ("Yenikapı", "Hacıosman", "M2 uçtan uca, tamamı GTFS'te"),
    ("Kadıköy", "Tavşantepe", "M4 uçtan uca, tamamı GTFS'te"),
    ("Üsküdar", "Çekmeköy", "M5, GTFS'in bittiği yere kadar"),
    ("Kabataş", "Bağcılar", "T1 uçtan uca tramvay"),
    ("Taksim", "Kabataş", "F1 füniküler, iki istasyon"),
    # --- into post-2020 extensions: these must read `estimated` ---
    ("Kadıköy", "Sabiha Gökçen Havalimanı", "M4 uzatması, 2020 sonrası"),
    ("Üsküdar", "Sultanbeyli", "M5 uzatması — koordinatlar GeoJSON'dan tamamlandı"),
    ("Kirazlı", "Kayaşehir Merkez", "M3 uzatması, 2020 sonrası"),
    ("Ataköy", "Olimpiyat", "M9, büyük bölümü 2020 sonrası"),
    # --- interchange, now that Marmaray is in the network ---
    ("Kadıköy", "Taksim", "M4 + Marmaray + M2 — projenin ana senaryosu"),
    ("Mecidiyeköy", "Yenikapı", "M2 + M1, adları farklı aktarma noktası"),
    # --- lines the feed cannot answer for ---
    ("Eminönü", "Alibeyköy Cep Otogarı", "T5 — hat GTFS'te hiç yok"),
    ("Moda", "Altıyol", "T3 — ring hat, koordinatları güvenilmez"),
    # --- not a station ---
    ("Kadıköy", "Paris", "bulunamayan istasyon"),
]


def load_external() -> dict[tuple[str, str], dict[str, str]]:
    if not EXTERNAL.exists():
        return {}
    with EXTERNAL.open(encoding="utf-8") as handle:
        return {
            (normalize(row["from"]), normalize(row["to"])): row for row in csv.DictReader(handle)
        }


def main() -> int:
    network = load_network()
    external = load_external()
    print(f"Ağ: {len(network.stations)} istasyon kaydı, {len(network.ring_lines)} ring hat\n")

    for index, (origin, destination, purpose) in enumerate(EXAMPLES, start=1):
        result = get_rail_travel_time(origin, destination, network=network)
        reference = external.get((normalize(origin), normalize(destination)))

        print("=" * 80)
        print(f"{index:>2}. {origin} → {destination}")
        print(f"    amaç: {purpose}")

        if result.seconds is None:
            print(f"    SONUÇ: cevaplanamadı  (method={result.method})")
        else:
            flags = []
            if result.estimated:
                flags.append("TAHMİN")
            if result.is_stale:
                flags.append("is_stale")
            margin = f"  {result.error_margin.phrase()}" if result.error_margin else ""
            print(
                f"    SONUÇ: {result.minutes} dk ({result.seconds} s)  "
                f"method={result.method}  {' '.join(flags)}{margin}"
            )
            for leg in result.legs:
                print(
                    f"      {leg.line:10} {leg.from_station[:22]:24} → "
                    f"{leg.to_station[:22]:24} {leg.stop_count:>2} durak "
                    f"{leg.seconds // 60:>3} dk  [{leg.detail}]"
                )
            if result.transfer_station:
                print(
                    f"      aktarma: {result.transfer_station} "
                    f"(+{result.transfer_penalty_s // 60} dk)"
                )
            print(f"    kaynak: {result.source}  ·  data_as_of={result.data_as_of}")

            if reference:
                external_minutes = float(reference["minutes"])
                delta = result.minutes - external_minutes
                unverified = "DOĞRULANMADI" in (reference.get("notes") or "")
                print(
                    f"    DIŞ KAYNAK: {external_minutes:g} dk  "
                    f"fark {delta:+.1f} dk ({delta / external_minutes:+.1%})"
                    + ("  [kaynak doğrulanmadı]" if unverified else "")
                )

        for warning in result.warnings:
            print(f"    UYARI: {warning}")
        for note in result.notes:
            print(f"    not: {note}")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
