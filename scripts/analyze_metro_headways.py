"""Check the two assumptions behind get_next_departures before building on them.

The schedule-quality pass turned up two things worth verifying rather than
reporting straight away:

1. frequencies.csv covers only 66 of 10894 metro trips, and the rows it does
   cover mostly read 10-minute headways between 23:50 and 02:00 -- which
   looks like night service, not the all-day schedule the plan assumed.
2. If so, headways have to come from the explicit departure times in
   stop_times instead. That only works if a line's trips are spread across
   the day with sane gaps.

Prints the frequencies rows for a few lines, then the real headway profile
computed from stop_times, so the difference is visible side by side.

    uv run python scripts/analyze_metro_headways.py
"""

from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from itertools import pairwise

from istanbul_agent.config import PROJECT_ROOT

GTFS_DIR = PROJECT_ROOT / "data" / "gtfs" / "public_transport"
GTFS_ENCODING = "cp1254"
SAMPLE_LINES = ["M2", "M4", "T1", "M7", "M8"]

DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def read_csv(name: str) -> list[dict[str, str]]:
    with (GTFS_DIR / name).open(encoding=GTFS_ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def to_seconds(value: str | None) -> int | None:
    if not value or value.count(":") < 2:
        return None
    try:
        hours, minutes, seconds = (int(p) for p in value.split(":")[:3])
    except ValueError:
        return None
    return hours * 3600 + minutes * 60 + seconds


def clock(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def main() -> int:
    agencies = {row["agency_id"]: row["agency_name"] for row in read_csv("agency.csv")}
    metro_ids = [aid for aid, name in agencies.items() if "Metro" in name]
    routes = {r["route_id"]: r for r in read_csv("routes.csv") if r["agency_id"] in metro_ids}
    trips = [t for t in read_csv("trips.csv") if t["route_id"] in routes]
    calendar = {row["service_id"]: row for row in read_csv("calendar.csv")}
    frequencies = read_csv("frequencies.csv")
    stop_times = read_csv("stop_times.csv")

    trip_ids = {t["trip_id"] for t in trips}
    freq_by_trip: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in frequencies:
        if row["trip_id"] in trip_ids:
            freq_by_trip[row["trip_id"]].append(row)

    # First departure of each trip = that trip's start.
    first_departure: dict[str, int] = {}
    for row in stop_times:
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

    print("=== 1. frequencies.csv satırları (örnek hatlar) ===")
    for line in SAMPLE_LINES:
        rows = [
            row for trip in by_line.get(line, []) for row in freq_by_trip.get(trip["trip_id"], [])
        ]
        print(f"\n  {line}: {len(rows)} frequencies satırı")
        for row in rows[:8]:
            headway_min = int(row["headway_secs"]) // 60 if row["headway_secs"].isdigit() else "?"
            print(
                f"    trip {row['trip_id']:>9}  {row['start_time']}-{row['end_time']}  "
                f"her {headway_min} dk"
            )

    print("\n\n=== 2. stop_times'tan gerçek sefer aralığı ===")
    print(
        f"{'Hat':6} {'yön':4} {'gün':10} {'sefer':>6} {'ilk':>6} {'son':>6} "
        f"{'ortanca':>8} {'min':>5} {'max':>5}"
    )

    for line in SAMPLE_LINES:
        for direction in ("0", "1"):
            # Group by weekday pattern so weekday and weekend differ visibly.
            buckets: dict[str, list[int]] = defaultdict(list)
            for trip in by_line.get(line, []):
                if trip["direction_id"] != direction:
                    continue
                start = first_departure.get(trip["trip_id"])
                if start is None:
                    continue
                service = calendar.get(trip["service_id"])
                if service is None:
                    buckets["(servis yok)"].append(start)
                    continue
                days = [d[:3] for d in DAY_NAMES if service[d] == "1"]
                label = (
                    "hafta içi"
                    if days == ["mon", "tue", "wed", "thu", "fri"]
                    else "cumartesi"
                    if days == ["sat"]
                    else "pazar"
                    if days == ["sun"]
                    else "+".join(days) or "(gün yok)"
                )
                buckets[label].append(start)

            for label, starts in sorted(buckets.items(), key=lambda kv: -len(kv[1]))[:3]:
                starts.sort()
                gaps = [(b - a) // 60 for a, b in pairwise(starts) if 0 < b - a <= 3 * 3600]
                if not gaps:
                    continue
                print(
                    f"{line:6} {direction:4} {label:10} {len(starts):6} "
                    f"{clock(starts[0]):>6} {clock(starts[-1]):>6} "
                    f"{int(statistics.median(gaps)):>8} {min(gaps):>5} {max(gaps):>5}"
                )

    print("\n\n=== 3. calendar.csv gün desenleri ===")
    patterns: dict[str, list[str]] = defaultdict(list)
    for service_id, row in calendar.items():
        days = "".join("1" if row[d] == "1" else "0" for d in DAY_NAMES)
        patterns[days].append(f"{service_id}({row['start_date'][:4]}-{row['end_date'][:4]})")
    for days, service_ids in sorted(patterns.items()):
        active = [DAY_NAMES[i][:3] for i, ch in enumerate(days) if ch == "1"]
        print(f"  {days}  {','.join(active) or '(hiç)':32} {len(service_ids):3} servis")
    return 0


if __name__ == "__main__":
    sys.exit(main())
