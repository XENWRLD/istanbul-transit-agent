"""Pick the route-factor bound from the distribution instead of guessing it.

The first pass rejected 85 segments for having track shorter than the chord,
with factors clustered at 0.75-0.94 while the accepted ones ran 0.95-1.39
around a median of 1.02. A continuous spread straddling 1.0 is what physics
predicts for a metro -- tunnels between adjacent stations are nearly straight
-- so most of those rejections are coordinate noise, not mismatches, and a
0.95 floor is throwing away good data.

Prints the unfiltered distribution so the bound separating noise from genuine
mismatch can be read off it.

    uv run python scripts/inspect_route_factor.py
"""

from __future__ import annotations

import statistics
import sys
from collections import Counter
from itertools import pairwise

from istanbul_agent.store.geo import haversine_m, parse_coordinate
from istanbul_agent.store.gtfs import GtfsFeed
from istanbul_agent.store.rail_reference import _line_family, load_rail_reference
from istanbul_agent.store.rail_segments import _match_station

UNRELIABLE_LINES = {"T3"}


def main() -> int:
    feed = GtfsFeed.load("public_transport", agency_filter="Metro")
    reference = load_rail_reference()

    stations_by_line: dict[str, list[dict[str, str]]] = {}
    for route in feed.routes:
        line = route["route_short_name"].strip()
        if line in UNRELIABLE_LINES:
            continue
        sequence = feed.longest_trip_sequence(route["route_id"])
        stations_by_line[line] = [
            feed.stops[row["stop_id"]] for row in sequence if row["stop_id"] in feed.stops
        ]

    branches: dict[str, list[str]] = {}
    for line in stations_by_line:
        branches.setdefault(_line_family(line), []).append(line)

    # Adjacency, so a pair can be labelled as neighbouring or not.
    adjacent: set[frozenset[str]] = set()
    for stations in stations_by_line.values():
        for left, right in pairwise(stations):
            adjacent.add(frozenset({left["stop_id"], right["stop_id"]}))

    rows: list[tuple[float, str, bool]] = []
    for segment in reference.segments:
        for line in branches.get(_line_family(segment.line), []):
            stations = stations_by_line[line]
            from_stop, _ = _match_station(segment.from_station, stations)
            to_stop, _ = _match_station(segment.to_station, stations)
            if from_stop is None or to_stop is None:
                continue
            if from_stop["stop_id"] == to_stop["stop_id"]:
                continue
            coords = (
                parse_coordinate(from_stop["stop_lat"]),
                parse_coordinate(from_stop["stop_lon"]),
                parse_coordinate(to_stop["stop_lat"]),
                parse_coordinate(to_stop["stop_lon"]),
            )
            if None in coords:
                continue
            chord = haversine_m(*coords)  # type: ignore[arg-type]
            if chord < 50:
                continue
            is_neighbour = frozenset({from_stop["stop_id"], to_stop["stop_id"]}) in adjacent
            rows.append(
                (
                    segment.metres / chord,
                    f"{line:5} {from_stop['stop_name'][:20]:22} {to_stop['stop_name'][:20]:22} "
                    f"ray={segment.metres:>8,.0f} düz={chord:>8,.0f}",
                    is_neighbour,
                )
            )

    factors = [factor for factor, _, _ in rows]
    print(f"Filtresiz eşleşme: {len(rows)}")
    print(
        f"  katsayı: ortanca={statistics.median(factors):.3f}  "
        f"ortalama={statistics.fmean(factors):.3f}  "
        f"min={min(factors):.3f}  max={max(factors):.3f}"
    )

    print("\n=== Katsayı dağılımı ===")
    buckets: Counter[str] = Counter()
    for factor in factors:
        if factor < 0.4:
            buckets["< 0.40"] += 1
        elif factor < 0.6:
            buckets["0.40-0.60"] += 1
        elif factor < 0.8:
            buckets["0.60-0.80"] += 1
        elif factor < 0.9:
            buckets["0.80-0.90"] += 1
        elif factor < 0.95:
            buckets["0.90-0.95"] += 1
        elif factor < 1.05:
            buckets["0.95-1.05"] += 1
        elif factor < 1.2:
            buckets["1.05-1.20"] += 1
        elif factor < 1.6:
            buckets["1.20-1.60"] += 1
        else:
            buckets["> 1.60"] += 1
    order = [
        "< 0.40",
        "0.40-0.60",
        "0.60-0.80",
        "0.80-0.90",
        "0.90-0.95",
        "0.95-1.05",
        "1.05-1.20",
        "1.20-1.60",
        "> 1.60",
    ]
    for label in order:
        count = buckets.get(label, 0)
        bar = "█" * round(count / max(1, max(buckets.values())) * 46)
        print(f"  {label:>10} {count:>4} {bar}")

    print("\n=== Komşuluk ayrımı ===")
    neighbours = [f for f, _, is_n in rows if is_n]
    others = [f for f, _, is_n in rows if not is_n]
    print(f"  GTFS'te komşu olan çiftler     : n={len(neighbours)}")
    if neighbours:
        print(
            f"    katsayı ortanca={statistics.median(neighbours):.3f}  "
            f"min={min(neighbours):.3f}  max={max(neighbours):.3f}"
        )
        below = [f for f in neighbours if f < 0.6]
        print(f"    0.6 altı: {len(below)}")
    print(f"  komşu OLMAYAN çiftler          : n={len(others)}")
    if others:
        print(
            f"    katsayı ortanca={statistics.median(others):.3f}  "
            f"min={min(others):.3f}  max={max(others):.3f}"
        )

    print("\n=== En düşük 12 katsayı (gerçek uyumsuzluk adayları) ===")
    for factor, label, is_neighbour in sorted(rows, key=lambda r: r[0])[:12]:
        print(f"  kat={factor:.3f}  {label}  {'komşu' if is_neighbour else 'KOMŞU DEĞİL'}")

    print("\n=== En yüksek 6 katsayı ===")
    for factor, label, is_neighbour in sorted(rows, key=lambda r: -r[0])[:6]:
        print(f"  kat={factor:.3f}  {label}  {'komşu' if is_neighbour else 'KOMŞU DEĞİL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
