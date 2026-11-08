"""Show which external rows pass the calibration gates, and why the rest fail.

The code is live but inactive: with the source links still placeholders, no
row passes gate 1 and every line keeps a factor of 1.0. Filling in a link
turns its line on without any code change, which is what this prints.

    uv run python scripts/show_calibration_gates.py
"""

from __future__ import annotations

import sys

from istanbul_agent.store.calibration import (
    MAX_FACTOR,
    MIN_DEVIATION_PCT,
    MIN_FACTOR,
    MIN_MINUTES,
    MIN_STOPS,
    evaluate_row,
    has_real_source,
    load_calibrations,
    read_external,
)
from istanbul_agent.store.rail_queries import get_rail_travel_time, load_network


def main() -> int:
    rows = read_external()
    if not rows:
        print("datasets/external_travel_times.csv boş.")
        return 1

    network = load_network()
    print("Kapılar:")
    print("  1. kaynak linki gerçek bir URL olmalı")
    print(f"  2. sapma > %{MIN_DEVIATION_PCT:.0f}")
    print(f"  3. yolculuk ≥ {MIN_STOPS} durak ve ≥ {MIN_MINUTES:.0f} dk")
    print("  4. bizim sonucumuz 'gtfs' olmalı")
    print(f"  (oran {MIN_FACTOR}-{MAX_FACTOR} arasında)\n")

    print(
        f"{'Hat':6} {'Yolculuk':32} {'link':>6} {'bizim':>7} {'dış':>6} "
        f"{'sapma':>7} {'oran':>6}  karar"
    )
    for row in rows:
        result = get_rail_travel_time(row["from"], row["to"], network=network)
        ours = result.seconds / 60 if result.seconds is not None else None
        calibration, reason = evaluate_row(row, ours, result.stop_count, result.method)
        external_minutes = float(row["minutes"])

        deviation = f"{(external_minutes - ours) / ours * 100:+6.1f}%" if ours else "     —"
        factor = f"{external_minutes / ours:6.3f}" if ours else "     —"
        print(
            f"{row['line']:6} {row['from'][:14]:15}→{row['to'][:14]:16} "
            f"{'EVET' if has_real_source(row['source_url']) else 'yok':>6} "
            f"{ours if ours else 0:>7.2f} {external_minutes:>6.1f} {deviation} {factor}  "
            f"{'✅ KABUL' if calibration else 'red — ' + reason}"
        )

    active = load_calibrations()
    print(f"\nAktif kalibrasyon: {len(active)} hat")
    if active:
        for line, entry in sorted(active.items()):
            print(
                f"  {line:6} ×{entry.factor:.3f}  "
                f"({entry.ours_minutes:.1f} → {entry.external_minutes:.1f} dk)  "
                f"{entry.source_url}"
            )
    else:
        print("  Hiçbiri — kaynak linkleri doldurulmayı bekliyor.")
        print("  datasets/external_travel_times.csv'de source_url alanını doldurmak yeterli;")
        print("  kod değişikliği gerekmiyor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
