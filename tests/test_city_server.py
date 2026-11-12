"""Şehir araçları: sözleşme, açıklama ve canlı kaynak hatası.

Canlı çağrılar taklit ediliyor. Sınanan şey verinin kendisi değil araçların
**davranışı**: kaynağa ulaşılamadığında boş liste değil hata dönüyor mu,
yarıçap sınırları uygulanıyor mu, araç açıklaması "ne zaman kullanılmaz"
diyor mu (karar 0012).

`get_air_quality` **bilerek yok**: uç nokta cevap veriyor ama ölçüm
değerlerinin tamamı boş (12/12 istasyon). Bir test bunu sabitliyor, çünkü
"araç neden yok" sorusunun cevabı ölçüm.
"""

from __future__ import annotations

import asyncio

import pytest

from istanbul_agent.clients.base import LiveDataError
from istanbul_agent.clients.city import CarPark, TrafficIndex
from istanbul_agent.servers import city_server as server
from istanbul_agent.store.db import database_path
from istanbul_agent.store.mobility import (
    KIND_LABELS,
    KIND_SYNONYMS,
    KIND_VOCABULARY,
    STORED_KINDS,
    resolve_kinds,
    tables_present,
)

TOOLS = ("find_parking", "get_traffic_status", "find_mobility_options")


@pytest.fixture(scope="module")
def listed():
    return {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}


def park(
    name: str,
    lat: float,
    lon: float,
    *,
    capacity: int | None = 100,
    empty: int | None = 20,
    is_open: bool = True,
) -> CarPark:
    return CarPark(
        park_id=abs(hash(name)) % 10_000,
        name=name,
        district="TEST",
        lat=lat,
        lon=lon,
        capacity=capacity,
        empty=empty,
        park_type="KAPALI OTOPARK",
        work_hours="24 Saat",
        free_minutes=15,
        is_open=is_open,
    )


class TestRegistration:
    def test_every_tool_is_exposed(self, listed):
        # Araç tanımı `main()` bloğundan sonra kalırsa stdio'da hiç
        # kaydolmuyor (karar 0012'de ölçüldü: in-process 8, stdio 6).
        assert set(listed) == set(TOOLS)

    @pytest.mark.parametrize("name", TOOLS)
    def test_description_says_when_not_to_use_the_tool(self, listed, name):
        assert "NE ZAMAN KULLANMA" in (listed[name].description or "")

    @pytest.mark.parametrize("name", TOOLS)
    def test_description_documents_every_parameter(self, listed, name):
        description = listed[name].description or ""
        for parameter in (listed[name].inputSchema or {}).get("properties", {}):
            assert parameter in description, f"{name}: {parameter}"

    def test_air_quality_is_deliberately_absent(self, listed):
        """Kaynak cevap veriyor ama bütün ölçüm değerleri boş (karar 0012)."""
        assert "get_air_quality" not in listed

    def test_the_traffic_tool_takes_no_parameters(self, listed):
        assert (listed["get_traffic_status"].inputSchema or {}).get("properties", {}) == {}


