"""The MCP tools: their contracts and their descriptions.

The descriptions are tested on purpose. Tool choice is made from them, so a
missing "NE ZAMAN KULLANMA" section is a behaviour change, not a docs
change (CLAUDE.md rule).
"""

from __future__ import annotations

import asyncio

import pytest

from istanbul_agent.servers import transit_server
from istanbul_agent.store.db import database_path

needs_store = pytest.mark.skipif(
    not database_path().exists(), reason="data/transit.duckdb kurulmamış"
)

TOOLS = (
    "get_service_alerts",
    "get_station_accessibility",
    "geocode_place",
    "find_nearby_stops",
    "get_lines_at_stop",
    "get_line_route",
    "get_rail_travel_time",
    "get_next_departures",
)


@pytest.fixture(scope="module")
def listed():
    return {tool.name: tool for tool in asyncio.run(transit_server.mcp.list_tools())}


class TestRegistration:
    def test_every_tool_is_exposed(self, listed):
        assert set(listed) == set(TOOLS)

    @pytest.mark.parametrize("name", TOOLS)
    def test_description_says_when_not_to_use_the_tool(self, listed, name):
        assert "NE ZAMAN KULLANMA" in (listed[name].description or ""), name

    @pytest.mark.parametrize("name", TOOLS)
    def test_description_documents_every_parameter(self, listed, name):
        description = listed[name].description or ""
        for parameter in (listed[name].inputSchema or {}).get("properties", {}):
            assert parameter in description, f"{name}: {parameter}"

    def test_the_travel_tool_takes_two_stations(self, listed):
        properties = listed["get_rail_travel_time"].inputSchema["properties"]
        assert set(properties) == {"from_station", "to_station"}

    def test_the_departures_tool_takes_an_optional_station_and_time(self, listed):
        schema = listed["get_next_departures"].inputSchema
        assert set(schema["properties"]) == {"line", "station", "at"}
        assert schema.get("required") == ["line"]


@needs_store
class TestTravelTimeTool:
    def test_the_main_journey(self):
        result = transit_server.get_rail_travel_time("kadıköy", "taksim")
        assert result.ok is True
        assert result.minutes == 35
        assert [leg.line for leg in result.legs] == ["M4", "Marmaray1", "M2"]
        assert result.transfers == 2
        assert result.is_stale is True

    def test_an_estimate_carries_its_margin(self):
        result = transit_server.get_rail_travel_time("üsküdar", "sultanbeyli")
        assert result.estimated is True
        assert result.error_margin and "dk" in result.error_margin

    def test_an_unknown_station_returns_candidates(self):
        result = transit_server.get_rail_travel_time("paris", "taksim")
        assert result.ok is False
        assert result.error == "istasyon_bulunamadi"
        assert len(result.candidates) == 3
        assert "paris" in result.asked


