"""İETT SOAP ve şehir kaynakları: ayrıştırma, kodlar, boş veri.

Ağa çıkılmıyor; Faz 0'da kaydedilen gerçek yanıtlar (`tests/fixtures/`)
kullanılıyor. Her test ölçülmüş bir davranışı sabitliyor (karar 0012):

- SOAP yanıtı JSON'ı **XML gövdesinin içinde** veriyor,
- `SGUNTIPI` kodları `I`/`C`/`P`,
- İSPARK koordinatları **metin**,
- trafik yanıtı 24 saatlik geçmiş veriyor, "şu an" en yeni kayıttır,
- hava kalitesi yanıtında ölçümlerin **tamamı boş**.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from istanbul_agent.clients import iett
from istanbul_agent.clients.base import LiveDataError
from istanbul_agent.clients.city import (
    nearest_station,
    parse_air_quality,
    parse_aqi_stations,
    parse_car_parks,
    parse_traffic_index,
)

FIXTURES = Path(__file__).parent / "fixtures"


def body(relative: str) -> object:
    payload = json.loads((FIXTURES / relative).read_text(encoding="utf-8"))
    return payload.get("response", payload) if isinstance(payload, dict) else payload


# ----------------------------------------------------------------- İETT SOAP


class TestEnvelope:
    def test_the_envelope_carries_the_method_and_parameters(self):
        xml = iett.build_envelope("GetPlanlananSeferSaati_json", {"HatKodu": "500T"}).decode()
        assert "<GetPlanlananSeferSaati_json" in xml
        assert "<HatKodu>500T</HatKodu>" in xml
        assert iett.TEMPURI in xml

    def test_a_method_without_parameters(self):
        xml = iett.build_envelope("GetDuyurular_json").decode()
        assert "<GetDuyurular_json" in xml

    def test_an_unknown_service_raises_instead_of_guessing(self):
        with pytest.raises(KeyError) as error:
            iett.endpoint("bilinmeyen")
        assert "bilinmeyen" in str(error.value)


class TestPayloadExtraction:
    def test_json_inside_the_xml_body_is_extracted(self):
        xml = (
            '<?xml version="1.0"?><soap:Envelope '
            'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
            "<GetDuyurular_jsonResponse><GetDuyurular_jsonResult>"
            '[{"HATKODU": "34", "MESAJ": "test"}]'
            "</GetDuyurular_jsonResult></GetDuyurular_jsonResponse>"
            "</soap:Body></soap:Envelope>"
        )
        rows = iett.extract_payload(xml, source="iett_alerts")
        assert rows == [{"HATKODU": "34", "MESAJ": "test"}]

    def test_a_soap_fault_is_reported_not_parsed(self):
        """Servis hatayı 200 ile de döndürebiliyor; gövdeyi JSON sanmak hatayı gizler."""
        xml = (
            '<?xml version="1.0"?><soap:Envelope '
            'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
            "<soap:Fault><faultstring>Sunucu hatası</faultstring></soap:Fault>"
            "</soap:Body></soap:Envelope>"
        )
        with pytest.raises(LiveDataError) as error:
            iett.extract_payload(xml, source="iett_alerts")
        assert "Sunucu hatası" in error.value.message

    def test_a_body_without_a_json_list_is_an_error(self):
        xml = (
            '<?xml version="1.0"?><soap:Envelope '
            'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            "<soap:Body><X>bos</X></soap:Body></soap:Envelope>"
        )
        with pytest.raises(LiveDataError):
            iett.extract_payload(xml, source="iett_timetable")

    def test_broken_xml_is_an_error(self):
        with pytest.raises(LiveDataError):
            iett.extract_payload("<not xml", source="iett_alerts")


@pytest.fixture(scope="module")
def alert_rows():
    return body("iett/duyurular.GetDuyurular_json.json")


@pytest.fixture(scope="module")
def departure_rows():
    return body("iett/planlanan-sefer-saati.GetPlanlananSeferSaati_json.json")


@pytest.fixture(scope="module")
def parks():
    return parse_car_parks(body("ispark/ispark-park-list.json"))


@pytest.fixture(scope="module")
def stations():
    return parse_aqi_stations(body("air_quality/aqi-stations.json"))


class TestAnnouncements:
    def test_announcements_are_parsed(self, alert_rows):
        alerts = iett.parse_announcements(alert_rows)
        assert alerts
        assert all(alert.message for alert in alerts)

    def test_a_cancelled_trip_is_recognised(self, alert_rows):
        alerts = iett.parse_announcements(alert_rows)
        assert any(alert.is_cancellation for alert in alerts)

    def test_times_are_pulled_out_of_the_message(self):
        alert = iett.Announcement(
            line="10",
            line_name="X",
            kind="Sefer",
            recorded_at="Kayit Saati: 04:05",
            message="Saat 19:10 de hareket etmesi planlanan seferimiz yapilamayacaktir.",
        )
        assert "19:10" in alert.times
        assert alert.is_cancellation is True


class TestPlannedDepartures:
    def test_day_type_codes_are_translated(self, departure_rows):
        """Ölçüm: `SGUNTIPI` alanı `I`, `C`, `P` değerlerini alıyor."""
        departures = iett.parse_planned_departures(departure_rows)
        assert departures
        assert {d.day_type for d in departures} <= {"hafta_ici", "cumartesi", "pazar"}

    def test_an_unknown_day_code_is_kept_not_dropped(self):
        """Sessizce düşürmek, hattın o gün hiç seferi yokmuş gibi gösterir."""
        departures = iett.parse_planned_departures(
            [{"SHATKODU": "X", "SGUNTIPI": "Z", "DT": "06:00"}]
        )
        assert len(departures) == 1
        assert departures[0].day_type == "Z"

    def test_a_row_without_a_time_is_skipped(self):
        assert iett.parse_planned_departures([{"SHATKODU": "X", "DT": ""}]) == []


class TestDepartureSummary:
    def make(self, times: list[str], direction: str = "D") -> list[iett.PlannedDeparture]:
        return [
            iett.PlannedDeparture("500T", "X", "r1", direction, "hafta_ici", "Normal", time)
            for time in times
        ]

    def test_first_last_and_headway(self):
        window = iett.summarise_departures(self.make(["06:00", "06:10", "06:20", "06:30"]))[0]
        assert (window.first, window.last, window.trips) == ("06:00", "06:30", 4)
        assert window.headway_minutes == 10

    def test_fewer_than_four_departures_produce_no_headway(self):
        """Karar 0005'teki kuralın aynısı: üç kalkıştan aralık üretilmez."""
        window = iett.summarise_departures(self.make(["06:00", "07:00", "08:00"]))[0]
        assert window.headway_minutes is None
        assert window.trips == 3

    def test_the_headway_is_the_median_not_the_mean(self):
        """Gece boşluğu ortalamayı bozuyor, ortancayı bozmuyor."""
        window = iett.summarise_departures(
            self.make(["06:00", "06:10", "06:20", "06:30", "23:00"])
        )[0]
        assert window.headway_minutes == 10

    def test_times_past_midnight_sort_last(self):
        window = iett.summarise_departures(self.make(["23:50", "24:10", "00:05"]))[0]
        assert window.first == "00:05"
        assert window.last == "24:10"

    def test_directions_are_reported_separately(self):
        departures = [*self.make(["06:00", "06:10"], "D"), *self.make(["07:00"], "G")]
        windows = iett.summarise_departures(departures)
        assert {window.direction for window in windows} == {"D", "G"}

    def test_filtering_by_day_type(self):
        rows = [
            iett.PlannedDeparture("X", "X", "r", "D", "hafta_ici", "Normal", "06:00"),
            iett.PlannedDeparture("X", "X", "r", "D", "pazar", "Normal", "09:00"),
        ]
        windows = iett.summarise_departures(rows, day_type="pazar")
        assert len(windows) == 1
        assert windows[0].first == "09:00"

    @pytest.mark.parametrize(
        ("date", "expected"),
        [
            (datetime(2026, 11, 11), "hafta_ici"),
            (datetime(2026, 11, 14), "cumartesi"),
            (datetime(2026, 11, 15), "pazar"),
        ],
    )
    def test_today_day_type(self, date, expected):
        assert iett.today_day_type(date) == expected