class TestFindParking:
    def test_parks_are_sorted_by_distance(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "fetch_car_parks",
            # Taksim 41.0370,28.9870: "Uzak" ~2.5 km, "Yakın" aynı noktada.
            lambda: [park("Uzak", 41.0500, 29.0100), park("Yakın", 41.0370, 28.9870)],
        )
        result = server.find_parking(around="Taksim", radius_m=5000)
        assert result.ok is True
        assert [item.name for item in result.parks] == ["Yakın", "Uzak"]
        assert result.parks[0].distance_m < result.parks[1].distance_m

    def test_occupancy_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            server, "fetch_car_parks", lambda: [park("X", 41.0370, 28.9870, capacity=50, empty=10)]
        )
        result = server.find_parking(around="Taksim")
        assert result.parks[0].occupancy_percent == 80
        assert result.parks[0].occupancy_known is True

    def test_a_park_reporting_zero_is_not_called_full(self, monkeypatch):
        """Araç "%100 dolu" demek yerine doluluğun bilinmediğini söyler."""
        monkeypatch.setattr(
            server,
            "fetch_car_parks",
            lambda: [park("Sessiz", 41.0370, 28.9870, capacity=1310, empty=0)],
        )
        result = server.find_parking(around="Taksim")
        option = result.parks[0]
        assert option.empty == 0
        assert option.occupancy_known is False
        assert option.occupancy_percent is None
        note = " ".join(result.notes)
        assert "Sessiz" in note
        assert "doluluk bildirmediği" in note
        assert "'dolu' deme" in note

    def test_closed_parks_are_hidden_by_default(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "fetch_car_parks",
            lambda: [park("Kapalı", 41.0370, 28.9870, is_open=False)],
        )
        assert server.find_parking(around="Taksim").parks == []
        assert server.find_parking(around="Taksim", only_open=False).parks

    def test_the_radius_is_capped(self, monkeypatch):
        monkeypatch.setattr(server, "fetch_car_parks", lambda: [])
        result = server.find_parking(around="Taksim", radius_m=99_999)
        assert result.radius_m == server.MAX_RADIUS_M

    def test_a_coordinate_works_without_a_name(self, monkeypatch):
        monkeypatch.setattr(server, "fetch_car_parks", lambda: [park("X", 41.0370, 28.9870)])
        result = server.find_parking(lat=41.0370, lon=28.9870)
        assert result.ok is True
        assert result.around.startswith("41.03")

    def test_neither_name_nor_coordinate_is_refused(self, monkeypatch):
        monkeypatch.setattr(server, "fetch_car_parks", lambda: [])
        result = server.find_parking()
        assert result.ok is False
        assert result.error == "yer_bulunamadi"

    def test_an_unknown_place_returns_candidates(self, monkeypatch):
        monkeypatch.setattr(server, "fetch_car_parks", lambda: [])
        result = server.find_parking(around="Paris")
        assert result.ok is False
        assert result.candidates

    def test_an_unreachable_source_is_an_error_not_an_empty_list(self, monkeypatch):
        """ "Otopark yok" ile "otopark verisine ulaşamadım" aynı şey değil."""

        def boom() -> list[CarPark]:
            raise LiveDataError("ispark", "ispark verisine ulaşılamadı: TimeoutException.")

        monkeypatch.setattr(server, "fetch_car_parks", boom)
        result = server.find_parking(around="Taksim")
        assert result.ok is False
        assert result.error == "canli_veri_yok"
        assert result.source == "ispark"
        assert "ulaşılamadı" in result.message

    def test_an_empty_radius_says_so(self, monkeypatch):
        monkeypatch.setattr(server, "fetch_car_parks", lambda: [park("Uzak", 41.5, 29.5)])
        result = server.find_parking(around="Taksim", radius_m=500)
        assert result.parks == []
        assert any("yok" in note for note in result.notes)

    def test_the_answer_carries_when_it_was_fetched(self, monkeypatch):
        monkeypatch.setattr(server, "fetch_car_parks", lambda: [park("X", 41.0370, 28.9870)])
        result = server.find_parking(around="Taksim")
        assert result.data_as_of
        assert "İSPARK" in result.source


class TestTrafficStatus:
    def test_the_index_and_its_trend(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "fetch_traffic_index",
            lambda: TrafficIndex(
                value=72,
                measured_at="2026-11-11T12:00:00",
                previous=(("2026-11-11T11:55:00", 60),),
            ),
        )
        result = server.get_traffic_status()
        assert result.ok is True
        assert (result.index, result.label, result.trend) == (72, "çok yoğun", "artıyor")
        assert result.recent

    def test_the_answer_says_the_index_is_city_wide(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "fetch_traffic_index",
            lambda: TrafficIndex(value=30, measured_at="2026-11-11T12:00:00", previous=()),
        )
        result = server.get_traffic_status()
        assert result.scope == "İstanbul geneli"
        assert any("geneli" in note for note in result.notes)

    def test_an_unreachable_source_is_an_error(self, monkeypatch):
        def boom() -> TrafficIndex:
            raise LiveDataError("traffic", "traffic verisine ulaşılamadı: ConnectError.")

        monkeypatch.setattr(server, "fetch_traffic_index", boom)
        result = server.get_traffic_status()
        assert result.ok is False
        assert result.error == "canli_veri_yok"


needs_store = pytest.mark.skipif(
    not database_path().exists(), reason="data/transit.duckdb kurulmamış"
)
needs_mobility = pytest.mark.skipif(
    not tables_present(), reason="mikromobilite tabloları kurulmamış"
)


