"""Hand-checkable examples of `get_next_departures`.

Fourteen queries covering what the tool has to get right: the two timetable
sources, a station's own times against the line's, the weekend, the night
service, service outside its hours, a line with no timetable at all, and an
announcement that overrides what the feed says.

Announcements come from the saved fixture by default so the output is stable;
`--live` fetches them from Metro İstanbul instead.

    uv run python scripts/show_departure_examples.py
    uv run python scripts/show_departure_examples.py --live
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

from istanbul_agent.clients.metro import Announcement, fetch_announcements, parse_announcements
from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.store.departures import get_next_departures

FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "metro" / "metro-announcements-tr.json"

# A Monday, a Saturday and a Sunday in November 2026, so the day type is
# fixed and the output does not change with the day the script is run.
MONDAY = datetime(2026, 11, 9, 8, 30)
MONDAY_EVENING = datetime(2026, 11, 9, 18, 15)
MONDAY_NIGHT = datetime(2026, 11, 9, 1, 0)
SATURDAY_NIGHT = datetime(2026, 11, 7, 23, 30)
SUNDAY = datetime(2026, 11, 8, 11, 0)
SUNDAY_NIGHT = datetime(2026, 11, 8, 2, 0)

EXAMPLES: tuple[tuple[str, str | None, datetime, str], ...] = (
    ("M2", None, MONDAY, "hafta içi sabah zirvesi, stop_times kaynaklı"),
    ("M2", "Taksim", MONDAY, "istasyon saatleri hattın saatlerinden farklı"),
    ("M4", "Kadıköy", MONDAY_EVENING, "akşam zirvesi, terminal istasyon"),
    ("M4", "Kadıköy", SATURDAY_NIGHT, "cumartesi gecesi: gece servisi ayrı"),
    ("M8", None, MONDAY, "frequencies kaynaklı hat (beyan edilen aralık)"),
    ("M8", None, MONDAY_NIGHT, "servis saatleri dışında"),
    ("M7", None, SUNDAY_NIGHT, "pazar gecesi: bir yön çalışıyor, biri değil"),
    ("M9", None, SUNDAY, "frequencies, pazar"),
    ("T1", None, MONDAY_EVENING, "tramvay, akşam"),
    ("T3", None, SUNDAY, "ring hat + duyuruda bakım çalışması"),
    ("F1", None, MONDAY, "iki istasyonluk füniküler"),
    ("TF2", None, MONDAY, "teleferik, duyuruda kış tarifesi"),
    ("M5", "Üsküdar", MONDAY, "M5 terminali"),
    ("Marmaray", None, MONDAY, "tarife verisi yok — açıkça söylemeli"),
)


def show(
    line: str,
    station: str | None,
    at: datetime,
    purpose: str,
    announcements: list[Announcement],
) -> None:
    result = get_next_departures(line, station, at=at, announcements=announcements)
    target = f"{line}" + (f" / {station}" if station else "")
    print("=" * 78)
    print(f"{target}  @ {at:%d.%m.%Y %H:%M} ({result.day_label})")
    print(f"    amaç: {purpose}")
    if not result.available:
        print("    SONUÇ: tarife verilemedi")
    for info in result.directions:
        wait = (
            f"ortalama bekleme ~{info.expected_wait_minutes} dk"
            if info.expected_wait_minutes
            else "şu an servis yok"
        )
        headway = (
            f"her {info.headway_minutes} dk (saat {info.headway_hour:02d})"
            if info.headway_minutes
            else "aralık bilinmiyor"
        )
        print(f"      {info.label[:26]:28} ilk {info.first}  son {info.last}  {headway:24} {wait}")
        if info.night_first:
            print(f"      {'':28} gece servisi {info.night_first}-{info.night_last}")
        if info.estimated:
            print(f"      {'':28} istasyon saatleri tahmin")
    print(f"    kaynak: {result.source}")
    print(f"    data_as_of={result.data_as_of}  is_stale={str(result.is_stale).lower()}")
    for note in result.notes:
        print(f"    not: {note}")
    for warning in result.warnings:
        print(f"    uyarı: {warning}")
    for alert in result.alerts:
        print(f"    DUYURU: {alert}")


def main() -> int:
    live = "--live" in sys.argv
    announcements: list[Announcement]
    if live:
        announcements = fetch_announcements()
        print(f"Canlı duyurular: {len(announcements)} kayıt\n")
    else:
        announcements = parse_announcements(json.loads(FIXTURE.read_text(encoding="utf-8")))
        print(f"Duyurular fixture'dan: {FIXTURE.name} ({len(announcements)} kayıt)\n")

    for line, station, at, purpose in EXAMPLES:
        show(line, station, at, purpose, announcements)
    print("=" * 78)
    print(f"{len(EXAMPLES)} örnek. Saatleri metro.istanbul ile karşılaştırın:")
    print("  https://www.metro.istanbul/YolcuHizmetleri/SeferSaatleri")
    return 0


if __name__ == "__main__":
    sys.exit(main())
