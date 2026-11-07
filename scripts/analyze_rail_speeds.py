"""Derive the speeds the travel-time estimate will use, from GTFS itself.

For stations the 2020 feed does not contain, `get_rail_travel_time` has to
estimate a duration, which needs a distance and a speed for that kind of
line. Both are measured here rather than assumed:

- every consecutive station pair in the feed yields an observed duration from
  stop_times (median across trips, so one delayed run cannot move it);
- joining those against dataset 312's track lengths -- matched line by line,
  see store/rail_segments.py -- gives an observed speed per segment;
- the same join measures the route factor, how much longer the track is than
  the straight line, which is what a coordinate-only estimate must apply.

    uv run python scripts/analyze_rail_speeds.py
"""

from __future__ import annotations

import json
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

OUT = PROJECT_ROOT / "data" / "rail_speed_profile.json"

ROUTE_TYPES = {
    "0": "tramvay",
    "1": "metro",
    "2": "tren",
    "5": "teleferik",
    "6": "teleferik",
    "7": "funikuler",
}

# Lines whose names do not sit on their own coordinates (decision 0003).
UNRELIABLE_LINES = {"T3"}

# A hop under 20 s or over 15 min is a feed artefact (duplicate timestamps,
# overnight wraps), not a normal station-to-station run.
MIN_SEGMENT_S = 20
MAX_SEGMENT_S = 900


def summarise(values: list[float], unit: str = "") -> str:
    if not values:
        return "veri yok"
    return (
        f"n={len(values):>4}  ortanca={statistics.median(values):>7.2f}{unit}  "
        f"ortalama={statistics.fmean(values):>7.2f}{unit}  "
        f"min={min(values):>6.2f}  max={max(values):>7.2f}"
    )


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

    index = build_segment_index(reference, stations_by_line)
    print(
        f"GTFS: {len(feed.routes)} hat, {len(feed.stops)} durak   ·   "
        f"312: {len(reference.segments)} segment"
    )
    print(
        f"Eşleştirme: {len(index.matched)} tutturuldu, "
        f"{len(index.rejected)} reddedildi, {len(index.unmatched_rows)} bulunamadı"
    )

    by_how: dict[str, int] = defaultdict(int)
    for segment in index.matched:
        by_how[segment.matched_by] += 1
    print("  eşleşme yöntemi: " + ", ".join(f"{k}={v}" for k, v in sorted(by_how.items())))

    if index.rejected:
        print(f"\n  Reddedilenler ({len(index.rejected)}):")
        for row in index.rejected[:10]:
            print(
                f"    {row['line']:5} {str(row['from'])[:20]:22} {str(row['to'])[:20]:22} "
                f"ray={row['track_m']:>8,.0f} düz={row['straight_m']:>8,.0f} "
                f"kat={row['route_factor']:.2f}  {row['reason']}"
            )
        if len(index.rejected) > 10:
            print(f"    ... (+{len(index.rejected) - 10})")

    # ---------- observed durations per consecutive pair ----------
    durations: dict[tuple[str, str, str], list[int]] = defaultdict(list)
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
                    durations[(line, left["stop_id"], right["stop_id"])].append(delta)

    segments: list[dict[str, Any]] = []
    for (line, from_id, to_id), observed in durations.items():
        from_stop, to_stop = feed.stops.get(from_id), feed.stops.get(to_id)
        if from_stop is None or to_stop is None:
            continue
        coords = (
            parse_coordinate(from_stop["stop_lat"]),
            parse_coordinate(from_stop["stop_lon"]),
            parse_coordinate(to_stop["stop_lat"]),
            parse_coordinate(to_stop["stop_lon"]),
        )
        straight = haversine_m(*coords) if None not in coords else None  # type: ignore[arg-type]
        route = feed.route_by_short_name(line)
        segments.append(
            {
                "line": line,
                "mode": ROUTE_TYPES.get(route["route_type"] if route else "", "bilinmiyor"),
                "from_stop_id": from_id,
                "to_stop_id": to_id,
                "from_station": from_stop["stop_name"],
                "to_station": to_stop["stop_name"],
                "duration_s": int(statistics.median(observed)),
                "observations": len(observed),
                "straight_m": round(straight, 1) if straight is not None else None,
                "track_m": index.track_m(from_id, to_id),
            }
        )

    print(f"\n{len(segments)} ardışık istasyon çifti ölçüldü")
    print(f"  ray uzunluğu eşleşen: {sum(1 for s in segments if s['track_m'])}")

    # ---------- per-mode profile ----------
    print("\n=== Mod bazında profil ===")
    profile: dict[str, dict[str, Any]] = {}
    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        by_mode[segment["mode"]].append(segment)

    for mode in sorted(by_mode):
        rows = by_mode[mode]
        with_track = [s for s in rows if s["track_m"]]
        track_speeds = [s["track_m"] / s["duration_s"] for s in with_track]
        factors = [
            s["track_m"] / s["straight_m"]
            for s in with_track
            if s["straight_m"] and s["straight_m"] > 50
        ]
        straight_speeds = [
            s["straight_m"] / s["duration_s"]
            for s in rows
            if s["straight_m"] and s["straight_m"] > 50
        ]

        print(f"\n  {mode}  ({len(with_track)} ray uzunluklu / {len(rows)} segment)")
        if track_speeds:
            print(f"    ray hızı        {summarise(track_speeds, ' m/s')}")
            print(f"                    ortanca {statistics.median(track_speeds) * 3.6:.1f} km/h")
        if factors:
            print(f"    güzergâh kat.   {summarise(factors)}")
        if straight_speeds:
            print(f"    düz çizgi hızı  {summarise(straight_speeds, ' m/s')}")
            print(
                f"                    ortanca {statistics.median(straight_speeds) * 3.6:.1f} km/h"
            )

        profile[mode] = {
            "segments_total": len(rows),
            "segments_with_track": len(with_track),
            "track_speed_ms_median": round(statistics.median(track_speeds), 3)
            if track_speeds
            else None,
            "route_factor_median": round(statistics.median(factors), 3) if factors else None,
            "straight_speed_ms_median": (
                round(statistics.median(straight_speeds), 3) if straight_speeds else None
            ),
            "straight_speed_ms_p25": (
                round(statistics.quantiles(straight_speeds, n=4)[0], 3)
                if len(straight_speeds) >= 4
                else None
            ),
            "straight_speed_ms_p75": (
                round(statistics.quantiles(straight_speeds, n=4)[2], 3)
                if len(straight_speeds) >= 4
                else None
            ),
        }

    dwells = [
        depart - arrive
        for trip_rows in feed.stop_times.values()
        for row in trip_rows
        if (arrive := to_seconds(row["arrival_time"])) is not None
        and (depart := to_seconds(row["departure_time"])) is not None
        and 0 <= depart - arrive <= 300
    ]
    print(f"\n=== İstasyonda bekleme ===\n  {summarise([float(d) for d in dwells], ' s')}")

    OUT.write_text(
        json.dumps(
            {
                "analyzed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "mode_profile": profile,
                "dwell_s_median": int(statistics.median(dwells)) if dwells else None,
                "segment_match": {
                    "matched": len(index.matched),
                    "rejected": index.rejected,
                    "unmatched": index.unmatched_rows,
                },
                "segments": segments,
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
