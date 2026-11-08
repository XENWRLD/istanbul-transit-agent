"""Service windows and headways, on a hand-built feed.

The real feed's quirks are what these pin down: a weekend 00:20 train that
belongs to the previous night rather than being the day's first service, a
route with four template trips that must not become a timetable, and the two
sources -- measured departure times for most lines, declared headways for the
seven whose daytime service exists only in frequencies.csv.
"""

from __future__ import annotations

import pytest

from istanbul_agent.store.gtfs import GtfsFeed
from istanbul_agent.store.timetable import (
    MIN_TRIPS_FOR_WINDOW,
    SEGMENT_DAY,
    SEGMENT_NIGHT,
    build_headways,
    build_service_windows,
    day_type_for,
)

SERVICE_ALL = "all"
SERVICE_WEEKDAY = "weekday"


def calendar_row(service_id: str, days: str) -> dict[str, str]:
    """`days` is a seven-character weekday pattern, Monday first."""
    names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    row = {"service_id": service_id, "start_date": "20200101", "end_date": "20201231"}
    row.update({name: flag for name, flag in zip(names, days, strict=True)})
    return row


def make_feed(
    departures: dict[str, list[str]],
    *,
    service_id: str = SERVICE_ALL,
    frequencies: dict[str, list[tuple[str, str, int]]] | None = None,
    travel_minutes: int = 10,
) -> GtfsFeed:
    """A one-line feed whose trips leave A at the given times and reach B.

    `departures` maps direction_id to departure times at the origin.
    """
    trips: list[dict[str, str]] = []
    stop_times: dict[str, list[dict[str, str]]] = {}
    index = 0
    for direction_id, times in departures.items():
        for time in times:
            index += 1
            trip_id = f"t{index}"
            trips.append(
                {
                    "route_id": "r1",
                    "service_id": service_id,
                    "trip_id": trip_id,
                    "trip_headsign": "B" if direction_id == "0" else "A",
                    "direction_id": direction_id,
                }
            )
            hours, minutes = (int(part) for part in time.split(":")[:2])
            arrival = hours * 60 + minutes + travel_minutes
            stop_times[trip_id] = [
                {
                    "trip_id": trip_id,
                    "stop_id": "s1",
                    "stop_sequence": "1",
                    "departure_time": f"{time}:00",
                    "arrival_time": f"{time}:00",
                },
                {
                    "trip_id": trip_id,
                    "stop_id": "s2",
                    "stop_sequence": "2",
                    "departure_time": f"{arrival // 60:02d}:{arrival % 60:02d}:00",
                    "arrival_time": f"{arrival // 60:02d}:{arrival % 60:02d}:00",
                },
            ]

    return GtfsFeed(
        feed="test",
        agencies={"1": "Test"},
        routes=[{"route_id": "r1", "route_short_name": "M9", "route_long_name": "A - B"}],
        trips=trips,
        stops={
            "s1": {"stop_id": "s1", "stop_name": "A Istasyonu"},
            "s2": {"stop_id": "s2", "stop_name": "B Istasyonu"},
        },
        calendar={
            SERVICE_ALL: calendar_row(SERVICE_ALL, "1111111"),
            SERVICE_WEEKDAY: calendar_row(SERVICE_WEEKDAY, "1111100"),
        },
        frequencies={
            trip_id: [
                {
                    "trip_id": trip_id,
                    "start_time": start,
                    "end_time": end,
                    "headway_secs": str(headway),
                }
                for start, end, headway in rows
            ]
            for trip_id, rows in (frequencies or {}).items()
        },
        stop_times=stop_times,
    )


class TestDayType:
    @pytest.mark.parametrize(
        ("weekday", "expected"),
        [(0, "hafta_ici"), (2, "hafta_ici"), (4, "hafta_ici"), (5, "cumartesi"), (6, "pazar")],
    )
    def test_weekday_index_maps_to_day_type(self, weekday, expected):
        assert day_type_for(weekday) == expected


