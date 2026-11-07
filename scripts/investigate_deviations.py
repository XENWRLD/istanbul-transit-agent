"""Why do our times differ from the external sources?

Three questions, each answerable from the feed:

1. **Is dwell in the data at all?** If `arrival_time` equals
   `departure_time` on every row, the feed records no station dwell and our
   constant is the only source of it. M2 being 3 minutes short over 15 stops
   is 12 s per station, which is dwell-shaped.
2. **Which trips enter the median?** If night and peak runs are pooled, the
   median is of a mixture rather than of a service pattern. T1 being 5
   minutes *over* cannot be dwell, so it has to be trip selection.
3. **Does the time of day change segment durations?** Measured directly, per
   line, by bucketing each trip by its start.

    uv run python scripts/investigate_deviations.py
"""

from __future__ import annotations

import statistics
import sys
from collections import Counter, defaultdict
from itertools import pairwise

from istanbul_agent.store.gtfs import WEEKDAYS, GtfsFeed, to_seconds

LINES = ("M2", "M4", "M5", "T1", "F1")
MIN_SEGMENT_S, MAX_SEGMENT_S = 20, 900

# Buckets by a trip's first departure.
BUCKETS = (
    ("gece", 0, 5 * 3600),
    ("sabah zirve", 7 * 3600, 10 * 3600),
    ("gün ortası", 10 * 3600, 16 * 3600),
    ("akşam zirve", 16 * 3600, 20 * 3600),
    ("akşam", 20 * 3600, 24 * 3600),
)


def bucket_for(seconds: int) -> str:
    for label, low, high in BUCKETS:
        if low <= seconds % 86400 < high:
            return label
    return "diğer"


def main() -> int:
    feed = GtfsFeed.load("public_transport", agency_filter="Metro")

    # ---------------- 1. is dwell recorded? ----------------
    print("=" * 78)
    print("1. stop_times'ta arrival_time ile departure_time farklı mı?")
    print("=" * 78)

    dwell_values: Counter[int] = Counter()
    rows_total = 0
    for trip_rows in feed.stop_times.values():
        for row in trip_rows:
            arrive = to_seconds(row["arrival_time"])
            depart = to_seconds(row["departure_time"])
            if arrive is None or depart is None:
                continue
            rows_total += 1
            dwell_values[depart - arrive] += 1

    print(f"  {rows_total} stop_times satırı")
    print(f"  {'bekleme':>10} {'satır':>8}  oran")
    for value, count in sorted(dwell_values.items()):
        print(f"  {value:>9} s {count:>8}  {count / rows_total:>6.1%}")

    if set(dwell_values) == {0}:
        print("\n  SONUÇ: besleme hiç bekleme kaydetmiyor; tek kaynağımız sabitimiz.")
    else:
        print(
            f"\n  SONUÇ: besleme bekleme kaydediyor (ortanca "
            f"{statistics.median(dwell_values.elements()):.0f} s), ama tek bir "
            "sabit değer olarak — gerçek beklemenin istasyona göre değiştiği "
            "bilgisi beslemede yok."
        )

    # Does a trip's own schedule already account for dwell, i.e. is the
    # end-to-end trip time equal to the sum of our segments plus our dwell?
    print("\n  Sefer uçtan uca süresi vs segment toplamı:")
    for line in LINES:
        route = feed.route_by_short_name(line)
        if route is None:
            continue
        gaps: list[tuple[int, int, int]] = []
        for trip in feed.trips_for_route(route["route_id"])[:400]:
            sequence = feed.stop_times.get(trip["trip_id"], [])
            if len(sequence) < 3:
                continue
            start = to_seconds(sequence[0]["departure_time"])
            end = to_seconds(sequence[-1]["arrival_time"])
            if start is None or end is None:
                continue
            travel = 0
            dwell = 0
            ok = True
            for left, right in pairwise(sequence):
                depart = to_seconds(left["departure_time"])
                arrive = to_seconds(right["arrival_time"])
                if depart is None or arrive is None:
                    ok = False
                    break
                travel += arrive - depart
                inner_arrive = to_seconds(right["arrival_time"])
                inner_depart = to_seconds(right["departure_time"])
                if (
                    right is not sequence[-1]
                    and inner_arrive is not None
                    and inner_depart is not None
                ):
                    dwell += inner_depart - inner_arrive
            if ok:
                gaps.append((end - start, travel, dwell))
        if gaps:
            total = statistics.median([g[0] for g in gaps])
            travel = statistics.median([g[1] for g in gaps])
            dwell = statistics.median([g[2] for g in gaps])
            print(
                f"    {line:5} uçtan uca {total / 60:>5.1f} dk = "
                f"seyir {travel / 60:>5.1f} dk + bekleme {dwell / 60:>4.1f} dk "
                f"(n={len(gaps)})"
            )

    # ---------------- 2 & 3. which trips, and does time of day matter? ----
    print("\n" + "=" * 78)
    print("2-3. Ortancaya hangi seferler giriyor, saat dilimi süreyi değiştiriyor mu?")
    print("=" * 78)

    for line in LINES:
        route = feed.route_by_short_name(line)
        if route is None:
            continue
        trips = feed.trips_for_route(route["route_id"])

        first_departure: dict[str, int] = {}
        for trip in trips:
            starts = [
                seconds
                for row in feed.stop_times.get(trip["trip_id"], [])
                if (seconds := to_seconds(row["departure_time"])) is not None
            ]
            if starts:
                first_departure[trip["trip_id"]] = min(starts)

        by_bucket: Counter[str] = Counter()
        weekday_only = 0
        for trip in trips:
            start = first_departure.get(trip["trip_id"])
            if start is None:
                continue
            by_bucket[bucket_for(start)] += 1
            service = feed.calendar.get(trip["service_id"])
            if service and all(service.get(day) == "1" for day in WEEKDAYS[:5]):
                weekday_only += 1

        print(f"\n  {line}  {len(trips)} sefer, hafta içi çalışan {weekday_only}")
        print(
            "    sefer dağılımı: "
            + ", ".join(f"{label}={by_bucket.get(label, 0)}" for label, _, _ in BUCKETS)
        )

        # Segment durations, bucketed.
        per_bucket: dict[str, list[int]] = defaultdict(list)
        for trip in trips:
            start = first_departure.get(trip["trip_id"])
            if start is None:
                continue
            label = bucket_for(start)
            sequence = feed.stop_times.get(trip["trip_id"], [])
            for left, right in pairwise(sequence):
                depart = to_seconds(left["departure_time"])
                arrive = to_seconds(right["arrival_time"])
                if depart is None or arrive is None:
                    continue
                delta = arrive - depart
                if MIN_SEGMENT_S <= delta <= MAX_SEGMENT_S:
                    per_bucket[label].append(delta)

        print(f"    {'dilim':14} {'n':>6} {'ortanca':>9} {'ortalama':>9}")
        for label, _, _ in BUCKETS:
            values = per_bucket.get(label, [])
            if not values:
                continue
            print(
                f"    {label:14} {len(values):>6} {statistics.median(values):>8.0f}s "
                f"{statistics.fmean(values):>8.0f}s"
            )
        everything = [v for values in per_bucket.values() for v in values]
        if everything:
            print(
                f"    {'TÜMÜ':14} {len(everything):>6} "
                f"{statistics.median(everything):>8.0f}s "
                f"{statistics.fmean(everything):>8.0f}s"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