# ------------------------------------------------------------------ İSPARK


class TestCarParks:
    def test_string_coordinates_are_parsed(self, parks):
        """İSPARK enlem/boylamı metin olarak veriyor."""
        assert parks
        assert all(isinstance(park.lat, float) for park in parks if park.lat is not None)

    def test_occupancy_is_computed_from_capacity(self, parks):
        park = parks[0]
        assert park.capacity and park.empty is not None
        assert park.occupancy_percent == round(100 * (park.capacity - park.empty) / park.capacity)

    def test_occupancy_is_none_without_capacity(self):
        park = parse_car_parks([{"parkID": 1, "parkName": "X", "capacity": None}])[0]
        assert park.occupancy_percent is None

    def test_a_park_without_coordinates_is_kept(self):
        """Adı ve doluluğu yine işe yarar; yalnızca mesafe hesabına girmez."""
        parks = parse_car_parks([{"parkID": 2, "parkName": "Y", "lat": "", "lng": ""}])
        assert len(parks) == 1
        assert parks[0].lat is None


# ------------------------------------------------------------------ trafik


class TestTrafficIndex:
    def test_the_newest_record_is_the_current_value(self):
        index = parse_traffic_index(body("traffic/traffic-index-history.json"))
        assert index.value == 76
        assert index.measured_at.startswith("2026-11-06T17:22")

    def test_order_comes_from_the_timestamp_not_the_list(self):
        """Kaynağın sırası değişirse "şu an" 24 saat önceki değer olurdu."""
        rows = [
            {"TrafficIndex": 10, "TrafficIndexDate": "2026-11-06T10:00:00"},
            {"TrafficIndex": 90, "TrafficIndexDate": "2026-11-06T17:00:00"},
        ]
        assert parse_traffic_index(rows).value == 90

    @pytest.mark.parametrize(
        ("value", "label"),
        [(10, "akıcı"), (30, "hafif yoğun"), (55, "yoğun"), (90, "çok yoğun")],
    )
    def test_labels(self, value, label):
        rows = [{"TrafficIndex": value, "TrafficIndexDate": "2026-11-06T17:00:00"}]
        assert parse_traffic_index(rows).label == label

    def test_trend_needs_at_least_two_points(self):
        rows = [{"TrafficIndex": 50, "TrafficIndexDate": "2026-11-06T17:00:00"}]
        assert parse_traffic_index(rows).trend == "bilinmiyor"

    def test_a_rising_trend(self):
        rows = [
            {"TrafficIndex": 70, "TrafficIndexDate": "2026-11-06T17:20:00"},
            {"TrafficIndex": 60, "TrafficIndexDate": "2026-11-06T17:15:00"},
            {"TrafficIndex": 50, "TrafficIndexDate": "2026-11-06T17:10:00"},
        ]
        assert parse_traffic_index(rows).trend == "artıyor"

    def test_an_empty_response_is_an_error_not_a_zero(self):
        with pytest.raises(LiveDataError):
            parse_traffic_index([])


