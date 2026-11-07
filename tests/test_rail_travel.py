"""Travel-time logic, on a hand-built network.

No GTFS files and no API calls: the graph is constructed in the test so each
behaviour is isolated. The cases come from what the real data did -- the
Seyrantepe spur, the Mecidiyeköy/Şişli-Mecidiyeköy name split, T3's refused
feed, M5's coordinate-less tail.
"""

import pytest

from istanbul_agent.store.rail_travel import (
    DWELL_S,
    LINE_MODEL,
    LINE_WARNINGS,
    MODE_MODEL,
    TRANSFER_PENALTY_S,
    ErrorMargin,
    RailNetwork,
    RailStation,
    estimate_error,
    model_for,
)


def station(name, line, order, *, mode="metro", lat=None, lon=None, spur=False, in_gtfs=True):
    return RailStation(
        name=name,
        line=line,
        mode=mode,
        stop_id=f"{line}-{order}",
        lat=lat,
        lon=lon,
        order=order,
        in_gtfs=in_gtfs,
        is_spur=spur,
    )


def pair(line, left, right):
    """Segment key. The line is part of it: Marmaray and M4 both run
    Bostancı to Küçükyalı, so names alone would collide."""
    from istanbul_agent.store.station_names import normalize

    a, b = normalize(left), normalize(right)
    return (line, a, b) if a <= b else (line, b, a)


@pytest.fixture
def simple_line():
    """Three stations on one line, every hop timed by the feed."""
    return RailNetwork(
        stations=[
            station("Alfa", "M9", 1),
            station("Beta", "M9", 2),
            station("Gama", "M9", 3),
        ],
        segment_durations={pair("M9", "Alfa", "Beta"): 100, pair("M9", "Beta", "Gama"): 200},
        segment_lengths={},
        transfers=[],
    )


class TestSameLine:
    def test_adjacent_hop_uses_feed_duration(self, simple_line):
        leg = simple_line.same_line_leg(simple_line.find("Alfa")[0], simple_line.find("Beta")[0])
        assert leg.seconds == 100
        assert leg.method == "gtfs"
        assert leg.stop_count == 1

    def test_dwell_added_at_intermediate_stations_only(self, simple_line):
        leg = simple_line.same_line_leg(simple_line.find("Alfa")[0], simple_line.find("Gama")[0])
        # 100 + 200 travelling, plus one dwell at Beta, none on arrival.
        assert leg.seconds == 100 + 200 + DWELL_S
        assert leg.stop_count == 2

    def test_reverse_direction_is_symmetric(self, simple_line):
        forward = simple_line.same_line_leg(
            simple_line.find("Alfa")[0], simple_line.find("Gama")[0]
        )
        backward = simple_line.same_line_leg(
            simple_line.find("Gama")[0], simple_line.find("Alfa")[0]
        )
        assert forward.seconds == backward.seconds

    def test_same_station_is_zero(self, simple_line):
        origin = simple_line.find("Alfa")[0]
        leg = simple_line.same_line_leg(origin, origin)
        assert leg.seconds == 0
        assert leg.stop_count == 0

    def test_missing_segment_data_gives_no_leg(self):
        network = RailNetwork(
            stations=[station("Alfa", "M9", 1), station("Beta", "M9", 2)],
            segment_durations={},
            segment_lengths={},
            transfers=[],
        )
        assert network.same_line_leg(network.find("Alfa")[0], network.find("Beta")[0]) is None


class TestEstimation:
    def test_length_without_duration_is_estimated(self):
        network = RailNetwork(
            stations=[station("Alfa", "M9", 1), station("Beta", "M9", 2)],
            segment_durations={},
            segment_lengths={pair("M9", "Alfa", "Beta"): 1200.0},
            transfers=[],
        )
        leg = network.same_line_leg(network.find("Alfa")[0], network.find("Beta")[0])
        assert leg.method == "estimated"
        assert leg.seconds == MODE_MODEL["metro"].seconds(1200.0)

    def test_coordinates_used_when_no_length_known(self):
        network = RailNetwork(
            stations=[
                station("Alfa", "M9", 1, lat=41.0, lon=29.0),
                station("Beta", "M9", 2, lat=41.01, lon=29.0),
            ],
            segment_durations={},
            segment_lengths={},
            transfers=[],
        )
        leg = network.same_line_leg(network.find("Alfa")[0], network.find("Beta")[0])
        assert leg.method == "estimated"
        assert leg.seconds > 0

    def test_mixed_journey_is_estimated_overall(self):
        """One estimated hop makes the whole leg estimated, not just that hop."""
        network = RailNetwork(
            stations=[
                station("Alfa", "M9", 1),
                station("Beta", "M9", 2),
                station("Gama", "M9", 3),
            ],
            segment_durations={pair("M9", "Alfa", "Beta"): 100},
            segment_lengths={pair("M9", "Beta", "Gama"): 1000.0},
            transfers=[],
        )
        leg = network.same_line_leg(network.find("Alfa")[0], network.find("Gama")[0])
        assert leg.method == "estimated"