@needs_store
class TestDeparturesTool:
    def test_station_hours_come_from_the_verified_file(self):
        result = transit_server.get_next_departures("M4", "Kadıköy", at="2026-11-09 18:15")
        assert result.ok is True
        assert result.hours_is_stale is False
        assert result.headway_is_stale is True
        toward_sabiha = next(d for d in result.directions if "Kadıköy" not in d.direction)
        assert toward_sabiha.first_departure == "06:00"
        assert toward_sabiha.last_departure == "00:00"

    def test_night_metro_has_no_last_departure(self):
        result = transit_server.get_next_departures("M2", at="2026-11-06 23:30")
        assert result.day_type == "cuma"
        assert all(d.runs_all_night for d in result.directions)
        assert all(d.last_departure is None for d in result.directions)

    def test_marmaray_answers_with_its_own_headway(self):
        result = transit_server.get_next_departures("Marmaray", "Bostancı", at="2026-11-09 08:30")
        assert result.headway_is_stale is False
        assert {d.headway_minutes for d in result.directions} == {8}

    def test_a_terminus_direction_is_flagged_as_an_arrival(self):
        """ "Kadıköy'den son sefer" must not be answered with 00:49, which is
        when the last train from the other end *reaches* Kadıköy."""
        result = transit_server.get_next_departures("M4", "Kadıköy", at="2026-11-09 18:15")
        toward_kadikoy = next(d for d in result.directions if "Kadıköy" in d.direction)
        away = next(d for d in result.directions if "Kadıköy" not in d.direction)
        assert toward_kadikoy.is_arrival_at_station is True
        assert away.is_arrival_at_station is False
        assert away.last_departure == "00:00"
        # The arrival direction reports no departure at all: the number
        # itself is what gets misquoted as "son kalkış".
        assert toward_kadikoy.last_departure is None
        assert toward_kadikoy.first_departure is None
        assert toward_kadikoy.last_arrival == "00:49"

    def test_a_mid_line_station_is_not_an_arrival_in_either_direction(self):
        """A passenger boards at Bostancı in both directions, so both report
        departures."""
        result = transit_server.get_next_departures("M4", "Bostancı", at="2026-11-09 18:15")
        assert all(not d.is_arrival_at_station for d in result.directions)
        assert all(d.first_departure and d.last_departure for d in result.directions)
        assert all(d.last_arrival is None for d in result.directions)

    def test_a_station_on_another_line_names_that_line(self):
        """Üsküdar is on M5 and Marmaray; asking M4 should redirect, not
        dead-end."""
        result = transit_server.get_next_departures("M4", "Üsküdar")
        assert result.ok is False
        assert result.error == "istasyon_bulunamadi"
        assert "M5" in result.candidates
        assert "M5" in result.message

    def test_an_unknown_line_lists_the_known_ones(self):
        result = transit_server.get_next_departures("M42")
        assert result.ok is False
        assert result.error == "hat_bulunamadi"
        assert "M4" in result.candidates

    def test_a_station_not_on_the_line_returns_candidates(self):
        result = transit_server.get_next_departures("M4", "Taksim")
        assert result.ok is False
        assert result.error == "istasyon_bulunamadi"
        assert result.candidates == ["M2", "F1"] or set(result.candidates) >= {"M2", "F1"}

    def test_a_line_without_a_timetable_is_reported(self):
        result = transit_server.get_next_departures("T5")
        assert result.ok is False
        assert result.error == "tarife_yok"
        assert result.notes

    def test_a_bad_time_is_ignored_rather_than_failing(self):
        result = transit_server.get_next_departures("M2", at="yarın akşam")
        assert result.ok is True


@needs_store
class TestGeocodeTool:
    def test_a_name_becomes_a_coordinate(self):
        result = transit_server.geocode_place("Kadıköy")
        assert result.ok is True
        assert result.match_method == "tam ad"
        first = result.places[0]
        assert 40.9 < first.lat < 41.1
        assert 28.9 < first.lon < 29.1

    def test_rail_is_offered_before_a_bus_flag(self):
        result = transit_server.geocode_place("Kadıköy")
        assert result.places[0].mode in {"metro", "tramvay", "tren", "vapur"}

    def test_a_rail_position_from_the_live_api_is_not_marked_stale(self):
        """Satır tarihi 2020 beslemesinden ama koordinat Metro API'den."""
        result = transit_server.geocode_place("Kadıköy", mode="metro")
        place = result.places[0]
        assert place.is_stale is False
        assert place.data_as_of > "2026-01-01"

    def test_mode_narrows_the_search(self):
        result = transit_server.geocode_place("Kadıköy", mode="otobus")
        assert {place.mode for place in result.places} == {"otobus"}

    def test_duplicate_points_are_merged_and_counted(self):
        result = transit_server.geocode_place("Kadıköy", mode="otobus")
        assert max(place.point_count for place in result.places) > 1

    def test_operator_suffixed_piers_surface_as_related(self):
        result = transit_server.geocode_place("Kadıköy")
        assert any("ŞH" in name or "İDO" in name for name in result.related)

    def test_an_unknown_name_returns_candidates(self):
        result = transit_server.geocode_place("Paris")
        assert result.ok is False
        assert result.error == "yer_bulunamadi"
        assert len(result.candidates) == 3


