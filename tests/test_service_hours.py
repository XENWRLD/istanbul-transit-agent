"""Reading the hand-verified service hours file.

The shipped file is the real one, so these tests also pin what it claims:
night metro on seven lines, none on M3/M8/M9, and Marmaray's per-direction
hours with its own headway.
"""

from __future__ import annotations

import pytest

from istanbul_agent.store.service_hours import (
    DAY_LABELS,
    OVERRIDE_DAY_TYPES,
    HeadwayRule,
    gtfs_day_type_for,
    load_service_hours,
    override_day_type_for,
)

NIGHT_METRO = ("M1A", "M1B", "M2", "M4", "M5", "M6", "M7")
NO_NIGHT_METRO = ("M3", "M8", "M9")


@pytest.fixture(scope="module")
def hours():
    load_service_hours.cache_clear()
    return load_service_hours()


class TestDayTypes:
    @pytest.mark.parametrize(
        ("weekday", "expected"),
        [
            (0, "pzt_pers"),
            (3, "pzt_pers"),
            (4, "cuma"),
            (5, "cumartesi"),
            (6, "pazar"),
        ],
    )
    def test_friday_is_its_own_day(self, weekday, expected):
        """Night metro runs the night joining Friday to Saturday."""
        assert override_day_type_for(weekday) == expected

    def test_all_four_days_are_labelled(self):
        assert set(OVERRIDE_DAY_TYPES) == set(DAY_LABELS)

    @pytest.mark.parametrize(
        ("day_type", "expected"),
        [
            ("pzt_pers", "hafta_ici"),
            ("cuma", "hafta_ici"),
            ("cumartesi", "cumartesi"),
            ("pazar", "pazar"),
        ],
    )
    def test_the_feed_only_knows_three(self, day_type, expected):
        """The headway still comes from the feed, which cannot see Friday."""
        assert gtfs_day_type_for(day_type) == expected


class TestShippedFile:
    def test_every_metro_line_is_covered(self, hours):
        for line in NIGHT_METRO + NO_NIGHT_METRO + ("Marmaray",):
            assert line.upper() in hours, line

    def test_night_metro_lines_run_through_friday_and_saturday_nights(self, hours):
        for line in NIGHT_METRO:
            entry = hours[line]
            for day in ("cuma", "cumartesi"):
                stated = entry.directions[0].for_day(day)
                assert stated.all_night is True, f"{line} {day}"
                assert stated.last is None
                assert stated.first == "06:00"

    def test_lines_without_night_metro_end_at_midnight_every_day(self, hours):
        for line in NO_NIGHT_METRO:
            entry = hours[line]
            for day in OVERRIDE_DAY_TYPES:
                stated = entry.directions[0].for_day(day)
                assert stated.all_night is False, f"{line} {day}"
                assert (stated.first, stated.last) == ("06:00", "00:00")

    def test_weekday_and_sunday_are_the_same_on_metro(self, hours):
        for line in NIGHT_METRO + NO_NIGHT_METRO:
            entry = hours[line]
            weekday = entry.directions[0].for_day("pzt_pers")
            sunday = entry.directions[0].for_day("pazar")
            assert (weekday.first, weekday.last) == (sunday.first, sunday.last)

    def test_every_record_carries_its_provenance(self, hours):
        for code, entry in hours.items():
            assert entry.source_type == "user_provided", code
            assert entry.retrieved_at == "2026-11-08", code

    def test_aliases_resolve_to_the_same_line(self, hours):
        assert hours["MARMARAY1"] is hours["MARMARAY"]
        assert hours["M2A"] is hours["M2"]


class TestMarmaray:
    def test_hours_differ_per_direction(self, hours):
        entry = hours["MARMARAY"]
        assert entry.per_direction is True
        halkali = entry.for_terminal("halkali").for_day("pzt_pers")
        gebze = entry.for_terminal("gebze").for_day("pzt_pers")
        assert (halkali.first, halkali.last) == ("05:58", "22:58")
        assert (gebze.first, gebze.last) == ("06:05", "22:50")

    def test_friday_and_saturday_run_later_but_do_end(self, hours):
        """Marmaray has no night service; it just finishes after midnight."""
        entry = hours["MARMARAY"]
        for day in ("cuma", "cumartesi"):
            halkali = entry.for_terminal("halkali").for_day(day)
            gebze = entry.for_terminal("gebze").for_day(day)
            assert halkali.all_night is False
            assert (halkali.last, gebze.last) == ("01:28", "01:20")

    def test_headway_rule_is_stated(self, hours):
        rule = hours["MARMARAY"].headway
        assert rule == HeadwayRule(
            default_minutes=15, extra_minutes=8, between=("Ataköy", "Pendik")
        )

    def test_the_dense_section_uses_the_extra_trains(self):
        rule = HeadwayRule(default_minutes=15, extra_minutes=8, between=("Ataköy", "Pendik"))
        assert rule.minutes_at(10, (7, 31)) == 8
        assert rule.minutes_at(40, (7, 31)) == 15
        assert rule.minutes_at(None, (7, 31)) == 15, "hat düzeyinde varsayılan aralık"
