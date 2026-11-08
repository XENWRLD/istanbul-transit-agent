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
import json
import statistics
import sys
import time
from collections import defaultdict
from itertools import combinations, pairwise
from pathlib import Path
from typing import Any

import httpx

from istanbul_agent.config import PROJECT_ROOT, get_settings
from istanbul_agent.store.db import connect
from istanbul_agent.store.geo import haversine_m, parse_coordinate
from istanbul_agent.store.gtfs import GtfsFeed, to_seconds
from istanbul_agent.store.rail_reference import _line_family, load_rail_reference
from istanbul_agent.store.rail_segments import build_segment_index
from istanbul_agent.store.rail_travel import GTFS_METRO_DATA_AS_OF, MARMARAY_DATA_AS_OF
from istanbul_agent.store.station_names import collapsed, normalize, token_key

METRO_API = "/MetroIstanbul/api/MetroMobile/V2"
RAIL_GEOJSON = PROJECT_ROOT / "data" / "geojson" / "rail_station_points.geojson"
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
DROP TABLE IF EXISTS rail_interchanges;
DROP TABLE IF EXISTS rail_transfers;
DROP TABLE IF EXISTS rail_stations;
DROP TABLE IF EXISTS rail_lines;

CREATE TABLE rail_lines (
    line           VARCHAR PRIMARY KEY,
    mode           VARCHAR NOT NULL,
    long_name      VARCHAR,
    in_gtfs        BOOLEAN NOT NULL,
    coords_reliable BOOLEAN NOT NULL,
    is_ring        BOOLEAN NOT NULL,
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
    coord_source   VARCHAR,
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

CREATE TABLE rail_interchanges (
    from_line      VARCHAR NOT NULL,
    from_key       VARCHAR NOT NULL,
    from_name      VARCHAR NOT NULL,
    to_line        VARCHAR NOT NULL,
    to_key         VARCHAR NOT NULL,
    to_name        VARCHAR NOT NULL,
    distance_m     DOUBLE NOT NULL,
    same_name      BOOLEAN NOT NULL,
    in_dataset_313 BOOLEAN NOT NULL,
    source         VARCHAR NOT NULL,
    PRIMARY KEY (from_line, from_key, to_line, to_key)
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


def load_geojson_coordinates() -> dict[str, tuple[float, float]]:
    """Station coordinates from dataset 6, keyed by normalised name.

    The live Metro API returns `Latitude: null` for M5's Veysel Karani,
    Hasanpaşa and Sultanbeyli -- both of its endpoints agree -- which is why
    journeys through them could not be estimated. This layer is refreshed
    weekly and has them. Only stations it marks as existing are taken;
    `PROJE_ASAMA` also covers lines still under construction.
    """
    if not RAIL_GEOJSON.exists():
        return {}
    data = json.loads(RAIL_GEOJSON.read_text(encoding="utf-8"))
    coordinates: dict[str, tuple[float, float]] = {}
    for feature in data.get("features", []):
        properties = feature.get("properties") or {}
        if "Mevcut" not in str(properties.get("PROJE_ASAMA", "")):
            continue
        name = properties.get("ISTASYON")
        geometry = feature.get("geometry") or {}
        if not name or geometry.get("type") != "Point":
            continue
        pair = geometry.get("coordinates") or []
        if len(pair) < 2:
            continue
        lon, lat = parse_coordinate(pair[0]), parse_coordinate(pair[1])
        if lat is None or lon is None:
            continue
        coordinates.setdefault(normalize(name), (lat, lon))
    return coordinates


def detect_rings(
    station_rows: dict[tuple[str, str], dict[str, Any]],
) -> set[str]:
    """Lines whose first and last listed stations sit next to each other.

    A ring line has no ends, so walking its station array in one direction
    misses the shorter way round. T3 closes with 122 m between its listed
    first and last stations against a 239 m typical spacing.
    """
    rings: set[str] = set()
    for line in {line for line, _ in station_rows}:
        ordered = sorted(
            (row for (candidate, _), row in station_rows.items() if candidate == line),
            key=lambda row: row["stop_order"] if row["stop_order"] is not None else 1 << 30,
        )
        if len(ordered) < 4:
            continue
        points = [
            (row["lat"], row["lon"])
            for row in ordered
            if row["lat"] is not None and row["lon"] is not None
        ]
        if len(points) < 4:
            continue
        spacings = [haversine_m(a[0], a[1], b[0], b[1]) for a, b in pairwise(points)]
        typical = statistics.median(spacings)
        closing = haversine_m(points[0][0], points[0][1], points[-1][0], points[-1][1])
        if typical > 0 and closing <= 2.5 * typical:
            rings.add(line)
    return rings


# An interchange is a physical fact: two stations on different lines close
# enough to walk between. Matching them by name instead invented 26 false
# interchanges -- M5 and Marmaray2 both have an "Altınşehir" 33 km apart, and
# M4 shares six names with Marmaray that are 1-2 km apart because M4 runs the
# E-5 while Marmaray follows the coast -- and missed the real M4-Marmaray
# interchange, whose two records are 7 m apart but spelled `Ayrılık Çeşmesi`
# and `Ayrılıkçeşme`. So names are not consulted at all here.
INTERCHANGE_RADIUS_M = 400.0


def build_interchanges(
    station_rows: dict[tuple[str, str], dict[str, Any]],
    sanctioned_pairs: set[frozenset[str]],
    radius: float = INTERCHANGE_RADIUS_M,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every walkable pair of stations on different lines.

    Returns (accepted, rejected_nearby). The second list holds pairs between
    `radius` and 1 km that dataset 313 names, so a human can see what the
    radius excluded rather than having it disappear silently.
    """
    positioned = [
        row for row in station_rows.values() if row["lat"] is not None and row["lon"] is not None
    ]
    accepted: list[dict[str, Any]] = []
    nearby: list[dict[str, Any]] = []

    for left, right in combinations(positioned, 2):
        if left["line"] == right["line"]:
            continue
        distance = haversine_m(left["lat"], left["lon"], right["lat"], right["lon"])
        if distance > 1000.0:
            continue
        backed = (
            frozenset({_line_family(left["line"]), _line_family(right["line"])}) in sanctioned_pairs
        )
        record = {
            "from_line": left["line"],
            "from_key": left["station_key"],
            "from_name": left["name"],
            "to_line": right["line"],
            "to_key": right["station_key"],
            "to_name": right["name"],
            "distance_m": round(distance, 1),
            "same_name": left["station_key"] == right["station_key"],
            "in_dataset_313": backed,
            "source": f"koordinat mesafesi ≤ {radius:.0f} m",
        }
        if distance <= radius:
            accepted.append(record)
            # Interchanges work both ways.
            accepted.append(
                {
                    **record,
                    "from_line": right["line"],
                    "from_key": right["station_key"],
                    "from_name": right["name"],
                    "to_line": left["line"],
                    "to_key": left["station_key"],
                    "to_name": left["name"],
                }
            )
        elif backed:
            nearby.append(record)

    return accepted, nearby


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
    # The integrity file marks a line unreliable both when its names sit on
    # the wrong coordinates and when it simply has no feed data at all. Only
    # the first is a coordinate problem; a line absent from the feed has
    # perfectly good live coordinates. Distinguish them by the note.
    unreliable = {
        line
        for line, row in integrity.items()
        if row.get("reliable", "").strip() == "hayır"
        and "GTFS'te hat yok" not in (row.get("note") or "")
    }
    schedule_source = read_decision_csv(
        PROJECT_ROOT / "datasets" / "metro_schedule_source.csv", "line"
    )

    print("GTFS okunuyor...")
    # Marmaray lives in the same feed under TCDD, not Metro İstanbul. Without
    # it there is no rail interchange between the two sides of the Bosphorus,
    # so Kadıköy to Taksim -- the project's headline question -- cannot be
    # answered at all. It carries its own, older date.
    feed = GtfsFeed.load("public_transport", agency_filter="Metro")
    marmaray = GtfsFeed.load("public_transport", agency_filter="TCDD")
    reference = load_rail_reference()

    marmaray_lines = {route["route_short_name"].strip() for route in marmaray.routes}

    stations_by_line: dict[str, list[dict[str, str]]] = {}
    mode_by_line: dict[str, str] = {}
    long_name_by_line: dict[str, str] = {}
    for route in [*feed.routes, *marmaray.routes]:
        line = route["route_short_name"].strip()
        source_feed = marmaray if line in marmaray_lines else feed
        sequence = source_feed.longest_trip_sequence(route["route_id"])
        stations_by_line[line] = [
            source_feed.stops[row["stop_id"]]
            for row in sequence
            if row["stop_id"] in source_feed.stops
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
                "coord_source": "GTFS 308",
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
                    existing["coord_source"] = "Metro API"
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
                "coord_source": "Metro API" if station["lat"] is not None else None,
                "source": "Metro API",
                "data_as_of": today,
            }

    # ---------------- coordinate gaps ----------------
    geojson_coordinates = load_geojson_coordinates()
    filled = 0
    for row in station_rows.values():
        if row["lat"] is not None:
            continue
        point = geojson_coordinates.get(row["station_key"])
        if point is None:
            continue
        row["lat"], row["lon"] = point
        row["coord_source"] = "Raylı Sistem İstasyon Noktaları (veri seti 6)"
        row["source"] = f"{row['source']} + veri seti 6"
        filled += 1
        print(f"  koordinat tamamlandı: {row['line']} {row['name']}")
    if geojson_coordinates:
        print(
            f"  GeoJSON'dan {filled} koordinat tamamlandı ({len(geojson_coordinates)} nokta okundu)"
        )
    still_missing = [r for r in station_rows.values() if r["lat"] is None]
    if still_missing:
        print(f"  UYARI: {len(still_missing)} istasyonun hâlâ koordinatı yok:")
        for row in still_missing[:8]:
            print(f"    {row['line']:6} {row['name']}")

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
    # Durations are collected for every line, including those whose
    # coordinates are untrustworthy: T3's recorded times are consistent
    # (41-99 s per hop, 8.6 min round the ring) and keyed to the right
    # stations. Only distance-derived estimates are refused on such a line.
    observed: dict[tuple[str, str], list[int]] = defaultdict(list)
    for route in [*feed.routes, *marmaray.routes]:
        line = route["route_short_name"].strip()
        source_feed = marmaray if line in marmaray_lines else feed
        for trip in source_feed.trips_for_route(route["route_id"]):
            sequence = source_feed.stop_times.get(trip["trip_id"], [])
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
                    "data_as_of": (
                        MARMARAY_DATA_AS_OF
                        if line in marmaray_lines
                        else (GTFS_METRO_DATA_AS_OF if duration is not None else today)
                    ),
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

    # ---------------- interchanges ----------------
    sanctioned = {
        frozenset({_line_family(transfer.from_line), _line_family(transfer.to_line)})
        for transfer in reference.rail_transfers()
        if transfer.from_line and transfer.to_line
    }
    interchange_rows, nearby_rows = build_interchanges(station_rows, sanctioned)
    print(
        f"\n  aktarma: {len(interchange_rows) // 2} çift "
        f"(≤ {INTERCHANGE_RADIUS_M:.0f} m), {len(nearby_rows)} çift sınırın dışında"
    )
    if nearby_rows:
        connected = {
            frozenset({_line_family(row["from_line"]), _line_family(row["to_line"])})
            for row in interchange_rows
        }
        orphaned = [
            row
            for row in nearby_rows
            if frozenset({_line_family(row["from_line"]), _line_family(row["to_line"])})
            not in connected
        ]
        if orphaned:
            print(
                f"  UYARI: {len(orphaned)} hat çiftinin 400 m içinde hiç bağlantısı yok, "
                "ama 313 aktarma olduğunu söylüyor:"
            )
            for row in orphaned:
                print(
                    f"    {row['from_line']:10} {row['from_name'][:24]:26} ↔ "
                    f"{row['to_line']:10} {row['to_name'][:24]:26} {row['distance_m']:>6.0f}m"
                )

    # ---------------- lines ----------------
    rings = detect_rings(station_rows)
    if rings:
        print(f"\n  ring hat: {', '.join(sorted(rings))}")

    line_rows: list[dict[str, Any]] = []
    for line in sorted({line for line, _ in station_rows}):
        count = sum(1 for candidate, _ in station_rows if candidate == line)
        in_gtfs = line in stations_by_line and bool(stations_by_line[line])
        is_marmaray = line in marmaray_lines
        notes: list[str] = []
        if line in unreliable:
            notes.append(
                "koordinatları güvenilmez (karar 0003); süreler kullanılır, "
                "koordinat tabanlı tahmin yapılmaz"
            )
        elif not in_gtfs:
            notes.append("GTFS'te yok; süreler tahmin edilir")
        if line in rings:
            notes.append("ring hat; iki yönden kısa olanı seçilir")
        if is_marmaray:
            notes.append("Marmaray (TCDD ajansı), beslemedeki tarihi daha eski")

        line_rows.append(
            {
                "line": line,
                "mode": mode_by_line.get(line) or mode_from_code(line),
                "long_name": long_name_by_line.get(line, ""),
                "in_gtfs": in_gtfs,
                "coords_reliable": line not in unreliable,
                "is_ring": line in rings,
                "station_count": count,
                "schedule_source": (schedule_source.get(line) or {}).get("source"),
                "source": "GTFS 308 (TCDD)" if is_marmaray else "GTFS 308 + Metro API",
                "data_as_of": (
                    MARMARAY_DATA_AS_OF
                    if is_marmaray
                    else (GTFS_METRO_DATA_AS_OF if in_gtfs else today)
                ),
                "note": "; ".join(notes) or None,
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
        insert("rail_interchanges", interchange_rows)
        connection.commit()

        print("\n=== Yazıldı ===")
        for table in (
            "rail_lines",
            "rail_stations",
            "rail_segments",
            "rail_transfers",
            "rail_interchanges",
        ):
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
            SELECT line, mode, station_count, in_gtfs, coords_reliable, is_ring, schedule_source
            FROM rail_lines ORDER BY line
            """
        ).fetchall():
            line, mode, count, in_gtfs, coords_ok, is_ring, source = row
            if not in_gtfs:
                flag = "GTFS yok"
            elif not coords_ok:
                flag = "koord. güvenilmez"
            else:
                flag = "güvenilir"
            print(
                f"  {line:10} {mode:10} {count:>3} istasyon  {flag:18}"
                f"tarife={source or '—':12}{'ring' if is_ring else ''}"
            )
    finally:
        connection.close()

    print(f"\n-> {get_settings().duckdb_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