class TestSegmentModel:
    def test_overhead_is_charged_once_per_hop(self):
        model = MODE_MODEL["metro"]
        assert model.seconds(0) == round(model.overhead_s)

    def test_longer_distance_takes_longer(self):
        model = MODE_MODEL["metro"]
        assert model.seconds(2000) > model.seconds(1000)

    def test_line_fit_preferred_over_mode(self):
        assert model_for("M2", "metro") is LINE_MODEL["M2"]

    def test_unfitted_line_falls_back_to_mode(self):
        # M1A and M3 were rejected for implausible fits (decision 0004).
        assert model_for("M1A", "metro") is MODE_MODEL["metro"]
        assert model_for("M3", "metro") is MODE_MODEL["metro"]

    def test_tram_is_slower_than_metro(self):
        assert MODE_MODEL["tramvay"].cruise_ms < MODE_MODEL["metro"].cruise_ms

    def test_unknown_mode_falls_back_to_metro(self):
        assert model_for("ZZ9", "bilinmiyor") is MODE_MODEL["metro"]


class TestSpurStations:
    """A branch station must not be traversed by a journey passing it."""

    @pytest.fixture
    def with_spur(self):
        # Mirrors M2: Seyrantepe sits between two stations the feed has as
        # adjacent, because it is really on the M2A branch.
        return RailNetwork(
            stations=[
                station("Sanayi Mahallesi", "M2", 1),
                station("Seyrantepe", "M2", 2, spur=True, in_gtfs=False),
                station("İTÜ-Ayazağa", "M2", 3),
            ],
            segment_durations={pair("M2", "Sanayi Mahallesi", "İTÜ-Ayazağa"): 90},
            segment_lengths={},
            transfers=[],
        )

    def test_journey_past_spur_skips_it(self, with_spur):
        leg = with_spur.same_line_leg(
            with_spur.find("Sanayi Mahallesi")[0], with_spur.find("İTÜ-Ayazağa")[0]
        )
        assert leg.seconds == 90
        assert leg.method == "gtfs"
        assert leg.stop_count == 1

    def test_spur_as_endpoint_is_kept(self, with_spur):
        """Travelling *to* the branch station must still be attempted."""
        network = RailNetwork(
            stations=with_spur.stations,
            segment_durations={pair("M2", "Sanayi Mahallesi", "Seyrantepe"): 70},
            segment_lengths={},
            transfers=[],
        )
        leg = network.same_line_leg(
            network.find("Sanayi Mahallesi")[0], network.find("Seyrantepe")[0]
        )
        assert leg is not None
        assert leg.seconds == 70


class TestUnreliableCoordinates:
    """T3's coordinates are untrustworthy; its recorded durations are not.

    The first reading of decision 0003 refused the whole line. Measuring the
    ring showed the opposite: T3's name sequence matches the live one and its
    nine segment durations are consistent (41-99 s). What fails is its
    coordinates, whose 164-763 m error exceeds the line's 239 m station
    spacing -- so only a distance-derived estimate has to be refused.
    """

    def test_recorded_duration_is_still_used(self):
        network = RailNetwork(
            stations=[
                station("Moda", "T3", 1, mode="tramvay"),
                station("Altıyol", "T3", 2, mode="tramvay"),
            ],
            segment_durations={pair("T3", "Moda", "Altıyol"): 60},
            segment_lengths={},
            transfers=[],
            unreliable_lines=frozenset({"T3"}),
        )
        leg = network.same_line_leg(network.find("Moda")[0], network.find("Altıyol")[0])
        assert leg is not None
        assert leg.seconds == 60
        assert leg.method == "gtfs"

    def test_coordinate_estimate_is_refused(self):
        """With no recorded duration, the coordinates must not be used."""
        network = RailNetwork(
            stations=[
                station("Moda", "T3", 1, mode="tramvay", lat=40.985, lon=29.025),
                station("Altıyol", "T3", 2, mode="tramvay", lat=40.990, lon=29.028),
            ],
            segment_durations={},
            segment_lengths={},
            transfers=[],
            unreliable_lines=frozenset({"T3"}),
        )
        assert network.same_line_leg(network.find("Moda")[0], network.find("Altıyol")[0]) is None

    def test_312_length_is_still_allowed(self):
        """A measured track length does not come from the suspect coordinates."""
        network = RailNetwork(
            stations=[
                station("Moda", "T3", 1, mode="tramvay"),
                station("Altıyol", "T3", 2, mode="tramvay"),
            ],
            segment_durations={},
            segment_lengths={pair("T3", "Moda", "Altıyol"): 400.0},
            transfers=[],
            unreliable_lines=frozenset({"T3"}),
        )
        leg = network.same_line_leg(network.find("Moda")[0], network.find("Altıyol")[0])
        assert leg is not None
        assert leg.method == "estimated"