# ------------------------------------------------------------------ hava kalitesi


class TestAirQuality:
    def test_wkt_locations_are_parsed_longitude_first(self, stations):
        station = next(s for s in stations if s.name == "Maslak")
        # POINT (29.0245 41.1000): WKT'de boylam önce gelir.
        assert station.lat == pytest.approx(41.1000, abs=1e-3)
        assert station.lon == pytest.approx(29.0245, abs=1e-3)

    def test_the_nearest_station_carries_its_distance(self, stations):
        near = nearest_station(stations, 40.9907, 29.0220)
        assert near is not None
        assert near.distance_m and near.distance_m > 0

    def test_all_readings_being_null_yields_none(self, stations):
        """Ölçüm: 721 kaydın tamamında `AQI: null` (karar 0012)."""
        reading = parse_air_quality(body("air_quality/aqi-by-station.json"), stations[0])
        assert reading is None

    def test_a_filled_reading_is_used_and_empties_are_counted(self, stations):
        rows = [
            {"ReadTime": "2026-11-11T10:00:00", "AQI": None, "Concentration": None},
            {"ReadTime": "2026-11-11T11:00:00", "AQI": 42, "Concentration": 12.5},
        ]
        reading = parse_air_quality(rows, stations[0])
        assert reading is not None
        assert (reading.aqi, reading.empty_readings) == (42, 1)
        assert reading.label == "iyi"
