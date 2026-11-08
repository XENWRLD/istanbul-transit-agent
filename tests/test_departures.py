"""`get_next_departures`: precedence, the two staleness flags, refusals.

The hand-verified hours in `datasets/metro_service_hours.yaml` replace the
feed's hours for the lines they cover, while the headway still comes from
the feed for everything but Marmaray. These tests pin that split, the
terminal-to-station shifting, night metro, and the cases where the tool has
to say it does not know.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from istanbul_agent.clients.metro import parse_announcements
from istanbul_agent.store.db import database_path
from istanbul_agent.store.departures import get_next_departures

FIXTURE = Path(__file__).parent / "fixtures" / "metro" / "metro-announcements-tr.json"

MONDAY = datetime(2026, 11, 9, 8, 30)
MONDAY_EVENING = datetime(2026, 11, 9, 18, 15)
FRIDAY_NIGHT = datetime(2026, 11, 6, 23, 30)
SATURDAY_SMALL_HOURS = datetime(2026, 11, 7, 3, 0)
SUNDAY = datetime(2026, 11, 8, 11, 0)
SUNDAY_SMALL_HOURS = datetime(2026, 11, 8, 2, 0)


@pytest.fixture(scope="module")
def announcements():
    return parse_announcements(json.loads(FIXTURE.read_text(encoding="utf-8")))


needs_store = pytest.mark.skipif(
    not database_path().exists(), reason="data/transit.duckdb kurulmamış"
)


@needs_store
class TestHandVerifiedHours:
    def test_hours_are_fresh_while_the_headway_is_not(self):
        """The whole point of two flags: M2's hours are current, its
        headway is still measured from the 2020 feed."""
        result = get_next_departures("M2", at=MONDAY)
        assert result.available is True
        assert result.hours_is_stale is False
        assert result.hours_source_type == "user_provided"
        assert result.hours_as_of == "2026-11-08"
        assert result.headway_is_stale is True
        assert result.headway_source_type == "gtfs"
        assert result.headway_as_of == "2020-07-09"

    def test_the_feeds_hours_are_not_used_at_all(self):
        """GTFS says M2 starts at 05:57; the verified hours say 06:00."""
        verified = get_next_departures("M2", at=MONDAY)
        feed = get_next_departures("M2", at=MONDAY, use_override=False)
        assert {info.first for info in verified.directions} == {"06:00"}
        assert {info.last for info in verified.directions} == {"00:00"}
        assert {info.first for info in feed.directions} != {"06:00"}

    def test_station_times_are_shifted_by_the_travel_time(self):
        """Terminal hours are the line's; Kadıköy is 50 minutes away."""
        result = get_next_departures("M4", "Kadıköy", at=MONDAY_EVENING)
        toward_kadikoy = next(
            info for info in result.directions if "Kadıköy" in info.direction_name
        )
        toward_sabiha = next(
            info for info in result.directions if "Kadıköy" not in info.direction_name
        )
        assert toward_sabiha.offset_minutes == 0, "Kadıköy bu yönün uç istasyonu"
        assert toward_sabiha.first == "06:00"
        assert toward_kadikoy.offset_minutes and toward_kadikoy.offset_minutes > 30
        assert toward_kadikoy.first != "06:00"
        assert any("kaydır" in note for note in result.notes)

    def test_a_terminus_arrival_is_labelled_as_one(self):
        result = get_next_departures("M4", "Kadıköy", at=MONDAY_EVENING)
        assert any("son durak" in note for note in result.notes)

    def test_night_metro_has_no_last_service(self):
        result = get_next_departures("M4", at=FRIDAY_NIGHT)
        assert result.day_type == "cuma"
        assert all(info.all_night for info in result.directions)
        assert all(info.last is None for info in result.directions)
        assert all("gece boyunca" in info.last_text for info in result.directions)
        assert any("servis kesilmiyor" in note for note in result.notes)

    def test_night_metro_is_running_in_the_small_hours(self):
        result = get_next_departures("M2", at=SATURDAY_SMALL_HOURS)
        assert result.day_type == "cumartesi"
        assert all(info.running_now for info in result.directions)

    def test_a_line_without_night_metro_closes_on_friday_too(self):
        result = get_next_departures("M9", at=FRIDAY_NIGHT)
        assert all(not info.all_night for info in result.directions)
        assert {info.last for info in result.directions} == {"00:00"}

    def test_sunday_matches_the_weekday_on_metro(self):
        weekday = get_next_departures("M5", at=MONDAY)
        sunday = get_next_departures("M5", at=SUNDAY)
        assert [(i.first, i.last) for i in weekday.directions] == [
            (i.first, i.last) for i in sunday.directions
        ]

    def test_outside_service_hours_nothing_is_running(self):
        """M9 has no night metro, so 02:00 Sunday is closed."""
        result = get_next_departures("M9", at=SUNDAY_SMALL_HOURS)
        assert all(not info.running_now for info in result.directions)
        assert all(info.expected_wait_minutes is None for info in result.directions)


