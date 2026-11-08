"""Build the timetable tables in data/transit.duckdb.

Two tables, both derived from the GTFS feed by `timetable.py`:

- `rail_service_windows` -- first and last departure per line, direction, day
  type and service segment (daytime / night), once for the line and once per
  station
- `rail_headways`        -- median headway per hour, same keys

Kept separate from `ingest_rail` because it answers a different question and
takes a different slice of the feed: the station graph needs the stop
sequence, the timetable needs the departure times.

    uv run python -m istanbul_agent.store.ingest_timetable
"""

from __future__ import annotations

import sys
from typing import Any

from istanbul_agent.store.db import connect
from istanbul_agent.store.gtfs import GtfsFeed, seconds_to_clock
from istanbul_agent.store.rail_travel import GTFS_METRO_DATA_AS_OF, MARMARAY_DATA_AS_OF
from istanbul_agent.store.timetable import (
    build_headways,
    build_service_windows,
    read_schedule_sources,
)

SCHEMA = """
DROP TABLE IF EXISTS rail_service_windows;
DROP TABLE IF EXISTS rail_headways;

CREATE TABLE rail_service_windows (
    line           VARCHAR NOT NULL,
    direction_id   VARCHAR NOT NULL,
    direction_name VARCHAR NOT NULL,
    day_type       VARCHAR NOT NULL,
    segment        VARCHAR NOT NULL,
    station_key    VARCHAR NOT NULL,
    station_name   VARCHAR NOT NULL,
    first_s        INTEGER NOT NULL,
    last_s         INTEGER NOT NULL,
    first_clock    VARCHAR NOT NULL,
    last_clock     VARCHAR NOT NULL,
    trip_count     INTEGER NOT NULL,
    estimated      BOOLEAN NOT NULL,
    source         VARCHAR NOT NULL,
    data_as_of     VARCHAR NOT NULL,
    PRIMARY KEY (line, direction_id, day_type, segment, station_key)
);

CREATE TABLE rail_headways (
    line           VARCHAR NOT NULL,
    direction_id   VARCHAR NOT NULL,
    direction_name VARCHAR NOT NULL,
    day_type       VARCHAR NOT NULL,
    hour           INTEGER NOT NULL,
    headway_s      INTEGER NOT NULL,
    departures     INTEGER NOT NULL,
    source         VARCHAR NOT NULL,
    data_as_of     VARCHAR NOT NULL,
    PRIMARY KEY (line, direction_id, day_type, hour)
);
"""

# Marmaray is TCDD in this feed, a year older than the metro's data, and the
# schedule-source decision table does not cover it -- its trips carry explicit
# departure times, so the default (stop_times) is right.
FEEDS: tuple[tuple[str, str], ...] = (
    ("Metro", GTFS_METRO_DATA_AS_OF),
    ("TCDD", MARMARAY_DATA_AS_OF),
)


def main() -> int:
    sources = read_schedule_sources()
    window_rows: list[dict[str, Any]] = []
    headway_rows: list[dict[str, Any]] = []

    for agency, data_as_of in FEEDS:
        feed = GtfsFeed.load(agency_filter=agency)
        lines = sorted({route["route_short_name"].strip() for route in feed.routes})
        print(f"\n=== {agency} ({len(lines)} hat) ===")

        windows = build_service_windows(feed, lines, sources)
        headways = build_headways(feed, lines, sources)
        print(f"  {len(windows)} servis penceresi, {len(headways)} saat satiri")

        missing = sorted(
            line for line in lines if not any(window.line == line for window in windows)
        )
        if missing:
            print(f"  UYARI: tarife uretilemedi: {', '.join(missing)}")

        window_rows.extend(
            {
                "line": window.line,
                "direction_id": window.direction_id,
                "direction_name": window.direction_name,
                "day_type": window.day_type,
                "segment": window.segment,
                "station_key": window.station_key,
                "station_name": window.station_name,
                "first_s": window.first_s,
                "last_s": window.last_s,
                "first_clock": seconds_to_clock(window.first_s) or "",
                "last_clock": seconds_to_clock(window.last_s) or "",
                "trip_count": window.trip_count,
                "estimated": window.estimated,
                "source": window.source,
                "data_as_of": data_as_of,
            }
            for window in windows
        )
        headway_rows.extend(
            {
                "line": hour.line,
                "direction_id": hour.direction_id,
                "direction_name": hour.direction_name,
                "day_type": hour.day_type,
                "hour": hour.hour,
                "headway_s": hour.headway_s,
                "departures": hour.departures,
                "source": hour.source,
                "data_as_of": data_as_of,
            }
            for hour in headways
        )

    connection = connect()
    try:
        connection.execute(SCHEMA)

        def insert(table: str, rows: list[dict[str, Any]]) -> None:
            if not rows:
                return
            columns = list(rows[0])
            placeholders = ", ".join("?" for _ in columns)
            connection.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                [[row[column] for column in columns] for row in rows],
            )

        insert("rail_service_windows", window_rows)
        insert("rail_headways", headway_rows)
        connection.commit()

        print("\n=== Yazildi ===")
        for table in ("rail_service_windows", "rail_headways"):
            count = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"  {table:22} {count:>5} satir")

        print("\n=== Hat basina tarife kaynagi ===")
        rows = connection.execute(
            "SELECT line, any_value(source), count(*) FROM rail_headways "
            "GROUP BY line ORDER BY line"
        ).fetchall()
        for line, source, hours in rows:
            print(f"  {line:10} {source:32} {hours:>3} saat satiri")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
