"""Duraktan geçen hatlar ve hattın güzergâhı.

Elle kurulan küçük bir depo kullanılıyor ve içinde gerçek veride karşılaşılan
üç tuzak var:

- bir hat kodunun **birden çok güzergâh varyantı** (500T'nin iki varyantı da
  66 durağa uğruyor ama biri günde 451, öteki 3 sefer yapıyor),
- aynı adı taşıyan **başka semtteki** durak (ad eşleşmesi tek başına yetmez),
- hat bilgisi hiç olmayan modlar (iskele, minibüs, dolmuş).
"""

from __future__ import annotations

import pytest

from istanbul_agent.store.db import connect
from istanbul_agent.store.line_queries import get_line_route, get_lines_at_stop
from istanbul_agent.store.places import load_places
from istanbul_agent.store.station_names import collapsed, normalize, token_key

SOURCE = "İETT GTFS (veri seti 75)"
AS_OF = "2026-04-21"

PLACES = [
    ("bus:1", "otobus", "KADIKÖY", "401031", None, 40.9933, 29.0243, SOURCE, AS_OF, False),
    # Aynı ad, şehrin öteki ucunda: hatları buradan okunmamalı.
    ("bus:9", "otobus", "KADIKÖY", "202951", None, 41.1106, 29.9053, SOURCE, AS_OF, False),
    ("vapur:1", "vapur", "Kadıköy ŞH.", None, None, 40.9918, 29.0235, "Deniz", "2026-11-04", False),
    (
        "rayli:uskudar",
        "metro",
        "Üsküdar",
        "M5",
        None,
        41.0256,
        29.0151,
        "Metro API",
        "2026-11-08",
        False,
    ),
]

BUS_STOPS = [
    ("100", "401031", "KADIKÖY", None, 40.9933, 29.0243),
    ("101", "401032", "ÇARŞI", None, 40.9950, 29.0250),
    ("102", "401033", "SONDURAK", None, 41.0100, 29.0400),
    ("900", "202951", "KADIKÖY", None, 41.1106, 29.9053),
    ("901", "202952", "UZAK DURAK", None, 41.1110, 29.9060),
]

BUS_ROUTES = [
    ("r1", "500T", "KADIKÖY - SONDURAK", None, "500T_D_D0", "3"),
    ("r2", "500T", "KADIKÖY - SONDURAK", None, "500T_D_D1", "3"),
    ("r3", "14R", "UZAK DURAK - KADIKÖY", None, "14R_D_D0", "3"),
]

# (trip_id, route_id, line_code, service_id, direction_id, headsign,
#  first_stop, last_stop, departure_s, arrival_s, duration_s, stop_count)
BUS_TRIPS = [
    ("t1", "r1", "500T", "0", "0", "SONDURAK", "100", "102", 6 * 3600, 7 * 3600, 3600, 3),
    ("t2", "r1", "500T", "0", "0", "SONDURAK", "100", "102", 7 * 3600, 8 * 3600, 3600, 3),
    ("t3", "r1", "500T", "6", "0", "SONDURAK", "100", "102", 8 * 3600, 9 * 3600, 3600, 3),
    # r2 aynı yönde ve aynı durak sayısında ama tek sefer yapıyor.
    ("t4", "r2", "500T", "0", "0", "SONDURAK", "100", "102", 23 * 3600, 24 * 3600, 3600, 3),
    ("t5", "r3", "14R", "0", "1", "KADIKÖY", "901", "900", 9 * 3600, 9 * 3600 + 1800, 1800, 2),
]

# (route_id, line_code, direction_id, stop_sequence, stop_id, trip_id,
#  trip_count, variant_count)
BUS_ROUTE_STOPS = [
    ("r1", "500T", "0", 1, "100", "t1", 3, 2),
    ("r1", "500T", "0", 2, "101", "t1", 3, 2),
    ("r1", "500T", "0", 3, "102", "t1", 3, 2),
    ("r2", "500T", "0", 1, "100", "t4", 1, 2),
    ("r2", "500T", "0", 2, "101", "t4", 1, 2),
    ("r2", "500T", "0", 3, "102", "t4", 1, 2),
    ("r3", "14R", "1", 1, "901", "t5", 1, 1),
    ("r3", "14R", "1", 2, "900", "t5", 1, 1),
]

BUS_CALENDAR = [
    ("0", "WEEKDAYS", True, True, True, True, True, False, False, "20260316", "20261231", True),
    ("6", "SATURDAY", False, False, False, False, False, True, False, "20260316", "20261231", True),
]

RAIL_STATIONS = [
    (
        "uskudar",
        "Üsküdar",
        "M5",
        "metro",
        1,
        41.0256,
        29.0151,
        "Metro API",
        "GTFS 308",
        "2020-07-09",
    ),
    (
        "fistikagaci",
        "Fıstıkağacı",
        "M5",
        "metro",
        2,
        41.0300,
        29.0200,
        "Metro API",
        "GTFS 308",
        "2020-07-09",
    ),
    (
        "uskudar",
        "Üsküdar",
        "Marmaray",
        "tren",
        5,
        41.0256,
        29.0151,
        "Metro API",
        "GTFS 308",
        "2020-07-09",
    ),
]