@needs_store
class TestMarmaray:
    def test_both_hours_and_headway_are_fresh(self):
        result = get_next_departures("Marmaray", at=MONDAY)
        assert result.hours_is_stale is False
        assert result.headway_is_stale is False
        assert result.headway_source_type == "user_provided"

    def test_each_direction_has_its_own_terminal_times(self):
        result = get_next_departures("Marmaray", at=MONDAY)
        times = {info.terminal: (info.first, info.last) for info in result.directions}
        assert times["Halkalı"] == ("05:58", "22:58")
        assert times["Gebze"] == ("06:05", "22:50")

    def test_the_dense_section_gets_the_eight_minute_headway(self):
        """Ataköy to Pendik has the extra trains; the ends do not."""
        bostanci = get_next_departures("Marmaray", "Bostancı", at=MONDAY)
        halkali = get_next_departures("Marmaray", "Halkalı", at=MONDAY)
        assert {info.headway_minutes for info in bostanci.directions} == {8}
        assert {info.headway_minutes for info in halkali.directions} == {15}

    def test_friday_runs_past_midnight(self):
        result = get_next_departures("Marmaray", at=FRIDAY_NIGHT)
        times = {info.terminal: info.last for info in result.directions}
        assert times["Halkalı"] == "01:28"
        assert times["Gebze"] == "01:20"
        assert all(info.running_now for info in result.directions)

    def test_a_station_is_shifted_from_the_terminal(self):
        result = get_next_departures("Marmaray", "Yenikapı", at=MONDAY)
        assert result.station == "Yenikapı"
        assert all(info.offset_minutes and info.offset_minutes > 0 for info in result.directions)
        assert all(info.first != "05:58" for info in result.directions)

    def test_the_feed_fragment_answers_with_the_lines_hours(self):
        """Marmaray1 is Zeytinburnu-Söğütlüçeşme in the feed; the trains
        calling there are Halkalı-Gebze trains."""
        result = get_next_departures("Marmaray1", "Üsküdar", at=MONDAY)
        assert result.available is True
        assert result.hours_is_stale is False
        assert {info.terminal for info in result.directions} == {"Halkalı", "Gebze"}


@needs_store
class TestFeedFallback:
    def test_a_line_not_in_the_file_still_comes_from_the_feed(self):
        result = get_next_departures("T1", at=MONDAY)
        assert result.available is True
        assert result.hours_is_stale is True
        assert result.headway_is_stale is True
        assert any("2020" in warning for warning in result.warnings)

    def test_the_feeds_night_segment_is_still_reported(self):
        result = get_next_departures("T4", at=datetime(2026, 11, 7, 23, 30))
        assert result.day_type == "cumartesi"
        assert result.available is True

    def test_a_line_without_a_timetable_says_so(self):
        """T5 and F4 are not in the 2020 feed at all."""
        result = get_next_departures("T5", at=MONDAY)
        assert result.available is False
        assert any("tarife verisi yok" in note for note in result.notes)

    def test_an_unknown_line_is_refused(self):
        assert get_next_departures("M42", at=MONDAY).available is False

    def test_an_unknown_station_is_reported_not_guessed(self):
        result = get_next_departures("M2", "Paris", at=MONDAY)
        assert any("Paris" in note for note in result.notes)


@needs_store
class TestAnnouncements:
    def test_schedule_announcements_are_attached(self, announcements):
        result = get_next_departures("M7", at=MONDAY, announcements=announcements)
        assert result.alerts
        assert any("tarife değişikliği" in warning for warning in result.warnings)

    def test_an_irrelevant_announcement_is_not_attached(self, announcements):
        result = get_next_departures("T4", at=MONDAY, announcements=announcements)
        assert all("T3 Kadıköy-Moda" not in alert for alert in result.alerts)
