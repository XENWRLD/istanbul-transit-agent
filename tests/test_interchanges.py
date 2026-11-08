"""Interchanges must be physical, not nominal.

A same-name shortcut once created 26 false interchanges here. The worst had
M5 and Marmaray2 joined at "Altınşehir", 33 km apart, and M4 joined to
Marmaray at six names 1-2 km apart -- M4 runs the E-5 while Marmaray follows
the coast. Routing Kadıköy to Taksim through "Göztepe" teleported the
passenger between two different places.

The same shortcut missed the real M4-Marmaray interchange, whose two records
are 7 m apart but spelled `Ayrılık Çeşmesi` and `Ayrılıkçeşme`.

These tests pin both directions of that failure. The ones touching the store
are skipped when it has not been built.
"""

from __future__ import annotations

import pytest

from istanbul_agent.store.db import database_path
from istanbul_agent.store.geo import haversine_m
from istanbul_agent.store.ingest_rail import INTERCHANGE_RADIUS_M, build_interchanges
from istanbul_agent.store.rail_travel import RailNetwork, RailStation


def row(line, name, key, lat, lon, order=1):
    return {
        "line": line,
        "name": name,
        "station_key": key,
        "lat": lat,
        "lon": lon,
        "stop_order": order,
    }


class TestDistanceRule:
    """build_interchanges decides on metres, never on spelling."""

    def test_same_name_far_apart_is_not_an_interchange(self):
        """The M4/Marmaray Göztepe case: one name, two places 1.8 km apart."""
        stations = {
            ("M4", "goztepe"): row("M4", "Göztepe", "goztepe", 40.9793, 29.0646),
            ("Marmaray", "goztepe"): row("Marmaray", "Göztepe", "goztepe", 40.9664, 29.0599),
        }
        accepted, _ = build_interchanges(stations, sanctioned_pairs=set())
        assert accepted == [], "aynı adlı ama uzak istasyonlar aktarma sayılmamalı"

    def test_different_names_close_together_is_an_interchange(self):
        """The real one: `Ayrılık Çeşmesi` and `Ayrılıkçeşme`, 7 m apart."""
        stations = {
            ("M4", "ayrilik cesmesi"): row(
                "M4", "Ayrılık Çeşmesi", "ayrilik cesmesi", 41.00020, 29.03015
            ),
            ("Marmaray", "ayrilikcesme"): row(
                "Marmaray", "Ayrılıkçeşme", "ayrilikcesme", 41.00014, 29.03014
            ),
        }
        accepted, _ = build_interchanges(stations, sanctioned_pairs=set())
        # One pair, recorded in both directions.
        assert len(accepted) == 2
        assert {entry["from_line"] for entry in accepted} == {"M4", "Marmaray"}
        assert accepted[0]["distance_m"] < 20
        assert accepted[0]["same_name"] is False

    def test_same_line_is_never_an_interchange(self):
        stations = {
            ("M4", "a"): row("M4", "A", "a", 41.0, 29.0),
            ("M4", "b"): row("M4", "B", "b", 41.0, 29.0005),
        }
        accepted, _ = build_interchanges(stations, sanctioned_pairs=set())
        assert accepted == []

    def test_pair_just_outside_the_radius_is_refused(self):
        far = INTERCHANGE_RADIUS_M + 50
        stations = {
            ("M1", "a"): row("M1", "A", "a", 41.0, 29.0),
            ("T1", "b"): row("T1", "B", "b", 41.0 + far / 111_195, 29.0),
        }
        accepted, nearby = build_interchanges(stations, sanctioned_pairs=set())
        assert accepted == []
        assert nearby == [], "313 desteği yoksa yakın liste de boş kalmalı"

    def test_sanctioned_pair_outside_the_radius_is_reported_not_accepted(self):
        """F4-M6 is 463 m and dataset 313 names it: surfaced, not used."""
        stations = {
            ("F4", "bogazici"): row("F4", "Boğaziçi Ü./Hisarüstü", "bogazici", 41.0850, 29.0450),
            ("M6", "bogazici"): row("M6", "Boğaziçi Ü.-Hisarüstü", "bogazici", 41.0892, 29.0450),
        }
        accepted, nearby = build_interchanges(stations, sanctioned_pairs={frozenset({"F4", "M6"})})
        assert accepted == []
        assert len(nearby) == 1
        assert nearby[0]["in_dataset_313"] is True
        assert INTERCHANGE_RADIUS_M < nearby[0]["distance_m"] <= 1000

    def test_station_without_coordinates_cannot_form_an_interchange(self):
        stations = {
            ("M1", "a"): row("M1", "A", "a", 41.0, 29.0),
            ("T1", "b"): row("T1", "B", "b", None, None),
        }
        accepted, _ = build_interchanges(stations, sanctioned_pairs=set())
        assert accepted == []