class TestWindowsFromStopTimes:
    def test_first_and_last_come_from_the_departure_times(self):
        feed = make_feed({"0": ["06:00", "06:10", "06:20", "22:40"]})
        windows = build_service_windows(feed, ["M9"], {"M9": "stop_times"})
        line = next(w for w in windows if w.day_type == "hafta_ici" and not w.station_key)
        assert (line.first, line.last) == ("06:00", "22:40")
        assert line.trip_count == 4
        assert line.estimated is False

    def test_a_station_has_its_own_window(self):
        """B is ten minutes down the line, so its last train is later."""
        feed = make_feed({"0": ["06:00", "06:10", "06:20", "22:40"]})
        windows = build_service_windows(feed, ["M9"], {"M9": "stop_times"})
        station = next(w for w in windows if w.day_type == "hafta_ici" and w.station_key == "b")
        assert (station.first, station.last) == ("06:10", "22:50")

    def test_night_service_is_not_the_first_train_of_the_day(self):
        """A 00:20 departure is the previous night's service, not 'ilk sefer'."""
        feed = make_feed(
            {"0": ["00:20", "00:40", "01:00", "01:20", "06:00", "06:10", "06:20", "22:40"]}
        )
        windows = build_service_windows(feed, ["M9"], {"M9": "stop_times"})
        day = next(
            w
            for w in windows
            if w.day_type == "hafta_ici" and not w.station_key and w.segment == SEGMENT_DAY
        )
        night = next(
            w
            for w in windows
            if w.day_type == "hafta_ici" and not w.station_key and w.segment == SEGMENT_NIGHT
        )
        assert (day.first, day.last) == ("06:00", "22:40")
        assert (night.first, night.last) == ("00:20", "01:20")

    def test_too_few_trips_produce_no_window(self):
        """Marmaray's case: a handful of template trips is not a timetable."""
        feed = make_feed({"0": ["06:00", "14:00"]})
        windows = build_service_windows(feed, ["M9"], {"M9": "stop_times"})
        assert windows == []
        assert MIN_TRIPS_FOR_WINDOW > 2

    def test_weekend_falls_back_to_nothing_when_the_service_is_weekday_only(self):
        feed = make_feed({"0": ["06:00", "06:10", "06:20", "22:40"]}, service_id=SERVICE_WEEKDAY)
        windows = build_service_windows(feed, ["M9"], {"M9": "stop_times"})
        assert {w.day_type for w in windows} == {"hafta_ici"}

    def test_both_directions_are_kept_apart(self):
        feed = make_feed(
            {
                "0": ["06:00", "06:10", "06:20", "22:40"],
                "1": ["05:50", "06:05", "06:15", "23:10"],
            }
        )
        windows = build_service_windows(feed, ["M9"], {"M9": "stop_times"})
        line_level = {
            w.direction_id: (w.first, w.last)
            for w in windows
            if w.day_type == "hafta_ici" and not w.station_key
        }
        assert line_level == {"0": ("06:00", "22:40"), "1": ("05:50", "23:10")}


class TestHeadwaysFromStopTimes:
    def test_median_gap_per_hour(self):
        feed = make_feed({"0": ["06:00", "06:05", "06:10", "06:20", "08:00"]})
        headways = build_headways(feed, ["M9"], {"M9": "stop_times"})
        six = next(h for h in headways if h.day_type == "hafta_ici" and h.hour == 6)
        # Gaps inside 06: 5, 5 and 10 minutes -> median 5. The 06:20 to 08:00
        # gap is not one of them; see test_gaps_crossing_an_hour_are_ignored.
        assert six.headway_s == 300
        assert six.departures == 4

    def test_gaps_crossing_an_hour_are_ignored(self):
        """A 100-minute gap into the next hour must not set this hour."""
        feed = make_feed({"0": ["20:00", "20:04", "20:08", "20:12", "21:52"]})
        headways = build_headways(feed, ["M9"], {"M9": "stop_times"})
        twenty = next(h for h in headways if h.hour == 20 and h.day_type == "hafta_ici")
        assert twenty.headway_s == 240

    def test_two_trips_are_not_a_headway(self):
        feed = make_feed({"0": ["06:00", "06:10"]})
        assert build_headways(feed, ["M9"], {"M9": "stop_times"}) == []


class TestFrequenciesSource:
    def test_window_is_the_union_of_the_bands(self):
        feed = make_feed(
            {"0": ["06:00"]},
            frequencies={"t1": [("06:00:00", "22:00:00", 360), ("22:00:00", "24:00:00", 480)]},
        )
        windows = build_service_windows(feed, ["M9"], {"M9": "frequencies"})
        line = next(w for w in windows if w.day_type == "hafta_ici" and not w.station_key)
        assert (line.first, line.last) == ("06:00", "00:00")

    def test_station_window_is_marked_estimated(self):
        """These trips have no real departure times, only a headway."""
        feed = make_feed({"0": ["06:00"]}, frequencies={"t1": [("06:00:00", "22:00:00", 360)]})
        windows = build_service_windows(feed, ["M9"], {"M9": "frequencies"})
        station = next(w for w in windows if w.station_key == "b")
        assert station.estimated is True
        assert (station.first, station.last) == ("06:10", "22:10")

    def test_headway_is_the_declared_one_per_hour(self):
        feed = make_feed(
            {"0": ["06:00"]},
            frequencies={"t1": [("06:00:00", "08:00:00", 360), ("08:00:00", "10:00:00", 240)]},
        )
        headways = build_headways(feed, ["M9"], {"M9": "frequencies"})
        by_hour = {h.hour: h.headway_s for h in headways if h.day_type == "hafta_ici"}
        assert by_hour[6] == 360
        assert by_hour[7] == 360
        assert by_hour[8] == 240
        assert 10 not in by_hour

    def test_overlapping_bands_take_the_tighter_headway(self):
        feed = make_feed(
            {"0": ["06:00"]},
            frequencies={"t1": [("06:00:00", "09:00:00", 600), ("08:00:00", "10:00:00", 240)]},
        )
        headways = build_headways(feed, ["M9"], {"M9": "frequencies"})
        by_hour = {h.hour: h.headway_s for h in headways}
        assert by_hour[8] == 240

    def test_night_band_is_its_own_segment(self):
        feed = make_feed(
            {"0": ["06:00"]},
            frequencies={"t1": [("00:20:00", "05:00:00", 1200), ("06:00:00", "24:00:00", 360)]},
        )
        windows = build_service_windows(feed, ["M9"], {"M9": "frequencies"})
        segments = {
            w.segment: (w.first, w.last)
            for w in windows
            if w.day_type == "hafta_ici" and not w.station_key
        }
        assert segments[SEGMENT_DAY] == ("06:00", "00:00")
        assert segments[SEGMENT_NIGHT] == ("00:20", "05:00")
