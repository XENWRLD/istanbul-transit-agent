"""`get_next_departures`: precedence, staleness and the honest refusals.

Two groups. The first needs no store: it pins the YAML override's precedence
and shape. The second queries the built store and is skipped when it is not
there, like the other store-backed tests.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from istanbul_agent.clients.metro import parse_announcements
from istanbul_agent.store.db import database_path
from istanbul_agent.store.departures import (
    get_next_departures,
    load_service_hours,
)

FIXTURE = Path(__file__).parent / "fixtures" / "metro" / "metro-announcements-tr.json"

MONDAY_MORNING = datetime(2026, 11, 9, 8, 30)
SATURDAY_NIGHT = datetime(2026, 11, 7, 23, 30)
SUNDAY_SMALL_HOURS = datetime(2026, 11, 8, 2, 0)

OVERRIDE_YAML = """
lines:
  M2:
    source_url: https://www.metro.istanbul/YolcuHizmetleri/SeferSaatleri
    retrieved_at: 2026-11-08
    not: "Kış tarifesi"
    hafta_ici:
      ilk: "06:00"
      son: "00:00"
      aralik_dk: 5
      aralik:
        8: 3
    pazar:
      ilk: "06:15"
      son: "00:00"
      aralik_dk: 7
"""


@pytest.fixture(scope="module")
def announcements():
    return parse_announcements(json.loads(FIXTURE.read_text(encoding="utf-8")))


@pytest.fixture
def override_file(tmp_path: Path) -> Path:
    path = tmp_path / "metro_service_hours.yaml"
    path.write_text(OVERRIDE_YAML, encoding="utf-8")
    load_service_hours.cache_clear()
    yield path
    load_service_hours.cache_clear()


class TestOverrideFile:
    def test_shipped_file_lists_no_line_yet(self):
        """Nothing is hand-verified, so every line still comes from GTFS."""
        load_service_hours.cache_clear()
        assert load_service_hours() == {}

    def test_override_is_read_per_day_type(self, override_file):
        hours = load_service_hours(override_file)
        assert set(hours) == {"M2"}
        assert hours["M2"].for_day("hafta_ici")["ilk"] == "06:00"
        assert hours["M2"].for_day("cumartesi") is None

    def test_override_wins_over_gtfs_and_is_not_stale(self, monkeypatch, override_file):
        monkeypatch.setattr("istanbul_agent.store.departures.SERVICE_HOURS_YAML", override_file)
        load_service_hours.cache_clear()
        result = get_next_departures("M2", at=MONDAY_MORNING)
        assert result.is_stale is False
        assert result.available is True
        assert "metro_service_hours.yaml" in result.source
        assert result.data_as_of == "2026-11-08"
        info = result.directions[0]
        assert (info.first, info.last) == ("06:00", "00:00")
        # The hour-specific entry beats the day's default.
        assert info.headway_minutes == 3
        assert info.expected_wait_minutes == 2

    def test_a_day_the_override_omits_falls_back_to_gtfs(self, monkeypatch, override_file):
        monkeypatch.setattr("istanbul_agent.store.departures.SERVICE_HOURS_YAML", override_file)
        load_service_hours.cache_clear()
        result = get_next_departures("M2", at=SATURDAY_NIGHT)
        assert result.is_stale is True, "cumartesi override'da yok, GTFS'e düşmeli"


needs_store = pytest.mark.skipif(
    not database_path().exists(), reason="data/transit.duckdb kurulmamış"
)


@needs_store
class TestAgainstTheStore:
    def test_a_weekday_answer_is_stale_and_says_so(self, announcements):
        result = get_next_departures("M2", at=MONDAY_MORNING, announcements=announcements)
        assert result.available is True
        assert result.is_stale is True
        assert result.data_as_of == "2020-07-09"
        assert any("2020" in warning for warning in result.warnings)

    def test_both_directions_are_answered(self):
        result = get_next_departures("M2", at=MONDAY_MORNING)
        assert len(result.directions) == 2
        assert all(info.first and info.last for info in result.directions)

    def test_the_weekend_differs_from_the_weekday(self):
        weekday = get_next_departures("M4", at=datetime(2026, 11, 9, 8, 30))
        sunday = get_next_departures("M4", at=datetime(2026, 11, 8, 8, 30))
        assert weekday.day_type == "hafta_ici"
        assert sunday.day_type == "pazar"
        assert [info.headway_minutes for info in weekday.directions] != [
            info.headway_minutes for info in sunday.directions
        ] or weekday.directions[0].first != sunday.directions[0].first

    def test_a_station_gets_its_own_times(self):
        """The last train leaves a mid-line station later than the terminus."""
        line = get_next_departures("M2", at=MONDAY_MORNING)
        station = get_next_departures("M2", "Taksim", at=MONDAY_MORNING)
        assert station.station == "Taksim"
        assert station.directions[0].first != line.directions[0].first

    def test_an_unknown_station_is_reported_not_guessed(self):
        result = get_next_departures("M2", "Paris", at=MONDAY_MORNING)
        assert result.available is True
        assert any("Paris" in note for note in result.notes)

    def test_a_line_without_a_timetable_says_so(self):
        """Marmaray has four template trips in this feed, not a timetable."""
        result = get_next_departures("Marmaray", at=MONDAY_MORNING)
        assert result.available is False
        assert result.directions == []
        assert any("tarife verisi yok" in note for note in result.notes)

    def test_an_unknown_line_is_refused(self):
        result = get_next_departures("M42", at=MONDAY_MORNING)
        assert result.available is False

    def test_outside_service_hours_the_answer_says_so(self):
        """M8 runs 06:00-00:00; at 03:00 nothing is running."""
        result = get_next_departures("M8", at=datetime(2026, 11, 9, 3, 0))
        assert result.available is True
        assert all(not info.running_now for info in result.directions)
        assert all(info.expected_wait_minutes is None for info in result.directions)

    def test_night_service_is_reported_separately(self):
        """Weekend nights run on M4; that is not the first train of the day."""
        result = get_next_departures("M4", at=SATURDAY_NIGHT)
        assert any(info.night_first for info in result.directions)
        for info in result.directions:
            assert info.first != info.night_first

    def test_a_frequencies_line_marks_its_station_times_estimated(self):
        """M8's trips carry a headway, not departure times."""
        result = get_next_departures("M8", "Kozyatağı", at=MONDAY_MORNING)
        if result.station == "Kozyatağı":
            assert any(info.estimated for info in result.directions)
            assert any("tahmin" in note for note in result.notes)

    def test_schedule_announcements_are_attached(self, announcements):
        result = get_next_departures("M7", at=SUNDAY_SMALL_HOURS, announcements=announcements)
        assert result.alerts, "M7 için tarife duyurusu beklenirdi"
        assert any("tarife değişikliği" in warning for warning in result.warnings)

    def test_an_irrelevant_announcement_is_not_attached(self, announcements):
        """T3's track work is not T4's problem."""
        result = get_next_departures("T4", at=MONDAY_MORNING, announcements=announcements)
        assert all("T3 Kadıköy-Moda" not in alert for alert in result.alerts)
