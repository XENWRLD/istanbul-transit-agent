"""Reading the İBB GTFS feeds.

Two things about these feeds are not what a GTFS reader would assume, both
established by measurement in Faz 1 (decisions 0002 and 0003):

- The CSVs are **cp1254**, not UTF-8. Decoding them as UTF-8 fails outright
  on Turkish names.
- `calendar.csv` has expired: every `end_date` is 2024-12-31 or earlier. Service
  selection therefore ignores the date window and matches on the weekday
  pattern alone, and services with no weekday set at all are dropped.

Times may exceed 24 hours (`25:10:00` means 01:10 the next day), so they are
carried as seconds past midnight rather than clock times.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from istanbul_agent.config import PROJECT_ROOT

__all__ = [
    "GTFS_ENCODING",
    "WEEKDAYS",
    "GtfsFeed",
    "feed_dir",
    "read_table",
    "seconds_to_clock",
    "to_seconds",
]

GTFS_ENCODING = "cp1254"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

GTFS_ROOT = PROJECT_ROOT / "data" / "gtfs"


def feed_dir(feed: str = "public_transport") -> Path:
    return GTFS_ROOT / feed


def read_table(name: str, feed: str = "public_transport") -> list[dict[str, str]]:
    """Read one GTFS CSV as a list of rows."""
    path = feed_dir(feed) / name
    with path.open(encoding=GTFS_ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def iter_table(name: str, feed: str = "public_transport") -> Iterator[dict[str, str]]:
    """Stream one GTFS CSV, for tables too large to hold twice."""
    path = feed_dir(feed) / name
    with path.open(encoding=GTFS_ENCODING, newline="") as handle:
        yield from csv.DictReader(handle)


def to_seconds(value: str | None) -> int | None:
    """Parse a GTFS time into seconds past midnight.

    Hours may exceed 23 for trips running past midnight, so this cannot go
    through `datetime.time`.
    """
    if not value:
        return None
    parts = value.strip().split(":")
    if len(parts) < 3:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in parts[:3])
    except ValueError:
        return None
    return hours * 3600 + minutes * 60 + seconds


def seconds_to_clock(seconds: int | None) -> str | None:
    """Render seconds past midnight as `HH:MM`, wrapping past 24h."""
    if seconds is None:
        return None
    return f"{(seconds // 3600) % 24:02d}:{(seconds % 3600) // 60:02d}"


@dataclass(frozen=True)
class GtfsFeed:
    """A loaded feed, with the tables the rail tools need."""

    feed: str
    agencies: dict[str, str]
    routes: list[dict[str, str]]
    trips: list[dict[str, str]]
    stops: dict[str, dict[str, str]]
    calendar: dict[str, dict[str, str]]
    frequencies: dict[str, list[dict[str, str]]]
    stop_times: dict[str, list[dict[str, str]]]

    @classmethod
    def load(cls, feed: str = "public_transport", *, agency_filter: str | None = None) -> GtfsFeed:
        """Load `feed`, optionally keeping only one agency's routes.

        `agency_filter` is matched as a substring of the agency name, so
        ``"Metro"`` selects Metro İstanbul. Filtering here keeps stop_times --
        the only large table -- down to the trips we care about.
        """
        agencies = {r["agency_id"]: r["agency_name"] for r in read_table("agency.csv", feed)}
        routes = read_table("routes.csv", feed)
        if agency_filter:
            wanted = {
                aid for aid, name in agencies.items() if agency_filter.lower() in name.lower()
            }
            routes = [r for r in routes if r.get("agency_id") in wanted]

        route_ids = {r["route_id"] for r in routes}
        trips = [t for t in read_table("trips.csv", feed) if t["route_id"] in route_ids]
        trip_ids = {t["trip_id"] for t in trips}

        stop_times: dict[str, list[dict[str, str]]] = {}
        for row in iter_table("stop_times.csv", feed):
            if row["trip_id"] in trip_ids:
                stop_times.setdefault(row["trip_id"], []).append(row)
        for rows in stop_times.values():
            rows.sort(key=lambda r: int(r["stop_sequence"] or 0))

        frequencies: dict[str, list[dict[str, str]]] = {}
        frequency_path = feed_dir(feed) / "frequencies.csv"
        if frequency_path.exists():
            for row in read_table("frequencies.csv", feed):
                if row["trip_id"] in trip_ids:
                    frequencies.setdefault(row["trip_id"], []).append(row)

        # Keep only the stops the selected trips actually visit. Loading the
        # whole table alongside an agency filter is a trap: this feed carries
        # 7073 stops, most of them minibus and ferry, and names like "Çarşı"
        # or "Site" then collide with a metro station during name matching.
        stops = {r["stop_id"]: r for r in read_table("stops.csv", feed)}
        if agency_filter:
            visited = {row["stop_id"] for rows in stop_times.values() for row in rows}
            stops = {sid: stop for sid, stop in stops.items() if sid in visited}

        return cls(
            feed=feed,
            agencies=agencies,
            routes=routes,
            trips=trips,
            stops=stops,
            calendar={r["service_id"]: r for r in read_table("calendar.csv", feed)},
            frequencies=frequencies,
            stop_times=stop_times,
        )

    # ------------------------------------------------------------------ views

    def route_by_short_name(self, short_name: str) -> dict[str, str] | None:
        for route in self.routes:
            if route["route_short_name"].strip().upper() == short_name.strip().upper():
                return route
        return None

    def trips_for_route(self, route_id: str) -> list[dict[str, str]]:
        return [t for t in self.trips if t["route_id"] == route_id]

    def longest_trip_sequence(self, route_id: str) -> list[dict[str, str]]:
        """The stop_times rows of the route's most complete trip.

        Short-turn trips exist on most lines, so the longest sequence is the
        best available picture of the full station order.
        """
        best: list[dict[str, str]] = []
        for trip in self.trips_for_route(route_id):
            sequence = self.stop_times.get(trip["trip_id"], [])
            if len(sequence) > len(best):
                best = sequence
        return best

    def serves_weekday(self, service_id: str, weekday: int) -> bool:
        """Does `service_id` run on `weekday` (0=Monday)?

        The date window is deliberately not consulted; see decision 0002.
        """
        service = self.calendar.get(service_id)
        if service is None:
            return False
        if not any(service.get(day) == "1" for day in WEEKDAYS):
            return False  # no day set at all: these trips never run
        return service.get(WEEKDAYS[weekday]) == "1"

    def active_service_ids(self, weekday: int) -> set[str]:
        return {sid for sid in self.calendar if self.serves_weekday(sid, weekday)}
