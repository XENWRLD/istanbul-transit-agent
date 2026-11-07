"""Are T3's stop_times durations usable, given its names are consistent?

Decision 0003 refused T3 outright on the grounds that its names were
scrambled relative to its coordinates. The ring analysis showed that is not
what happened: the name sequence matches the live one (rotated by one, plus
one station the feed predates), and the coordinates are imprecise by 164-763 m
on a line whose stations sit 239 m apart -- so it is the coordinates that
cannot be trusted, not the names.

If so, T3's durations are keyed to the right stations after all, and only
coordinate-based estimates on T3 have to be refused. Check that its segment
times are internally sensible before acting on that.

    uv run python scripts/inspect_t3_durations.py
"""

from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from itertools import pairwise

from istanbul_agent.store.gtfs import GtfsFeed, to_seconds

LINE = "T3"
MIN_SEGMENT_S, MAX_SEGMENT_S = 20, 900

# T3 is a 4.8 km tram ring with 239 m between stations; a hop should take
# roughly 239 m at tram speed plus the stop-and-start overhead.
PLAUSIBLE_HOP = (30, 180)


def main() -> int:
    feed = GtfsFeed.load("public_transport", agency_filter="Metro")
    route = feed.route_by_short_name(LINE)
    if route is None:
        print(f"{LINE} GTFS'te yok")
        return 1

    trips = feed.trips_for_route(route["route_id"])
    print(f"{LINE}: {len(trips)} sefer\n")

    durations: dict[tuple[str, str], list[int]] = defaultdict(list)
    for trip in trips:
        sequence = feed.stop_times.get(trip["trip_id"], [])
        for left, right in pairwise(sequence):
            depart = to_seconds(left["departure_time"]) or to_seconds(left["arrival_time"])
            arrive = to_seconds(right["arrival_time"]) or to_seconds(right["departure_time"])
            if depart is None or arrive is None:
                continue
            delta = arrive - depart
            if MIN_SEGMENT_S <= delta <= MAX_SEGMENT_S:
                durations[(left["stop_id"], right["stop_id"])].append(delta)

    print(f"{'A':26} {'B':26} {'ortanca':>8} {'n':>5}  makul?")
    values: list[int] = []
    implausible = 0
    for (from_id, to_id), observed in durations.items():
        from_stop = feed.stops.get(from_id)
        to_stop = feed.stops.get(to_id)
        if from_stop is None or to_stop is None:
            continue
        median = int(statistics.median(observed))
        values.append(median)
        ok = PLAUSIBLE_HOP[0] <= median <= PLAUSIBLE_HOP[1]
        if not ok:
            implausible += 1
        print(
            f"{from_stop['stop_name'][:25]:26} {to_stop['stop_name'][:25]:26} "
            f"{median:>7}s {len(observed):>5}  {'evet' if ok else 'HAYIR'}"
        )

    if values:
        print(
            f"\n  {len(values)} segment; ortanca {statistics.median(values):.0f}s, "
            f"min {min(values)}s, max {max(values)}s"
        )
        print(f"  makul aralık ({PLAUSIBLE_HOP[0]}-{PLAUSIBLE_HOP[1]}s) dışında: {implausible}")
        total = sum(values)
        print(f"  ring turu toplamı: {total}s = {total / 60:.1f} dk")
        print(
            "\n  SONUÇ: süreler tutarlı; T3'ün stop_times verisi kullanılabilir."
            if implausible == 0
            else f"\n  SONUÇ: {implausible} segment makul değil; dikkatli ol."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