@needs_store
class TestNearbyTool:
    def test_stops_around_a_named_place(self):
        result = transit_server.find_nearby_stops(around="Kadıköy", radius_m=400)
        assert result.ok is True
        assert result.stops
        distances = [stop.distance_m for stop in result.stops]
        assert distances == sorted(distances)

    def test_a_coordinate_works_without_a_name(self):
        result = transit_server.find_nearby_stops(lat=40.9907, lon=29.0220, radius_m=300)
        assert result.ok is True
        assert result.around.startswith("40.99")

    def test_radius_is_capped(self):
        result = transit_server.find_nearby_stops(around="Kadıköy", radius_m=99_999)
        assert result.radius_m == 2000.0

    def test_mode_filter(self):
        result = transit_server.find_nearby_stops(around="Kadıköy", radius_m=800, mode="vapur")
        assert {stop.mode for stop in result.stops} == {"vapur"}

    def test_neither_name_nor_coordinate_is_refused(self):
        result = transit_server.find_nearby_stops()
        assert result.ok is False

    def test_an_unknown_name_returns_candidates(self):
        result = transit_server.find_nearby_stops(around="Paris")
        assert result.ok is False
        assert result.candidates


@needs_store
class TestLinesAtStopTool:
    def test_a_busy_bus_stop_lists_many_lines(self):
        result = transit_server.get_lines_at_stop("Kadıköy", mode="otobus")
        assert result.ok is True
        assert result.line_count > 20
        assert result.lines[0].trip_count >= result.lines[-1].trip_count

    def test_a_rail_station_lists_its_lines_and_says_the_data_is_old(self):
        result = transit_server.get_lines_at_stop("Üsküdar", mode="metro")
        assert {line.line for line in result.lines} >= {"M5"}
        assert all(line.is_stale for line in result.lines)

    def test_a_pier_says_there_is_no_line_data(self):
        result = transit_server.get_lines_at_stop("Kadıköy ŞH. (Yeni)")
        assert result.ok is True
        assert result.lines == []
        assert any("hat bilgisi yok" in note for note in result.notes)

    def test_an_unknown_stop_returns_candidates(self):
        result = transit_server.get_lines_at_stop("Paris")
        assert result.ok is False
        assert result.candidates


@needs_store
class TestLineRouteTool:
    def test_a_bus_line_has_two_directions_with_ordered_stops(self):
        result = transit_server.get_line_route("500T")
        assert result.ok is True
        assert len(result.directions) == 2
        for direction in result.directions:
            sequences = [stop.sequence for stop in direction.stops]
            assert sequences == sorted(sequences)
            assert direction.stop_count > 50

    def test_service_windows_carry_first_and_last_departure(self):
        route = transit_server.get_line_route("500T").directions[0]
        windows = {window.service: window for window in route.service_windows}
        assert set(windows) >= {"WEEKDAYS", "SATURDAY", "SUNDAY"}
        assert windows["WEEKDAYS"].first_departure
        assert windows["WEEKDAYS"].median_duration_minutes

    def test_trip_count_is_the_direction_total_not_one_variant(self):
        """İki varyantın durak sayısı eşit olduğunda sefer sayısı seçmez."""
        for direction in transit_server.get_line_route("500T").directions:
            assert direction.trip_count > 100

    def test_a_rail_line_is_one_direction_and_marked_stale(self):
        result = transit_server.get_line_route("M4")
        assert len(result.directions) == 1
        route = result.directions[0]
        assert route.stops[0].name == "Kadıköy"
        assert route.is_stale is True

    def test_an_unknown_line_is_refused_with_guidance(self):
        result = transit_server.get_line_route("YOK99")
        assert result.ok is False
        assert "hat kodu" in result.message


