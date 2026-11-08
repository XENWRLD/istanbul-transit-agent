"""The calibration gates, especially the ones that keep it switched off.

The code is written but inactive: no external row has a real source link yet,
so every line keeps a factor of 1.0. These tests pin that it stays off until
a link arrives, and that each gate rejects for the right reason.
"""

from __future__ import annotations

import pytest

from istanbul_agent.store.calibration import (
    MAX_FACTOR,
    MIN_DEVIATION_PCT,
    MIN_FACTOR,
    MIN_MINUTES,
    MIN_STOPS,
    Calibration,
    evaluate_row,
    factor_for,
    has_real_source,
    read_external,
)


def external_row(**overrides):
    """A row that passes every gate, for one field at a time to be broken."""
    row = {
        "from": "Yenikapı",
        "to": "Hacıosman",
        "line": "M2",
        "minutes": "32",
        "source_url": "https://example.org/m2",
        "retrieved_at": "2026-11-08",
        "notes": "",
    }
    row.update(overrides)
    return row


class TestSourceLink:
    @pytest.mark.parametrize(
        "url",
        ["https://metro.istanbul/x", "http://example.org/a?b=1"],
    )
    def test_real_urls_accepted(self, url):
        assert has_real_source(url)

    @pytest.mark.parametrize(
        "url",
        ["", "   ", "<link>", "<M2 linki>", "TBD", "todo: bul", "metro.istanbul", "link"],
    )
    def test_placeholders_rejected(self, url):
        assert not has_real_source(url)

    def test_row_without_a_link_does_not_calibrate(self):
        calibration, reason = evaluate_row(
            external_row(source_url="<link>"), ours_minutes=29.1, stop_count=14, method="gtfs"
        )
        assert calibration is None
        assert "link" in reason


class TestDeviationGate:
    def test_deviation_above_threshold_is_accepted(self):
        """M2: ours 29.1, external 32 -- a 10% drift."""
        calibration, reason = evaluate_row(
            external_row(), ours_minutes=29.08, stop_count=14, method="gtfs"
        )
        assert calibration is not None, reason
        assert calibration.factor == pytest.approx(32 / 29.08, rel=1e-6)

    def test_small_deviation_is_noise(self):
        """M4's 0.9% must not scale the line (the user's explicit call)."""
        calibration, reason = evaluate_row(
            external_row(line="M4", minutes="38.5"),
            ours_minutes=38.15,
            stop_count=18,
            method="gtfs",
        )
        assert calibration is None
        assert "gürültü" in reason

    def test_threshold_is_five_percent(self):
        assert MIN_DEVIATION_PCT == 5.0

    def test_negative_deviation_also_qualifies(self):
        """T1 runs the other way: ours 67.8, external 63."""
        calibration, reason = evaluate_row(
            external_row(line="T1", minutes="63"),
            ours_minutes=67.78,
            stop_count=30,
            method="gtfs",
        )
        assert calibration is not None, reason
        assert calibration.factor < 1.0


class TestJourneyLengthGate:
    def test_too_few_stops_rejected(self):
        """F1: one stop, 2 minutes, a 30-second difference."""
        calibration, reason = evaluate_row(
            external_row(line="F1", minutes="2.5"),
            ours_minutes=2.0,
            stop_count=1,
            method="gtfs",
        )
        assert calibration is None
        assert "durak" in reason

    def test_too_short_in_minutes_rejected(self):
        calibration, reason = evaluate_row(
            external_row(minutes="9"), ours_minutes=8.0, stop_count=6, method="gtfs"
        )
        assert calibration is None
        assert "dk" in reason

    def test_thresholds_are_documented_values(self):
        assert MIN_STOPS == 5
        assert MIN_MINUTES == 10.0


class TestMethodGate:
    def test_estimate_cannot_be_calibrated(self):
        """Folding the estimate's own error into a factor would compound it."""
        calibration, reason = evaluate_row(
            external_row(line="M4", to="Sabiha Gökçen Havalimanı", minutes="52"),
            ours_minutes=49.98,
            stop_count=22,
            method="estimated",
        )
        assert calibration is None
        assert "estimated" in reason

    def test_unanswerable_journey_cannot_be_calibrated(self):
        calibration, _ = evaluate_row(
            external_row(), ours_minutes=None, stop_count=0, method="unavailable"
        )
        assert calibration is None


class TestUnverifiedGate:
    def test_unverified_source_is_refused(self):
        """The M5 row the user flagged."""
        calibration, reason = evaluate_row(
            external_row(line="M5", minutes="34", notes="DOĞRULANMADI — başka güzergâh olabilir"),
            ours_minutes=26.65,
            stop_count=15,
            method="gtfs",
        )
        assert calibration is None
        assert "doğrulanmadı" in reason


class TestFactorBounds:
    @pytest.mark.parametrize("minutes", ["5", "100"])
    def test_implausible_ratios_refused(self, minutes):
        calibration, reason = evaluate_row(
            external_row(minutes=minutes), ours_minutes=29.08, stop_count=14, method="gtfs"
        )
        assert calibration is None
        assert "aralık" in reason or "gürültü" in reason

    def test_bounds_bracket_one(self):
        assert MIN_FACTOR < 1.0 < MAX_FACTOR


class TestCurrentState:
    """While the links are placeholders, calibration must stay off."""

    def test_no_row_in_the_file_has_a_real_link_yet(self):
        rows = read_external()
        assert rows, "datasets/external_travel_times.csv boş"
        with_links = [row for row in rows if has_real_source(row["source_url"])]
        # The Wikipedia row for Sabiha Gökçen is real, but that row is an
        # estimate and fails the method gate instead.
        for row in with_links:
            assert "wikipedia" in row["source_url"].lower(), (
                f"{row['line']} artık gerçek bir link taşıyor; kalibrasyon aktif olabilir"
            )

    def test_factor_defaults_to_one(self):
        assert factor_for("M2", calibrations={}) == 1.0
        assert factor_for("ZZ9", calibrations={}) == 1.0

    def test_accepted_factor_is_applied(self):
        table = {
            "M2": Calibration(
                line="M2",
                factor=1.1,
                from_station="Yenikapı",
                to_station="Hacıosman",
                ours_minutes=29.08,
                external_minutes=32.0,
                source_url="https://example.org/m2",
            )
        }
        assert factor_for("M2", calibrations=table) == pytest.approx(1.1)
        assert factor_for("T1", calibrations=table) == 1.0
