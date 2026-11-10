"""Build the bus and stop-location tables in data/transit.duckdb.

Kaynaklar: İETT GTFS beslemesi (veri seti 75, Nisan 2026) ve dört nokta
katmanı — İETT otobüs durakları (128), deniz iskeleleri (15), minibüs
durakları (13), taksi-dolmuş durakları (9).

Yazılan tablolar:

- `bus_stops`        -- İETT GTFS durakları, koordinatları onarılmış
- `bus_routes`       -- hat varyantları (9279 satır, 1097 hat kodu)
- `bus_calendar`     -- servis günleri; bu beslemede tarih penceresi geçerli
- `bus_trips`        -- sefer başına kalkış/varış saati ve durak sayısı
- `bus_route_stops`  -- hat + yön başına sıralı durak listesi
- `transit_places`   -- tüm modların aranabilir nokta tablosu
- `ingest_meta`      -- tablo başına kaynak, veri tarihi, satır sayısı

**Ham `stop_times` saklanmıyor.** 6 155 692 satırın yalnızca %4.4'ünde saat
var ve o saatler sefer başına tam ikidir: ilk duraktan kalkış, son durağa
varış (ölçüm: `scripts/inspect_iett_times.py`). Ara duraklarda saat
olmadığı için ham tabloyu saklamak, `bus_trips` artı `bus_route_stops`'un
vermediği hiçbir şeyi vermez; onun yerine sefer özeti ve hat başına en
eksiksiz seferin durak sırası yazılır (karar 0007).

    uv run python -m istanbul_agent.store.ingest
    uv run python -m istanbul_agent.store.ingest --skip-places
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output
from istanbul_agent.store.db import connect
from istanbul_agent.store.geo import (
    ISTANBUL_LAT_RANGE,
    ISTANBUL_LON_RANGE,
    parse_coordinate,
    parse_grouped_coordinate,
)
from istanbul_agent.store.gtfs import dialect_for, iter_table, read_table, to_seconds
from istanbul_agent.store.station_names import collapsed, normalize, token_key

GEOJSON_DIR = PROJECT_ROOT / "data" / "geojson"
STOPS_MANIFEST = GEOJSON_DIR / "_stops_manifest.json"

IETT_FEED = "iett"

# slug -> (mod, ad alanı, ek alan)
POINT_LAYERS: dict[str, tuple[str, str, str | None]] = {
    "ferry_piers": ("vapur", "ISKELE_AD", None),
    "minibus_stops": ("minibus", "DURAK_ADI", None),
    "dolmus_stops": ("dolmus", "DURAK_ADI", "ACIKLAMA"),
}

SCHEMA = """
DROP TABLE IF EXISTS transit_places;
DROP TABLE IF EXISTS bus_route_stops;
DROP TABLE IF EXISTS bus_trips;
DROP TABLE IF EXISTS bus_calendar;
DROP TABLE IF EXISTS bus_routes;
DROP TABLE IF EXISTS bus_stops;

CREATE TABLE bus_stops (
    stop_id      VARCHAR PRIMARY KEY,
    stop_code    VARCHAR,
    name         VARCHAR NOT NULL,
    name_key     VARCHAR NOT NULL,
    name_key_flat VARCHAR NOT NULL,
    name_key_sorted VARCHAR NOT NULL,
    direction    VARCHAR,
    lat          DOUBLE NOT NULL,
    lon          DOUBLE NOT NULL,
    source       VARCHAR NOT NULL,
    data_as_of   VARCHAR NOT NULL
);

CREATE TABLE bus_routes (
    route_id     VARCHAR PRIMARY KEY,
    line_code    VARCHAR NOT NULL,
    long_name    VARCHAR,
    description  VARCHAR,
    route_code   VARCHAR,
    route_type   VARCHAR,
    source       VARCHAR NOT NULL,
    data_as_of   VARCHAR NOT NULL
);

