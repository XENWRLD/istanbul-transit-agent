"""Measure how wrong the travel-time estimate is, on segments we can check.

For stations the 2020 feed lacks, `get_rail_travel_time` estimates from a
distance and a mode speed. The only honest way to know what that estimate is
worth is to apply it where the feed *does* give the answer, and compare.

Two held-out tests:

1. **Per segment.** For every adjacent pair with a GTFS duration, estimate
   that hop as if the duration were unknown, and compare. This isolates the
   distance-and-speed model.
2. **Per journey.** For random station pairs several stops apart on one line,
   compare the GTFS sum against the fully estimated sum. Errors on single
   hops can cancel or accumulate, and the journey figure is what a user
   actually sees.

Reports mean and maximum error for both, split by mode and by distance
source (dataset 312 track length vs. straight line). The model being tested
is the fitted `overhead + distance / cruise` from scripts/fit_travel_model.py,
applied through `store.rail_travel.model_for`.

    uv run python scripts/validate_travel_estimates.py
    uv run python scripts/validate_travel_estimates.py --journeys 400
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from itertools import pairwise
from typing import Any

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.store.geo import haversine_m, parse_coordinate
from istanbul_agent.store.gtfs import GtfsFeed, to_seconds
from istanbul_agent.store.rail_reference import load_rail_reference
from istanbul_agent.store.rail_segments import build_segment_index
from istanbul_agent.store.rail_travel import DWELL_S, model_for

OUT = PROJECT_ROOT / "data" / "travel_estimate_validation.json"

ROUTE_TYPES = {
    "0": "tramvay",
    "1": "metro",
    "2": "tren",
    "5": "teleferik",
    "6": "teleferik",
    "7": "funikuler",
}
UNRELIABLE_LINES = {"T3"}
MIN_SEGMENT_S = 20
MAX_SEGMENT_S = 900


def describe(errors: list[float], unit: str) -> str:
    if not errors:
        return "veri yok"
    absolute = [abs(e) for e in errors]
    return (
        f"n={len(errors):>4}  ortalama|hata|={statistics.fmean(absolute):>6.1f}{unit}  "
        f"ortanca={statistics.median(absolute):>6.1f}{unit}  "
        f"max={max(absolute):>7.1f}{unit}  "
        f"yanlılık={statistics.fmean(errors):>+6.1f}{unit}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journeys", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20261108)
    args = parser.parse_args()
    random.seed(args.seed)

    feed = GtfsFeed.load("public_transport", agency_filter="Metro")
    reference = load_rail_reference()

    stations_by_line: dict[str, list[dict[str, str]]] = {}
    mode_by_line: dict[str, str] = {}
    for route in feed.routes:
        line = route["route_short_name"].strip()
        if line in UNRELIABLE_LINES:
            continue
        sequence = feed.longest_trip_sequence(route["route_id"])
        stations_by_line[line] = [
            feed.stops[row["stop_id"]] for row in sequence if row["stop_id"] in feed.stops
        ]
        mode_by_line[line] = ROUTE_TYPES.get(route["route_type"], "bilinmiyor")

    index = build_segment_index(reference, stations_by_line)

    # ---------- observed durations ----------
    observed: dict[tuple[str, str], list[int]] = defaultdict(list)
    for route in feed.routes:
        line = route["route_short_name"].strip()
        if line in UNRELIABLE_LINES:
            continue
        for trip in feed.trips_for_route(route["route_id"]):
            sequence = feed.stop_times.get(trip["trip_id"], [])
            for left, right in pairwise(sequence):
                depart = to_seconds(left["departure_time"]) or to_seconds(left["arrival_time"])
                arrive = to_seconds(right["arrival_time"]) or to_seconds(right["departure_time"])
                if depart is None or arrive is None:
                    continue
                delta = arrive - depart
                if MIN_SEGMENT_S <= delta <= MAX_SEGMENT_S:
                    key = (left["stop_id"], right["stop_id"])
                    observed[key].append(delta)

    duration_of = {key: int(statistics.median(values)) for key, values in observed.items()}

    def coordinates(stop: dict[str, str]) -> tuple[float, float] | None:
        lat = parse_coordinate(stop["stop_lat"])
        lon = parse_coordinate(stop["stop_lon"])
        return (lat, lon) if lat is not None and lon is not None else None

    def estimate_hop(
        line: str, left: dict[str, str], right: dict[str, str]
    ) -> tuple[int, str] | None:
        """Estimate one hop as if its GTFS duration were unknown."""
        track = index.track_m(left["stop_id"], right["stop_id"])
        source = "312"
        metres = track
        if metres is None:
            a, b = coordinates(left), coordinates(right)
            if a is None or b is None:
                return None
            metres = haversine_m(a[0], a[1], b[0], b[1])
            source = "koordinat"
        model = model_for(line, mode_by_line.get(line, ""))
        return model.seconds(metres), source

    # ---------- test 1: per segment ----------
    per_segment: list[dict[str, Any]] = []
    for line, stations in stations_by_line.items():
        for left, right in pairwise(stations):
            actual = duration_of.get((left["stop_id"], right["stop_id"])) or duration_of.get(
                (right["stop_id"], left["stop_id"])
            )
            if actual is None:
                continue
            estimated = estimate_hop(line, left, right)
            if estimated is None:
                continue
            seconds, source = estimated
            per_segment.append(
                {
                    "line": line,
                    "mode": mode_by_line[line],
                    "from": left["stop_name"],
                    "to": right["stop_name"],
                    "actual_s": actual,
                    "estimated_s": seconds,
                    "error_s": seconds - actual,
                    "distance_source": source,
                }
            )

    print("=" * 78)
    print("TEST 1 — Tek segment (ardışık istasyon çifti)")
    print("=" * 78)
    errors = [row["error_s"] for row in per_segment]
    print(f"\n  TÜMÜ            {describe([float(e) for e in errors], ' s')}")

    for mode in sorted({row["mode"] for row in per_segment}):
        subset = [float(r["error_s"]) for r in per_segment if r["mode"] == mode]
        print(f"  {mode:15} {describe(subset, ' s')}")

    print()
    for source in sorted({row["distance_source"] for row in per_segment}):
        subset = [float(r["error_s"]) for r in per_segment if r["distance_source"] == source]
        print(f"  mesafe={source:11} {describe(subset, ' s')}")

    worst = sorted(per_segment, key=lambda r: -abs(r["error_s"]))[:10]
    print("\n  En kötü 10 segment:")
    print(f"    {'Hat':5} {'A':22} {'B':22} {'gerçek':>7} {'tahmin':>7} {'hata':>7} kaynak")
    for row in worst:
        print(
            f"    {row['line']:5} {row['from'][:21]:22} {row['to'][:21]:22} "
            f"{row['actual_s']:>7} {row['estimated_s']:>7} {row['error_s']:>+7} "
            f"{row['distance_source']}"
        )

    # ---------- test 2: whole journeys ----------
    print("\n" + "=" * 78)
    print("TEST 2 — Tam yolculuk (aynı hat, birkaç durak)")
    print("=" * 78)

    journeys: list[dict[str, Any]] = []
    candidates = [
        (line, stations) for line, stations in stations_by_line.items() if len(stations) >= 4
    ]
    attempts = 0
    while len(journeys) < args.journeys and attempts < args.journeys * 40 and candidates:
        attempts += 1
        line, stations = random.choice(candidates)
        start = random.randrange(0, len(stations) - 2)
        end = random.randrange(start + 2, len(stations))
        path = stations[start : end + 1]

        actual_total = 0
        estimated_total = 0
        sources: set[str] = set()
        complete = True
        for position, (left, right) in enumerate(pairwise(path)):
            actual = duration_of.get((left["stop_id"], right["stop_id"])) or duration_of.get(
                (right["stop_id"], left["stop_id"])
            )
            estimated = estimate_hop(line, left, right)
            if actual is None or estimated is None:
                complete = False
                break
            actual_total += actual
            estimated_total += estimated[0]
            sources.add(estimated[1])
            if position < len(path) - 2:
                actual_total += DWELL_S
                estimated_total += DWELL_S
        if not complete:
            continue

        journeys.append(
            {
                "line": line,
                "mode": mode_by_line[line],
                "from": path[0]["stop_name"],
                "to": path[-1]["stop_name"],
                "stops": len(path) - 1,
                "actual_s": actual_total,
                "estimated_s": estimated_total,
                "error_s": estimated_total - actual_total,
                "error_pct": (estimated_total - actual_total) / actual_total * 100,
                "distance_sources": sorted(sources),
            }
        )

    if journeys:
        print(f"\n  {len(journeys)} yolculuk ({attempts} deneme)")
        print(f"  saniye          {describe([float(j['error_s']) for j in journeys], ' s')}")
        print(f"  dakika          {describe([j['error_s'] / 60 for j in journeys], ' dk')}")
        print(f"  yüzde           {describe([j['error_pct'] for j in journeys], '%')}")

        for mode in sorted({j["mode"] for j in journeys}):
            subset = [j["error_pct"] for j in journeys if j["mode"] == mode]
            print(f"  {mode:15} {describe(subset, '%')}")

        print("\n  Durak sayısına göre:")
        buckets = [(2, 3), (4, 6), (7, 10), (11, 100)]
        for low, high in buckets:
            subset = [j["error_pct"] for j in journeys if low <= j["stops"] <= high]
            if subset:
                label = f"{low}-{high if high < 100 else '+'} durak"
                print(f"    {label:14} {describe(subset, '%')}")

        worst_journeys = sorted(journeys, key=lambda j: -abs(j["error_pct"]))[:8]
        print("\n  En kötü 8 yolculuk:")
        print(
            f"    {'Hat':5} {'A':20} {'B':20} {'durak':>5} {'gerçek':>8} {'tahmin':>8} {'hata':>8}"
        )
        for journey in worst_journeys:
            print(
                f"    {journey['line']:5} {journey['from'][:19]:20} {journey['to'][:19]:20} "
                f"{journey['stops']:>5} {journey['actual_s'] / 60:>7.1f}dk "
                f"{journey['estimated_s'] / 60:>7.1f}dk {journey['error_pct']:>+7.1f}%"
            )

    OUT.write_text(
        json.dumps(
            {
                "validated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "dwell_s": DWELL_S,
                "seed": args.seed,
                "per_segment": per_segment,
                "journeys": journeys,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n-> {OUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
