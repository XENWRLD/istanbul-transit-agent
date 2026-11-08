"""Finding the station the user typed, and offering candidates when not.

The tools are called with free text, so these are the spellings a user
actually produces: no Turkish letters, wrong case, a missing suffix, a typo.
"""

from __future__ import annotations

import pytest

from istanbul_agent.store.db import database_path
from istanbul_agent.store.station_search import (
    CANDIDATE_COUNT,
    find_station,
    suggest_stations,
)

needs_store = pytest.mark.skipif(
    not database_path().exists(), reason="data/transit.duckdb kurulmamış"
)


@pytest.fixture(scope="module")
def network():
    from istanbul_agent.store.rail_queries import load_network

    return load_network()


@needs_store
class TestSpellings:
    @pytest.mark.parametrize(
        ("typed", "expected"),
        [
            ("kadıköy", "Kadıköy"),
            ("KADIKOY", "Kadıköy"),
            ("kadikoy", "Kadıköy"),
            ("taksim", "Taksim"),
            ("uskudar", "Üsküdar"),
            ("asiyan", "Aşiyan"),
            ("mecidiyekoy", "Mecidiyeköy"),
            ("ayrılık çeşmesi", "Ayrılık Çeşmesi"),
            ("4. levent", "4.Levent"),
        ],
    )
    def test_a_user_spelling_finds_the_station(self, network, typed, expected):
        match = find_station(network, typed)
        assert match.found, typed
        assert match.name == expected

    def test_a_missing_word_is_resolved_by_prefix(self, network):
        match = find_station(network, "sabiha gökçen")
        assert match.name == "Sabiha Gökçen Havalimanı"
        assert match.how == "ön ek"

    def test_the_lines_serving_the_station_come_back(self, network):
        match = find_station(network, "yenikapı")
        assert {"M2", "Marmaray"} <= set(match.lines)

    def test_an_empty_name_is_not_a_match(self, network):
        assert find_station(network, "   ").found is False


@needs_store
class TestCandidates:
    def test_an_unknown_name_returns_three_candidates(self, network):
        match = find_station(network, "paris")
        assert match.found is False
        assert len(match.candidates) == CANDIDATE_COUNT

    def test_candidates_are_real_station_names(self, network):
        match = find_station(network, "zzzz")
        names = {station.name for station in network.stations}
        for candidate in suggest_stations(network, "zzzz"):
            assert candidate in names
        assert match.found is False

    def test_suggestions_never_come_back_empty(self, network):
        assert suggest_stations(network, "qqqqqqqq")


@needs_store
class TestLineScope:
    def test_a_search_can_be_restricted_to_one_line(self, network):
        """Göztepe exists on M4 and on Marmaray, 1.8 km apart."""
        m4 = find_station(network, "göztepe", line="M4")
        marmaray = find_station(network, "göztepe", line="Marmaray")
        assert m4.station.line == "M4"
        assert marmaray.station.line == "Marmaray"
        assert m4.station.lat != marmaray.station.lat

    def test_a_station_on_another_line_is_not_found(self, network):
        match = find_station(network, "Taksim", line="M4")
        assert match.found is False
        assert match.candidates