class TestMobilityKinds:
    """Tür sözlüğü tabloyla karşılaştırılıyor (karar 0015'in hata sınıfı)."""

    def test_every_synonym_resolves_inside_the_stored_kinds(self):
        for kinds in KIND_SYNONYMS.values():
            assert set(kinds) <= STORED_KINDS

    def test_the_labels_cover_exactly_the_stored_kinds(self):
        assert set(KIND_LABELS) == set(STORED_KINDS)

    def test_every_word_the_error_lists_is_accepted(self):
        # Mesaj ne yazıyorsa araç onu kabul etmek zorunda (karar 0015/4).
        for word in KIND_VOCABULARY:
            kinds, error = resolve_kinds(word)
            assert error is None, word
            assert kinds

    def test_an_unknown_kind_is_an_error_not_an_empty_result(self):
        kinds, error = resolve_kinds("metro")
        assert kinds == ()
        assert error and "bisiklet_park" in error

    def test_no_kind_means_no_constraint(self):
        assert resolve_kinds(None) == ((), None)


@needs_store
@needs_mobility
class TestFindMobilityOptions:
    def test_points_are_sorted_by_distance(self):
        result = server.find_mobility_options(around="Kadıköy", radius_m=600)
        assert result.ok is True
        distances = [point.distance_m for point in result.points]
        assert distances == sorted(distances)

    def test_a_kind_narrows_the_result(self):
        result = server.find_mobility_options(around="Levent", kind="sarj", radius_m=800)
        assert {point.kind for point in result.points} == {"sarj"}

    def test_charging_data_is_marked_stale(self):
        # Kaynak portal tarihi 2025-05; eski veri uyarısız sunulmamalı.
        result = server.find_mobility_options(around="Levent", kind="sarj", radius_m=800)
        assert result.is_stale is True

    def test_fresh_layers_are_not_marked_stale(self):
        result = server.find_mobility_options(around="Kadıköy", kind="park", radius_m=600)
        assert result.points
        assert result.is_stale is False

    def test_socket_counts_are_a_lower_bound_and_say_so(self):
        result = server.find_mobility_options(around="Levent", kind="sarj", radius_m=800)
        assert any("alt sınır" in note for note in result.notes)

    def test_planned_paths_are_hidden_by_default(self):
        result = server.find_mobility_options(around="Kadıköy", radius_m=1500)
        assert all(path.exists_now for path in result.paths)

    def test_planned_paths_can_be_asked_for_and_carry_their_stage(self):
        result = server.find_mobility_options(
            around="Kadıköy", radius_m=1500, include_planned_paths=True
        )
        planned = [path for path in result.paths if not path.exists_now]
        assert planned, "bu yarıçapta planlı kayıt bekleniyor"
        assert all(path.stage for path in planned)

    def test_private_charging_is_hidden_by_default_and_counted(self):
        hidden = server.find_mobility_options(around="Levent", kind="sarj", radius_m=800)
        shown = server.find_mobility_options(
            around="Levent", kind="sarj", radius_m=800, public_only=False, limit=50
        )
        assert all(point.public_access for point in hidden.points)
        assert any(point.public_access is False for point in shown.points)

    def test_the_radius_is_capped(self):
        result = server.find_mobility_options(around="Kadıköy", radius_m=99_999)
        assert result.radius_m == 5000.0

    def test_a_coordinate_works_without_a_name(self):
        result = server.find_mobility_options(lat=40.9907, lon=29.0220, radius_m=400)
        assert result.ok is True
        assert result.around.startswith("40.99")

    def test_neither_name_nor_coordinate_is_refused(self):
        result = server.find_mobility_options()
        assert result.ok is False

    def test_an_unknown_place_returns_candidates(self):
        result = server.find_mobility_options(around="Paris")
        assert result.ok is False
        assert result.candidates

    def test_an_invalid_kind_lists_the_valid_ones(self):
        result = server.find_mobility_options(around="Kadıköy", kind="otobus")
        assert result.ok is False
        assert result.error == "gecersiz_tur"
        assert "sarj" in result.valid_kinds

    def test_an_empty_radius_says_so_instead_of_returning_silence(self):
        # Boş sonuç sessizce dönmesin: not yarıçapı artırmayı söylüyor.
        result = server.find_mobility_options(lat=41.6, lon=28.0, radius_m=100)
        assert result.points == []
        assert any("bulunamadı" in note for note in result.notes)