CREATE TABLE bus_calendar (
    service_id   VARCHAR PRIMARY KEY,
    service_name VARCHAR,
    monday       BOOLEAN, tuesday BOOLEAN, wednesday BOOLEAN, thursday BOOLEAN,
    friday       BOOLEAN, saturday BOOLEAN, sunday BOOLEAN,
    start_date   VARCHAR,
    end_date     VARCHAR,
    window_usable BOOLEAN NOT NULL,
    source       VARCHAR NOT NULL,
    data_as_of   VARCHAR NOT NULL
);

CREATE TABLE bus_trips (
    trip_id        VARCHAR PRIMARY KEY,
    route_id       VARCHAR NOT NULL,
    line_code      VARCHAR,
    service_id     VARCHAR,
    direction_id   VARCHAR,
    headsign       VARCHAR,
    first_stop_id  VARCHAR,
    last_stop_id   VARCHAR,
    departure_s    INTEGER,
    arrival_s      INTEGER,
    duration_s     INTEGER,
    stop_count     INTEGER NOT NULL,
    source         VARCHAR NOT NULL,
    data_as_of     VARCHAR NOT NULL
);

CREATE TABLE bus_route_stops (
    route_id       VARCHAR NOT NULL,
    line_code      VARCHAR,
    direction_id   VARCHAR,
    stop_sequence  INTEGER NOT NULL,
    stop_id        VARCHAR NOT NULL,
    trip_id        VARCHAR NOT NULL,
    trip_count     INTEGER NOT NULL,
    variant_count  INTEGER NOT NULL,
    source         VARCHAR NOT NULL,
    data_as_of     VARCHAR NOT NULL
);

