"""Build the rail tables in data/transit.duckdb.

Writes four tables, each row carrying where it came from and how old it is:

- `rail_stations`   -- one row per station per line, with coordinates
- `rail_segments`   -- adjacent pairs, with a GTFS duration and/or a 312 length
- `rail_transfers`  -- interchanges from dataset 313
- `rail_lines`      -- per-line metadata, including whether its feed data is
                       usable at all (T3's is not, decision 0003)

Station identity is the problem this has to solve: GTFS and the live API spell
names differently, and the 49 stations the 2020 feed never had exist only in
the API. So the table is keyed on the normalised name and records which
sources contributed each row.

    uv run python -m istanbul_agent.store.ingest_rail
    uv run python -m istanbul_agent.store.ingest_rail --no-live
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from collections import defaultdict
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx

from istanbul_agent.config import PROJECT_ROOT, get_settings
from istanbul_agent.store.db import connect
from istanbul_agent.store.geo import haversine_m, parse_coordinate
from istanbul_agent.store.gtfs import GtfsFeed, to_seconds
from istanbul_agent.store.rail_reference import load_rail_reference
from istanbul_agent.store.rail_segments import build_segment_index
from istanbul_agent.store.rail_travel import GTFS_METRO_DATA_AS_OF
from istanbul_agent.store.station_names import collapsed, normalize, token_key

METRO_API = "/MetroIstanbul/api/MetroMobile/V2"
NAME_MAP = PROJECT_ROOT / "datasets" / "station_name_map.csv"
INTEGRITY = PROJECT_ROOT / "datasets" / "metro_line_integrity.csv"

ROUTE_TYPES = {
    "0": "tramvay",
    "1": "metro",
    "2": "tren",
    "5": "teleferik",
    "6": "teleferik",
    "7": "funikuler",
}

# For lines the feed never had (F4, T5) the mode comes from the line code,
# which is the only thing available -- the live API does not state it.
CODE_MODE_PREFIX = (("TF", "teleferik"), ("F", "funikuler"), ("T", "tramvay"), ("M", "metro"))


def mode_from_code(line: str) -> str:
    for prefix, mode in CODE_MODE_PREFIX:
        if line.upper().startswith(prefix):
            return mode
    return "bilinmiyor"


MIN_SEGMENT_S, MAX_SEGMENT_S = 20, 900

SCHEMA = """
DROP TABLE IF EXISTS rail_segments;
DROP TABLE IF EXISTS rail_transfers;
DROP TABLE IF EXISTS rail_stations;
DROP TABLE IF EXISTS rail_lines;

CREATE TABLE rail_lines (
    line           VARCHAR PRIMARY KEY,
    mode           VARCHAR NOT NULL,
    long_name      VARCHAR,
    in_gtfs        BOOLEAN NOT NULL,
    gtfs_reliable  BOOLEAN NOT NULL,
    station_count  INTEGER NOT NULL,
    schedule_source VARCHAR,
    source         VARCHAR NOT NULL,
    data_as_of     VARCHAR NOT NULL,
    note           VARCHAR
);

CREATE TABLE rail_stations (
    station_key    VARCHAR NOT NULL,
    name           VARCHAR NOT NULL,
    line           VARCHAR NOT NULL,
    mode           VARCHAR NOT NULL,
    stop_order     INTEGER,
    gtfs_stop_id   VARCHAR,
    lat            DOUBLE,
    lon            DOUBLE,
    in_gtfs        BOOLEAN NOT NULL,
    in_live_api    BOOLEAN NOT NULL,
    is_spur        BOOLEAN NOT NULL,
    source         VARCHAR NOT NULL,
    data_as_of     VARCHAR NOT NULL,
    PRIMARY KEY (line, station_key)
);

