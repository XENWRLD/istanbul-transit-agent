"""Is the GTFS feed usable for departures and travel times at all?

The coverage analysis answers "which stations exist". This answers the two
questions the tools actually depend on:

- `get_next_departures` needs service days (calendar) and headways
  (frequencies) per metro route. Several routes carry only 2 trips, which
  only works if frequencies.csv fills in the rest.
- `get_rail_travel_time` needs arrival/departure times on consecutive stops.
  A feed where every stop on a trip shares one timestamp cannot give
  station-to-station durations.

    uv run python scripts/analyze_metro_schedule.py
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
import time
from collections import defaultdict
from typing import Any

from istanbul_agent.config import PROJECT_ROOT

GTFS_DIR = PROJECT_ROOT / "data" / "gtfs" / "public_transport"
OUT = PROJECT_ROOT / "data" / "metro_schedule_quality.json"
GTFS_ENCODING = "cp1254"


def read_csv(name: str) -> list[dict[str, str]]:
    with (GTFS_DIR / name).open(encoding=GTFS_ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def to_seconds(value: str | None) -> int | None:
    """GTFS times may exceed 24h (`25:10:00`), so parse by hand."""
    if not value or value.count(":") < 2:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in value.split(":")[:3])
    except ValueError:
        return None
    return hours * 3600 + minutes * 60 + seconds


def main() -> int:
    agencies = {row["agency_id"]: row["agency_name"] for row in read_csv("agency.csv")}
    metro_ids = [aid for aid, name in agencies.items() if "Metro" in name]
    routes = [r for r in read_csv("routes.csv") if r["agency_id"] in metro_ids]
    trips = read_csv("trips.csv")
    calendar = {row["service_id"]: row for row in read_csv("calendar.csv")}
    frequencies = read_csv("frequencies.csv")
    stop_times = read_csv("stop_times.csv")

    route_ids = {r["route_id"] for r in routes}
    metro_trips = [t for t in trips if t["route_id"] in route_ids]
    metro_trip_ids = {t["trip_id"] for t in metro_trips}

    freq_by_trip: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in frequencies:
        freq_by_trip[row["trip_id"]].append(row)

    times_by_trip: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in stop_times:
        if row["trip_id"] in metro_trip_ids:
            times_by_trip[row["trip_id"]].append(row)

    trips_by_route: dict[str, list[dict[str, str]]] = defaultdict(list)
    for trip in metro_trips:
        trips_by_route[trip["route_id"]].append(trip)

    today = time.strftime("%Y%m%d")
    report: list[dict[str, Any]] = []

    print("=== Hat bazında tarife kalitesi ===")
    header = (
        f"{'Hat':7} {'sefer':>6} {'freq':>5} {'servis':>7} {'süre?':>6} "
        f"{'aralık(dk)':>12} {'ilk':>6} {'son':>6}  calendar"
    )
    print(header)

    for route in sorted(routes, key=lambda r: r["route_short_name"]):
        route_trips = trips_by_route.get(route["route_id"], [])
        trip_ids = [t["trip_id"] for t in route_trips]

        with_freq = [tid for tid in trip_ids if tid in freq_by_trip]
        headways = [
            int(row["headway_secs"])
            for tid in with_freq
            for row in freq_by_trip[tid]
            if row["headway_secs"].isdigit()
        ]

        # Do consecutive stops carry distinct times? If not, no durations.
        usable_durations = 0
        checked = 0
        for tid in trip_ids[:40]:
            sequence = sorted(
                times_by_trip.get(tid, []), key=lambda r: int(r["stop_sequence"] or 0)
            )
            if len(sequence) < 2:
                continue
            checked += 1
            stamps = [to_seconds(r["arrival_time"]) for r in sequence]
            if len({s for s in stamps if s is not None}) > 1:
                usable_durations += 1

        starts = [to_seconds(row["start_time"]) for tid in with_freq for row in freq_by_trip[tid]]
        ends = [to_seconds(row["end_time"]) for tid in with_freq for row in freq_by_trip[tid]]
        starts = [s for s in starts if s is not None]
        ends = [e for e in ends if e is not None]

        service_ids = {t["service_id"] for t in route_trips}
        active = []
        for sid in service_ids:
            row = calendar.get(sid)
            if row and row["start_date"] <= today <= row["end_date"]:
                active.append(sid)
        windows = sorted(
            {
                f"{calendar[s]['start_date']}-{calendar[s]['end_date']}"
                for s in service_ids
                if s in calendar
            }
        )

        def clock(seconds: int | None) -> str:
            return (
                f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}" if seconds is not None else "—"
            )

        headway_label = f"{min(headways) // 60}-{max(headways) // 60}" if headways else "—"
        duration_label = f"{usable_durations}/{checked}" if checked else "—"

        print(
            f"{route['route_short_name']:7} {len(route_trips):6} {len(with_freq):5} "
            f"{len(service_ids):7} {duration_label:>6} {headway_label:>12} "
            f"{clock(min(starts) if starts else None):>6} {clock(max(ends) if ends else None):>6}  "
            f"{','.join(windows)[:30]} {'(aktif ' + str(len(active)) + ')' if active else '(HİÇBİRİ AKTİF DEĞİL)'}"
        )

        report.append(
            {
                "line": route["route_short_name"],
                "route_id": route["route_id"],
                "trip_count": len(route_trips),
                "trips_with_frequencies": len(with_freq),
                "headway_secs_min": min(headways) if headways else None,
                "headway_secs_max": max(headways) if headways else None,
                "headway_secs_median": int(statistics.median(headways)) if headways else None,
                "service_window_start": min(starts) if starts else None,
                "service_window_end": max(ends) if ends else None,
                "trips_with_usable_durations": usable_durations,
                "trips_checked_for_durations": checked,
                "service_ids": sorted(service_ids),
                "calendar_windows": windows,
                "active_service_ids_today": active,
            }
        )

    total_freq = sum(r["trips_with_frequencies"] for r in report)
    total_trips = sum(r["trip_count"] for r in report)
    usable = sum(r["trips_with_usable_durations"] for r in report)
    checked_total = sum(r["trips_checked_for_durations"] for r in report)
    no_active = [r["line"] for r in report if not r["active_service_ids_today"]]

    print(f"\n  frequencies kaydı olan sefer: {total_freq}/{total_trips}")
    print(f"  ardışık durak süresi kullanılabilir: {usable}/{checked_total} (örneklenen sefer)")
    print(f"  bugün ({today}) aktif servisi olmayan hat: {len(no_active)}/{len(report)}")
    if no_active:
        print(f"    {', '.join(no_active)}")

    OUT.write_text(
        json.dumps(
            {"analyzed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "lines": report},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n-> {OUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