CREATE TABLE transit_places (
    place_id     VARCHAR NOT NULL,
    mode         VARCHAR NOT NULL,
    name         VARCHAR NOT NULL,
    name_key     VARCHAR NOT NULL,
    name_key_flat VARCHAR NOT NULL,
    name_key_sorted VARCHAR NOT NULL,
    code         VARCHAR,
    detail       VARCHAR,
    lat          DOUBLE NOT NULL,
    lon          DOUBLE NOT NULL,
    source       VARCHAR NOT NULL,
    data_as_of   VARCHAR NOT NULL,
    is_stale     BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_meta (
    table_name   VARCHAR NOT NULL,
    source       VARCHAR NOT NULL,
    data_as_of   VARCHAR NOT NULL,
    rows         BIGINT NOT NULL,
    is_stale     BOOLEAN NOT NULL,
    note         VARCHAR,
    ingested_at  VARCHAR NOT NULL
);
"""


@dataclass
class TripSummary:
    """Bir seferin özeti; ham `stop_times` yerine bu saklanır."""

    trip_id: str
    stops: list[tuple[int, str]]
    first_departure_s: int | None = None
    last_arrival_s: int | None = None

    @property
    def ordered(self) -> list[tuple[int, str]]:
        return sorted(self.stops)

    @property
    def signature(self) -> tuple[str, ...]:
        return tuple(stop_id for _, stop_id in self.ordered)


def layer_metadata() -> dict[str, dict[str, Any]]:
    """İndirme manifest'inden katman başına kaynak ve veri tarihi."""
    if not STOPS_MANIFEST.exists():
        return {}
    payload = json.loads(STOPS_MANIFEST.read_text(encoding="utf-8"))
    return {entry["slug"]: entry for entry in payload.get("layers", []) if "slug" in entry}


def inside_istanbul(lat: float, lon: float) -> bool:
    return (
        ISTANBUL_LAT_RANGE[0] <= lat <= ISTANBUL_LAT_RANGE[1]
        and ISTANBUL_LON_RANGE[0] <= lon <= ISTANBUL_LON_RANGE[1]
    )


# ------------------------------------------------------------------ GTFS


def load_bus_stops() -> tuple[list[tuple[Any, ...]], dict[str, int]]:
    """İETT duraklarını oku; koordinatı onarılamayan satırı atla."""
    dialect = dialect_for(IETT_FEED)
    rows: list[tuple[Any, ...]] = []
    dropped_coordinate = dropped_outside = dropped_name = 0
    for stop in read_table("stops", IETT_FEED):
        name = (stop.get("stop_name") or "").strip()
        lat = parse_grouped_coordinate(stop.get("stop_lat"))
        lon = parse_grouped_coordinate(stop.get("stop_lon"))
        if not name:
            dropped_name += 1
            continue
        if lat is None or lon is None:
            dropped_coordinate += 1
            continue
        if not inside_istanbul(lat, lon):
            dropped_outside += 1
            continue
        rows.append(
            (
                stop["stop_id"],
                (stop.get("stop_code") or "").strip() or None,
                name,
                normalize(name),
                collapsed(name),
                token_key(name),
                (stop.get("stop_desc") or "").strip() or None,
                lat,
                lon,
                dialect.source,
                dialect.data_as_of,
            )
        )
    return rows, {
        "dropped_coordinate": dropped_coordinate,
        "dropped_outside_istanbul": dropped_outside,
        "dropped_empty_name": dropped_name,
    }


def load_bus_routes() -> tuple[list[tuple[Any, ...]], dict[str, str]]:
    """Hat varyantlarını oku ve `route_id -> hat kodu` eşlemesini döndür."""
    dialect = dialect_for(IETT_FEED)
    rows: list[tuple[Any, ...]] = []
    line_of_route: dict[str, str] = {}
    for route in read_table("routes", IETT_FEED):
        route_id = route.get("route_id") or ""
        if not route_id:
            continue
        line_code = (route.get("route_short_name") or "").strip()
        line_of_route[route_id] = line_code
        rows.append(
            (
                route_id,
                line_code,
                (route.get("route_long_name") or "").strip() or None,
                (route.get("route_desc") or "").strip() or None,
                (route.get("route_code") or "").strip() or None,
                (route.get("route_type") or "").strip() or None,
                dialect.source,
                dialect.data_as_of,
            )
        )
    return rows, line_of_route


def load_bus_calendar() -> list[tuple[Any, ...]]:
    dialect = dialect_for(IETT_FEED)
    rows: list[tuple[Any, ...]] = []
    for service in read_table("calendar", IETT_FEED):
        rows.append(
            (
                service.get("service_id"),
                (service.get("service_name") or "").strip() or None,
                *[
                    service.get(day) == "1"
                    for day in (
                        "monday",
                        "tuesday",
                        "wednesday",
                        "thursday",
                        "friday",
                        "saturday",
                        "sunday",
                    )
                ],
                (service.get("start_date") or "").strip() or None,
                (service.get("end_date") or "").strip() or None,
                dialect.calendar_window_usable,
                dialect.source,
                dialect.data_as_of,
            )
        )
    return rows


def summarise_trips() -> tuple[dict[str, TripSummary], dict[str, int]]:
    """`stop_times`'ı bir kez akıtarak sefer özetlerini çıkar.

    Besleme sefer başına iki saat veriyor (ilk duraktan kalkış, son durağa
    varış); saatsiz satırlar yalnızca durak sırasına katkı sağlıyor.
    """
    summaries: dict[str, TripSummary] = {}
    current: TripSummary | None = None
    rows = timed = 0
    for row in iter_table("stop_times", IETT_FEED):
        rows += 1
        trip_id = row["trip_id"]
        if current is None or current.trip_id != trip_id:
            if current is not None:
                summaries[current.trip_id] = current
            current = summaries.pop(trip_id, None) or TripSummary(trip_id=trip_id, stops=[])
        try:
            sequence = int(row["stop_sequence"])
        except (KeyError, TypeError, ValueError):
            continue
        current.stops.append((sequence, row["stop_id"]))
        departure = to_seconds(row.get("departure_time"))
        arrival = to_seconds(row.get("arrival_time"))
        if departure is not None or arrival is not None:
            timed += 1
            if current.first_departure_s is None and departure is not None:
                current.first_departure_s = departure
            if arrival is not None:
                current.last_arrival_s = arrival
    if current is not None:
        summaries[current.trip_id] = current
    return summaries, {"stop_times_rows": rows, "timed_rows": timed}


def build_trip_rows(
    summaries: dict[str, TripSummary], line_of_route: dict[str, str]
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], dict[str, int]]:
    """Sefer özetlerini ve hat+yön başına sıralı durak listesini satırlara çevir."""
    dialect = dialect_for(IETT_FEED)
    trips = read_table("trips", IETT_FEED)
    trip_rows: list[tuple[Any, ...]] = []
    # (route_id, direction_id) -> en eksiksiz sefer
    best: dict[tuple[str, str], TripSummary] = {}
    counts: dict[tuple[str, str], int] = defaultdict(int)
    signatures: dict[tuple[str, str], set[tuple[str, ...]]] = defaultdict(set)
    missing_summary = 0

    for trip in trips:
        trip_id = trip.get("trip_id") or ""
        summary = summaries.get(trip_id)
        if summary is None:
            missing_summary += 1
            continue
        route_id = trip.get("route_id") or ""
        direction = (trip.get("direction_id") or "").strip()
        line_code = line_of_route.get(route_id)
        ordered = summary.ordered
        first_stop = ordered[0][1] if ordered else None
        last_stop = ordered[-1][1] if ordered else None
        duration = (
            summary.last_arrival_s - summary.first_departure_s
            if summary.first_departure_s is not None and summary.last_arrival_s is not None
            else None
        )
        trip_rows.append(
            (
                trip_id,
                route_id,
                line_code,
                (trip.get("service_id") or "").strip() or None,
                direction or None,
                (trip.get("trip_headsign") or "").strip() or None,
                first_stop,
                last_stop,
                summary.first_departure_s,
                summary.last_arrival_s,
                duration,
                len(ordered),
                dialect.source,
                dialect.data_as_of,
            )
        )
        key = (route_id, direction)
        counts[key] += 1
        signatures[key].add(summary.signature)
        incumbent = best.get(key)
        if incumbent is None or len(summary.stops) > len(incumbent.stops):
            best[key] = summary

    route_stop_rows: list[tuple[Any, ...]] = []
    for (route_id, direction), summary in best.items():
        for sequence, stop_id in summary.ordered:
            route_stop_rows.append(
                (
                    route_id,
                    line_of_route.get(route_id),
                    direction or None,
                    sequence,
                    stop_id,
                    summary.trip_id,
                    counts[(route_id, direction)],
                    len(signatures[(route_id, direction)]),
                    dialect.source,
                    dialect.data_as_of,
                )
            )
    return (
        trip_rows,
        route_stop_rows,
        {
            "trips": len(trips),
            "trips_without_stop_times": missing_summary,
            "route_directions": len(best),
        },
    )


