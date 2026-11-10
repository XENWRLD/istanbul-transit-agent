"""Reading the İBB GTFS feeds.

The portal publishes two feeds and **they are not the same dialect**. Both
sets of quirks were measured before any of this was written (decisions 0002,
0003 and 0007); a reader that assumes plain GTFS gets wrong answers from one
feed or the other.

`public_transport` (dataset 308, metro/ferry/minibus, 2020 data):

- CSVs are **cp1254**, not UTF-8. Decoding them as UTF-8 fails outright.
- Comma separated, as GTFS specifies.
- `calendar.csv` has **expired**: every `end_date` is 2024-12-31 or earlier.
  Service selection therefore ignores the date window and matches on the
  weekday pattern alone (decision 0002).

`iett` (dataset 75, buses, April 2026):

- CSVs are **UTF-8 with a BOM**, and the text inside them is **double
  encoded** (`KADIKÃ–Y`): 11 867 values in `routes.csv` alone. Repaired by
  `store/text_repair.py`.
- **Semicolon** separated -- except `agency.csv` and `stop_times.txt`, which
  are comma separated. The delimiter is per file, not per feed.
- `stops.csv` coordinates arrive **mangled**: `410.191.700.005.564` means
  41.0191700005564. 15 386 of 15 390 stops repair into İstanbul's bounding
  box; the remaining 4 rows have shifted columns and are dropped.
- `calendar.csv` is **current** (2026-03-16 to 2026-12-31), so unlike the
  other feed its date window can be trusted. `calendar_dates.csv` is still
  missing, so public holidays remain unknown.
- `stop_times` is shipped **zipped** (`stop_times.zip`, member
  `stop_times.txt`, 150 MB) and carries only **two** times per trip: the
  departure from the first stop and the arrival at the last. Intermediate
  stops have empty times -- per-stop bus times need the İETT live service,
  not this feed.

Times may exceed 24 hours (`25:10:00` means 01:10 the next day), so they are
carried as seconds past midnight rather than clock times.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.store.text_repair import repair_double_encoded

__all__ = [
    "DIALECTS",
    "GTFS_ENCODING",
    "WEEKDAYS",
    "FeedDialect",
    "GtfsFeed",
    "dialect_for",
    "feed_dir",
    "read_table",
    "seconds_to_clock",
    "to_seconds",
]

# Geriye dönük ad: 308 beslemesinin kodlaması. Yeni kod `dialect_for` kullanır.
GTFS_ENCODING = "cp1254"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

GTFS_ROOT = PROJECT_ROOT / "data" / "gtfs"


@dataclass(frozen=True)
class FeedDialect:
    """One feed's measured quirks.

    `files` maps a logical table name (`stops`, `stop_times`) to the file it
    actually lives in, because the İETT feed ships `stop_times` zipped and
    names its tables `.csv` while the zipped member is `.txt`.
    """

    feed: str
    encoding: str
    delimiter: str
    source: str
    data_as_of: str
    is_stale: bool
    calendar_window_usable: bool
    repairs_text: bool
    repairs_coordinates: bool
    files: dict[str, str] = field(default_factory=dict)
    delimiter_overrides: dict[str, str] = field(default_factory=dict)

    def file_name(self, table: str) -> str:
        key = table.removesuffix(".csv").removesuffix(".txt")
        return self.files.get(key, f"{key}.csv")

    def delimiter_for(self, table: str) -> str:
        key = table.removesuffix(".csv").removesuffix(".txt")
        return self.delimiter_overrides.get(key, self.delimiter)


DIALECTS: dict[str, FeedDialect] = {
    "public_transport": FeedDialect(
        feed="public_transport",
        encoding="cp1254",
        delimiter=",",
        source="Public Transport GTFS (veri seti 308)",
        data_as_of="2020-07-09",
        is_stale=True,
        calendar_window_usable=False,
        repairs_text=False,
        repairs_coordinates=False,
    ),
    "iett": FeedDialect(
        feed="iett",
        encoding="utf-8-sig",
        delimiter=";",
        source="İETT GTFS (veri seti 75)",
        data_as_of="2026-04-21",
        is_stale=False,
        calendar_window_usable=True,
        repairs_text=True,
        repairs_coordinates=True,
        files={"stop_times": "stop_times.zip"},
        delimiter_overrides={"agency": ",", "stop_times": ","},
    ),
}


def dialect_for(feed: str) -> FeedDialect:
    """The measured dialect of `feed`.

    Unknown feeds raise rather than defaulting: guessing a dialect is how a
    reader silently returns mojibake or a coordinate in the Gulf of Guinea.
    """
    try:
        return DIALECTS[feed]
    except KeyError:
        known = ", ".join(sorted(DIALECTS))
        raise KeyError(f"bilinmeyen GTFS beslemesi: {feed!r} (bilinenler: {known})") from None


def feed_dir(feed: str = "public_transport") -> Path:
    return GTFS_ROOT / feed


@contextmanager
def _open_table(table: str, dialect: FeedDialect) -> Iterator[TextIO]:
    """Open a table, reaching inside a zip when the feed ships one."""
    path = feed_dir(dialect.feed) / dialect.file_name(table)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            member = archive.infolist()[0]
            with archive.open(member) as binary:
                yield io.TextIOWrapper(binary, encoding=dialect.encoding, newline="")
        return
    with path.open(encoding=dialect.encoding, newline="") as handle:
        yield handle


def _clean(row: dict[str, str], dialect: FeedDialect) -> dict[str, str]:
    if not dialect.repairs_text:
        return row
    return {
        key: (repair_double_encoded(value) if isinstance(value, str) else value)
        for key, value in row.items()
    }


def read_table(name: str, feed: str = "public_transport") -> list[dict[str, str]]:
    """Read one GTFS table as a list of rows, repaired per the feed dialect."""
    return list(iter_table(name, feed))


def iter_table(name: str, feed: str = "public_transport") -> Iterator[dict[str, str]]:
    """Stream one GTFS table, for tables too large to hold twice."""
    dialect = dialect_for(feed)
    with _open_table(name, dialect) as handle:
        reader = csv.DictReader(handle, delimiter=dialect.delimiter_for(name))
        for row in reader:
            yield _clean(row, dialect)


def table_exists(name: str, feed: str = "public_transport") -> bool:
    """Is this table present in the feed at all?"""
    dialect = dialect_for(feed)
    return (feed_dir(feed) / dialect.file_name(name)).exists()


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
        if table_exists("frequencies", feed):
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
