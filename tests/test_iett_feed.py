"""İETT GTFS beslemesinin ağzı: kodlama, ayırıcı, zip, onarımlar, ingest.

Bu testler gerçek beslemeyi değil, onun ölçülen her kusurunu küçük ölçekte
taşıyan bir fixture besleme üretip kullanır (karar 0007):

- `agency.csv` virgüllü, diğer tablolar noktalı virgüllü,
- dosyalar UTF-8 **BOM**'lu, metin **çifte kodlanmış** (`KADIKÃ–Y`),
- `stops.csv` koordinatları **binlik noktalarla parçalanmış**, bir satırda
  kolonlar kaymış,
- `stop_times` **zip** içinde, virgüllü ve sefer başına yalnızca iki saat
  taşıyor (ilk duraktan kalkış, son durağa varış),
- `calendar.csv` tarih penceresi geçerli.

Böylece hem okuyucu hem ingest, gerçek beslemenin davranışına karşı ağ
erişimi olmadan sabitleniyor.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from istanbul_agent.store import gtfs as gtfs_module
from istanbul_agent.store import ingest as ingest_module
from istanbul_agent.store.db import connect
from istanbul_agent.store.geo import parse_grouped_coordinate
from istanbul_agent.store.gtfs import dialect_for, read_table
from istanbul_agent.store.text_repair import looks_double_encoded, repair_double_encoded

# "KADIKÖY - KİRAZLITEPE" beslemede böyle duruyor.
BROKEN_LONG_NAME = "KADIKÃ–Y - KÄ°RAZLITEPE"
# "ESATPAŞA" latin1 üzerinden bozulmuş hali; cp1252 bunu kodlayamaz.
BROKEN_MIXED = "KADIKÃ–Y - ESATPAÅ\x9eA"


def _double_encode(text: str) -> str:
    """Metni beslemedeki hale getir: UTF-8 baytlarını cp1252 sanarak oku."""
    return text.encode("utf-8").decode("cp1252")


@pytest.fixture
def feed(tmp_path, monkeypatch):
    """Gerçek beslemenin kusurlarını taşıyan küçük bir `iett` beslemesi kur."""
    root = tmp_path / "gtfs"
    feed_dir = root / "iett"
    feed_dir.mkdir(parents=True)

    def write(name: str, text: str, *, bom: bool = True) -> None:
        prefix = "﻿" if bom else ""
        (feed_dir / name).write_text(prefix + text, encoding="utf-8")

    # agency: virgüllü, BOM'suz -- gerçek beslemede de böyle.
    write("agency.csv", "agency_id,agency_name\n1,IETT\n", bom=False)
    write(
        "calendar.csv",
        "service_id;service_name;monday;tuesday;wednesday;thursday;friday;saturday;sunday"
        ";start_date;end_date\n"
        "0;WEEKDAYS;1;1;1;1;1;0;0;20260316;20261231\n"
        "6;SATURDAY;0;0;0;0;0;1;0;20260316;20261231\n",
    )
    write(
        "routes.csv",
        "route_id;agency_id;route_short_name;route_long_name;route_type;route_desc;route_code\n"
        f"1;1;14R;{BROKEN_LONG_NAME};3;{_double_encode('SAATLER FERAH CADDESİNDEN GİDER.')};1_D_D0\n"
        f"2;1;14R;{BROKEN_MIXED};3;;2_D_D0\n",
    )
    # Üçüncü durakta kolonlar kaymış: gerçek beslemedeki 4 bozuk satırın aynısı.
    write(
        "stops.csv",
        "stop_id;stop_code;stop_name;stop_desc;stop_lat;stop_lon;location_type;\n"
        "100;100001;KADIKÖY İSKELE;direction: KADIKÖY;409.902.000.000.001;290.250.000.000.001;0;\n"
        "200;100002;KİRAZLITEPE;direction: KİRAZLITEPE;410.500.000.000.001;290.900.000.000.001;0;\n"
        "300;100003;BOZUK SATIR;direction: GİDİŞ;direction: GİDİŞ;4,10E+14;0;\n",
    )
    write(
        "trips.csv",
        "trip_id;route_id;service_id;trip_headsign;direction_id\n"
        "t1;1;0;KİRAZLITEPE;1\n"
        "t2;1;0;KİRAZLITEPE;1\n"
        "t3;2;6;KADIKÖY;0\n",
    )
    # stop_times: zip içinde, virgüllü, sefer başına iki saat.
    stop_times = (
        "trip_id,stop_id,stop_sequence,arrival_time,departure_time,timepoint\n"
        "t1,100,1,06:40:00,06:40:00,1\n"
        "t1,200,2,,,0\n"
        "t1,300,3,07:10:00,07:10:00,0\n"
        "t2,100,1,08:40:00,08:40:00,1\n"
        "t2,200,2,09:05:00,09:05:00,0\n"
        "t3,200,1,25:10:00,25:10:00,1\n"
        "t3,100,2,25:40:00,25:40:00,0\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("stop_times.txt", stop_times.encode("utf-8"))
    (feed_dir / "stop_times.zip").write_bytes(buffer.getvalue())

    monkeypatch.setattr(gtfs_module, "GTFS_ROOT", root)
    monkeypatch.setattr(ingest_module, "GEOJSON_DIR", tmp_path / "geojson")
    monkeypatch.setattr(ingest_module, "STOPS_MANIFEST", tmp_path / "geojson" / "_m.json")
    return feed_dir


# ----------------------------------------------------------------- dialect


def test_dialects_differ_between_the_two_feeds():
    iett, public = dialect_for("iett"), dialect_for("public_transport")
    assert (iett.encoding, iett.delimiter) == ("utf-8-sig", ";")
    assert (public.encoding, public.delimiter) == ("cp1254", ",")
    # Tarih penceresi yalnızca İETT beslemesinde güvenilir (kararlar 0002/0007).
    assert iett.calendar_window_usable is True
    assert public.calendar_window_usable is False


def test_unknown_feed_raises_instead_of_guessing():
    with pytest.raises(KeyError) as error:
        dialect_for("bilinmeyen")
    assert "bilinmeyen" in str(error.value)


def test_delimiter_is_per_file_not_per_feed():
    dialect = dialect_for("iett")
    assert dialect.delimiter_for("stops") == ";"
    assert dialect.delimiter_for("agency.csv") == ","
    assert dialect.delimiter_for("stop_times") == ","
    assert dialect.file_name("stop_times") == "stop_times.zip"


# ----------------------------------------------------------------- reading


def test_reads_semicolon_tables_and_comma_agency(feed):
    assert read_table("agency", "iett") == [{"agency_id": "1", "agency_name": "IETT"}]
    calendar = read_table("calendar", "iett")
    assert [row["service_name"] for row in calendar] == ["WEEKDAYS", "SATURDAY"]


def test_reads_stop_times_from_inside_the_zip(feed):
    rows = read_table("stop_times", "iett")
    assert len(rows) == 7
    assert rows[0]["departure_time"] == "06:40:00"
    # Saatsiz ara durak: besleme boş bırakıyor, okuyucu uydurmuyor.
    assert rows[1]["departure_time"] == ""


def test_reading_repairs_double_encoded_text(feed):
    routes = read_table("routes", "iett")
    assert routes[0]["route_long_name"] == "KADIKÖY - KİRAZLITEPE"
    assert routes[0]["route_desc"] == "SAATLER FERAH CADDESİNDEN GİDER."
    # Aynı dizede iki ayrı kod sayfasından bozulma: ikisi de onarılır.
    assert routes[1]["route_long_name"] == "KADIKÖY - ESATPAŞA"


def test_bom_does_not_leak_into_the_first_column_name(feed):
    assert "route_id" in read_table("routes", "iett")[0]


# ----------------------------------------------------------------- repairs


@pytest.mark.parametrize(
    ("broken", "expected"),
    [
        (BROKEN_LONG_NAME, "KADIKÖY - KİRAZLITEPE"),
        (BROKEN_MIXED, "KADIKÖY - ESATPAŞA"),
        (_double_encode("ÜSKÜDAR"), "ÜSKÜDAR"),
        (_double_encode("ŞİŞLİ"), "ŞİŞLİ"),
    ],
)
def test_repair_double_encoded(broken, expected):
    assert repair_double_encoded(broken) == expected


@pytest.mark.parametrize(
    "clean",
    ["KADIKÖY", "ÜSKÜDAR", "GÜZERGÂHI", "4.LEVENT METRO", "ŞİFA SONDURAK", ""],
)
def test_repair_leaves_clean_text_alone(clean):
    assert repair_double_encoded(clean) == clean


def test_guzergah_is_not_treated_as_mojibake():
    """`Â` gerçek Türkçe; onu iz sayan tespit onarılmış dizeleri bozuk gösterir."""
    assert looks_double_encoded("GÜZERGÂHI") is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("410.191.700.005.564", 41.0191700005564),
        ("286.843.529.999.755", 28.6843529999755),
        ("41.0191700005564", 41.0191700005564),  # besleme düzelirse de çalışır
        ("", None),
        ("direction: GİDİŞ", None),
        ("4,10E+14", None),
    ],
)
def test_parse_grouped_coordinate(raw, expected):
    result = parse_grouped_coordinate(raw)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


# ----------------------------------------------------------------- ingest


@pytest.fixture
def ingested(feed, tmp_path):
    """Fixture beslemeyi geçici bir DuckDB dosyasına aktar."""
    database = tmp_path / "test.duckdb"
    connection = connect(database)
    try:
        connection.execute(ingest_module.SCHEMA)
        stop_rows, stop_stats = ingest_module.load_bus_stops()
        route_rows, line_of_route = ingest_module.load_bus_routes()
        calendar_rows = ingest_module.load_bus_calendar()
        summaries, _ = ingest_module.summarise_trips()
        trip_rows, route_stop_rows, trip_stats = ingest_module.build_trip_rows(
            summaries, line_of_route
        )
        ingest_module.insert(connection, "bus_stops", stop_rows)
        ingest_module.insert(connection, "bus_routes", route_rows)
        ingest_module.insert(connection, "bus_calendar", calendar_rows)
        ingest_module.insert(connection, "bus_trips", trip_rows)
        ingest_module.insert(connection, "bus_route_stops", route_stop_rows)
        connection.commit()
        yield connection, stop_stats, trip_stats
    finally:
        connection.close()


def test_ingest_drops_only_the_broken_coordinate_row(ingested):
    connection, stop_stats, _ = ingested
    names = [
        row[0] for row in connection.execute("SELECT name FROM bus_stops ORDER BY name").fetchall()
    ]
    assert names == ["KADIKÖY İSKELE", "KİRAZLITEPE"]
    assert stop_stats["dropped_coordinate"] == 1


def test_ingest_repairs_coordinates(ingested):
    connection, _, _ = ingested
    lat, lon = connection.execute("SELECT lat, lon FROM bus_stops WHERE stop_id = '100'").fetchone()
    assert lat == pytest.approx(40.9902, abs=1e-4)
    assert lon == pytest.approx(29.0250, abs=1e-4)


def test_ingest_writes_searchable_name_keys(ingested):
    connection, _, _ = ingested
    row = connection.execute(
        "SELECT name_key, name_key_flat FROM bus_stops WHERE stop_id = '100'"
    ).fetchone()
    assert "kadikoy" in row[0]
    assert " " not in row[1]


def test_trip_summary_carries_first_departure_and_last_arrival(ingested):
    connection, _, _ = ingested
    row = connection.execute(
        """
        SELECT departure_s, arrival_s, duration_s, stop_count, first_stop_id, last_stop_id
        FROM bus_trips WHERE trip_id = 't1'
        """
    ).fetchone()
    assert row[0] == 6 * 3600 + 40 * 60
    assert row[1] == 7 * 3600 + 10 * 60
    assert row[2] == 30 * 60
    assert row[3] == 3
    assert (row[4], row[5]) == ("100", "300")


def test_times_past_midnight_stay_past_midnight(ingested):
    """`25:10:00` 01:10 demek; saniyeye çevrilirken 24 saat korunur."""
    connection, _, _ = ingested
    departure = connection.execute(
        "SELECT departure_s FROM bus_trips WHERE trip_id='t3'"
    ).fetchone()[0]
    assert departure == 25 * 3600 + 10 * 60


def test_route_stops_come_from_the_most_complete_trip(ingested):
    connection, _, trip_stats = ingested
    rows = connection.execute(
        """
        SELECT stop_sequence, stop_id, trip_id, trip_count, variant_count
        FROM bus_route_stops WHERE route_id = '1' ORDER BY stop_sequence
        """
    ).fetchall()
    # t1 üç durak, t2 iki durak: en eksiksiz olan seçilir.
    assert [row[1] for row in rows] == ["100", "200", "300"]
    assert {row[2] for row in rows} == {"t1"}
    assert rows[0][3] == 2  # yöndeki sefer sayısı
    assert rows[0][4] == 2  # iki ayrı durak deseni görüldü
    assert trip_stats["route_directions"] == 2


def test_calendar_window_is_recorded_as_usable(ingested):
    connection, _, _ = ingested
    rows = connection.execute(
        "SELECT service_id, monday, saturday, window_usable FROM bus_calendar ORDER BY service_id"
    ).fetchall()
    assert rows[0] == ("0", True, False, True)
    assert rows[1] == ("6", False, True, True)


def test_every_table_carries_source_and_data_as_of(ingested):
    connection, _, _ = ingested
    for table in ("bus_stops", "bus_routes", "bus_calendar", "bus_trips", "bus_route_stops"):
        source, data_as_of = connection.execute(
            f"SELECT source, data_as_of FROM {table} LIMIT 1"
        ).fetchone()
        assert "İETT GTFS" in source
        assert data_as_of == "2026-04-21"
