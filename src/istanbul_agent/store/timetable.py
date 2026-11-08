"""Service hours and headways per line, from the GTFS feed.

`get_next_departures` answers three things: when the first and last service
run, how often trains come right now, and how the weekend differs. None of
that can be read straight out of this feed:

- `frequencies.csv` covers 66 of 10894 metro trips, and for the busy lines
  its rows are night service only (`23:50-02:00, her 10 dk`). So the source
  is chosen per line, from `datasets/metro_schedule_source.csv`: the lines
  whose daytime service exists only in `frequencies.csv` (F2, F3, M7, M8,
  M9, TF1, TF2) use it, the rest are measured from the explicit departure
  times in `stop_times.csv` (decision 0002).
- `calendar.csv` has expired, so a trip's day type comes from the weekday
  pattern alone -- weekday, Saturday, Sunday (decision 0002).

Headways are measured, not declared: the gaps between consecutive departures
of one direction are collected per hour and the median of each hour is kept.
The median rather than the mean because a line's last few trips before
midnight leave gaps three times the daytime figure, and one such gap would
drag an hour's average off the service a passenger actually meets.

Everything here is a pure function of the feed; the ingest writes the result
to DuckDB and `departures.py` reads it back.
"""

from __future__ import annotations

import csv
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Literal

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.store.gtfs import GtfsFeed, seconds_to_clock, to_seconds

__all__ = [
    "DAY_TYPES",
    "DAY_TYPE_LABELS",
    "MIN_TRIPS_FOR_WINDOW",
    "DayType",
    "HeadwayHour",
    "ServiceWindow",
    "build_headways",
    "build_service_windows",
    "day_type_for",
    "read_schedule_sources",
]

DayType = Literal["hafta_ici", "cumartesi", "pazar"]

DAY_TYPES: tuple[DayType, ...] = ("hafta_ici", "cumartesi", "pazar")
DAY_TYPE_LABELS: dict[str, str] = {
    "hafta_ici": "hafta içi",
    "cumartesi": "cumartesi",
    "pazar": "pazar",
}
# The weekday each day type is measured on. Weekday service is read off a
# Wednesday: Monday and Friday carry the same calendar pattern, and picking a
# midweek day keeps a Friday-only service (one exists) out of the figure.
DAY_TYPE_WEEKDAY: dict[str, int] = {"hafta_ici": 2, "cumartesi": 5, "pazar": 6}

SCHEDULE_SOURCE_CSV = PROJECT_ROOT / "datasets" / "metro_schedule_source.csv"

# Departures before this hour are night service, not the start of the day.
# The feed writes a weekend 00:20 train as 00:20 rather than 24:20, so taking
# the earliest departure literally would answer "M2's first train is 00:20"
# when the first daytime train is at 06:00 and the 00:20 one is the tail of
# Friday night. Day and night windows are therefore reported separately.
NIGHT_END_S = 4 * 3600

# A window measured from explicit departure times needs enough of them to be
# a timetable rather than a sample. The Marmaray routes in this feed carry
# two to four trips each -- template trips, no frequencies rows -- and a
# "first and last service" read off those would be a fabrication, so those
# lines produce no window at all and the tool says so.
MIN_TRIPS_FOR_WINDOW = 4
SEGMENT_DAY = "gunduz"
SEGMENT_NIGHT = "gece"


def day_type_for(weekday: int) -> DayType:
    """Day type for a weekday index (0 = Monday)."""
    if weekday >= 6:
        return "pazar"
    if weekday == 5:
        return "cumartesi"
    return "hafta_ici"


@dataclass(frozen=True)
class ServiceWindow:
    """First and last departure of one line, direction and day type.

    `station_key` is empty for the line as a whole; otherwise it is the
    normalised station name the times were measured at. `segment` separates
    daytime service from the night service some lines run at the weekend.
    `estimated` marks a window derived from a headway range plus a travel
    offset rather than read from explicit departure times.
    """

    line: str
    direction_id: str
    direction_name: str
    day_type: str
    segment: str
    station_key: str
    station_name: str
    first_s: int
    last_s: int
    trip_count: int
    source: str
    estimated: bool

    @property
    def first(self) -> str:
        return seconds_to_clock(self.first_s) or ""

    @property
    def last(self) -> str:
        return seconds_to_clock(self.last_s) or ""


