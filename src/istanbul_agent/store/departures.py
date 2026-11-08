"""The query behind `get_next_departures`.

Answers, for one rail line and optionally one station: when the first and
last service run, how often trains come in the current hour, and how today
differs from a weekday. Three sources, in this order of precedence:

1. `datasets/metro_service_hours.yaml` -- hand-verified current hours. A line
   listed there is answered from it and is **not** stale.
2. The timetable tables built from GTFS by `ingest_timetable`. The feed is
   dated 09.07.2020, so these answers carry `is_stale: true`.
3. Nothing. Marmaray's routes carry four template trips in this feed, which
   is not a timetable, so the tool says it has none rather than inventing
   one from four departures.

Schedule-related announcements for the line are attached to every answer:
the 2020 feed cannot know that Metro İstanbul switched to its winter
timetable last month, but the announcement says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from istanbul_agent.clients.metro import Announcement, schedule_alerts
from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.store.db import database_path, read_only_connection
from istanbul_agent.store.station_names import collapsed, normalize
from istanbul_agent.store.timetable import (
    DAY_TYPE_LABELS,
    SEGMENT_DAY,
    SEGMENT_NIGHT,
    day_type_for,
)

__all__ = [
    "SERVICE_HOURS_YAML",
    "DepartureInfo",
    "DeparturesResult",
    "ServiceHours",
    "get_next_departures",
    "load_service_hours",
]

SERVICE_HOURS_YAML = PROJECT_ROOT / "datasets" / "metro_service_hours.yaml"

GTFS_SOURCE = "Public Transport GTFS (veri seti 308)"


@dataclass(frozen=True)
class ServiceHours:
    """One line's hand-verified hours, from the YAML override."""

    line: str
    source_url: str
    retrieved_at: str
    note: str | None
    days: dict[str, dict[str, Any]]

    def for_day(self, day_type: str) -> dict[str, Any] | None:
        return self.days.get(day_type)


@dataclass(frozen=True)
class DepartureInfo:
    """Service for one direction of the line."""

    direction_id: str
    direction_name: str
    first: str
    last: str
    headway_minutes: int | None
    headway_hour: int | None
    running_now: bool
    expected_wait_minutes: int | None
    night_first: str | None = None
    night_last: str | None = None
    trip_count: int = 0
    estimated: bool = False

    @property
    def label(self) -> str:
        return self.direction_name or f"yön {self.direction_id}"


@dataclass
class DeparturesResult:
    """What `get_next_departures` returns."""

    line: str
    station: str | None
    day_type: str
    asked_at: str
    available: bool
    directions: list[DepartureInfo] = field(default_factory=list)
    source: str = ""
    data_as_of: str = ""
    is_stale: bool = True
    alerts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def day_label(self) -> str:
        return DAY_TYPE_LABELS.get(self.day_type, self.day_type)


def _parse_clock(value: str) -> int | None:
    parts = str(value).strip().split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60
    except ValueError:
        return None


@lru_cache(maxsize=1)
def load_service_hours(path: Path = SERVICE_HOURS_YAML) -> dict[str, ServiceHours]:
    """Hand-verified hours per line, keyed by line code.

    Returns an empty mapping when the file lists no line, which is the state
    until someone verifies a line's hours against an official source.
    """
    if not path.exists():
        return {}
    try:
        import yaml
    except ModuleNotFoundError:  # pragma: no cover - dependency is declared
        return {}

    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    lines = document.get("lines") or {}
    overrides: dict[str, ServiceHours] = {}
    for line, entry in lines.items():
        if not isinstance(entry, dict):
            continue
        days = {
            day: value
            for day, value in entry.items()
            if day in DAY_TYPE_LABELS and isinstance(value, dict)
        }
        if not days:
            continue
        overrides[str(line).strip().upper()] = ServiceHours(
            line=str(line).strip().upper(),
            source_url=str(entry.get("source_url") or "").strip(),
            retrieved_at=str(entry.get("retrieved_at") or "").strip(),
            note=entry.get("not"),
            days=days,
        )
    return overrides


def _resolve_station(rows: list[dict[str, Any]], station: str) -> str | None:
    """The station key in `rows` matching `station`, or None.

    Exact normalised name first, then the spacing-insensitive form, then a
    unique prefix, so "Sabiha Gökçen" finds "Sabiha Gökçen Havalimanı".
    """
    wanted = normalize(station)
    keys = {row["station_key"] for row in rows if row["station_key"]}
    if wanted in keys:
        return wanted
    tight = collapsed(station)
    by_collapsed = {collapsed(row["station_name"]): row["station_key"] for row in rows}
    if tight in by_collapsed:
        return by_collapsed[tight]
    if len(tight) >= 4:
        prefixed = {key for name, key in by_collapsed.items() if name.startswith(tight)}
        if len(prefixed) == 1:
            return prefixed.pop()
    return None