class TestRingLines:
    """A ring has no ends, so the shorter way round has to win."""

    @pytest.fixture
    def ring(self):
        # Five stations round a loop. Going 1->5 the long way is four hops;
        # over the join it is one.
        return RailNetwork(
            stations=[
                station(name, "T9", index, mode="tramvay")
                for index, name in enumerate("ABCDE", start=1)
            ],
            segment_durations={
                pair("T9", "A", "B"): 100,
                pair("T9", "B", "C"): 100,
                pair("T9", "C", "D"): 100,
                pair("T9", "D", "E"): 100,
                pair("T9", "E", "A"): 60,
            },
            segment_lengths={},
            transfers=[],
            ring_lines=frozenset({"T9"}),
        )

    def test_shorter_way_round_is_chosen(self, ring):
        leg = ring.same_line_leg(ring.find("A")[0], ring.find("E")[0])
        assert leg.seconds == 60
        assert leg.stop_count == 1

    def test_linear_line_ignores_the_join(self):
        network = RailNetwork(
            stations=[
                station(name, "T9", index, mode="tramvay")
                for index, name in enumerate("ABCDE", start=1)
            ],
            segment_durations={
                pair("T9", "A", "B"): 100,
                pair("T9", "B", "C"): 100,
                pair("T9", "C", "D"): 100,
                pair("T9", "D", "E"): 100,
                pair("T9", "E", "A"): 60,
            },
            segment_lengths={},
            transfers=[],
            # ring_lines deliberately empty
        )
        leg = network.same_line_leg(network.find("A")[0], network.find("E")[0])
        assert leg.stop_count == 4
        assert leg.seconds == 400 + 3 * DWELL_S


class TestTransfers:
    @pytest.fixture
    def two_lines(self):
        """M2 and M7 meet at a station each line names differently."""
        return RailNetwork(
            stations=[
                station("Yenikapı", "M2", 1),
                station("Şişli-Mecidiyeköy", "M2", 2, lat=41.0675, lon=28.9935),
                station("Mecidiyeköy", "M7", 1, lat=41.0673, lon=28.9940),
                station("Kağıthane", "M7", 2),
            ],
            segment_durations={
                pair("M2", "Yenikapı", "Şişli-Mecidiyeköy"): 400,
                pair("M7", "Mecidiyeköy", "Kağıthane"): 150,
            },
            segment_lengths={},
            transfers=[
                ("sisli mecidiyekoy", "M2", "M7"),
                ("mecidiyekoy", "M7", "M2"),
            ],
        )

    def test_interchange_paired_across_differing_names(self, two_lines):
        options = two_lines.transfer_options("M2", "M7")
        assert options, "aktarma noktası bulunamadı"
        from_station, to_station = options[0]
        assert from_station.line == "M2"
        assert to_station.line == "M7"

    def test_same_name_interchange_is_found(self):
        network = RailNetwork(
            stations=[
                station("Kirazlı", "M1B", 1),
                station("Kirazlı", "M3", 1),
            ],
            segment_durations={},
            segment_lengths={},
            transfers=[],
        )
        options = network.transfer_options("M1B", "M3")
        assert len(options) == 1
        assert options[0][0].line == "M1B"
        assert options[0][1].line == "M3"

    def test_far_apart_namesakes_are_not_paired(self):
        """313 names a station on each side, but they are kilometres apart."""
        network = RailNetwork(
            stations=[
                station("Kuzey", "M2", 1, lat=41.10, lon=29.00),
                station("Guney", "M7", 1, lat=40.95, lon=29.00),
            ],
            segment_durations={},
            segment_lengths={},
            transfers=[("kuzey", "M2", "M7"), ("guney", "M7", "M2")],
        )
        assert network.transfer_options("M2", "M7") == []