class TestNetworkRespectsTheTable:
    def test_network_uses_only_the_given_interchanges(self):
        """Even two identically named stations are not joined unless listed."""
        network = RailNetwork(
            stations=[
                RailStation(
                    name="Göztepe", line="M4", mode="metro", order=1, lat=40.9793, lon=29.0646
                ),
                RailStation(
                    name="Göztepe",
                    line="Marmaray",
                    mode="metro",
                    order=1,
                    lat=40.9664,
                    lon=29.0599,
                ),
            ],
            segment_durations={},
            segment_lengths={},
            transfers=[],
            interchanges=[],
        )
        assert network.transfer_options("M4", "Marmaray") == []

    def test_listed_interchange_is_offered(self):
        network = RailNetwork(
            stations=[
                RailStation(name="Ayrılık Çeşmesi", line="M4", mode="metro", order=1),
                RailStation(name="Ayrılıkçeşme", line="Marmaray", mode="metro", order=1),
            ],
            segment_durations={},
            segment_lengths={},
            transfers=[],
            interchanges=[("M4", "ayrilik cesmesi", "Marmaray", "ayrilikcesme")],
        )
        options = network.transfer_options("M4", "Marmaray")
        assert len(options) == 1
        assert options[0][0].line == "M4"
        assert options[0][1].line == "Marmaray"


@pytest.mark.skipif(
    not database_path().exists(), reason="data/transit.duckdb yok; önce ingest_rail çalıştır"
)
class TestStoreIsClean:
    """The regression the user caught: nothing far apart may be an interchange."""

    @pytest.fixture(scope="class")
    @classmethod
    def network(cls) -> RailNetwork:
        from istanbul_agent.store.rail_queries import load_network

        return load_network()

    def test_no_interchange_exceeds_the_radius(self, network):
        offenders: list[str] = []
        lines = sorted({station.line for station in network.stations})
        for from_line in lines:
            for to_line in lines:
                if from_line >= to_line:
                    continue
                for change_from, change_to in network.transfer_options(from_line, to_line):
                    if not (change_from.has_position and change_to.has_position):
                        continue
                    distance = haversine_m(
                        change_from.lat, change_from.lon, change_to.lat, change_to.lon
                    )
                    if distance > INTERCHANGE_RADIUS_M:
                        offenders.append(
                            f"{from_line} {change_from.name} ↔ {to_line} "
                            f"{change_to.name}: {distance:.0f} m"
                        )
        assert not offenders, "yarıçapı aşan aktarmalar:\n  " + "\n  ".join(offenders)

    def test_same_name_different_place_pairs_are_absent(self, network):
        """Spot-check the six M4/Marmaray names that are 1-2 km apart."""
        for name in ("Göztepe", "Bostancı", "Küçükyalı", "Maltepe", "Kartal", "Pendik"):
            for marmaray in ("Marmaray", "Marmaray1", "Marmaray2"):
                for change_from, change_to in network.transfer_options("M4", marmaray):
                    keys = {network.key(change_from.name), network.key(change_to.name)}
                    assert network.key(name) not in keys, (
                        f"M4 ↔ {marmaray} {name} aktarması olmamalı"
                    )

    def test_the_real_m4_marmaray_interchange_exists(self, network):
        found = [
            (change_from, change_to)
            for marmaray in ("Marmaray", "Marmaray1")
            for change_from, change_to in network.transfer_options("M4", marmaray)
            if "ayrilik" in network.key(change_from.name)
        ]
        assert found, "M4 ↔ Marmaray Ayrılık Çeşmesi aktarması bulunamadı"

    def test_kadikoy_to_taksim_routes_over_ayrilik_cesmesi(self, network):
        """The journey the whole project is built around."""
        from istanbul_agent.store.rail_queries import get_rail_travel_time

        result = get_rail_travel_time("Kadıköy", "Taksim", network=network)
        assert result.seconds is not None, "Kadıköy → Taksim cevaplanamadı"
        lines = [leg.line for leg in result.legs]
        assert lines[0] == "M4"
        assert any(line.startswith("Marmaray") for line in lines)
        assert lines[-1] == "M2"
        assert "Ayrılık" in (result.transfer_station or "")