# ------------------------------------------------------------------ GeoJSON


def read_points(slug: str, name_field: str, detail_field: str | None) -> list[dict[str, Any]]:
    """Bir nokta katmanını oku; noktası olmayan özelliği atla."""
    path = GEOJSON_DIR / f"{slug}.geojson"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    points: list[dict[str, Any]] = []
    for index, feature in enumerate(payload.get("features", [])):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Point":
            continue
        coordinates = geometry.get("coordinates") or []
        if len(coordinates) < 2:
            continue
        lon, lat = parse_coordinate(coordinates[0]), parse_coordinate(coordinates[1])
        if lat is None or lon is None or not inside_istanbul(lat, lon):
            continue
        properties = feature.get("properties") or {}
        name = str(properties.get(name_field) or "").strip()
        if not name:
            continue
        points.append(
            {
                "index": index,
                "name": name,
                "detail": str(properties.get(detail_field) or "").strip() or None
                if detail_field
                else None,
                "lat": lat,
                "lon": lon,
                "properties": properties,
            }
        )
    return points


def rail_stations_available(connection: duckdb.DuckDBPyConnection) -> bool:
    """`ingest_rail` çalıştırılmış mı?"""
    found = connection.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'rail_stations'"
    ).fetchone()
    return bool(found and found[0])


