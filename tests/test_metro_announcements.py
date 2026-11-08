"""Reading Metro İstanbul announcements, against the saved live response.

The fixture is a real `GetAnnouncements/TR` response (6 Kasım 2026). It holds
exactly the cases that matter: one schedule change naming M7, one line
opening that names M5 but is not about the timetable, and one winter-timetable
notice that names no line at all and therefore applies to all of them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from istanbul_agent.clients.metro import (
    Announcement,
    parse_announcements,
    schedule_alerts,
    unwrap,
)

FIXTURE = Path(__file__).parent / "fixtures" / "metro" / "metro-announcements-tr.json"


@pytest.fixture(scope="module")
def announcements() -> list[Announcement]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return parse_announcements(payload)


class TestUnwrap:
    def test_success_false_raises_despite_http_200(self):
        """The API reports failure in the body, not the status code."""
        with pytest.raises(RuntimeError, match="başarısız"):
            unwrap({"Success": False, "Error": "Line not found", "Data": None})

    def test_missing_data_is_an_empty_list(self):
        assert unwrap({"Success": True, "Data": None}) == []


class TestParsing:
    def test_html_is_stripped(self, announcements):
        repair = next(a for a in announcements if a.id == 669)
        assert "<p>" not in repair.text
        assert "&ouml;" not in repair.text
        assert "Mecidiyeköy" in repair.text

    def test_line_codes_are_extracted(self, announcements):
        assert "M7" in next(a for a in announcements if a.id == 669).lines
        assert "M5" in next(a for a in announcements if a.id == 680).lines
        assert "TF2" in next(a for a in announcements if a.id == 714).lines

    def test_the_winter_timetable_notice_names_no_line(self, announcements):
        """It says "tüm hatlarımızda", so no code appears in the text."""
        assert next(a for a in announcements if a.id == 711).lines == frozenset()

    def test_summary_is_truncated_with_an_ellipsis(self, announcements):
        summary = next(a for a in announcements if a.id == 669).summary(60)
        assert len(summary) <= 60
        assert summary.endswith("…")


class TestScheduleFilter:
    def test_a_repair_affecting_services_is_a_schedule_alert(self, announcements):
        assert 669 in {a.id for a in schedule_alerts(announcements, "M7")}

    def test_a_line_opening_is_not_a_schedule_alert(self, announcements):
        """M5's extension opening changes the map, not M5's headway."""
        opening = next(a for a in announcements if a.id == 680)
        assert opening.is_schedule_related is False
        assert 680 not in {a.id for a in schedule_alerts(announcements, "M5")}

    def test_a_lineless_schedule_notice_reaches_every_line(self, announcements):
        for line in ("M2", "M5", "T1", "F1"):
            assert 711 in {a.id for a in schedule_alerts(announcements, line)}, line

    def test_another_lines_alert_is_not_reported(self, announcements):
        """T3's track work must not show up under T1."""
        assert 713 not in {a.id for a in schedule_alerts(announcements, "T1")}
        assert 713 in {a.id for a in schedule_alerts(announcements, "T3")}
