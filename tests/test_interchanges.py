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
from istanbul_agent.store.ingest_rail import build_interchanges, sanctioned_station_pairs
from istanbul_agent.store.rail_travel import (
    INTERCHANGE_RADIUS_M,
    SANCTIONED_INTERCHANGE_RADIUS_M,
    WALK_SPEED_MS,
    RailNetwork,
    RailStation,
    transfer_penalty_seconds,
)


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
        assert len(nearby) == 1, "sınırın hemen dışındaki çift raporlanmalı"

    def test_sanctioned_pair_outside_the_normal_radius_is_accepted(self):
        """F4-M6 is 463 m and dataset 313 names it, so it stands.

        The operator saying a change exists outweighs our distance
        heuristic; the extra metres are paid for in walking time instead.
        """
        stations = {
            ("F4", "bogazici"): row("F4", "Boğaziçi Ü./Hisarüstü", "bogazici", 41.0850, 29.0450),
            ("M6", "bogazici"): row("M6", "Boğaziçi Ü.-Hisarüstü", "bogazici", 41.0892, 29.0450),
        }
        accepted, nearby = build_interchanges(
            stations,
            sanctioned_pairs={frozenset({("F4", "bogazici"), ("M6", "bogazici")})},
        )
        assert nearby == []
        assert len(accepted) == 2
        assert accepted[0]["in_dataset_313"] is True
        assert INTERCHANGE_RADIUS_M < accepted[0]["distance_m"] <= SANCTIONED_INTERCHANGE_RADIUS_M
        # Walked, not strolled across a platform: 463 m at 1.2 m/s.
        assert accepted[0]["walk_s"] == pytest.approx(
            accepted[0]["distance_m"] / WALK_SPEED_MS, abs=1
        )

    def test_unsanctioned_pair_inside_the_wide_radius_is_still_refused(self):
        """500 m with no 313 entry is not an interchange."""
        stations = {
            ("M1", "a"): row("M1", "A", "a", 41.0, 29.0),
            ("T1", "b"): row("T1", "B", "b", 41.0 + 500 / 111_195, 29.0),
        }
        accepted, nearby = build_interchanges(stations, sanctioned_pairs=set())
        assert accepted == []
        assert len(nearby) == 1
        assert nearby[0]["in_dataset_313"] is False

    def test_sanctioned_pair_beyond_the_wide_radius_is_refused(self):
        """313 widens the radius; it does not remove it."""
        stations = {
            ("F4", "a"): row("F4", "A", "a", 41.0, 29.0),
            ("M6", "b"): row("M6", "B", "b", 41.0 + 1500 / 111_195, 29.0),
        }
        accepted, nearby = build_interchanges(
            stations, sanctioned_pairs={frozenset({("F4", "a"), ("M6", "b")})}
        )
        assert accepted == []
        assert nearby == [], "1 km ötesi hiç raporlanmıyor"

    def test_station_without_coordinates_cannot_form_an_interchange(self):
        stations = {
            ("M1", "a"): row("M1", "A", "a", 41.0, 29.0),
            ("T1", "b"): row("T1", "B", "b", None, None),
        }
        accepted, _ = build_interchanges(stations, sanctioned_pairs=set())
        assert accepted == []


class TestSanctionedPairs:
    """313 widens the radius for a named pair of stations, not of lines."""

    def test_both_station_names_are_resolved_to_the_feed(self):
        """313 writes `Vadi İstanbul`; the feed writes `Vadistanbul`."""
        from istanbul_agent.store.rail_reference import Transfer

        stations = {
            ("F3", "vadistanbul"): row("F3", "Vadistanbul", "vadistanbul", 41.11, 28.99),
            ("M2A", "seyrantepe"): row("M2A", "Seyrantepe", "seyrantepe", 41.105, 28.997),
        }
        transfers = [
            Transfer(
                line_family="F3",
                from_line="F3",
                from_station="Vadi İstanbul",
                to_line="M2",
                to_station="Seyrantepe",
                to_is_rail=True,
            )
        ]
        pairs = sanctioned_station_pairs(transfers, stations)
        assert frozenset({("F3", "vadistanbul"), ("M2A", "seyrantepe")}) in pairs

    def test_a_named_pair_does_not_sanction_the_whole_line_pair(self):
        """`M4 Kadıköy -> T3` must not attach Kadıköy to all of T3.

        Sanctioning line pairs instead of station pairs pulled seven T3
        stations up to 826 m from M4 Kadıköy into the table.
        """
        from istanbul_agent.store.rail_reference import Transfer

        stations = {
            ("M4", "kadikoy"): row("M4", "Kadıköy", "kadikoy", 40.9926, 29.0238),
            ("T3", "rizapasa"): row("T3", "Rızapaşa", "rizapasa", 40.9900, 29.0280),
            ("T3", "bahariye"): row("T3", "Bahariye", "bahariye", 40.9880, 29.0310),
        }
        transfers = [
            Transfer(
                line_family="M4",
                from_line="M4",
                from_station="Kadıköy",
                to_line="T3",
                to_station="Rızapaşa",
                to_is_rail=True,
            )
        ]
        pairs = sanctioned_station_pairs(transfers, stations)
        assert frozenset({("M4", "kadikoy"), ("T3", "rizapasa")}) in pairs
        assert frozenset({("M4", "kadikoy"), ("T3", "bahariye")}) not in pairs


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

    def test_no_interchange_exceeds_its_radius(self, network):
        """Every stored pair must respect the radius that applies to it.

        400 m normally; 1 km for a pair dataset 313 names, which is the
        only reason F4 is attached to the network at all.
        """
        from istanbul_agent.store.db import read_only_connection

        with read_only_connection() as connection:
            rows = connection.execute(
                "SELECT from_line, from_name, to_line, to_name, distance_m, "
                "in_dataset_313, walk_s FROM rail_interchanges"
            ).fetchall()
        assert rows, "aktarma tablosu boş"

        offenders: list[str] = []
        for from_line, from_name, to_line, to_name, distance, backed, walk_s in rows:
            limit = SANCTIONED_INTERCHANGE_RADIUS_M if backed else INTERCHANGE_RADIUS_M
            if distance > limit:
                offenders.append(
                    f"{from_line} {from_name} ↔ {to_line} {to_name}: "
                    f"{distance:.0f} m > {limit:.0f} m (313: {backed})"
                )
            expected = transfer_penalty_seconds(distance) - 210
            if walk_s != expected:
                offenders.append(
                    f"{from_line} {from_name} ↔ {to_line} {to_name}: "
                    f"yürüme {walk_s} s, beklenen {expected} s"
                )
        assert not offenders, "kuralı aşan aktarmalar:\n  " + "\n  ".join(offenders)

    def test_a_far_interchange_costs_more_than_a_platform_change(self, network):
        """F4 to M6 is a 463 m street walk, not a cross-platform change."""
        options = network.transfer_options("F4", "M6")
        assert options, "F4 ↔ M6 aktarması bulunamadı (313 destekli, 463 m)"
        change_from, change_to = options[0]
        penalty = network.transfer_penalty("F4", change_from.name, "M6", change_to.name)
        assert penalty > 210 + 150, "uzak aktarma düz 150 s yürümeyle fiyatlanmamalı"
        assert penalty == pytest.approx(
            210
            + haversine_m(change_from.lat, change_from.lon, change_to.lat, change_to.lon)
            / WALK_SPEED_MS,
            abs=2,
        )

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