class TestConstants:
    def test_transfer_penalty_decomposition(self):
        """210 s measured wait + 150 s assumed walk (decision 0004)."""
        assert TRANSFER_PENALTY_S == 210 + 150

    def test_dwell_is_the_measured_median(self):
        assert DWELL_S == 15


class TestLookup:
    def test_name_lookup_is_spelling_insensitive(self, simple_line):
        assert simple_line.find("ALFA")
        assert simple_line.find("alfa")

    def test_unknown_name_returns_nothing(self, simple_line):
        assert simple_line.find("Paris") == []

    def test_lines_at_reports_every_line(self):
        network = RailNetwork(
            stations=[station("Kirazlı", "M1B", 1), station("Kirazlı", "M3", 1)],
            segment_durations={},
            segment_lengths={},
            transfers=[],
        )
        assert network.lines_at("Kirazlı") == {"M1B", "M3"}


class TestMultiLegPlanning:
    """Two interchanges, because one is not enough for this network.

    Kadıköy to Taksim needs M4, then Marmaray under the Bosphorus, then M2:
    the metro lines do not meet each other across the water.
    """

    @pytest.fixture
    def three_lines(self):
        return RailNetwork(
            stations=[
                station("Kadıköy", "M4", 1),
                station("Ayrılık Çeşmesi", "M4", 2),
                station("Ayrılık Çeşmesi", "MR", 1),
                station("Yenikapı", "MR", 2),
                station("Yenikapı", "M2", 1),
                station("Taksim", "M2", 2),
            ],
            segment_durations={
                pair("M4", "Kadıköy", "Ayrılık Çeşmesi"): 120,
                pair("MR", "Ayrılık Çeşmesi", "Yenikapı"): 600,
                pair("M2", "Yenikapı", "Taksim"): 300,
            },
            segment_lengths={},
            transfers=[],
        )

    def test_two_interchanges_are_planned(self, three_lines):
        legs = three_lines.plan(three_lines.find("Kadıköy"), three_lines.find("Taksim"))
        assert legs is not None
        assert [leg.line for leg in legs] == ["M4", "MR", "M2"]
        assert [leg.seconds for leg in legs] == [120, 600, 300]

    def test_legs_are_merged_per_line(self):
        """Riding several hops on one line is one leg, not one per hop."""
        network = RailNetwork(
            stations=[station(name, "M9", index) for index, name in enumerate("ABCD", start=1)],
            segment_durations={
                pair("M9", "A", "B"): 100,
                pair("M9", "B", "C"): 100,
                pair("M9", "C", "D"): 100,
            },
            segment_lengths={},
            transfers=[],
        )
        legs = network.plan(network.find("A"), network.find("D"))
        assert len(legs) == 1
        assert legs[0].stop_count == 3

    def test_unreachable_pair_returns_nothing(self):
        network = RailNetwork(
            stations=[station("Alfa", "M9", 1), station("Uzak", "T9", 1)],
            segment_durations={},
            segment_lengths={},
            transfers=[],
        )
        assert network.plan(network.find("Alfa"), network.find("Uzak")) is None


class TestErrorMargin:
    def test_margin_narrows_as_the_journey_lengthens(self):
        short_mean, _ = estimate_error(2)
        long_mean, _ = estimate_error(15)
        assert short_mean > long_mean

    @pytest.mark.parametrize(
        ("stops", "expected_mean"),
        [(1, 10.9), (3, 10.9), (4, 3.3), (6, 3.3), (7, 2.2), (10, 2.2), (11, 1.4), (30, 1.4)],
    )
    def test_bands_match_the_validation_table(self, stops, expected_mean):
        assert estimate_error(stops)[0] == expected_mean

    def test_max_is_never_below_mean(self):
        for stops in range(1, 40):
            mean_error, max_error = estimate_error(stops)
            assert max_error >= mean_error

    def test_margin_phrase_is_at_least_one_minute(self):
        """A margin must not round to zero and imply exactness."""
        margin = ErrorMargin(mean_pct=1.4, max_pct=5.3, mean_seconds=10, max_seconds=30)
        assert margin.mean_minutes == 1
        assert "±1 dk" in margin.phrase()


class TestLineWarnings:
    def test_m6_carries_its_known_bias(self):
        assert "M6" in LINE_WARNINGS
        assert "%29" in LINE_WARNINGS["M6"]

    def test_other_lines_have_no_warning(self):
        assert "M2" not in LINE_WARNINGS
