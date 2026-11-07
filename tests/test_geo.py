"""Distance helpers used for station matching and travel-time estimates."""

import pytest

from istanbul_agent.store.geo import haversine_m, parse_coordinate


class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine_m(41.0, 29.0, 41.0, 29.0) == 0.0

    def test_symmetric(self):
        a = haversine_m(41.0055, 28.9513, 41.0366, 28.9868)
        b = haversine_m(41.0366, 28.9868, 41.0055, 28.9513)
        assert a == pytest.approx(b)

    def test_known_istanbul_distance(self):
        """Kadıköy to Üsküdar ferry piers: about 3 km across the Bosphorus."""
        distance = haversine_m(40.9923, 29.0244, 41.0255, 29.0133)
        assert 3_000 < distance < 4_200

    def test_one_degree_of_latitude(self):
        """A degree of latitude is ~111 km anywhere."""
        assert haversine_m(41.0, 29.0, 42.0, 29.0) == pytest.approx(111_195, rel=0.01)

    def test_matches_measured_station_pair(self):
        """M6 Boğaziçi Üniversitesi: the 9 m pair from decision 0003."""
        distance = haversine_m(41.0858, 29.0176, 41.08581, 29.01771)
        assert distance < 300


class TestParseCoordinate:
    def test_float_passthrough(self):
        assert parse_coordinate(41.0055) == 41.0055

    def test_string_from_metro_api(self):
        # The Metro API returns coordinates as strings inside DetailInfo.
        assert parse_coordinate("41.0055971704") == pytest.approx(41.0055971704)

    def test_comma_decimal_separator(self):
        assert parse_coordinate("41,0055") == pytest.approx(41.0055)

    def test_surrounding_whitespace(self):
        assert parse_coordinate("  29.0133  ") == pytest.approx(29.0133)

    @pytest.mark.parametrize(
        "value",
        [None, "", "   ", "abc", "null", 0, 0.0, "0", "0.0", 181.0, -181.0, True, False],
    )
    def test_unusable_values_give_none(self, value):
        """A zero placeholder must not become a point in the Gulf of Guinea."""
        assert parse_coordinate(value) is None
