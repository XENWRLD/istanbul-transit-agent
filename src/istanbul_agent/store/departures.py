"""The query behind `get_next_departures`.

Answers, for one rail line and optionally one station: when the first and
last service run, how often trains come right now, and how the day differs
from a weekday.

Hours and headway have separate sources and separate staleness, because they
really do come from different places:

| | kaynak | taze mi |
|---|---|---|
| ilk/son sefer, `metro_service_hours.yaml`'daki hatlarda | elle doğrulanmış | `hours_is_stale: false` |
| ilk/son sefer, diğer hatlarda | GTFS 09.07.2020 | `hours_is_stale: true` |
| sefer aralığı, Marmaray | elle doğrulanmış | `headway_is_stale: false` |
| sefer aralığı, diğer hatlarda | GTFS 09.07.2020 | `headway_is_stale: true` |

So a metro answer normally carries fresh hours and a stale headway, and says
so rather than averaging the two into one flag.

The hand-verified hours are stated at the line's terminals. A station's own
times are those shifted by how long the train takes to reach it, measured
from `rail_segments` -- M4's last train leaves Kadıköy 38 minutes after it
leaves Tavşantepe, and answering "00:00" at both would be wrong at one of
them.

Schedule-related announcements are attached to every answer: no timetable,
however current, knows that a line is running on an adjusted schedule this
week, and the announcement does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from istanbul_agent.clients.metro import Announcement, schedule_alerts
from istanbul_agent.store.db import database_path, read_only_connection
from istanbul_agent.store.rail_travel import RailNetwork
from istanbul_agent.store.service_hours import (
    DAY_LABELS,
    SERVICE_HOURS_YAML,
    DayHours,
    HeadwayRule,
    LineHours,
    gtfs_day_type_for,
    load_service_hours,
    override_day_type_for,
)
from istanbul_agent.store.station_names import collapsed, normalize, similarity
from istanbul_agent.store.timetable import DAY_TYPE_LABELS, SEGMENT_DAY, SEGMENT_NIGHT

__all__ = [
    "GTFS_DATA_AS_OF",
    "GTFS_SOURCE",
    "DepartureInfo",
    "DeparturesResult",
    "get_next_departures",
]

GTFS_SOURCE = "Public Transport GTFS (veri seti 308)"
GTFS_DATA_AS_OF = "2020-07-09"


@dataclass(frozen=True)
class DepartureInfo:
    """Service in one direction of the line.

    `terminal` is the station the stated hours belong to and
    `offset_minutes` how far the queried station is from it; together they
    say where the times came from.
    """

    direction_name: str
    first: str | None
    last: str | None
    headway_minutes: int | None
    headway_hour: int | None
    running_now: bool
    expected_wait_minutes: int | None
    all_night: bool = False
    terminal: str | None = None
    offset_minutes: int | None = None
    night_first: str | None = None
    night_last: str | None = None
    hours_source: str = ""
    headway_source: str = ""
    trip_count: int = 0
    estimated: bool = False

    @property
    def label(self) -> str:
        return self.direction_name or "her iki yön"

    @property
    def last_text(self) -> str:
        if self.all_night:
            return "son sefer yok (gece boyunca)"
        return self.last or "bilinmiyor"


@dataclass
class DeparturesResult:
    """What `get_next_departures` returns."""

    line: str
    station: str | None
    day_type: str
    asked_at: str
    available: bool
    directions: list[DepartureInfo] = field(default_factory=list)
    hours_source: str = ""
    hours_is_stale: bool = True
    hours_as_of: str = ""
    hours_source_type: str = ""
    headway_source: str = ""
    headway_is_stale: bool = True
    headway_as_of: str = ""
    headway_source_type: str = ""
    alerts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def day_label(self) -> str:
        return DAY_LABELS.get(self.day_type, DAY_TYPE_LABELS.get(self.day_type, self.day_type))


# --------------------------------------------------------------- helpers


def _parse_clock(value: str | None) -> int | None:
    if not value:
        return None
    parts = str(value).strip().split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60
    except ValueError:
        return None


def _clock(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    return f"{(seconds // 3600) % 24:02d}:{(seconds % 3600) // 60:02d}"


def _running(now_s: int, first_s: int | None, last_s: int | None) -> bool:
    """Is the service running at `now_s`?

    A window ending after midnight is stated as a smaller number than its
    start (06:00 to 01:20), so the end is pushed into the next day before
    comparing.
    """
    if first_s is None or last_s is None:
        return False
    end = last_s if last_s >= first_s else last_s + 24 * 3600
    return first_s <= now_s <= end or first_s <= now_s + 24 * 3600 <= end


def _match_direction_name(name: str, destination: str) -> float:
    """How well a GTFS headsign matches a terminal name."""
    if not name or not destination:
        return 0.0
    if normalize(name) == normalize(destination) or collapsed(name) == collapsed(destination):
        return 1.0
    return similarity(name, destination)


# ------------------------------------------------------- the GTFS tables


def _read_windows(line: str) -> list[dict[str, Any]]:
    columns = (
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
    )
    with read_only_connection() as connection:
        return [
            dict(zip(columns, row, strict=True))
            for row in connection.execute(
                f"SELECT {', '.join(columns)} FROM rail_service_windows WHERE upper(line) = ?",
                [line.upper()],
            ).fetchall()
        ]


def _read_headways(line: str) -> list[dict[str, Any]]:
    columns = (
        "direction_id",
        "direction_name",
        "day_type",
        "hour",
        "headway_s",
        "departures",
        "source",
        "data_as_of",
    )
    with read_only_connection() as connection:
        return [
            dict(zip(columns, row, strict=True))
            for row in connection.execute(
                f"SELECT {', '.join(columns)} FROM rail_headways WHERE upper(line) = ?",
                [line.upper()],
            ).fetchall()
        ]


def _headway_at(rows: list[dict[str, Any]], hour: int) -> tuple[int | None, int | None, str, bool]:
    """Headway for `hour` among `rows`, the hour it describes, and whether
    that is the asked-for hour.

    An hour with no row is an hour the feed has no service in, so the answer
    is the next hour that does run -- the service the passenger meets when it
    resumes. Quoting the numerically nearest hour instead would answer
    someone asking at 02:00 with the previous evening's trains. The caller
    needs to know which case it got: on a night-metro line the feed having
    no row for 02:00 does not mean nothing runs, it means the feed cannot say
    how often.
    """
    if not rows:
        return None, None, "", False
    exact = next((row for row in rows if row["hour"] == hour), None)
    chosen = exact or min(rows, key=lambda row: (row["hour"] - hour) % 24)
    return chosen["headway_s"], chosen["hour"], chosen["source"], exact is not None


def _resolve_station(rows: list[dict[str, Any]], station: str) -> str | None:
    """The station key in `rows` matching `station`, or None."""
    wanted = normalize(station)
    if wanted in {row["station_key"] for row in rows if row["station_key"]}:
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


# ------------------------------------------------- hand-verified answers


def _geometry(code: str, parent: str) -> tuple[RailNetwork, str]:
    """The network and the line whose station order the offsets follow.

    A line runs in the feed under more than one code: Marmaray's
    Zeytinburnu-Söğütlüçeşme fragment is `Marmaray1`, and the trains calling
    there are Halkalı-Gebze trains. So offsets are measured along the parent
    line when the feed has it, which is also what makes the terminals in the
    answer the real ones.
    """
    from istanbul_agent.store.rail_queries import load_network

    network = load_network()
    # The YAML keys lines in upper case; the feed does not ("Marmaray").
    by_code = {station.line.upper(): station.line for station in network.stations}
    line = by_code.get(parent.upper()) or by_code.get(code.upper()) or code
    return network, line


def _offset_seconds(
    network: RailNetwork, line: str, terminal: str, station_key: str | None
) -> int | None:
    """Travel time from `terminal` to `station_key` along `line`."""
    if not station_key:
        return 0
    stations = [s for s in network.stations_on(line)]
    origin = next((s for s in stations if network.key(s.name) == normalize(terminal)), None)
    target = next((s for s in stations if network.key(s.name) == station_key), None)
    if origin is None or target is None:
        return None
    if network.key(origin.name) == network.key(target.name):
        return 0
    leg = network.same_line_leg(origin, target)
    return leg.seconds if leg is not None else None


def _dense_bounds(
    network: RailNetwork, line: str, rule: HeadwayRule | None
) -> tuple[int, int] | None:
    """Index range of the section where the stated extra trains run."""
    if rule is None or rule.between is None:
        return None
    order = [network.key(s.name) for s in network.stations_on(line)]
    try:
        first = order.index(normalize(rule.between[0]))
        last = order.index(normalize(rule.between[1]))
    except ValueError:
        return None
    return (min(first, last), max(first, last))


def _terminal_pairs(network: RailNetwork, line: str, hours: LineHours) -> list[tuple[str, str]]:
    """`(terminal, destination)` for each direction the answer covers."""
    stations = network.stations_on(line)
    if len(stations) < 2:
        return []
    ends = (stations[0].name, stations[-1].name)
    if not hours.per_direction:
        return [(ends[0], ends[1]), (ends[1], ends[0])]

    pairs: list[tuple[str, str]] = []
    for direction in hours.directions:
        if direction.terminal is None:
            continue
        terminal = direction.terminal
        other = next(
            (name for name in ends if normalize(name) != normalize(terminal)),
            ends[1],
        )
        pairs.append((terminal, other))
    return pairs


def _from_override(
    hours: LineHours,
    code: str,
    station: str | None,
    at: datetime,
    alerts: list[str],
) -> DeparturesResult:
    day_type = override_day_type_for(at.weekday())
    network, line = _geometry(code, hours.line)
    result = DeparturesResult(
        line=code,
        station=station,
        day_type=day_type,
        asked_at=at.strftime("%Y-%m-%d %H:%M"),
        available=False,
        hours_source=f"{SERVICE_HOURS_YAML.name} ({hours.source_type})",
        hours_is_stale=False,
        hours_as_of=hours.retrieved_at,
        hours_source_type=hours.source_type,
        alerts=alerts,
    )

    station_key: str | None = None
    if station:
        names = [
            {"station_key": network.key(s.name), "station_name": s.name}
            for s in network.stations_on(line)
        ]
        station_key = _resolve_station(names, station)
        if station_key is None:
            result.notes.append(
                f"'{station}' {code} hattında bulunamadı; saatler hattın uç "
                "istasyonları için verildi."
            )
        else:
            result.station = next(
                (s.name for s in network.stations_on(line) if network.key(s.name) == station_key),
                station,
            )

    # Headway: stated here only for Marmaray; every other line still measures
    # it from the feed, which is why the two staleness flags differ.
    gtfs_headways = _read_headways(code) if database_path().exists() else []
    gtfs_day = gtfs_day_type_for(day_type)
    bounds = _dense_bounds(network, line, hours.headway)
    order = [network.key(s.name) for s in network.stations_on(line)]
    station_index = order.index(station_key) if station_key in order else None

    now_s = at.hour * 3600 + at.minute * 60
    for terminal, destination in _terminal_pairs(network, line, hours):
        direction = hours.for_terminal(normalize(terminal))
        day = direction.for_day(day_type) if direction else None
        if day is None:
            continue
        offset = _offset_seconds(network, line, terminal, station_key)
        info = _direction_info(
            day=day,
            terminal=terminal,
            destination=destination,
            offset=offset,
            now_s=now_s,
            hours=hours,
            rule=hours.headway,
            station_index=station_index,
            bounds=bounds,
            gtfs_headways=[row for row in gtfs_headways if row["day_type"] == gtfs_day],
            destination_for_headway=destination,
            at_hour=at.hour,
            result=result,
        )
        result.directions.append(info)

    if not result.directions:
        result.notes.append(
            f"{code} için {DAY_LABELS.get(day_type, day_type)} saatleri tanımlı değil."
        )
        return result

    result.available = True
    if hours.note:
        result.notes.append(hours.note)
    result.notes.append(
        f"İlk/son sefer saatleri {SERVICE_HOURS_YAML.name} dosyasından "
        f"({hours.source_type}, {hours.retrieved_at}); bu hat için GTFS saatleri "
        "kullanılmıyor."
    )
    terminus_of = [
        info.label
        for info in result.directions
        if station_key and normalize(info.direction_name) == station_key
    ]
    if terminus_of:
        result.notes.append(
            f"{result.station} bu hatta son durak: {', '.join(terminus_of)} yönündeki "
            "saatler oraya varış saatleridir, binme saati değil."
        )
    if station_key and any(info.offset_minutes for info in result.directions):
        result.notes.append(
            "Saatler uç istasyon saatlerinin sefer süresi kadar kaydırılmasıyla "
            "bulundu (rail_segments)."
        )
    if any(info.all_night for info in result.directions):
        result.notes.append(
            f"{DAY_LABELS.get(day_type, day_type)} gecesi servis kesilmiyor: son sefer yok. "
            "Gece sefer aralığı bu veride yok, bu yüzden verilmiyor."
        )
    if result.headway_is_stale:
        result.warnings.append(
            "Sefer aralığı hâlâ 09.07.2020 tarihli GTFS beslemesinden ölçülüyor "
            "(ilk/son sefer saatleri güncel)."
        )
    if alerts:
        result.warnings.append(
            "Metro İstanbul duyurularında bu hat için tarife değişikliği var; "
            "duyuru hesaplanan saatlerin önüne geçer."
        )
    return result


def _direction_info(
    *,
    day: DayHours,
    terminal: str,
    destination: str,
    offset: int | None,
    now_s: int,
    hours: LineHours,
    rule: HeadwayRule | None,
    station_index: int | None,
    bounds: tuple[int, int] | None,
    gtfs_headways: list[dict[str, Any]],
    destination_for_headway: str,
    at_hour: int,
    result: DeparturesResult,
) -> DepartureInfo:
    """One direction's answer: stated hours, shifted, plus a headway."""
    shift = offset or 0
    first_s = _parse_clock(day.first)
    last_s = _parse_clock(day.last)
    first = _clock(first_s + shift) if first_s is not None else None
    last = _clock(last_s + shift) if last_s is not None else None

    if rule is not None:
        headway_minutes: int | None = rule.minutes_at(station_index, bounds)
        headway_hour = None
        headway_source = f"{SERVICE_HOURS_YAML.name} ({hours.source_type})"
        result.headway_is_stale = False
        result.headway_source = headway_source
        result.headway_as_of = hours.retrieved_at
        result.headway_source_type = hours.source_type
    else:
        rows = _best_direction_rows(gtfs_headways, destination_for_headway)
        headway_s, headway_hour, source, exact = _headway_at(rows, at_hour)
        if day.all_night and not exact:
            # Service is running -- the stated hours say so -- but the feed
            # has no trips at this hour, so it cannot say how often. Saying
            # nothing is the honest answer; quoting the daytime figure would
            # invent a night timetable.
            headway_s, headway_hour, source = None, None, ""
        headway_minutes = round(headway_s / 60) if headway_s else None
        headway_source = f"{GTFS_SOURCE} — {source}" if source else ""
        result.headway_source = headway_source or result.headway_source
        result.headway_as_of = GTFS_DATA_AS_OF
        result.headway_source_type = "gtfs"

    running = (
        True
        if day.all_night
        else _running(
            now_s,
            None if first_s is None else first_s + shift,
            None if last_s is None else last_s + shift,
        )
    )
    wait = math.ceil(headway_minutes / 2) if running and headway_minutes else None

    return DepartureInfo(
        direction_name=destination,
        first=first,
        last=last,
        headway_minutes=headway_minutes,
        headway_hour=headway_hour,
        running_now=running,
        expected_wait_minutes=wait,
        all_night=day.all_night,
        terminal=terminal,
        offset_minutes=round(shift / 60) if shift else 0,
        hours_source=f"{SERVICE_HOURS_YAML.name} ({hours.source_type}, {hours.retrieved_at})",
        headway_source=headway_source,
    )