def build_places(
    bus_stop_rows: list[tuple[Any, ...]], connection: duckdb.DuckDBPyConnection
) -> tuple[list[tuple[Any, ...]], dict[str, int]]:
    """Tüm modları tek aranabilir nokta tablosunda birleştir."""
    metadata = layer_metadata()
    rows: list[tuple[Any, ...]] = []
    counts: dict[str, int] = {}

    dialect = dialect_for(IETT_FEED)
    for stop in bus_stop_rows:
        stop_id, stop_code, name, key, flat, sorted_key = (
            stop[0],
            stop[1],
            stop[2],
            stop[3],
            stop[4],
            stop[5],
        )
        rows.append(
            (
                f"bus:{stop_id}",
                "otobus",
                name,
                key,
                flat,
                sorted_key,
                stop_code,
                stop[6],
                stop[7],
                stop[8],
                dialect.source,
                dialect.data_as_of,
                dialect.is_stale,
            )
        )
    counts["otobus"] = len(bus_stop_rows)

    for slug, (mode, name_field, detail_field) in POINT_LAYERS.items():
        entry = metadata.get(slug, {})
        source = entry.get("title") or slug
        dataset_no = entry.get("dataset_no")
        if dataset_no:
            source = f"{source} (veri seti {dataset_no})"
        data_as_of = entry.get("data_as_of") or "bilinmiyor"
        points = read_points(slug, name_field, detail_field)
        for point in points:
            name = point["name"]
            rows.append(
                (
                    f"{mode}:{point['index']}",
                    mode,
                    name,
                    normalize(name),
                    collapsed(name),
                    token_key(name),
                    None,
                    point["detail"],
                    point["lat"],
                    point["lon"],
                    source,
                    data_as_of,
                    False,
                )
            )
        counts[mode] = len(points)

    # Raylı sistem istasyonlarını `ingest_rail` yazıyor; burada yeniden
    # çözümlenmiyor, o tablodan alınıyor. Tablo **yoksa** (ingest_rail hiç
    # çalışmamış) atlanıyor, ama sorgu hatası yutulmuyor: ilk denemede
    # kolon adı yanlıştı (`station`, doğrusu `name`) ve hata yutulduğu için
    # tablo sessizce raylı sistem içermedi.
    rail_rows = 0
    if rail_stations_available(connection):
        rail = connection.execute(
            """
            SELECT name, line, mode, lat, lon, coord_source, data_as_of
            FROM rail_stations
            WHERE lat IS NOT NULL AND lon IS NOT NULL
            ORDER BY name, line
            """
        ).fetchall()
        # Nokta tablosunda önemli olan **koordinatın** kaynağı, satırın
        # kaynağı değil: 198 istasyonun satır tarihi 2020 beslemesinden
        # geliyor ama koordinatı canlı Metro API'den. İlk sürüm satırın
        # tarihini yazıp `is_stale=false` diyordu; bu, 2020 tarihli bir
        # konumu güncel göstermekle aynı hata sınıfıydı.
        live_date = connection.execute(
            """
            SELECT max(data_as_of) FROM rail_stations
            WHERE coord_source IS NOT NULL AND coord_source NOT LIKE 'GTFS%'
            """
        ).fetchone()
        live_as_of = (live_date[0] if live_date else None) or "bilinmiyor"

        # Aynı istasyon birden çok hatta geçiyor; nokta tablosunda bir kez
        # dursun ama hangi hatlar olduğu `code` alanında görünsün.
        lines_by_station: dict[str, list[str]] = defaultdict(list)
        best_by_station: dict[str, tuple[Any, ...]] = {}
        for name, line, mode, lat, lon, coord_source, data_as_of in rail:
            key = normalize(name)
            if line:
                lines_by_station[key].append(line)
            best_by_station.setdefault(key, (name, mode, lat, lon, coord_source, data_as_of))
        for key, (name, mode, lat, lon, coord_source, data_as_of) in best_by_station.items():
            from_feed = (coord_source or "").startswith("GTFS")
            rows.append(
                (
                    f"rayli:{key}",
                    mode or "rayli",
                    name,
                    key,
                    collapsed(name),
                    token_key(name),
                    ", ".join(sorted(set(lines_by_station[key]))) or None,
                    f"konum kaynağı: {coord_source}" if coord_source else None,
                    float(lat),
                    float(lon),
                    coord_source or "raylı sistem tabloları",
                    (data_as_of or "bilinmiyor") if from_feed else live_as_of,
                    from_feed,
                )
            )
            rail_rows += 1
    counts["rayli"] = rail_rows
    return rows, counts