@dataclass(frozen=True)
class HeadwayHour:
    """Median gap between departures during one hour of the service day."""

    line: str
    direction_id: str
    direction_name: str
    day_type: str
    hour: int
    headway_s: int
    departures: int
    source: str


def read_schedule_sources(path: Path = SCHEDULE_SOURCE_CSV) -> dict[str, str]:
    """Which table each line's daytime service lives in.

    Produced by `scripts/decide_schedule_source.py`; committed because it is
    a decision, not a cache (decision 0002).
    """
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["line"]: row["source"] for row in csv.DictReader(handle) if row.get("line")}


def _line_routes(feed: GtfsFeed, line: str) -> list[dict[str, str]]:
    wanted = line.strip().upper()
    return [r for r in feed.routes if r["route_short_name"].strip().upper() == wanted]


def _direction_names(feed: GtfsFeed, trips: Iterable[dict[str, str]]) -> dict[str, str]:
    """The most common headsign per direction, which is where a trip ends."""
    counts: dict[str, dict[str, int]] = {}
    for trip in trips:
        headsign = (trip.get("trip_headsign") or "").strip()
        if not headsign:
            continue
        bucket = counts.setdefault(trip.get("direction_id") or "0", {})
        bucket[headsign] = bucket.get(headsign, 0) + 1
    return {
        direction: max(names.items(), key=lambda item: item[1])[0]
        for direction, names in counts.items()
    }


def _trips_by_direction(
    feed: GtfsFeed, line: str, day_type: str
) -> dict[str, list[dict[str, str]]]:
    """Trips of `line` that run on `day_type`, grouped by direction."""
    weekday = DAY_TYPE_WEEKDAY[day_type]
    grouped: dict[str, list[dict[str, str]]] = {}
    for route in _line_routes(feed, line):
        for trip in feed.trips_for_route(route["route_id"]):
            if not feed.serves_weekday(trip["service_id"], weekday):
                continue
            grouped.setdefault(trip.get("direction_id") or "0", []).append(trip)
    return grouped


def _departures_at(feed: GtfsFeed, trip_id: str, station_key: str | None) -> int | None:
    """Departure time of one trip, at its origin or at one station."""
    from istanbul_agent.store.station_names import normalize

    rows = feed.stop_times.get(trip_id, [])
    if not rows:
        return None
    if station_key is None:
        return to_seconds(rows[0].get("departure_time"))
    for row in rows:
        stop = feed.stops.get(row["stop_id"])
        if stop is not None and normalize(stop.get("stop_name", "")) == station_key:
            return to_seconds(row.get("departure_time"))
    return None


def _station_sequence(feed: GtfsFeed, trips: list[dict[str, str]]) -> list[dict[str, str]]:
    """stop_times rows of the direction's most complete trip."""
    best: list[dict[str, str]] = []
    for trip in trips:
        rows = feed.stop_times.get(trip["trip_id"], [])
        if len(rows) > len(best):
            best = rows
    return best