RAIL_LINES = [
    (
        "M5",
        "metro",
        "ÜSKÜDAR - ÇEKMEKÖY",
        True,
        True,
        False,
        2,
        "yaml",
        "GTFS 308",
        "2020-07-09",
        None,
    ),
    (
        "Marmaray",
        "tren",
        "GEBZE-HALKALI",
        True,
        True,
        False,
        1,
        "yaml",
        "GTFS 308",
        "2020-07-09",
        None,
    ),
]

SCHEMA = """
CREATE TABLE transit_places (
    place_id VARCHAR, mode VARCHAR, name VARCHAR, name_key VARCHAR,
    name_key_flat VARCHAR, name_key_sorted VARCHAR, code VARCHAR, detail VARCHAR,
    lat DOUBLE, lon DOUBLE, source VARCHAR, data_as_of VARCHAR, is_stale BOOLEAN
);
CREATE TABLE bus_stops (
    stop_id VARCHAR, stop_code VARCHAR, name VARCHAR, name_key VARCHAR,
    name_key_flat VARCHAR, name_key_sorted VARCHAR, direction VARCHAR,
    lat DOUBLE, lon DOUBLE, source VARCHAR, data_as_of VARCHAR
);
CREATE TABLE bus_routes (
    route_id VARCHAR, line_code VARCHAR, long_name VARCHAR, description VARCHAR,
    route_code VARCHAR, route_type VARCHAR, source VARCHAR, data_as_of VARCHAR
);
CREATE TABLE bus_calendar (
    service_id VARCHAR, service_name VARCHAR, monday BOOLEAN, tuesday BOOLEAN,
    wednesday BOOLEAN, thursday BOOLEAN, friday BOOLEAN, saturday BOOLEAN,
    sunday BOOLEAN, start_date VARCHAR, end_date VARCHAR, window_usable BOOLEAN,
    source VARCHAR, data_as_of VARCHAR
);
CREATE TABLE bus_trips (
    trip_id VARCHAR, route_id VARCHAR, line_code VARCHAR, service_id VARCHAR,
    direction_id VARCHAR, headsign VARCHAR, first_stop_id VARCHAR,
    last_stop_id VARCHAR, departure_s INTEGER, arrival_s INTEGER,
    duration_s INTEGER, stop_count INTEGER, source VARCHAR, data_as_of VARCHAR
);
CREATE TABLE bus_route_stops (
    route_id VARCHAR, line_code VARCHAR, direction_id VARCHAR,
    stop_sequence INTEGER, stop_id VARCHAR, trip_id VARCHAR, trip_count INTEGER,
    variant_count INTEGER, source VARCHAR, data_as_of VARCHAR
);
CREATE TABLE rail_stations (
    station_key VARCHAR, name VARCHAR, line VARCHAR, mode VARCHAR,
    stop_order INTEGER, gtfs_stop_id VARCHAR, lat DOUBLE, lon DOUBLE,
    in_gtfs BOOLEAN, in_live_api BOOLEAN, is_spur BOOLEAN, coord_source VARCHAR,
    source VARCHAR, data_as_of VARCHAR
);
CREATE TABLE rail_lines (
    line VARCHAR, mode VARCHAR, long_name VARCHAR, in_gtfs BOOLEAN,
    coords_reliable BOOLEAN, is_ring BOOLEAN, station_count INTEGER,
    schedule_source VARCHAR, source VARCHAR, data_as_of VARCHAR, note VARCHAR
);
"""


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    database = tmp_path_factory.mktemp("store") / "lines.duckdb"
    connection = connect(database)
    try:
        connection.execute(SCHEMA)
        connection.executemany(
            "INSERT INTO transit_places VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    pid,
                    mode,
                    name,
                    normalize(name),
                    collapsed(name),
                    token_key(name),
                    code,
                    detail,
                    lat,
                    lon,
                    source,
                    as_of,
                    stale,
                )
                for pid, mode, name, code, detail, lat, lon, source, as_of, stale in PLACES
            ],
        )
        connection.executemany(
            "INSERT INTO bus_stops VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    stop_id,
                    code,
                    name,
                    normalize(name),
                    collapsed(name),
                    token_key(name),
                    direction,
                    lat,
                    lon,
                    SOURCE,
                    AS_OF,
                )
                for stop_id, code, name, direction, lat, lon in BUS_STOPS
            ],
        )
        connection.executemany(
            "INSERT INTO bus_routes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(*row, SOURCE, AS_OF) for row in BUS_ROUTES],
        )
        connection.executemany(
            "INSERT INTO bus_calendar VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(*row, SOURCE, AS_OF) for row in BUS_CALENDAR],
        )
        connection.executemany(
            "INSERT INTO bus_trips VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(*row, SOURCE, AS_OF) for row in BUS_TRIPS],
        )
        connection.executemany(
            "INSERT INTO bus_route_stops VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(*row, SOURCE, AS_OF) for row in BUS_ROUTE_STOPS],
        )
        connection.executemany(
            "INSERT INTO rail_stations VALUES (?, ?, ?, ?, ?, NULL, ?, ?, TRUE, TRUE, FALSE, ?, ?, ?)",
            RAIL_STATIONS,
        )
        connection.executemany(
            "INSERT INTO rail_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", RAIL_LINES
        )
        connection.commit()
    finally:
        connection.close()
    return database


