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

TOOLS = ("get_rail_travel_time", "get_next_departures")


@pytest.fixture(scope="module")
def listed():
    return {tool.name: tool for tool in asyncio.run(transit_server.mcp.list_tools())}


class TestRegistration:
    def test_both_tools_are_exposed(self, listed):
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