def _best_direction_rows(rows: list[dict[str, Any]], destination: str) -> list[dict[str, Any]]:
    """Feed rows for the direction heading to `destination`.

    The feed names a direction by its headsign, so the direction is picked by
    matching that against the terminal the answer is about; without a match
    the rows are left as they are rather than guessing a direction.
    """
    if not rows:
        return []
    scored = {
        row["direction_id"]: _match_direction_name(row["direction_name"], destination)
        for row in rows
    }
    best = max(scored.values(), default=0.0)
    if best < 0.6:
        return rows
    wanted = {direction for direction, score in scored.items() if score == best}
    return [row for row in rows if row["direction_id"] in wanted]


# ------------------------------------------------------- the feed answer


def _from_gtfs(
    code: str,
    station: str | None,
    at: datetime,
    alerts: list[str],
) -> DeparturesResult:
    from istanbul_agent.store.timetable import day_type_for

    day_type = day_type_for(at.weekday())
    result = DeparturesResult(
        line=code,
        station=station,
        day_type=day_type,
        asked_at=at.strftime("%Y-%m-%d %H:%M"),
        available=False,
        hours_source=GTFS_SOURCE,
        hours_is_stale=True,
        hours_as_of=GTFS_DATA_AS_OF,
        hours_source_type="gtfs",
        headway_source=GTFS_SOURCE,
        headway_is_stale=True,
        headway_as_of=GTFS_DATA_AS_OF,
        headway_source_type="gtfs",
        alerts=alerts,
    )
    if not database_path().exists():
        result.notes.append("Yerel tarife deposu kurulmamış: ingest_timetable çalıştırılmalı.")
        return result

    windows = _read_windows(code)
    headways = _read_headways(code)
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
            result.station = next(
                (row["station_name"] for row in windows if row["station_key"] == station_key),
                station,
            )

    today = [row for row in windows if row["day_type"] == day_type]
    if not today:
        result.notes.append(
            f"{code} hattının {DAY_TYPE_LABELS.get(day_type, day_type)} için seferi yok."
        )
        return result

    now_s = at.hour * 3600 + at.minute * 60
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
        headway_s, hour, headway_source, _ = _headway_at(
            [
                row
                for row in headways
                if row["direction_id"] == direction_id and row["day_type"] == day_type
            ],
            at.hour,
        )
        running = _running(now_s, day["first_s"], day["last_s"]) or (
            night is not None and _running(now_s, night["first_s"], night["last_s"])
        )
        result.directions.append(
            DepartureInfo(
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
                hours_source=f"{GTFS_SOURCE} — {day['source']}",
                headway_source=f"{GTFS_SOURCE} — {headway_source}" if headway_source else "",
                trip_count=day["trip_count"],
                estimated=bool(day["estimated"]),
            )
        )

    if not result.directions:
        result.notes.append(f"{code} için {day_type} tarifesi okunamadı.")
        return result

    result.available = True
    result.hours_source = f"{GTFS_SOURCE} — {today[0]['source']}"
    if any(info.estimated for info in result.directions):
        result.notes.append(
            "Bu hattın seferleri beslemede yalnızca sefer aralığı olarak var; istasyon "
            "saatleri hattın başlangıcına sefer süresi eklenerek tahmin edildi."
        )
    result.warnings.append(
        "Saatler ve sefer aralığı 09.07.2020 tarihli GTFS beslemesinden; bugünkü "
        "tarife farklı olabilir. Elle doğrulanmış saatler "
        "datasets/metro_service_hours.yaml dosyasına eklenirse onlar kullanılır."
    )
    if alerts:
        result.warnings.append(
            "Metro İstanbul duyurularında bu hat için tarife değişikliği var; "
            "duyuru hesaplanan saatlerin önüne geçer."
        )
    if any(info.night_first for info in result.directions):
        result.notes.append(
            "Gece servisi ayrı gösterildi: beslemede 00:20-03:40 arası seferler bir "
            "önceki günün gece hizmetidir."
        )
    return result


# --------------------------------------------------------------- the tool


def get_next_departures(
    line: str,
    station: str | None = None,
    *,
    at: datetime | None = None,
    announcements: list[Announcement] | None = None,
    use_override: bool = True,
) -> DeparturesResult:
    """First and last service, and the current headway, for one rail line.

    Args:
        line: line code, e.g. `M2`, `T1`, `Marmaray`.
        station: optional station; the times are then that station's, which
            differ from the terminal's by the travel time between them.
        at: the moment to answer for; defaults to now.
        announcements: Metro İstanbul announcements, checked for schedule
            changes. Passed in so unit tests need no network.
        use_override: set False to read the feed even for a line whose hours
            are hand-verified. Only for comparing the two; a caller
            answering a user wants the default.
    """
    at = at or datetime.now()
    code = line.strip().upper()
    alerts = [
        f"{alert.title} — {alert.summary(200)}"
        for alert in schedule_alerts(announcements or [], code)
    ]

    hours = load_service_hours(SERVICE_HOURS_YAML).get(code) if use_override else None
    if hours is not None:
        day_type = override_day_type_for(at.weekday())
        if hours.covers(day_type):
            return _from_override(hours, code, station, at, alerts)

    return _from_gtfs(code, station, at, alerts)
