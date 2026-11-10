"""Is dataset 312 internally misaligned, or just wrong?

118 of 183 matched segments give a route factor below 0.95 -- track shorter
than the straight line, which is impossible. Two hypotheses worth separating
before deciding whether to use this table at all:

A. The lengths are offset by a row from the station pairs they belong to (the
   usual spreadsheet slip). Testable: shifting the length column by +/-1
   within a line should collapse the impossible cases.
B. The lengths are simply unreliable per row.

If A holds, the table is recoverable. If B holds, the estimate has to fall
back on straight-line distance with a measured route factor, and 312 can only
be used to tell which stations exist.

    uv run python scripts/inspect_length_alignment.py
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from itertools import pairwise

import openpyxl

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output
from istanbul_agent.store.geo import haversine_m, parse_coordinate
from istanbul_agent.store.gtfs import GtfsFeed
from istanbul_agent.store.rail_reference import _clean, _split_pair
from istanbul_agent.store.station_names import normalize

REFERENCE = PROJECT_ROOT / "data" / "rail_reference" / "hat_uzunluklari.xlsx"


def main() -> int:
    # ---- 312 rows in file order, per line ----
    workbook = openpyxl.load_workbook(REFERENCE, data_only=True)
    sheet = workbook.worksheets[0]
    rows = [row for row in sheet.iter_rows(values_only=True) if any(row)][1:]

    per_line: dict[str, list[tuple[str, str, float | None]]] = defaultdict(list)
    for raw in rows:
        line, segment, length = (tuple(raw) + (None,) * 3)[:3]
        line_text = _clean(line)
        pair = _split_pair(_clean(segment)) if segment else None
        try:
            metres = float(_clean(length).replace(",", ".")) if length is not None else None
        except ValueError:
            metres = None
        if line_text and pair:
            per_line[line_text].append((pair[0], pair[1], metres))

    # ---- straight-line distances from GTFS coordinates ----
    feed = GtfsFeed.load("public_transport", agency_filter="Metro")
    coords: dict[str, tuple[float, float]] = {}
    for stop in feed.stops.values():
        lat = parse_coordinate(stop["stop_lat"])
        lon = parse_coordinate(stop["stop_lon"])
        if lat is not None and lon is not None:
            coords[normalize(stop["stop_name"])] = (lat, lon)

    def straight(left: str, right: str) -> float | None:
        a, b = coords.get(normalize(left)), coords.get(normalize(right))
        return haversine_m(a[0], a[1], b[0], b[1]) if a and b else None

    print(f"{len(rows)} satır, {len(per_line)} hat; GTFS'te {len(coords)} istasyon koordinatı\n")

    # ---- test each shift ----
    print("=== Uzunluk kolonunu kaydırma testi ===")
    print(f"{'kayma':>6} {'eşleşen':>8} {'kat<0.95':>9} {'kat ortanca':>12} {'kat ortalama':>13}")

    results: dict[int, dict[str, float]] = {}
    for shift in (-1, 0, 1):
        factors: list[float] = []
        for segments in per_line.values():
            for index, (left, right, _) in enumerate(segments):
                source = index + shift
                if not 0 <= source < len(segments):
                    continue
                metres = segments[source][2]
                if metres is None or metres <= 0:
                    continue
                chord = straight(left, right)
                if chord is None or chord < 50:
                    continue
                factors.append(metres / chord)
        if not factors:
            continue
        impossible = sum(1 for f in factors if f < 0.95)
        results[shift] = {
            "matched": len(factors),
            "impossible": impossible,
            "median": statistics.median(factors),
            "mean": statistics.fmean(factors),
        }
        print(
            f"{shift:>+6} {len(factors):>8} {impossible:>9} "
            f"{statistics.median(factors):>12.2f} {statistics.fmean(factors):>13.2f}"
        )

    best = min(results, key=lambda s: results[s]["impossible"] / results[s]["matched"])
    print(
        f"\n  en az imkansız oran: kayma {best:+d} "
        f"({results[best]['impossible']}/{results[best]['matched']} = "
        f"{results[best]['impossible'] / results[best]['matched']:.0%})"
    )

    if best == 0:
        print("  SONUÇ: kaydırma düzeltmiyor → hipotez B, satır bazında güvenilmez.")
    else:
        print(f"  SONUÇ: {best:+d} kayma belirgin düzeltme sağlıyor → hipotez A.")

    # ---- are 312's pairs even adjacent in GTFS? ----
    print("\n=== 312'nin çiftleri GTFS'te komşu mu? ===")
    adjacency: dict[str, set[frozenset[str]]] = defaultdict(set)
    for route in feed.routes:
        line = route["route_short_name"].strip()
        sequence = feed.longest_trip_sequence(route["route_id"])
        for left, right in pairwise(sequence):
            left_stop = feed.stops.get(left["stop_id"])
            right_stop = feed.stops.get(right["stop_id"])
            if left_stop and right_stop:
                adjacency[line].add(
                    frozenset(
                        {normalize(left_stop["stop_name"]), normalize(right_stop["stop_name"])}
                    )
                )

    all_adjacent: set[frozenset[str]] = set()
    for pairs in adjacency.values():
        all_adjacent |= pairs

    adjacent = known = total = 0
    examples: list[str] = []
    for line, segments in per_line.items():
        for left, right, _ in segments:
            total += 1
            pair = frozenset({normalize(left), normalize(right)})
            if normalize(left) in coords and normalize(right) in coords:
                known += 1
                if pair in all_adjacent:
                    adjacent += 1
                elif len(examples) < 10:
                    chord = straight(left, right)
                    examples.append(
                        f"{line:5} {left[:22]:24} {right[:22]:24} düz={chord:.0f}m"
                        if chord
                        else f"{line} {left} {right}"
                    )
    print(f"  312 satırı: {total}, iki istasyonu da GTFS'te olan: {known}")
    print(f"  GTFS'te komşu olan: {adjacent}/{known}")
    if examples:
        print("\n  komşu OLMAYAN örnekler:")
        for example in examples:
            print(f"    {example}")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