# ------------------------------------------------------------------ writing


def record_meta(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    source: str,
    data_as_of: str,
    rows: int,
    *,
    is_stale: bool = False,
    note: str | None = None,
) -> None:
    connection.execute("DELETE FROM ingest_meta WHERE table_name = ?", [table])
    connection.execute(
        """
        INSERT INTO ingest_meta
            (table_name, source, data_as_of, rows, is_stale, note, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            table,
            source,
            data_as_of,
            rows,
            is_stale,
            note,
            time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        ],
    )


def insert(connection: duckdb.DuckDBPyConnection, table: str, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    placeholders = ", ".join("?" * len(rows[0]))
    connection.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)


PLACES_SCHEMA = SCHEMA[SCHEMA.index("CREATE TABLE transit_places") :]


def rebuild_places_only(database: Path | None, dialect: Any) -> int:
    """Yalnızca `transit_places`'i yeniden kur, `bus_stops`'tan okuyarak."""
    connection = connect(database)
    try:
        existing = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'bus_stops'"
        ).fetchone()
        if not (existing and existing[0]):
            print("bus_stops tablosu yok; önce ingest'i tam çalıştır")
            return 1
        stop_rows = connection.execute(
            """
            SELECT stop_id, stop_code, name, name_key, name_key_flat, name_key_sorted,
                   direction, lat, lon, source, data_as_of
            FROM bus_stops
            """
        ).fetchall()
        connection.execute("DROP TABLE IF EXISTS transit_places")
        connection.execute(PLACES_SCHEMA)
        place_rows, place_counts = build_places(list(stop_rows), connection)
        insert(connection, "transit_places", place_rows)
        record_meta(
            connection,
            "transit_places",
            "İETT GTFS + nokta katmanları (128, 15, 13, 9) + raylı sistem tabloları",
            dialect.data_as_of,
            len(place_rows),
            note=", ".join(f"{mode}={count}" for mode, count in sorted(place_counts.items())),
        )
        connection.commit()
    finally:
        connection.close()
    print(
        "  transit_places: "
        + ", ".join(f"{mode} {count}" for mode, count in sorted(place_counts.items()))
        + f" — toplam {len(place_rows)}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--skip-places", action="store_true", help="transit_places tablosunu atla")
    parser.add_argument(
        "--only-places",
        action="store_true",
        help="yalnızca transit_places'i yeniden kur (otobüs tabloları yerinde kalır)",
    )
    args = parser.parse_args()

    dialect = dialect_for(IETT_FEED)
    feed_path = PROJECT_ROOT / "data" / "gtfs" / IETT_FEED
    if not feed_path.exists():
        print(f"İETT beslemesi yok: {feed_path}")
        print("  uv run python scripts/download_gtfs.py --feed iett")
        return 1

    started = time.perf_counter()
    print(f"=== {dialect.source}  (data_as_of={dialect.data_as_of}) ===")

    if args.only_places:
        # `stop_times` akıtması 8 dakika sürüyor; nokta tablosunu düzeltmek
        # için onu yeniden çalıştırmak gerekmiyor.
        return rebuild_places_only(args.database, dialect)

    stop_rows, stop_stats = load_bus_stops()
    print(
        f"  duraklar: {len(stop_rows)} satır; atılan — koordinat "
        f"{stop_stats['dropped_coordinate']}, sınır dışı "
        f"{stop_stats['dropped_outside_istanbul']}, adsız "
        f"{stop_stats['dropped_empty_name']}"
    )

    route_rows, line_of_route = load_bus_routes()
    line_codes = {code for code in line_of_route.values() if code}
    print(f"  hatlar: {len(route_rows)} varyant, {len(line_codes)} hat kodu")

    calendar_rows = load_bus_calendar()
    print(
        f"  servis günleri: {len(calendar_rows)} satır, tarih penceresi "
        f"{'geçerli' if dialect.calendar_window_usable else 'dolmuş'}"
    )

    print("  stop_times akıtılıyor (150 MB)...", flush=True)
    summaries, time_stats = summarise_trips()
    print(
        f"    {time_stats['stop_times_rows']} satır, {len(summaries)} sefer, "
        f"saatli satır {time_stats['timed_rows']}"
    )

    trip_rows, route_stop_rows, trip_stats = build_trip_rows(summaries, line_of_route)
    print(
        f"  seferler: {len(trip_rows)} satır (stop_times'ta olmayan "
        f"{trip_stats['trips_without_stop_times']}), hat+yön "
        f"{trip_stats['route_directions']}, sıralı durak {len(route_stop_rows)}"
    )

    connection = connect(args.database)
    try:
        connection.execute(SCHEMA)
        insert(connection, "bus_stops", stop_rows)
        insert(connection, "bus_routes", route_rows)
        insert(connection, "bus_calendar", calendar_rows)
        insert(connection, "bus_trips", trip_rows)
        insert(connection, "bus_route_stops", route_stop_rows)

        record_meta(
            connection,
            "bus_stops",
            dialect.source,
            dialect.data_as_of,
            len(stop_rows),
            note=f"koordinatlar onarıldı; {stop_stats['dropped_coordinate']} satır kolonları kaymış",
        )
        record_meta(connection, "bus_routes", dialect.source, dialect.data_as_of, len(route_rows))
        record_meta(
            connection,
            "bus_calendar",
            dialect.source,
            dialect.data_as_of,
            len(calendar_rows),
            note="tarih penceresi geçerli; calendar_dates.csv yok, resmî tatiller bilinmiyor",
        )
        record_meta(
            connection,
            "bus_trips",
            dialect.source,
            dialect.data_as_of,
            len(trip_rows),
            note="sefer başına yalnızca ilk kalkış ve son varış saati var",
        )
        record_meta(
            connection,
            "bus_route_stops",
            dialect.source,
            dialect.data_as_of,
            len(route_stop_rows),
            note="hat+yön başına en eksiksiz seferin durak sırası",
        )

        if args.skip_places:
            print("  transit_places atlandı (--skip-places)")
        else:
            place_rows, place_counts = build_places(stop_rows, connection)
            insert(connection, "transit_places", place_rows)
            record_meta(
                connection,
                "transit_places",
                "İETT GTFS + nokta katmanları (128, 15, 13, 9) + raylı sistem tabloları",
                dialect.data_as_of,
                len(place_rows),
                note=", ".join(f"{mode}={count}" for mode, count in sorted(place_counts.items())),
            )
            print(
                "  transit_places: "
                + ", ".join(f"{mode} {count}" for mode, count in sorted(place_counts.items()))
                + f" — toplam {len(place_rows)}"
            )
        connection.commit()
    finally:
        connection.close()

    print(f"\nBitti: {time.perf_counter() - started:.1f} s")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
