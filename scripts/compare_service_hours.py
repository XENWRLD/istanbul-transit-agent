"""What the hand-verified hours changed, line by line.

Prints the feed's hours next to the verified ones for every line
`datasets/metro_service_hours.yaml` covers, then five of the departure
examples before and after. Reporting only -- nothing here changes data.

    uv run python scripts/compare_service_hours.py
"""

from __future__ import annotations

import sys
from datetime import datetime

from istanbul_agent.store.departures import DeparturesResult, get_next_departures
from istanbul_agent.store.service_hours import DAY_LABELS, load_service_hours

MONDAY = datetime(2026, 11, 9, 8, 30)
MONDAY_EVENING = datetime(2026, 11, 9, 18, 15)
SATURDAY_NIGHT = datetime(2026, 11, 7, 23, 30)

BEFORE_AFTER = (
    ("M2", None, MONDAY, "#1  hafta içi sabah"),
    ("M4", "Kadıköy", MONDAY_EVENING, "#3  akşam, terminal istasyon"),
    ("M4", "Kadıköy", SATURDAY_NIGHT, "#4  cumartesi gecesi"),
    ("M5", "Üsküdar", MONDAY, "#13 M5 terminali"),
    ("Marmaray", None, MONDAY, "#14 önce tarife yoktu"),
)


def window(result: DeparturesResult) -> str:
    if not result.available:
        return "tarife yok"
    parts = []
    for info in result.directions:
        first = info.first or "?"
        parts.append(f"{first}-{info.last_text if info.all_night else info.last or '?'}")
    return " / ".join(dict.fromkeys(parts))


def main() -> int:
    hours = load_service_hours()
    lines = sorted({entry.line for entry in hours.values()})

    print("=== Hat bazında: GTFS saatleri vs elle doğrulanmış saatler ===\n")
    print(f"{'Hat':10} {'Gün':20} {'GTFS (2020-07-09)':28} {'Yeni (2026-11-08)':28}")
    print("-" * 90)
    days = (
        ("pzt_pers", datetime(2026, 11, 9, 8, 30)),
        ("cuma", datetime(2026, 11, 6, 8, 30)),
        ("cumartesi", datetime(2026, 11, 7, 8, 30)),
        ("pazar", datetime(2026, 11, 8, 8, 30)),
    )
    for line in lines:
        for day_type, at in days:
            feed = get_next_departures(line, at=at, use_override=False)
            verified = get_next_departures(line, at=at)
            print(f"{line:10} {DAY_LABELS[day_type]:20} {window(feed):28} {window(verified):28}")
        print()

    print("\n=== Örnekler: önce / sonra ===")
    for line, station, at, label in BEFORE_AFTER:
        target = line + (f" / {station}" if station else "")
        print("=" * 78)
        print(f"{label}   {target}  @ {at:%d.%m.%Y %H:%M}")
        for title, result in (
            ("ÖNCE (GTFS)", get_next_departures(line, station, at=at, use_override=False)),
            ("SONRA (elle doğrulanmış)", get_next_departures(line, station, at=at)),
        ):
            print(f"  {title} — {result.day_label}")
            if not result.available:
                print("    tarife verilemedi")
            for info in result.directions:
                headway = f"her {info.headway_minutes} dk" if info.headway_minutes else "aralık yok"
                shift = (
                    f"  [{info.terminal} + {info.offset_minutes} dk]"
                    if info.terminal and info.offset_minutes
                    else ""
                )
                print(
                    f"    {info.label[:24]:26} ilk {info.first or '-':>5}  "
                    f"son {info.last_text:26} {headway}{shift}"
                )
            print(
                f"    hours_is_stale={str(result.hours_is_stale).lower()} "
                f"({result.hours_source_type or '-'}, {result.hours_as_of or '-'})  "
                f"headway_is_stale={str(result.headway_is_stale).lower()} "
                f"({result.headway_source_type or '-'}, {result.headway_as_of or '-'})"
            )
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
