"""Run the hand-checkable examples for `get_rail_travel_time`.

These are meant to be compared against metro.istanbul by a person, so the
set deliberately spans the cases that behave differently: journeys entirely
inside the feed, journeys into post-2020 extensions, an interchange, a line
whose feed data is refused, and a pair that cannot be answered at all.

    uv run python scripts/show_travel_examples.py
"""

from __future__ import annotations

import sys

from istanbul_agent.store.rail_queries import get_rail_travel_time, load_network

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
    ("Üsküdar", "Sultanbeyli", "M5 uzatması — son 3 istasyonun koordinatı yok"),
    ("Kirazlı", "Kayaşehir Merkez", "M3 uzatması, 2020 sonrası"),
    ("Ataköy", "Olimpiyat", "M9, büyük bölümü 2020 sonrası"),
    # --- interchange ---
    ("Kadıköy", "Taksim", "M4 + M2, Yenikapı/Şişhane aktarmalı"),
    ("Mecidiyeköy", "Yenikapı", "M2 + M1, aktarmalı"),
    # --- lines the feed cannot answer for ---
    ("Eminönü", "Alibeyköy Cep Otogarı", "T5 — hat GTFS'te hiç yok"),
    ("Moda", "Altıyol", "T3 — adlar koordinatlarla uyuşmuyor, reddedilmeli"),
    # --- not a station ---
    ("Kadıköy", "Paris", "bulunamayan istasyon"),
]


def main() -> int:
    network = load_network()
    print(f"Ağ: {len(network.stations)} istasyon kaydı\n")

    for index, (origin, destination, purpose) in enumerate(EXAMPLES, start=1):
        result = get_rail_travel_time(origin, destination, network=network)
        print("=" * 78)
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
            print(
                f"    SONUÇ: {result.minutes} dk ({result.seconds} s)  "
                f"method={result.method}  {' '.join(flags)}"
            )
            for leg in result.legs:
                print(
                    f"      {leg.line:5} {leg.from_station[:24]:26} → "
                    f"{leg.to_station[:24]:26} {leg.stop_count:>2} durak "
                    f"{leg.seconds // 60:>3} dk  [{leg.detail}]"
                )
            if result.transfer_station:
                print(
                    f"      aktarma: {result.transfer_station} "
                    f"(+{result.transfer_penalty_s // 60} dk)"
                )
            print(f"    kaynak: {result.source}  ·  data_as_of={result.data_as_of}")
        for note in result.notes:
            print(f"    not: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
