"""Decide, per metro line, which GTFS table carries its real timetable.

The feed splits the schedule between two tables inconsistently, so neither
alone answers "when is the next train". For the busy lines, stop_times holds
the all-day timetable and frequencies.csv only describes night service; for
the lines that were new in 2020 (M7, M8, M9) it is the other way round --
they carry 2 trips and their daytime headways live in frequencies.

This writes the per-line choice to datasets/metro_schedule_source.csv, which
`get_next_departures` reads. The file is a decision output, so it is
committed; it is also deterministic, so re-running this script on the same
feed reproduces it exactly.

    uv run python scripts/decide_schedule_source.py
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from typing import Any

from istanbul_agent.config import PROJECT_ROOT

GTFS_DIR = PROJECT_ROOT / "data" / "gtfs" / "public_transport"
OUT = PROJECT_ROOT / "datasets" / "metro_schedule_source.csv"
GTFS_ENCODING = "cp1254"

DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# A line needs at least this many trips starting in daylight before we treat
# stop_times as its real timetable rather than a handful of sample runs.
DAYTIME_TRIP_THRESHOLD = 50

# "Daylight" window used to tell a real timetable from night-only service.
DAY_START_S = 5 * 3600
DAY_END_S = 22 * 3600

# Lines whose names do not sit on their own coordinates (decision 0003).
UNRELIABLE_LINES = {"T3"}


def read_csv(name: str) -> list[dict[str, str]]:
    with (GTFS_DIR / name).open(encoding=GTFS_ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def to_seconds(value: str | None) -> int | None:
    if not value or value.count(":") < 2:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in value.split(":")[:3])
    except ValueError:
        return None
    return hours * 3600 + minutes * 60 + seconds


def main() -> int:
    agencies = {r["agency_id"]: r["agency_name"] for r in read_csv("agency.csv")}
    metro_ids = [aid for aid, name in agencies.items() if "Metro" in name]
    routes = {r["route_id"]: r for r in read_csv("routes.csv") if r["agency_id"] in metro_ids}
    trips = [t for t in read_csv("trips.csv") if t["route_id"] in routes]
    calendar = {r["service_id"]: r for r in read_csv("calendar.csv")}

    trip_ids = {t["trip_id"] for t in trips}
    freq_by_trip: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv("frequencies.csv"):
        if row["trip_id"] in trip_ids:
            freq_by_trip[row["trip_id"]].append(row)

    first_departure: dict[str, int] = {}
    for row in read_csv("stop_times.csv"):
        tid = row["trip_id"]
        if tid not in trip_ids:
            continue
        seconds = to_seconds(row["departure_time"]) or to_seconds(row["arrival_time"])
        if seconds is None:
            continue
        if tid not in first_departure or seconds < first_departure[tid]:
            first_departure[tid] = seconds

    by_line: dict[str, list[dict[str, str]]] = defaultdict(list)
    for trip in trips:
        by_line[routes[trip["route_id"]]["route_short_name"]].append(trip)

    rows: list[dict[str, Any]] = []
    for line in sorted(by_line):
        line_trips = by_line[line]
        daytime = [
            t
            for t in line_trips
            if (start := first_departure.get(t["trip_id"])) is not None
            and DAY_START_S <= start <= DAY_END_S
        ]
        with_freq = [t for t in line_trips if t["trip_id"] in freq_by_trip]
        freq_covers_daytime = any(
            (start := to_seconds(row["start_time"])) is not None and start < 12 * 3600
            for t in line_trips
            for row in freq_by_trip.get(t["trip_id"], [])
        )
        dayless = sum(
            1
            for t in line_trips
            if t["service_id"] in calendar
            and not any(calendar[t["service_id"]][day] == "1" for day in DAY_NAMES)
        )

        if line in UNRELIABLE_LINES:
            source, note = "yok", "hat güvenilmez: adlar koordinatlarla uyuşmuyor (karar 0003)"
        elif len(daytime) >= DAYTIME_TRIP_THRESHOLD:
            source, note = "stop_times", f"{len(daytime)} gündüz seferi açık saatlerle kayıtlı"
        elif freq_covers_daytime:
            source, note = "frequencies", "gündüz aralığı yalnız frequencies.csv'de"
        elif with_freq:
            source, note = "frequencies", "yalnız gece/kısmi servis kaydı var"
        else:
            source, note = "yok", "tarife verisi yok"

        rows.append(
            {
                "line": line,
                "source": source,
                "trips": len(line_trips),
                "daytime_trips": len(daytime),
                "trips_with_frequencies": len(with_freq),
                "frequencies_covers_daytime": "evet" if freq_covers_daytime else "hayır",
                "trips_on_dayless_service": dayless,
                "note": note,
            }
        )

    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"{'Hat':6} {'kaynak':12} {'sefer':>6} {'gündüz':>7} {'freq':>5} {'günsüz':>7}  not")
    for row in rows:
        print(
            f"{row['line']:6} {row['source']:12} {row['trips']:6} {row['daytime_trips']:7} "
            f"{row['trips_with_frequencies']:5} {row['trips_on_dayless_service']:7}  {row['note']}"
        )

    tally: dict[str, int] = defaultdict(int)
    for row in rows:
        tally[row["source"]] += 1
    print("\n  " + "   ".join(f"{source}: {count}" for source, count in sorted(tally.items())))
    print(f"\n-> {OUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
