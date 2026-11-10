"""Compare dataset 312's segment order against the GTFS station order.

With the stop-table collision fixed, the question narrows: does 312 list real
adjacent pairs with wrong lengths, or does it list pairs that are not adjacent
at all? Prints both orders side by side for a few lines and measures every
312 row against the straight line between the same two metro stations.

    uv run python scripts/inspect_312_order.py
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict

import openpyxl

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output
from istanbul_agent.store.geo import haversine_m, parse_coordinate
from istanbul_agent.store.gtfs import GtfsFeed
from istanbul_agent.store.rail_reference import _clean, _line_family, _split_pair
from istanbul_agent.store.station_names import normalize

REFERENCE = PROJECT_ROOT / "data" / "rail_reference" / "hat_uzunluklari.xlsx"
SHOW_LINES = ("M3", "M1A", "M2", "T1")


def main() -> int:
    workbook = openpyxl.load_workbook(REFERENCE, data_only=True)
    rows = [r for r in workbook.worksheets[0].iter_rows(values_only=True) if any(r)][1:]

    per_line: dict[str, list[tuple[str, str, float | None]]] = defaultdict(list)
    for raw in rows:
        line, segment, length = (tuple(raw) + (None,) * 3)[:3]
        pair = _split_pair(_clean(segment)) if segment else None
        try:
            metres = float(_clean(length).replace(",", ".")) if length is not None else None
        except ValueError:
            metres = None
        if _clean(line) and pair:
            per_line[_clean(line)].append((pair[0], pair[1], metres))

    feed = GtfsFeed.load("public_transport", agency_filter="Metro")
    print(f"GTFS metro durak sayısı (filtrelenmiş): {len(feed.stops)}\n")

    coords: dict[str, tuple[float, float]] = {}
    for stop in feed.stops.values():
        lat = parse_coordinate(stop["stop_lat"])
        lon = parse_coordinate(stop["stop_lon"])
        if lat is not None and lon is not None:
            coords.setdefault(normalize(stop["stop_name"]), (lat, lon))

    def straight(left: str, right: str) -> float | None:
        a, b = coords.get(normalize(left)), coords.get(normalize(right))
        return haversine_m(a[0], a[1], b[0], b[1]) if a and b else None

    for line in SHOW_LINES:
        route = feed.route_by_short_name(line)
        if route is None:
            continue
        sequence = feed.longest_trip_sequence(route["route_id"])
        gtfs_order = [
            feed.stops[r["stop_id"]]["stop_name"] for r in sequence if r["stop_id"] in feed.stops
        ]

        family_rows = [
            (left, right, metres)
            for key, segments in per_line.items()
            if _line_family(key) == _line_family(line)
            for left, right, metres in segments
        ]

        print(f"\n{'=' * 88}")
        print(
            f"{line}:  GTFS {len(gtfs_order)} istasyon  ·  312 ({_line_family(line)}) {len(family_rows)} segment"
        )
        print(f"{'=' * 88}")
        print(
            f"{'#':>3}  {'GTFS sırası':28} | {'312 segmenti':40} {'uzunluk':>9} {'düz':>8} {'kat':>5}"
        )
        for index in range(max(len(gtfs_order), len(family_rows))):
            gtfs_name = gtfs_order[index] if index < len(gtfs_order) else ""
            if index < len(family_rows):
                left, right, metres = family_rows[index]
                chord = straight(left, right)
                factor = f"{metres / chord:.2f}" if metres and chord and chord > 50 else "—"
                segment_label = f"{left[:18]} → {right[:18]}"
                length_label = f"{metres:,.0f}" if metres else "—"
                chord_label = f"{chord:,.0f}" if chord else "—"
            else:
                segment_label = length_label = chord_label = factor = ""
            print(
                f"{index + 1:>3}  {gtfs_name[:27]:28} | {segment_label:40} "
                f"{length_label:>9} {chord_label:>8} {factor:>5}"
            )

    # ---- overall: every 312 row against the chord ----
    print(f"\n\n{'=' * 60}\nTüm 312 satırları (metro durakları ile ölçülebilen)\n{'=' * 60}")
    factors: list[float] = []
    impossible: list[str] = []
    for line, segments in per_line.items():
        for left, right, metres in segments:
            chord = straight(left, right)
            if not metres or not chord or chord < 50:
                continue
            factor = metres / chord
            factors.append(factor)
            if factor < 0.95 and len(impossible) < 12:
                impossible.append(
                    f"{line:5} {left[:20]:22} {right[:20]:22} "
                    f"ray={metres:>8,.0f} düz={chord:>8,.0f} kat={factor:.2f}"
                )

    print(f"  ölçülebilen: {len(factors)}")
    if factors:
        print(
            f"  katsayı ortanca={statistics.median(factors):.2f}  "
            f"ortalama={statistics.fmean(factors):.2f}"
        )
        print(f"  imkansız (kat<0.95): {sum(1 for f in factors if f < 0.95)}")
        print(f"  makul (1.0-1.6): {sum(1 for f in factors if 1.0 <= f <= 1.6)}")
    for row in impossible:
        print(f"    {row}")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