@needs_store
class TestModeParameter:
    def test_a_synonym_is_accepted(self):
        """Model `mode="iskele"` yazdı; tablodaki değer `vapur`."""
        result = transit_server.geocode_place("Kadıköy", mode="iskele")
        assert result.ok is True
        assert {place.mode for place in result.places} == {"vapur"}
        # İskeleler işletmeci ekiyle kayıtlı; dördü de Kadıköy iskelesi.
        assert any("ŞH" in place.name or "İDO" in place.name for place in result.places)

    def test_turkish_spelling_of_bus_is_accepted(self):
        result = transit_server.find_nearby_stops(around="Kadıköy", mode="otobüs", radius_m=300)
        assert result.ok is True
        assert {stop.mode for stop in result.stops} == {"otobus"}

    def test_an_unknown_mode_is_refused_with_the_valid_list(self):
        result = transit_server.geocode_place("Kadıköy", mode="uçak")
        assert result.ok is False
        assert result.error == "gecersiz_mod"
        assert "vapur" in result.valid_modes

    def test_lines_at_stop_refuses_an_unknown_mode_too(self):
        result = transit_server.get_lines_at_stop("Kadıköy", mode="uçak")
        assert result.error == "gecersiz_mod"


class TestStoreGuard:
    """Depo kurulmamışken araçlar ham istisna değil, yapılacak işi döner.

    Kurulum adımını atlayan biri (README adım 4) ham bir DuckDB
    `IOException` görüyordu; bu, MCP üzerinden yığın izi olarak dönüyor ve
    kullanıcıya ne yapacağını söylemiyordu (karar 0011).
    """

    @pytest.fixture
    def no_store(self, monkeypatch, tmp_path):
        from istanbul_agent.store import db

        monkeypatch.setattr(db, "database_path", lambda: tmp_path / "yok.duckdb")
        return tmp_path

    @pytest.mark.parametrize(
        ("name", "args"),
        [
            ("geocode_place", ("Kadıköy",)),
            ("find_nearby_stops", ("Kadıköy",)),
            ("get_lines_at_stop", ("Kadıköy",)),
            ("get_line_route", ("500T",)),
            ("get_rail_travel_time", ("Kadıköy", "Taksim")),
            ("get_next_departures", ("M4",)),
        ],
    )
    def test_every_tool_reports_the_missing_store(self, no_store, name, args):
        result = getattr(transit_server, name)(*args)
        assert result.ok is False
        assert result.error == "depo_yok"
        assert "ingest" in result.message

    def test_the_message_names_the_database(self, no_store):
        result = transit_server.geocode_place("Kadıköy")
        assert "yok.duckdb" in result.message