@pytest.fixture(scope="module")
def places(store):
    return load_places(store)


class TestLinesAtStop:
    def test_lists_the_lines_serving_a_bus_stop(self, store, places):
        result = get_lines_at_stop("Kadıköy", modes=("otobus",), path=store, places=places)
        assert result.found
        assert [line.line for line in result.lines] == ["500T"]
        assert result.lines[0].long_name == "KADIKÖY - SONDURAK"

    def test_counts_variants_and_trips(self, store, places):
        result = get_lines_at_stop("Kadıköy", modes=("otobus",), path=store, places=places)
        line = result.lines[0]
        # İki varyant, dört sefer (r1'de üç, r2'de bir).
        assert line.variant_count == 2
        assert line.trip_count == 4

    def test_a_same_named_stop_elsewhere_is_not_mixed_in(self, store, places):
        """Ad eşleşmesi yetmez: 14R yalnızca uzaktaki KADIKÖY'den geçiyor."""
        result = get_lines_at_stop("Kadıköy", modes=("otobus",), path=store, places=places)
        assert "14R" not in [line.line for line in result.lines]
        assert any("ayrı otobüs durağı" in note for note in result.notes)

    def test_rail_station_lists_its_lines_and_flags_stale(self, store, places):
        result = get_lines_at_stop("Üsküdar", path=store, places=places)
        assert sorted(line.line for line in result.lines) == ["M5", "Marmaray"]
        assert all(line.is_stale for line in result.lines)

    def test_a_pier_says_there_is_no_line_data_instead_of_guessing(self, store, places):
        result = get_lines_at_stop("Kadıköy ŞH.", path=store, places=places)
        assert result.found
        assert result.lines == []
        assert any("hat bilgisi yok" in note for note in result.notes)

    def test_an_unknown_stop_returns_candidates(self, store, places):
        result = get_lines_at_stop("Paris", path=store, places=places)
        assert not result.found
        assert result.candidates


class TestLineRoute:
    def test_returns_the_ordered_stops(self, store):
        routes = get_line_route("500T", path=store)
        assert len(routes) == 1
        assert [stop.name for stop in routes[0].stops] == ["KADIKÖY", "ÇARŞI", "SONDURAK"]

    def test_line_code_is_case_insensitive(self, store):
        assert get_line_route("500t", path=store)

    def test_trip_count_covers_every_variant_in_the_direction(self, store):
        """Seçilen varyantın değil, yöndeki bütün seferlerin sayısı.

        İki varyant da aynı durak sayısına sahip; eşitliği sefer sayısına
        bakmadan bozmak hattı günde 1 sefer çalışıyor gibi gösteriyordu.
        """
        route = get_line_route("500T", path=store)[0]
        assert route.trip_count == 4
        assert route.variant_count == 2
        assert any("varyant" in note for note in route.notes)

    def test_the_busiest_variant_is_the_one_listed(self, store):
        route = get_line_route("500T", path=store)[0]
        assert route.stops[0].name == "KADIKÖY"
        # r1 günde üç sefer yapıyor, r2 bir: listelenen r1'in seferi olmalı.
        assert route.direction_name == "SONDURAK"

    def test_service_windows_per_service_day(self, store):
        route = get_line_route("500T", path=store)[0]
        windows = {window.service_name: window for window in route.service_windows}
        assert windows["WEEKDAYS"].trips == 3
        assert windows["WEEKDAYS"].first_departure == "06:00"
        assert windows["WEEKDAYS"].last_departure == "23:00"
        assert windows["SATURDAY"].trips == 1
        assert windows["WEEKDAYS"].median_duration_min == 60

    def test_direction_filter(self, store):
        assert get_line_route("14R", direction="1", path=store)
        assert get_line_route("14R", direction="0", path=store) == []

    def test_rail_route_is_ordered_and_marked_stale(self, store):
        routes = get_line_route("M5", path=store)
        assert len(routes) == 1
        assert [stop.name for stop in routes[0].stops] == ["Üsküdar", "Fıstıkağacı"]
        assert routes[0].is_stale is True
        assert routes[0].direction_name == "ÜSKÜDAR - ÇEKMEKÖY"

    def test_an_unknown_line_is_an_empty_list(self, store):
        assert get_line_route("YOK99", path=store) == []
