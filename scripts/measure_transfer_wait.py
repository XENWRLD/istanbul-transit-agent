"""Ground the transfer penalty in the headways we can actually measure.

An interchange costs a passenger two things: walking between platforms, and
waiting for the connecting train. The second is measurable -- for a line
running every H minutes, a passenger arriving at random waits H/2 on average
-- so it is measured here rather than assumed, and only the walking half is
left as a judgement call.

Headways come from whichever table carries a line's real timetable, per
decision 0002: stop_times for the 12 lines that have one, frequencies.csv for
the 7 that do not.

    uv run python scripts/measure_transfer_wait.py
"""

from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict
from itertools import pairwise

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.store.gtfs import WEEKDAYS, GtfsFeed, to_seconds

SOURCE_CSV = PROJECT_ROOT / "datasets" / "metro_schedule_source.csv"

# Daytime only: a transfer at 02:00 is not what the constant is sized for.
DAY_START_S, DAY_END_S = 7 * 3600, 20 * 3600


def main() -> int:
    feed = GtfsFeed.load("public_transport", agency_filter="Metro")

    with SOURCE_CSV.open(encoding="utf-8") as handle:
        source_of = {row["line"]: row["source"] for row in csv.DictReader(handle)}

    first_departure: dict[str, int] = {}
    for trip_id, rows in feed.stop_times.items():
        starts = [
            seconds
            for row in rows
            if (seconds := to_seconds(row["departure_time"]) or to_seconds(row["arrival_time"]))
            is not None
        ]
        if starts:
            first_departure[trip_id] = min(starts)

    print(f"{'Hat':6} {'kaynak':12} {'n':>5} {'ortanca aralık':>15} {'ortalama bekleme':>17}")
    waits: list[float] = []

    for route in sorted(feed.routes, key=lambda r: r["route_short_name"]):
        line = route["route_short_name"].strip()
        source = source_of.get(line, "?")
        trips = feed.trips_for_route(route["route_id"])

        if source == "stop_times":
            # Weekday, one direction, daytime: the gaps between consecutive
            # departures are the headway.
            per_direction: dict[str, list[int]] = defaultdict(list)
            for trip in trips:
                service = feed.calendar.get(trip["service_id"])
                if service is None or service.get(WEEKDAYS[0]) != "1":
                    continue
                start = first_departure.get(trip["trip_id"])
                if start is None or not DAY_START_S <= start <= DAY_END_S:
                    continue
                per_direction[trip["direction_id"]].append(start)
            gaps: list[int] = []
            for starts in per_direction.values():
                starts.sort()
                gaps += [b - a for a, b in pairwise(starts) if 0 < b - a <= 3600]
            if not gaps:
                print(f"{line:6} {source:12} {'—':>5}")
                continue
            headway = statistics.median(gaps)
            count = len(gaps)
        elif source == "frequencies":
            headways = [
                int(row["headway_secs"])
                for trip in trips
                for row in feed.frequencies.get(trip["trip_id"], [])
                if row["headway_secs"].isdigit()
                and (start := to_seconds(row["start_time"])) is not None
                and start <= DAY_END_S
            ]
            if not headways:
                print(f"{line:6} {source:12} {'—':>5}")
                continue
            headway = statistics.median(headways)
            count = len(headways)
        else:
            print(f"{line:6} {source:12} {'—':>5}  (kaynak yok)")
            continue

        wait = headway / 2
        waits.append(wait)
        print(f"{line:6} {source:12} {count:>5} {headway / 60:>12.1f} dk {wait / 60:>14.1f} dk")

    if waits:
        print(
            f"\n  Hatlar arası ortanca bekleme : {statistics.median(waits) / 60:.1f} dk "
            f"({statistics.median(waits):.0f} s)"
        )
        print(f"  ortalama                     : {statistics.fmean(waits) / 60:.1f} dk")
        print(f"  en kötü hat                  : {max(waits) / 60:.1f} dk")
    return 0


if __name__ == "__main__":
    sys.exit(main())