class TestServiceAlerts:
    """Canlı duyurular: kaynak ayrımı, mod çıkarımı, kısmi başarısızlık."""

    def bus_alert(self, line: str = "500T", message: str = "Sefer yapilamayacaktir.") -> object:
        from istanbul_agent.clients.iett import Announcement

        return Announcement(
            line=line,
            line_name="X",
            kind="Sefer",
            recorded_at="Kayit Saati: 04:05",
            message=message,
        )

    def metro_alert(self, lines: set[str] | None = None) -> object:
        from istanbul_agent.clients.metro import Announcement as MetroAnnouncement

        return MetroAnnouncement(
            id=1,
            title="Sefer düzenlemesi",
            text="M7 seferleri düzenlendi.",
            lines=frozenset(lines or set()),
        )

    @pytest.fixture
    def stub(self, monkeypatch):
        import istanbul_agent.clients.metro as metro_client
        from istanbul_agent.clients import iett

        state = {
            "bus": [self.bus_alert()],
            "metro": [self.metro_alert({"M7"})],
            "status": [],
        }
        monkeypatch.setattr(iett, "fetch_announcements", lambda: state["bus"])
        monkeypatch.setattr(transit_server, "fetch_announcements", lambda: state["metro"])
        monkeypatch.setattr(metro_client, "fetch_service_statuses", lambda: state["status"])
        return state

    def test_a_bus_line_only_scans_iett(self, stub):
        """Ölçümde ağ geneli bir metro duyurusu otobüs sorgusuna sızmıştı."""
        result = transit_server.get_service_alerts(line="500T")
        assert result.ok is True
        assert result.asked_mode == "otobus"
        assert result.sources_ok == ["İETT duyuruları"]
        assert {alert.source for alert in result.alerts} == {"İETT"}

    def test_a_rail_line_only_scans_metro(self, stub):
        result = transit_server.get_service_alerts(line="M7")
        assert result.asked_mode == "rayli"
        assert "İETT duyuruları" not in result.sources_ok

    @pytest.mark.parametrize(
        ("line", "mode"),
        [
            ("500T", "otobus"),
            ("34", "otobus"),
            ("M2", "rayli"),
            ("T1", "rayli"),
            ("Marmaray", "rayli"),
        ],
    )
    def test_the_mode_is_inferred_from_the_line_code(self, stub, line, mode):
        assert transit_server.get_service_alerts(line=line).asked_mode == mode

    def test_a_cancellation_is_flagged(self, stub):
        result = transit_server.get_service_alerts(line="500T")
        assert result.alerts[0].is_cancellation is True

    def test_a_line_without_alerts_says_so(self, stub):
        stub["bus"] = []
        result = transit_server.get_service_alerts(line="500T")
        assert result.alerts == []
        assert any("duyuru yok" in note for note in result.notes)

    def test_one_failing_source_still_answers_and_says_which(self, stub, monkeypatch):
        from istanbul_agent.clients import iett
        from istanbul_agent.clients.base import LiveDataError

        def boom() -> list:
            raise LiveDataError("iett_alerts", "iett_alerts verisine ulaşılamadı.")

        monkeypatch.setattr(iett, "fetch_announcements", boom)
        result = transit_server.get_service_alerts()
        assert result.ok is True
        assert result.sources_failed
        assert any("ulaşılamadı" in note for note in result.notes)

    def test_every_source_failing_is_an_error(self, stub, monkeypatch):
        import istanbul_agent.clients.metro as metro_client
        from istanbul_agent.clients import iett
        from istanbul_agent.clients.base import LiveDataError

        def boom(*args: object, **kwargs: object) -> list:
            raise LiveDataError("x", "ulaşılamadı")

        monkeypatch.setattr(iett, "fetch_announcements", boom)
        monkeypatch.setattr(transit_server, "fetch_announcements", boom)
        monkeypatch.setattr(metro_client, "fetch_service_statuses", boom)
        result = transit_server.get_service_alerts()
        assert result.ok is False
        assert result.error == "canli_veri_yok"