def _windows_from_stop_times(
    feed: GtfsFeed,
    line: str,
    day_type: str,
    direction_id: str,
    direction_name: str,
    trips: list[dict[str, str]],
) -> list[ServiceWindow]:
    """Service windows read off the explicit departure times.

    One window per station as well as one for the line, because "when is the
    last train" is asked at a station and the last train leaves Kadıköy
    nearly an hour before it leaves the far end of the line. Day and night
    service are kept apart; see NIGHT_END_S.
    """
    from istanbul_agent.store.station_names import normalize

    source = "GTFS stop_times"
    windows: list[ServiceWindow] = []

    for segment in (SEGMENT_DAY, SEGMENT_NIGHT):
        per_station: dict[str, tuple[str, list[int]]] = {}
        origin_departures: list[int] = []
        for trip in trips:
            rows = feed.stop_times.get(trip["trip_id"], [])
            if not rows:
                continue
            origin = to_seconds(rows[0].get("departure_time"))
            if origin is None:
                continue
            is_night = origin < NIGHT_END_S
            if is_night != (segment == SEGMENT_NIGHT):
                continue
            origin_departures.append(origin)
            for row in rows:
                seconds = to_seconds(row.get("departure_time"))
                stop = feed.stops.get(row["stop_id"])
                if seconds is None or stop is None:
                    continue
                name = stop.get("stop_name", "").strip()
                entry = per_station.setdefault(normalize(name), (name, []))
                entry[1].append(seconds)

        if len(origin_departures) < MIN_TRIPS_FOR_WINDOW:
            continue

        windows.append(
            ServiceWindow(
                line=line,
                direction_id=direction_id,
                direction_name=direction_name,
                day_type=day_type,
                segment=segment,
                station_key="",
                station_name="",
                first_s=min(origin_departures),
                last_s=max(origin_departures),
                trip_count=len(origin_departures),
                source=source,
                estimated=False,
            )
        )
        windows.extend(
            ServiceWindow(
                line=line,
                direction_id=direction_id,
                direction_name=direction_name,
                day_type=day_type,
                segment=segment,
                station_key=key,
                station_name=name,
                first_s=min(times),
                last_s=max(times),
                trip_count=len(times),
                source=source,
                estimated=False,
            )
            for key, (name, times) in sorted(per_station.items())
        )
    return windows


def _frequency_rows(feed: GtfsFeed, trips: list[dict[str, str]]) -> list[tuple[int, int, int]]:
    """`(start, end, headway)` in seconds for a direction's template trips."""
    rows: list[tuple[int, int, int]] = []
    for trip in trips:
        for row in feed.frequencies.get(trip["trip_id"], []):
            start = to_seconds(row.get("start_time"))
            end = to_seconds(row.get("end_time"))
            try:
                headway = int(row.get("headway_secs") or 0)
            except ValueError:
                headway = 0
            if start is None or end is None or headway <= 0:
                continue
            rows.append((start, end, headway))
    return sorted(set(rows))


