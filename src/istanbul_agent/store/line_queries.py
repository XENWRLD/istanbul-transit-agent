"""Bir duraktan geçen hatlar ve bir hattın güzergâhı.

`get_lines_at_stop` ve `get_line_route` araçlarının veri katmanı. İki ayrı
veri dünyasını tek cevap biçiminde birleştiriyor:

- **otobüs**: İETT GTFS beslemesi (veri seti 75, Nisan 2026). Hat kodu
  (`500T`) başına birden çok `route_id` var — besleme güzergâh varyantlarını
  ayrı hat kaydı olarak tutuyor (9274 varyant, 1095 hat kodu). Bu yüzden
  sorgular **hat kodu** üzerinden toplanır, `route_id` üzerinden değil.
- **raylı sistem**: `rail_stations` / `rail_lines` tabloları (karar 0003),
  2020 beslemesinden geldiği için `is_stale`.

Minibüs, dolmuş ve vapur için **hat bilgisi yok**: yerel depoda yalnızca
durak noktaları var, hangi hattın hangi duraktan geçtiği değil. Araç bunu
söylüyor, tahmin üretmiyor.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from istanbul_agent.store.db import read_only_connection
from istanbul_agent.store.geo import haversine_m
from istanbul_agent.store.gtfs import seconds_to_clock
from istanbul_agent.store.places import CLUSTER_RADIUS_M, Place, find_places, load_places
from istanbul_agent.store.station_names import normalize

__all__ = [
    "LINE_DATA_MODES",
    "LineAtStop",
    "LineRoute",
    "RouteStop",
    "ServiceWindow",
    "StopLines",
    "get_line_route",
    "get_lines_at_stop",
]

# Yerel depoda hat-durak ilişkisi yalnızca bu modlarda var.
LINE_DATA_MODES = ("otobus", "metro", "tramvay", "funikuler", "teleferik", "tren", "rayli")

# Durak adı aynı olan noktalardan hangilerinin "aynı durak" sayılacağı,
# `places` ile aynı yarıçap: bir kavşağın iki yönü tek duraktır.
STOP_MATCH_RADIUS_M = CLUSTER_RADIUS_M


@dataclass(frozen=True)
class LineAtStop:
    """Bir duraktan geçen tek bir hat."""

    line: str
    mode: str
    long_name: str | None
    headsigns: tuple[str, ...]
    variant_count: int
    trip_count: int
    source: str
    data_as_of: str
    is_stale: bool


@dataclass
class StopLines:
    """`get_lines_at_stop` sonucu."""

    stop_name: str | None
    mode: str | None
    lines: list[LineAtStop] = field(default_factory=list)
    matched_points: int = 0
    candidates: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    notes: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.stop_name is not None


@dataclass(frozen=True)
class RouteStop:
    """Güzergâhtaki bir durak."""

    sequence: int
    name: str
    lat: float | None
    lon: float | None


@dataclass(frozen=True)
class ServiceWindow:
    """Bir yöndeki servis penceresi: ilk kalkış, son kalkış, sefer sayısı."""

    service_id: str | None
    service_name: str | None
    trips: int
    first_departure: str | None
    last_departure: str | None
    median_duration_min: int | None


@dataclass
class LineRoute:
    """`get_line_route` sonucu: tek yön."""

    line: str
    mode: str
    direction_id: str | None
    direction_name: str | None
    stops: list[RouteStop] = field(default_factory=list)
    variant_count: int = 0
    trip_count: int = 0
    service_windows: list[ServiceWindow] = field(default_factory=list)
    source: str = ""
    data_as_of: str = ""
    is_stale: bool = False
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ duraktan geçen hatlar


def _bus_stop_ids(connection: object, name: str, lat: float, lon: float) -> list[str]:
    """Verilen ad ve konumdaki otobüs durağı kayıtları.

    Aynı adı taşıyan duraklar şehrin başka yerlerinde de olabildiği için ad
    eşleşmesi tek başına yetmiyor; konum da 300 m içinde olmak zorunda.
    """
    rows = connection.execute(  # type: ignore[attr-defined]
        "SELECT stop_id, lat, lon FROM bus_stops WHERE name_key = ?", [normalize(name)]
    ).fetchall()
    return [
        stop_id
        for stop_id, stop_lat, stop_lon in rows
        if haversine_m(lat, lon, stop_lat, stop_lon) <= STOP_MATCH_RADIUS_M
    ]


def get_lines_at_stop(
    stop: str,
    *,
    modes: Sequence[str] | None = None,
    path: Path | None = None,
    places: Sequence[Place] | None = None,
) -> StopLines:
    """`stop` adlı duraktan geçen hatlar.

    Durak önce nokta tablosunda aranır (dört adımlı ad eşleştirme), sonra
    bulunan kümenin moduna göre otobüs ya da raylı sistem tabloları
    sorgulanır. Mod verilirse arama o moda kısıtlanır — "Üsküdar" hem metro
    istasyonu hem otobüs durağı hem iskele. `modes` birden çok değer
    taşıyabilir (şemsiye mod `rayli` dört moda açılır).
    """
    pool = list(places) if places is not None else load_places(path)
    search = find_places(stop, modes=modes or None, places=pool, limit=3)
    if not search.found:
        return StopLines(
            stop_name=None,
            mode=modes[0] if modes and len(modes) == 1 else None,
            candidates=search.candidates,
            related=search.related,
            notes=[f"'{stop}' adlı bir durak veya istasyon bulunamadı."],
        )

    cluster = search.clusters[0]
    result = StopLines(
        stop_name=cluster.name,
        mode=cluster.mode,
        matched_points=cluster.point_count,
        related=search.related,
    )
    same_name = [
        other
        for other in search.clusters[1:]
        if other.name == cluster.name and other.mode == cluster.mode
    ]
    other_places = [other for other in search.clusters[1:] if other not in same_name]
    if same_name:
        # Aynı adı taşıyan ama başka semtteki duraklar. Hangisinin
        # kullanıldığı koordinatla belli olsun, yoksa "KADIKÖY, KADIKÖY" diye
        # kendini tekrar eden bir not çıkıyor.
        result.notes.append(
            f"'{cluster.name}' adlı {len(same_name) + 1} ayrı {cluster.mode_label} var; "
            f"kullanılan {cluster.lat:.4f},{cluster.lon:.4f} konumundaki "
            f"({cluster.point_count} kayıt)."
        )
    if other_places:
        others = ", ".join(f"{other.name} ({other.mode_label})" for other in other_places[:2])
        result.notes.append(
            f"Aynı ada uyan başka yerler de var: {others}. Cevapta hangisini kullandığını belirt."
        )

    with read_only_connection(path) as connection:
        if cluster.mode == "otobus":
            stop_ids = _bus_stop_ids(connection, cluster.name, cluster.lat, cluster.lon)
            if not stop_ids:
                result.notes.append("Durak nokta tablosunda var ama GTFS durak kaydı bulunamadı.")
                return result
            placeholders = ", ".join("?" * len(stop_ids))
            # Önce duraktan geçen (hat varyantı, yön) çiftleri, sonra o
            # çiftlerin sefer sayısı. Tek sorguda saymak, durağın güzergâhta
            # iki kez geçtiği ring hatlarda seferleri ikiye katlıyordu.
            rows = connection.execute(
                f"""
                WITH serving AS (
                    SELECT DISTINCT rs.line_code, rs.route_id,
                           coalesce(rs.direction_id, '') AS direction_id,
                           rs.trip_id, rs.source, rs.data_as_of
                    FROM bus_route_stops rs
                    WHERE rs.stop_id IN ({placeholders})
                ),
                trips AS (
                    SELECT route_id, coalesce(direction_id, '') AS direction_id,
                           count(*) AS trips
                    FROM bus_trips
                    GROUP BY route_id, coalesce(direction_id, '')
                )
                SELECT s.line_code,
                       count(DISTINCT s.route_id)                  AS variants,
                       coalesce(sum(t.trips), 0)                   AS trips,
                       any_value(r.long_name)                      AS long_name,
                       list(DISTINCT h.headsign)                   AS headsigns,
                       any_value(s.source)                         AS source,
                       any_value(s.data_as_of)                     AS data_as_of
                FROM serving s
                LEFT JOIN trips t
                       ON t.route_id = s.route_id AND t.direction_id = s.direction_id
                LEFT JOIN bus_routes r ON r.route_id = s.route_id
                LEFT JOIN bus_trips h ON h.trip_id = s.trip_id
                GROUP BY s.line_code
                ORDER BY s.line_code
                """,
                stop_ids,
            ).fetchall()
            for line_code, variants, trips, long_name, headsigns, source, data_as_of in rows:
                result.lines.append(
                    LineAtStop(
                        line=line_code,
                        mode="otobus",
                        long_name=long_name,
                        headsigns=tuple(sorted(h for h in (headsigns or []) if h)),
                        variant_count=int(variants or 0),
                        trip_count=int(trips or 0),
                        source=source,
                        data_as_of=data_as_of,
                        is_stale=False,
                    )
                )
            return result

        if cluster.mode in LINE_DATA_MODES:
            rows = connection.execute(
                """
                SELECT s.line, s.mode, l.long_name, l.station_count, s.source, s.data_as_of
                FROM rail_stations s
                LEFT JOIN rail_lines l ON l.line = s.line
                WHERE s.station_key = ? OR s.name = ?
                ORDER BY s.line
                """,
                [normalize(cluster.name), cluster.name],
            ).fetchall()
            for line, line_mode, long_name, _station_count, source, data_as_of in rows:
                result.lines.append(
                    LineAtStop(
                        line=line,
                        mode=line_mode or cluster.mode,
                        long_name=long_name or None,
                        headsigns=(),
                        variant_count=1,
                        trip_count=0,
                        source=source,
                        data_as_of=data_as_of,
                        is_stale=True,
                    )
                )
            result.notes.append(
                "Raylı sistem hat bilgisi 2020 tarihli beslemeden geliyor; "
                "sonradan açılan istasyonlar eksik olabilir."
            )
            return result

    result.notes.append(
        f"{cluster.mode_label} için yerel depoda hat bilgisi yok: bu kaynak yalnızca "
        "durak konumlarını içeriyor, hangi hattın geçtiğini içermiyor."
    )
    return result


# ------------------------------------------------------------------ hattın güzergâhı


def _bus_route(connection: object, line: str, direction: str | None) -> list[LineRoute]:
    # Sıralamada durak sayısından sonra **sefer sayısı** geliyor. 500T'nin iki
    # varyantı da 66 durağa uğruyor ama biri günde 451, öteki 3 sefer yapıyor;
    # eşitliği sefer sayısına bakmadan bozmak, hattı günde 3 sefer çalışıyor
    # gibi gösteriyordu.
    rows = connection.execute(  # type: ignore[attr-defined]
        """
        WITH trips_per_variant AS (
            SELECT route_id, coalesce(direction_id, '') AS direction_id, count(*) AS trips
            FROM bus_trips
            GROUP BY route_id, coalesce(direction_id, '')
        )
        SELECT rs.direction_id, rs.route_id, rs.trip_id, count(*) AS stops,
               coalesce(any_value(tv.trips), 0) AS trips,
               any_value(rs.source) AS source, any_value(rs.data_as_of) AS data_as_of
        FROM bus_route_stops rs
        LEFT JOIN trips_per_variant tv
               ON tv.route_id = rs.route_id
              AND tv.direction_id = coalesce(rs.direction_id, '')
        WHERE upper(rs.line_code) = upper(?)
        GROUP BY rs.direction_id, rs.route_id, rs.trip_id
        ORDER BY stops DESC, trips DESC
        """,
        [line],
    ).fetchall()
    if not rows:
        return []

    # Hat kodu başına birden çok varyant var; her yön için en eksiksiz
    # güzergâhı seçiyoruz, kaç varyant olduğunu da cevaba yazıyoruz. Sefer
    # sayısı ise yöndeki **bütün** varyantların toplamı: kullanıcı "bu hat
    # günde kaç sefer yapıyor" diye soruyor, seçilen varyantı sormuyor.
    chosen: dict[str | None, tuple] = {}
    variants_by_direction: dict[str | None, int] = {}
    trips_by_direction: dict[str | None, int] = {}
    for row in rows:
        direction_id = row[0]
        variants_by_direction[direction_id] = variants_by_direction.get(direction_id, 0) + 1
        trips_by_direction[direction_id] = trips_by_direction.get(direction_id, 0) + int(
            row[4] or 0
        )
        if direction_id not in chosen:
            chosen[direction_id] = row

    routes: list[LineRoute] = []
    for direction_id, row in sorted(chosen.items(), key=lambda pair: str(pair[0])):
        if direction is not None and str(direction_id) != str(direction):
            continue
        _, route_id, trip_id, _, _, source, data_as_of = row
        trips = trips_by_direction.get(direction_id, 0)
        stops = connection.execute(  # type: ignore[attr-defined]
            """
            SELECT rs.stop_sequence, coalesce(s.name, rs.stop_id), s.lat, s.lon
            FROM bus_route_stops rs
            LEFT JOIN bus_stops s ON s.stop_id = rs.stop_id
            WHERE rs.route_id = ? AND rs.trip_id = ?
            ORDER BY rs.stop_sequence
            """,
            [route_id, trip_id],
        ).fetchall()
        windows = connection.execute(  # type: ignore[attr-defined]
            """
            SELECT t.service_id, c.service_name, count(*) AS trips,
                   min(t.departure_s) AS first_departure,
                   max(t.departure_s) AS last_departure,
                   median(t.duration_s) AS median_duration
            FROM bus_trips t
            LEFT JOIN bus_calendar c ON c.service_id = t.service_id
            WHERE upper(t.line_code) = upper(?)
              AND coalesce(t.direction_id, '') = coalesce(?, '')
            GROUP BY t.service_id, c.service_name
            ORDER BY t.service_id
            """,
            [line, direction_id],
        ).fetchall()
        headsign = connection.execute(  # type: ignore[attr-defined]
            "SELECT headsign FROM bus_trips WHERE trip_id = ?", [trip_id]
        ).fetchone()
        routes.append(
            LineRoute(
                line=line.upper(),
                mode="otobus",
                direction_id=direction_id,
                direction_name=(headsign[0] if headsign else None),
                stops=[
                    RouteStop(sequence=int(sequence), name=name, lat=lat, lon=lon)
                    for sequence, name, lat, lon in stops
                ],
                variant_count=variants_by_direction.get(direction_id, 1),
                trip_count=int(trips or 0),
                service_windows=[
                    ServiceWindow(
                        service_id=service_id,
                        service_name=service_name,
                        trips=int(window_trips or 0),
                        first_departure=seconds_to_clock(int(first) if first is not None else None),
                        last_departure=seconds_to_clock(int(last) if last is not None else None),
                        median_duration_min=(
                            round(float(duration) / 60) if duration is not None else None
                        ),
                    )
                    for service_id, service_name, window_trips, first, last, duration in windows
                ],
                source=source,
                data_as_of=data_as_of,
                is_stale=False,
                notes=(
                    [
                        f"Bu yönde {variants_by_direction.get(direction_id, 1)} güzergâh "
                        "varyantı var; listelenen, en çok durağa uğrayan varyant."
                    ]
                    if variants_by_direction.get(direction_id, 1) > 1
                    else []
                ),
            )
        )
    return routes


def _rail_route(connection: object, line: str) -> list[LineRoute]:
    rows = connection.execute(  # type: ignore[attr-defined]
        """
        SELECT s.stop_order, s.name, s.lat, s.lon, s.mode, s.source, s.data_as_of
        FROM rail_stations s
        WHERE upper(s.line) = upper(?)
        ORDER BY s.stop_order
        """,
        [line],
    ).fetchall()
    if not rows:
        return []
    meta = connection.execute(  # type: ignore[attr-defined]
        "SELECT long_name, is_ring, in_gtfs, note FROM rail_lines WHERE upper(line) = upper(?)",
        [line],
    ).fetchone()
    notes: list[str] = []
    if meta:
        if meta[1]:
            notes.append("Bu hat ring; güzergâh kendi üzerine kapanıyor.")
        if not meta[2]:
            notes.append("Hat 2020 beslemesinde yok; istasyon sırası referans tablolarından.")
        if meta[3]:
            notes.append(str(meta[3]))
    return [
        LineRoute(
            line=line.upper(),
            mode=rows[0][4] or "rayli",
            direction_id=None,
            direction_name=(meta[0] if meta and meta[0] else None),
            stops=[
                RouteStop(
                    sequence=int(order) if order is not None else index + 1,
                    name=name,
                    lat=lat,
                    lon=lon,
                )
                for index, (order, name, lat, lon, _, _, _) in enumerate(rows)
            ],
            variant_count=1,
            trip_count=0,
            source=rows[0][5],
            data_as_of=rows[0][6],
            is_stale=True,
            notes=[
                *notes,
                "İstasyon sırası 2020 tarihli beslemeden; sonradan açılan "
                "istasyonlar eksik olabilir.",
            ],
        )
    ]


def get_line_route(
    line: str, *, direction: str | None = None, path: Path | None = None
) -> list[LineRoute]:
    """`line` hattının güzergâhı: sıralı duraklar, yönler, servis pencereleri.

    Otobüs hatları için her yön ayrı bir sonuç döner. Raylı sistemde yön
    kaydı yok, tek sonuç döner ve sıra uç istasyondan başlar.
    """
    wanted = (line or "").strip()
    if not wanted:
        return []
    with read_only_connection(path) as connection:
        routes = _bus_route(connection, wanted, direction)
        if routes:
            return routes
        return _rail_route(connection, wanted)