class TestStationAccessibility:
    @pytest.fixture
    def stub(self, monkeypatch):
        import istanbul_agent.clients.metro as metro_client
        from istanbul_agent.clients.metro import (
            EquipmentCount,
            FaultyEquipment,
            StationFacilities,
        )

        state = {
            "facilities": [
                StationFacilities(
                    station="Kadıköy", line="M4", escalators=16, lifts=6, wc=True, baby_room=False
                ),
                StationFacilities(
                    station="Ayrılık Çeşmesi",
                    line="M4",
                    escalators=4,
                    lifts=0,
                    wc=False,
                    baby_room=False,
                ),
            ],
            "counts": [
                EquipmentCount(group="Asansör", name="Asansörler", active=669, inactive=29),
                EquipmentCount(
                    group="Yürüyen Merdiven", name="Yürüyen Merdiven", active=1966, inactive=56
                ),
            ],
            "faults": [
                FaultyEquipment(
                    group="Asansör",
                    line="M4",
                    station="Kadıköy",
                    location="",
                    kind="Arıza",
                    date="2026-11-11T00:00:00.000",
                    description="M4/AS/1",
                )
            ],
        }
        monkeypatch.setattr(metro_client, "fetch_station_facilities", lambda: state["facilities"])
        monkeypatch.setattr(metro_client, "fetch_equipment_counts", lambda: state["counts"])
        monkeypatch.setattr(metro_client, "fetch_faulty_equipment", lambda group: state["faults"])
        return state

    def test_a_station_reports_its_equipment_and_faults(self, stub):
        result = transit_server.get_station_accessibility("Kadıköy")
        assert result.ok is True
        station = result.stations[0]
        assert (station.lifts, station.escalators) == (6, 16)
        assert station.faults and "Asansör" in station.faults[0]

    def test_a_station_without_a_lift_is_called_out(self, stub):
        result = transit_server.get_station_accessibility("Ayrılık Çeşmesi")
        assert result.stations[0].lifts == 0
        assert any("asansör bulunmuyor" in note.casefold() for note in result.notes)

    def test_without_a_station_only_network_counts_come_back(self, stub):
        result = transit_server.get_station_accessibility()
        assert result.stations == []
        assert result.network_faults["Asansörler"] == 29

    def test_an_unknown_station_returns_candidates(self, stub):
        result = transit_server.get_station_accessibility("Paris")
        assert result.ok is False
        assert result.error == "istasyon_bulunamadi"

    def test_the_answer_warns_that_the_list_is_not_a_guarantee(self, stub):
        result = transit_server.get_station_accessibility("Kadıköy")
        assert any("garantisi değildir" in note for note in result.notes)

    def test_an_unreachable_source_is_an_error(self, monkeypatch):
        import istanbul_agent.clients.metro as metro_client
        from istanbul_agent.clients.base import LiveDataError

        def boom(*args: object, **kwargs: object) -> list:
            raise LiveDataError("metro_equipment", "ulaşılamadı")

        monkeypatch.setattr(metro_client, "fetch_station_facilities", boom)
        result = transit_server.get_station_accessibility("Kadıköy")
        assert result.ok is False
        assert result.error == "canli_veri_yok"


class TestBusDepartures:
    @pytest.fixture
    def stub(self, monkeypatch):
        from istanbul_agent.clients import iett

        departures = [
            iett.PlannedDeparture(
                "500T", "TUZLA - CEVİZLİBAĞ", "r1", "D", "hafta_ici", "Normal", time
            )
            for time in ("05:00", "05:10", "05:20", "05:30")
        ]
        monkeypatch.setattr(iett, "fetch_planned_departures", lambda line: departures)
        return departures

    def test_a_bus_line_answers_from_the_live_service(self, stub):
        result = transit_server.get_next_departures("500T")
        assert result.ok is True
        assert result.mode == "otobus"
        window = result.windows[0]
        assert (window.first_departure, window.last_departure) == ("05:00", "05:30")
        assert window.headway_minutes == 10

    def test_the_answer_says_the_times_are_from_the_first_stop(self, stub):
        result = transit_server.get_next_departures("500T")
        assert any("ilk durağından" in note for note in result.notes)

    def test_a_line_with_no_departures_is_not_found(self, monkeypatch):
        from istanbul_agent.clients import iett

        monkeypatch.setattr(iett, "fetch_planned_departures", lambda line: [])
        result = transit_server.get_next_departures("YOK99")
        assert result.ok is False
        assert result.error == "hat_bulunamadi"

    def test_an_unreachable_service_is_reported(self, monkeypatch):
        from istanbul_agent.clients import iett
        from istanbul_agent.clients.base import LiveDataError

        def boom(line: str) -> list:
            raise LiveDataError("iett_timetable", "iett_timetable verisine ulaşılamadı.")

        monkeypatch.setattr(iett, "fetch_planned_departures", boom)
        result = transit_server.get_next_departures("500T")
        assert result.ok is False
        assert result.error == "canli_veri_yok"