def _windows_from_frequencies(
    feed: GtfsFeed,
    line: str,
    day_type: str,
    direction_id: str,
    direction_name: str,
    trips: list[dict[str, str]],
) -> list[ServiceWindow]:
    """Service windows for a line whose daytime service is only a headway.

    The line's window is the union of its frequency bands. A station's window
    is that window shifted by how long the template trip takes to reach the
    station, which is an estimate -- the feed states no departure times for
    these trips -- and is marked as one.
    """
    from istanbul_agent.store.station_names import normalize

    bands = _frequency_rows(feed, trips)
    if not bands:
        return []
    source = "GTFS frequencies"
    sequence = _station_sequence(feed, trips)
    origin = to_seconds(sequence[0].get("departure_time")) if sequence else None
    windows: list[ServiceWindow] = []

    for segment in (SEGMENT_DAY, SEGMENT_NIGHT):
        chosen = [
            (start, end, headway)
            for start, end, headway in bands
            if (start < NIGHT_END_S) == (segment == SEGMENT_NIGHT)
        ]
        if not chosen:
            continue
        first_s = min(start for start, _, _ in chosen)
        last_s = max(end for _, end, _ in chosen)
        trip_count = sum(max(1, (end - start) // headway) for start, end, headway in chosen)
        windows.append(
            ServiceWindow(
                line=line,
                direction_id=direction_id,
                direction_name=direction_name,
                day_type=day_type,
                segment=segment,
                station_key="",
                station_name="",
                first_s=first_s,
                last_s=last_s,
                trip_count=trip_count,
                source=source,
                estimated=False,
            )
        )
        for row in sequence:
            seconds = to_seconds(row.get("departure_time"))
            stop = feed.stops.get(row["stop_id"])
            if seconds is None or origin is None or stop is None:
                continue
            offset = seconds - origin
            name = stop.get("stop_name", "").strip()
            windows.append(
                ServiceWindow(
                    line=line,
                    direction_id=direction_id,
                    direction_name=direction_name,
                    day_type=day_type,
                    segment=segment,
                    station_key=normalize(name),
                    station_name=name,
                    first_s=first_s + offset,
                    last_s=last_s + offset,
                    trip_count=trip_count,
                    source=source + " + sefer suresi",
                    estimated=True,
                )
            )
    return windows


def _headways_from_stop_times(
    feed: GtfsFeed,
    line: str,
    day_type: str,
    direction_id: str,
    direction_name: str,
    trips: list[dict[str, str]],
) -> list[HeadwayHour]:
    """Median gap per hour, measured between consecutive departures."""
    departures = sorted(
        seconds
        for trip in trips
        if (seconds := _departures_at(feed, trip["trip_id"], None)) is not None
    )
    if len(departures) < MIN_TRIPS_FOR_WINDOW:
        # Same reason as the windows: a median gap between two trips is not
        # a headway.
        return []

    counts: dict[int, int] = {}
    for seconds in departures:
        hour = (seconds // 3600) % 24
        counts[hour] = counts.get(hour, 0) + 1

    # Only gaps between two departures of the same hour count. A gap that
    # runs into the next hour is the end of this hour's service, not its
    # headway: on M9 the 06:20 train is followed by the 08:00 one, and
    # counting that 100-minute gap would report hour 6 as a 7-minute service
    # when the trains a passenger finds there are five minutes apart.
    gaps: dict[int, list[int]] = {}
    for earlier, later in pairwise(departures):
        gap = later - earlier
        if gap <= 0 or earlier // 3600 != later // 3600:
            continue
        gaps.setdefault((earlier // 3600) % 24, []).append(gap)

    return [
        HeadwayHour(
            line=line,
            direction_id=direction_id,
            direction_name=direction_name,
            day_type=day_type,
            hour=hour,
            headway_s=int(statistics.median(values)),
            departures=counts.get(hour, 0),
            source="GTFS stop_times (olculen)",
        )
        for hour, values in sorted(gaps.items())
    ]


def _headways_from_frequencies(
    feed: GtfsFeed,
    line: str,
    day_type: str,
    direction_id: str,
    direction_name: str,
    trips: list[dict[str, str]],
) -> list[HeadwayHour]:
    bands = _frequency_rows(feed, trips)
    if not bands:
        return []
    # An hour covered by two bands takes the tighter headway: the bands meet
    # at their edges and the service a passenger finds there is the denser one.
    best: dict[int, int] = {}
    for start, end, headway in bands:
        for hour_start in range(start // 3600 * 3600, end, 3600):
            if hour_start + 3600 <= start:
                continue
            hour = (hour_start // 3600) % 24
            best[hour] = min(best.get(hour, headway), headway)
    return [
        HeadwayHour(
            line=line,
            direction_id=direction_id,
            direction_name=direction_name,
            day_type=day_type,
            hour=hour,
            headway_s=headway,
            departures=3600 // headway if headway else 0,
            source="GTFS frequencies (beyan)",
        )
        for hour, headway in sorted(best.items())
    ]


def build_service_windows(
    feed: GtfsFeed, lines: Iterable[str], sources: dict[str, str]
) -> list[ServiceWindow]:
    """Service windows for every line, direction and day type."""
    windows: list[ServiceWindow] = []
    for line in lines:
        source = sources.get(line, "stop_times")
        build = _windows_from_frequencies if source == "frequencies" else _windows_from_stop_times
        for day_type in DAY_TYPES:
            grouped = _trips_by_direction(feed, line, day_type)
            names = _direction_names(feed, (t for trips in grouped.values() for t in trips))
            for direction_id, trips in sorted(grouped.items()):
                windows.extend(
                    build(feed, line, day_type, direction_id, names.get(direction_id, ""), trips)
                )
    return windows


def build_headways(
    feed: GtfsFeed, lines: Iterable[str], sources: dict[str, str]
) -> list[HeadwayHour]:
    """Median headway per hour, for every line, direction and day type."""
    headways: list[HeadwayHour] = []
    for line in lines:
        source = sources.get(line, "stop_times")
        build = _headways_from_frequencies if source == "frequencies" else _headways_from_stop_times
        for day_type in DAY_TYPES:
            grouped = _trips_by_direction(feed, line, day_type)
            names = _direction_names(feed, (t for trips in grouped.values() for t in trips))
            for direction_id, trips in sorted(grouped.items()):
                headways.extend(
                    build(feed, line, day_type, direction_id, names.get(direction_id, ""), trips)
                )
    return headways