def _read_tables(line: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with read_only_connection() as connection:
        windows = [
            dict(
                zip(
                    (
                        "direction_id",
                        "direction_name",
                        "day_type",
                        "segment",
                        "station_key",
                        "station_name",
                        "first_s",
                        "last_s",
                        "first_clock",
                        "last_clock",
                        "trip_count",
                        "estimated",
                        "source",
                        "data_as_of",
                    ),
                    row,
                    strict=True,
                )
            )
            for row in connection.execute(
                "SELECT direction_id, direction_name, day_type, segment, station_key, "
                "station_name, first_s, last_s, first_clock, last_clock, trip_count, "
                "estimated, source, data_as_of FROM rail_service_windows WHERE upper(line) = ?",
                [line.upper()],
            ).fetchall()
        ]
        headways = [
            dict(
                zip(
                    ("direction_id", "day_type", "hour", "headway_s", "departures", "source"),
                    row,
                    strict=True,
                )
            )
            for row in connection.execute(
                "SELECT direction_id, day_type, hour, headway_s, departures, source "
                "FROM rail_headways WHERE upper(line) = ?",
                [line.upper()],
            ).fetchall()
        ]
    return windows, headways


def _headway_for(
    headways: list[dict[str, Any]], direction_id: str, day_type: str, hour: int
) -> tuple[int | None, int | None]:
    """Headway in seconds for `hour`, and the hour it actually describes.

    An hour with no row is an hour with no service, so the figure returned is
    the one for the next hour that does run -- the service the passenger will
    meet when it resumes. Answering with the numerically nearest hour instead
    would quote the previous night's last trains at someone asking at 02:00.
    """
    rows = [
        row
        for row in headways
        if row["direction_id"] == direction_id and row["day_type"] == day_type
    ]
    if not rows:
        return None, None
    exact = next((row for row in rows if row["hour"] == hour), None)
    if exact is not None:
        return exact["headway_s"], exact["hour"]
    ahead = min(rows, key=lambda row: (row["hour"] - hour) % 24)
    return ahead["headway_s"], ahead["hour"]


def _running(now_s: int, first_s: int, last_s: int) -> bool:
    """Is the service running at `now_s`?

    Windows may end past midnight (`last_s` of 90000 is 01:00), so the
    comparison is done both on the raw seconds and on the next day's.
    """
    return first_s <= now_s <= last_s or first_s <= now_s + 24 * 3600 <= last_s


def _from_override(
    hours: ServiceHours, line: str, day_type: str, at: datetime, alerts: list[str]
) -> DeparturesResult:
    entry = hours.for_day(day_type) or {}
    first = str(entry.get("ilk") or "")
    last = str(entry.get("son") or "")
    first_s = _parse_clock(first)
    last_s = _parse_clock(last)
    headway_minutes = entry.get("aralik_dk")
    by_hour = entry.get("aralik") or {}
    if isinstance(by_hour, dict) and at.hour in by_hour:
        headway_minutes = by_hour[at.hour]
    now_s = at.hour * 3600 + at.minute * 60

    running = (
        _running(now_s, first_s, last_s if last_s > first_s else last_s + 24 * 3600)
        if first_s is not None and last_s is not None
        else True
    )
    info = DepartureInfo(
        direction_id="",
        direction_name="her iki yön",
        first=first,
        last=last,
        headway_minutes=int(headway_minutes) if headway_minutes else None,
        headway_hour=at.hour,
        running_now=running,
        expected_wait_minutes=(
            math.ceil(int(headway_minutes) / 2) if headway_minutes and running else None
        ),
    )
    notes = [f"Saatler elle doğrulanmış kaynaktan: {hours.source_url}"]
    if hours.note:
        notes.append(str(hours.note))
    return DeparturesResult(
        line=line,
        station=None,
        day_type=day_type,
        asked_at=at.strftime("%Y-%m-%d %H:%M"),
        available=True,
        directions=[info],
        source=f"datasets/metro_service_hours.yaml ({hours.source_url})",
        data_as_of=hours.retrieved_at,
        is_stale=False,
        alerts=alerts,
        notes=notes,
    )


def get_next_departures(
    line: str,
    station: str | None = None,
    *,
    at: datetime | None = None,
    announcements: list[Announcement] | None = None,
) -> DeparturesResult:
    """First and last service, and the current headway, for one rail line.

    Args:
        line: line code, e.g. `M2`, `T1`, `F1`.
        station: optional station; first and last service are then the times
            at that station, which differ from the line's by up to an hour.
        at: the moment to answer for; defaults to now.
        announcements: Metro İstanbul announcements to check for schedule
            changes. Pass them in so unit tests need no network.
    """
    at = at or datetime.now()
    code = line.strip().upper()
    day_type = day_type_for(at.weekday())
    alerts = [
        f"{alert.title} — {alert.summary(200)}"
        for alert in schedule_alerts(announcements or [], code)
    ]

    # Passed explicitly rather than defaulted so the path stays overridable
    # (the default argument would bind at import time).
    override = load_service_hours(SERVICE_HOURS_YAML).get(code)
    if override is not None and override.for_day(day_type):
        return _from_override(override, code, day_type, at, alerts)

    result = DeparturesResult(
        line=code,
        station=station,
        day_type=day_type,
        asked_at=at.strftime("%Y-%m-%d %H:%M"),
        available=False,
        source=GTFS_SOURCE,
        alerts=alerts,
    )
    if not database_path().exists():
        result.notes.append("Yerel tarife deposu kurulmamış: ingest_timetable çalıştırılmalı.")
        return result

    windows, headways = _read_tables(code)
    if not windows:
        result.notes.append(
            f"{code} için tarife verisi yok. 2020 tarihli GTFS beslemesinde bu hat "
            "yalnızca birkaç örnek seferle yer alıyor; ilk/son sefer ve sefer aralığı "
            "bu veriden türetilemez."
        )
        return result

    station_key = ""
    if station:
        resolved = _resolve_station(windows, station)
        if resolved is None:
            result.notes.append(
                f"'{station}' {code} hattında bulunamadı; saatler hattın tamamı için verildi."
            )
        else:
            station_key = resolved

    today = [row for row in windows if row["day_type"] == day_type]
    if not today:
        result.notes.append(
            f"{code} hattının {DAY_TYPE_LABELS.get(day_type, day_type)} için seferi yok."
        )
        result.data_as_of = windows[0]["data_as_of"]
        return result

    now_s = at.hour * 3600 + at.minute * 60
    directions: list[DepartureInfo] = []
    for direction_id in sorted({row["direction_id"] for row in today}):
        rows = [
            row
            for row in today
            if row["direction_id"] == direction_id and row["station_key"] == station_key
        ]
        day = next((row for row in rows if row["segment"] == SEGMENT_DAY), None)
        night = next((row for row in rows if row["segment"] == SEGMENT_NIGHT), None)
        if day is None:
            continue
        headway_s, hour = _headway_for(headways, direction_id, day_type, at.hour)
        running = _running(now_s, day["first_s"], day["last_s"]) or (
            night is not None and _running(now_s, night["first_s"], night["last_s"])
        )
        directions.append(
            DepartureInfo(
                direction_id=direction_id,
                direction_name=day["direction_name"],
                first=day["first_clock"],
                last=day["last_clock"],
                headway_minutes=round(headway_s / 60) if headway_s else None,
                headway_hour=hour,
                running_now=running,
                expected_wait_minutes=(
                    math.ceil(headway_s / 120) if headway_s and running else None
                ),
                night_first=night["first_clock"] if night else None,
                night_last=night["last_clock"] if night else None,
                trip_count=day["trip_count"],
                estimated=bool(day["estimated"]),
            )
        )

    if not directions:
        result.notes.append(f"{code} için {day_type} tarifesi okunamadı.")
        return result

    result.available = True
    result.directions = directions
    result.data_as_of = today[0]["data_as_of"]
    result.is_stale = True
    source_detail = today[0]["source"]
    result.source = f"{GTFS_SOURCE} — {source_detail}"
    if station_key:
        result.station = next(
            (row["station_name"] for row in today if row["station_key"] == station_key), station
        )
    if any(info.estimated for info in directions):
        result.notes.append(
            "Bu hattın seferleri beslemede yalnızca sefer aralığı olarak var; istasyon "
            "saatleri hattın başlangıcına sefer süresi eklenerek tahmin edildi."
        )
    result.warnings.append(
        "Saatler 09.07.2020 tarihli GTFS beslemesinden; bugünkü tarife farklı olabilir. "
        "Elle doğrulanmış saatler datasets/metro_service_hours.yaml dosyasına eklenirse "
        "onlar kullanılır."
    )
    if alerts:
        result.warnings.append(
            "Metro İstanbul duyurularında bu hat için tarife değişikliği var; "
            "aşağıdaki duyuru hesaplanan saatlerin önüne geçer."
        )
    if any(info.night_first for info in directions):
        result.notes.append(
            "Gece servisi ayrı gösterildi: beslemede 00:20-03:40 arası seferler bir "
            "önceki günün gece hizmetidir."
        )
    return result