CREATE TABLE rail_segments (
    line           VARCHAR NOT NULL,
    from_key       VARCHAR NOT NULL,
    to_key         VARCHAR NOT NULL,
    from_name      VARCHAR NOT NULL,
    to_name        VARCHAR NOT NULL,
    duration_s     INTEGER,
    observations   INTEGER,
    track_m        DOUBLE,
    straight_m     DOUBLE,
    duration_source VARCHAR,
    length_source  VARCHAR,
    source         VARCHAR NOT NULL,
    data_as_of     VARCHAR NOT NULL,
    PRIMARY KEY (line, from_key, to_key)
);

CREATE TABLE rail_transfers (
    station_key    VARCHAR NOT NULL,
    station_name   VARCHAR NOT NULL,
    from_line      VARCHAR NOT NULL,
    to_line        VARCHAR NOT NULL,
    to_is_rail     BOOLEAN NOT NULL,
    source         VARCHAR NOT NULL,
    data_as_of     VARCHAR NOT NULL
);
"""


def _feed_key_for(
    live_name: str, line: str, station_rows: dict[tuple[str, str], dict[str, Any]]
) -> str | None:
    """The feed's key for a live station spelled differently, or None.

    Only looks at rows already on the same line, so a loose key cannot pull
    in a station from elsewhere in the network.
    """
    candidates = [
        (station_key, row)
        for (candidate_line, station_key), row in station_rows.items()
        if candidate_line == line and row["in_gtfs"] and not row["in_live_api"]
    ]
    for key_fn in (collapsed, token_key):
        wanted = key_fn(live_name)
        hits = [station_key for station_key, row in candidates if key_fn(row["name"]) == wanted]
        if len(hits) == 1:
            return hits[0]
    return None


def read_decision_csv(path: Path, key: str) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return {row[key]: row for row in csv.DictReader(handle) if row.get(key)}


def fetch_live_network(client: httpx.Client, base: str) -> dict[str, list[dict[str, Any]]]:
    """Today's stations per line, from the working Metro endpoints."""
    response = client.get(f"{base}{METRO_API}/GetLines")
    response.raise_for_status()
    out: dict[str, list[dict[str, Any]]] = {}
    for line in response.json()["Data"]:
        stations_response = client.get(f"{base}{METRO_API}/GetStationById/{line['Id']}")
        rows: list[dict[str, Any]] = []
        if stations_response.status_code == 200:
            payload = stations_response.json()
            if payload.get("Success"):
                for station in sorted(payload.get("Data") or [], key=lambda s: s.get("Order") or 0):
                    detail = station.get("DetailInfo") or {}
                    rows.append(
                        {
                            "name": station.get("Description") or station["Name"],
                            "order": station.get("Order"),
                            "lat": parse_coordinate(detail.get("Latitude")),
                            "lon": parse_coordinate(detail.get("Longitude")),
                        }
                    )
        out[line["Name"].strip()] = rows
        time.sleep(0.2)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-live",
        action="store_true",
        help="skip the Metro API; build from GTFS and the spreadsheets only",
    )
    args = parser.parse_args()

    settings = get_settings()
    base = settings.ibb_api_base_url.rstrip("/")

    integrity = read_decision_csv(INTEGRITY, "line")
    unreliable = {
        line for line, row in integrity.items() if row.get("reliable", "").strip() == "hayır"
    }
    schedule_source = read_decision_csv(
        PROJECT_ROOT / "datasets" / "metro_schedule_source.csv", "line"
    )

    print("GTFS okunuyor...")
    feed = GtfsFeed.load("public_transport", agency_filter="Metro")
    reference = load_rail_reference()

    stations_by_line: dict[str, list[dict[str, str]]] = {}
    mode_by_line: dict[str, str] = {}
    long_name_by_line: dict[str, str] = {}
    for route in feed.routes:
        line = route["route_short_name"].strip()
        sequence = feed.longest_trip_sequence(route["route_id"])
        stations_by_line[line] = [
            feed.stops[row["stop_id"]] for row in sequence if row["stop_id"] in feed.stops
        ]
        mode_by_line[line] = ROUTE_TYPES.get(route["route_type"], "bilinmiyor")
        long_name_by_line[line] = route.get("route_long_name", "")

    usable = {line: stops for line, stops in stations_by_line.items() if line not in unreliable}
    index = build_segment_index(reference, usable)
    print(
        f"  {len(feed.routes)} hat, {len(feed.stops)} durak; "
        f"312 segment eşleşmesi {len(index.matched)}"
    )

    live: dict[str, list[dict[str, Any]]] = {}
    if not args.no_live:
        print("Canlı Metro API'den bugünkü ağ alınıyor...")
        try:
            with httpx.Client(timeout=settings.ibb_request_timeout) as client:
                live = fetch_live_network(client, base)
            print(f"  {len(live)} hat, {sum(len(v) for v in live.values())} istasyon")
        except Exception as exc:
            print(f"  UYARI: canlı API alınamadı ({type(exc).__name__}); GTFS ile devam")

    # ---------------- stations ----------------
    station_rows: dict[tuple[str, str], dict[str, Any]] = {}

    for line, stops in stations_by_line.items():
        for order, stop in enumerate(stops, start=1):
            key = normalize(stop["stop_name"])
            station_rows[(line, key)] = {
                "station_key": key,
                "name": stop["stop_name"],
                "line": line,
                "mode": mode_by_line.get(line, "bilinmiyor"),
                "stop_order": order,
                "gtfs_stop_id": stop["stop_id"],
                "lat": parse_coordinate(stop["stop_lat"]),
                "lon": parse_coordinate(stop["stop_lon"]),
                "in_gtfs": True,
                "in_live_api": False,
                "is_spur": False,
                "source": "GTFS 308",
                "data_as_of": GTFS_METRO_DATA_AS_OF,
            }

    # Manual matches confirmed by distance in decision 0003: the live API and
    # the feed use different names for one station, so the feed row has to be
    # merged into the live one instead of standing alongside it.
    manual_match: dict[tuple[str, str], str] = {}
    for row in read_decision_csv(NAME_MAP, "api_station").values():
        if row.get("decision") == "ELLE EŞLEME" and row.get("gtfs_station"):
            manual_match[(row["line"], normalize(row["api_station"]))] = normalize(
                row["gtfs_station"]
            )

    today = time.strftime("%Y-%m-%d")
    for line, stations in live.items():
        for station in stations:
            key = normalize(station["name"])
            existing = station_rows.get((line, key))

            if existing is None:
                # Does the feed hold this station under another spelling? Try
                # the same ladder the distance validation used (decision
                # 0003): exact name, then spaces collapsed, then word order
                # ignored. Without it `Ayşe Kadın` and `Ayşekadın` become two
                # stations and every journey through them gains a phantom hop.
                feed_key = manual_match.get((line, key)) or _feed_key_for(
                    station["name"], line, station_rows
                )
                if feed_key is not None:
                    feed_row = station_rows.pop((line, feed_key), None)
                    if feed_row is not None:
                        feed_row["station_key"] = key
                        station_rows[(line, key)] = feed_row
                        existing = feed_row

            if existing is not None:
                existing["in_live_api"] = True
                # Prefer the live name and position: they are current.
                existing["name"] = station["name"]
                existing["source"] = "GTFS 308 + Metro API"
                if station["lat"] is not None:
                    existing["lat"] = station["lat"]
                    existing["lon"] = station["lon"]
                if station["order"] is not None:
                    existing["stop_order"] = station["order"]
                continue
            station_rows[(line, key)] = {
                "station_key": key,
                "name": station["name"],
                "line": line,
                "mode": mode_by_line.get(line) or mode_from_code(line),
                "stop_order": station["order"],
                "gtfs_stop_id": None,
                "lat": station["lat"],
                "lon": station["lon"],
                "in_gtfs": False,
                "in_live_api": True,
                "is_spur": False,
                "source": "Metro API",
                "data_as_of": today,
            }

    # ---------------- branch stations ----------------
    # The live API lists some stations mid-line that physically sit on a
    # branch: M2 reports Seyrantepe between Sanayi Mahallesi and İTÜ-Ayazağa,
    # but it is on the M2A spur. The feed is the evidence -- if those two
    # neighbours are adjacent in GTFS, nothing was inserted between them, so
    # the live station is a branch and a journey past it must not detour
    # through it. A station appended at the end of a line (the post-2020
    # extensions) breaks no adjacency and is left alone.
    gtfs_adjacent: set[frozenset[str]] = set()
    for line, stops in stations_by_line.items():
        for left, right in pairwise(stops):
            gtfs_adjacent.add(
                frozenset(
                    {
                        f"{line}|{normalize(left['stop_name'])}",
                        f"{line}|{normalize(right['stop_name'])}",
                    }
                )
            )

    spur_count = 0
    for line in sorted({line for line, _ in station_rows}):
        ordered = sorted(
            (row for (candidate, _), row in station_rows.items() if candidate == line),
            key=lambda row: row["stop_order"] if row["stop_order"] is not None else 1 << 30,
        )
        for before, middle, after in zip(ordered, ordered[1:], ordered[2:], strict=False):
            if middle["in_gtfs"] or not middle["in_live_api"]:
                continue
            pair = frozenset(
                {
                    f"{line}|{normalize(before['name'])}",
                    f"{line}|{normalize(after['name'])}",
                }
            )
            if pair in gtfs_adjacent:
                middle["is_spur"] = True
                spur_count += 1
                print(
                    f"  şube istasyonu: {line} {middle['name']} "
                    f"({before['name']} ile {after['name']} GTFS'te komşu)"
                )
    if spur_count:
        print(f"  {spur_count} şube istasyonu işaretlendi")

    # ---------------- segments ----------------
    observed: dict[tuple[str, str], list[int]] = defaultdict(list)
    for route in feed.routes:
        line = route["route_short_name"].strip()
        if line in unreliable:
            continue
        for trip in feed.trips_for_route(route["route_id"]):
            sequence = feed.stop_times.get(trip["trip_id"], [])
            for left, right in pairwise(sequence):
                depart = to_seconds(left["departure_time"]) or to_seconds(left["arrival_time"])
                arrive = to_seconds(right["arrival_time"]) or to_seconds(right["departure_time"])
                if depart is None or arrive is None:
                    continue
                delta = arrive - depart
                if MIN_SEGMENT_S <= delta <= MAX_SEGMENT_S:
                    observed[(left["stop_id"], right["stop_id"])].append(delta)

    segment_rows: list[dict[str, Any]] = []
    for line in sorted({line for line, _ in station_rows}):
        line_stations = sorted(
            (
                row
                for (candidate, _), row in station_rows.items()
                if candidate == line and not row["is_spur"]
            ),
            key=lambda row: row["stop_order"] if row["stop_order"] is not None else 1 << 30,
        )
        for left, right in pairwise(line_stations):
            durations = []
            if left["gtfs_stop_id"] and right["gtfs_stop_id"]:
                durations = observed.get(
                    (left["gtfs_stop_id"], right["gtfs_stop_id"]), []
                ) or observed.get((right["gtfs_stop_id"], left["gtfs_stop_id"]), [])
            duration = int(statistics.median(durations)) if durations else None

            track = None
            if left["gtfs_stop_id"] and right["gtfs_stop_id"]:
                track = index.track_m(left["gtfs_stop_id"], right["gtfs_stop_id"])
            if track is None:
                track = reference.segment_metres(left["name"], right["name"], line)

            straight = None
            if None not in (left["lat"], left["lon"], right["lat"], right["lon"]):
                straight = round(
                    haversine_m(left["lat"], left["lon"], right["lat"], right["lon"]), 1
                )

            sources = []
            if duration is not None:
                sources.append("GTFS 308")
            if track is not None:
                sources.append("312")
            if not sources:
                sources.append("Metro API koordinatı")

            segment_rows.append(
                {
                    "line": line,
                    "from_key": left["station_key"],
                    "to_key": right["station_key"],
                    "from_name": left["name"],
                    "to_name": right["name"],
                    "duration_s": duration,
                    "observations": len(durations) or None,
                    "track_m": track,
                    "straight_m": straight,
                    "duration_source": "GTFS 308" if duration is not None else None,
                    "length_source": "312"
                    if track is not None
                    else ("koordinat" if straight else None),
                    "source": " + ".join(sources),
                    "data_as_of": GTFS_METRO_DATA_AS_OF if duration is not None else today,
                }
            )

    # ---------------- transfers ----------------
    transfer_rows = [
        {
            "station_key": normalize(transfer.from_station),
            "station_name": transfer.from_station,
            "from_line": transfer.from_line or transfer.line_family,
            "to_line": transfer.to_line or transfer.to_station,
            "to_is_rail": transfer.to_is_rail,
            "source": "313",
            "data_as_of": "2025-03-07",
        }
        for transfer in reference.transfers
    ]

    # ---------------- lines ----------------
    line_rows: list[dict[str, Any]] = []
    for line in sorted({line for line, _ in station_rows}):
        count = sum(1 for candidate, _ in station_rows if candidate == line)
        in_gtfs = line in stations_by_line and bool(stations_by_line[line])
        note = ""
        if line in unreliable:
            note = "GTFS adları koordinatlarla uyuşmuyor (karar 0003); tarife kullanılmıyor"
        elif not in_gtfs:
            note = "GTFS'te yok; süreler tahmin edilir"
        line_rows.append(
            {
                "line": line,
                "mode": mode_by_line.get(line) or mode_from_code(line),
                "long_name": long_name_by_line.get(line, ""),
                "in_gtfs": in_gtfs,
                "gtfs_reliable": in_gtfs and line not in unreliable,
                "station_count": count,
                "schedule_source": (schedule_source.get(line) or {}).get("source"),
                "source": "GTFS 308 + Metro API",
                "data_as_of": GTFS_METRO_DATA_AS_OF if in_gtfs else today,
                "note": note or None,
            }
        )

    # ---------------- write ----------------
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

        insert("rail_lines", line_rows)
        insert("rail_stations", list(station_rows.values()))
        insert("rail_segments", segment_rows)
        insert("rail_transfers", transfer_rows)
        connection.commit()

        print("\n=== Yazıldı ===")
        for table in ("rail_lines", "rail_stations", "rail_segments", "rail_transfers"):
            count = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"  {table:16} {count:>5} satır")

        print("\n=== Segment kapsamı ===")
        rows = connection.execute(
            """
            SELECT
                count(*) AS total,
                count(duration_s) AS with_duration,
                count(track_m) AS with_track,
                count(straight_m) AS with_straight
            FROM rail_segments
            """
        ).fetchone()
        print(
            f"  toplam {rows[0]}, GTFS süresi {rows[1]}, 312 uzunluğu {rows[2]}, koordinat {rows[3]}"
        )

        print("\n=== Hat durumu ===")
        for row in connection.execute(
            """
            SELECT line, mode, station_count, in_gtfs, gtfs_reliable, schedule_source
            FROM rail_lines ORDER BY line
            """
        ).fetchall():
            flag = "güvenilir" if row[4] else ("GTFS yok" if not row[3] else "GÜVENİLMEZ")
            print(
                f"  {row[0]:6} {row[1]:10} {row[2]:>3} istasyon  {flag:12} tarife={row[5] or '—'}"
            )
    finally:
        connection.close()

    print(f"\n-> {get_settings().duckdb_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
