"""Reading `datasets/metro_service_hours.yaml`.

Hand-verified service hours, which take precedence over the 2020 GTFS feed
for the lines they cover. Two things are deliberately separate:

- **Hours** (first and last service). For a line listed here these come only
  from this file; the feed's are not consulted, and the answer is not stale.
- **Headway.** Only Marmaray states one here. For the metro lines the
  headway is still measured from the feed, so one answer can carry fresh
  hours and a stale headway -- and says so.

Day types are finer than the feed's. The feed can only tell weekday from
Saturday from Sunday; night metro runs on the nights that join Friday to
Saturday and Saturday to Sunday, so Friday has to be its own day.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from istanbul_agent.config import PROJECT_ROOT

__all__ = [
    "DAY_LABELS",
    "OVERRIDE_DAY_TYPES",
    "SERVICE_HOURS_YAML",
    "DayHours",
    "DirectionHours",
    "HeadwayRule",
    "LineHours",
    "gtfs_day_type_for",
    "load_service_hours",
    "override_day_type_for",
]

SERVICE_HOURS_YAML = PROJECT_ROOT / "datasets" / "metro_service_hours.yaml"

OVERRIDE_DAY_TYPES: tuple[str, ...] = ("pzt_pers", "cuma", "cumartesi", "pazar")

DAY_LABELS: dict[str, str] = {
    "pzt_pers": "Pazartesi-Perşembe",
    "cuma": "Cuma",
    "cumartesi": "Cumartesi",
    "pazar": "Pazar",
}


def override_day_type_for(weekday: int) -> str:
    """Day type for a weekday index (0 = Monday).

    Friday is its own day type because the night metro runs the night that
    joins Friday to Saturday; grouping it with the rest of the week would
    lose exactly the difference this file exists to record.
    """
    if weekday <= 3:
        return "pzt_pers"
    return OVERRIDE_DAY_TYPES[weekday - 3]


def gtfs_day_type_for(day_type: str) -> str:
    """The feed's coarser day type, for the headway that still comes from it."""
    return "hafta_ici" if day_type in ("pzt_pers", "cuma") else day_type


@dataclass(frozen=True)
class DayHours:
    """First and last service on one day type, at one terminal.

    `last` is None when the service does not stop that night; `all_night`
    says that is the reason, as opposed to the hours being unknown.
    """

    day_type: str
    first: str | None
    last: str | None
    all_night: bool = False
    night_headway_minutes: int | None = None


@dataclass(frozen=True)
class DirectionHours:
    """Hours measured at one terminal, for trains leaving it."""

    terminal: str | None  # None: applies to both directions, each at its own end
    days: dict[str, DayHours]

    def for_day(self, day_type: str) -> DayHours | None:
        return self.days.get(day_type)


@dataclass(frozen=True)
class HeadwayRule:
    """A stated headway, optionally denser between two stations."""

    default_minutes: int
    extra_minutes: int | None = None
    between: tuple[str, str] | None = None

    def minutes_at(self, station_index: int | None, bounds: tuple[int, int] | None) -> int:
        """Headway at a station, given its position and the dense section.

        Both are resolved by the caller against the line's station order,
        because this module knows nothing about the network.
        """
        if (
            self.extra_minutes is not None
            and station_index is not None
            and bounds is not None
            and bounds[0] <= station_index <= bounds[1]
        ):
            return self.extra_minutes
        return self.default_minutes


@dataclass(frozen=True)
class LineHours:
    """One line's hand-verified hours."""

    line: str
    source_type: str
    retrieved_at: str
    note: str | None
    directions: tuple[DirectionHours, ...]
    headway: HeadwayRule | None = None
    aliases: tuple[str, ...] = ()

    @property
    def per_direction(self) -> bool:
        """Does this line state different hours per direction?"""
        return any(direction.terminal is not None for direction in self.directions)

    def for_terminal(self, terminal_key: str) -> DirectionHours | None:
        """The block measured at `terminal_key`, or the shared one."""
        from istanbul_agent.store.station_names import normalize

        for direction in self.directions:
            if direction.terminal is None:
                return direction
            if normalize(direction.terminal) == terminal_key:
                return direction
        return None

    def covers(self, day_type: str) -> bool:
        return any(direction.for_day(day_type) for direction in self.directions)


def _day_hours(day_type: str, entry: dict[str, Any]) -> DayHours:
    all_night = bool(entry.get("gece_boyunca"))
    last = entry.get("son")
    return DayHours(
        day_type=day_type,
        first=str(entry["ilk"]) if entry.get("ilk") else None,
        last=None if all_night or last is None else str(last),
        all_night=all_night,
        night_headway_minutes=(
            int(entry["gece_aralik_dk"]) if entry.get("gece_aralik_dk") else None
        ),
    )


def _days(block: dict[str, Any]) -> dict[str, DayHours]:
    return {
        day: _day_hours(day, value)
        for day, value in (block or {}).items()
        if day in DAY_LABELS and isinstance(value, dict)
    }


def _headway_rule(block: dict[str, Any] | None) -> HeadwayRule | None:
    if not block or not block.get("varsayilan_dk"):
        return None
    between = block.get("ek_sefer_arasi")
    return HeadwayRule(
        default_minutes=int(block["varsayilan_dk"]),
        extra_minutes=int(block["ek_sefer_dk"]) if block.get("ek_sefer_dk") else None,
        between=(str(between[0]), str(between[1])) if between and len(between) == 2 else None,
    )


def _line_hours(line: str, entry: dict[str, Any]) -> LineHours | None:
    directions: list[DirectionHours] = []
    for block in entry.get("yonler") or []:
        days = _days(block.get("gunler"))
        if days:
            directions.append(DirectionHours(terminal=str(block.get("terminal") or ""), days=days))
    shared = _days(entry.get("gunler"))
    if shared:
        directions.append(DirectionHours(terminal=None, days=shared))
    if not directions:
        return None

    aliases = tuple(str(alias).strip().upper() for alias in entry.get("aliases") or [])
    return LineHours(
        line=line,
        source_type=str(entry.get("source_type") or "").strip(),
        retrieved_at=str(entry.get("retrieved_at") or "").strip(),
        note=str(entry["not"]) if entry.get("not") else None,
        directions=tuple(directions),
        headway=_headway_rule(entry.get("aralik")),
        aliases=aliases,
    )


@lru_cache(maxsize=4)
def load_service_hours(path: Path = SERVICE_HOURS_YAML) -> dict[str, LineHours]:
    """Hand-verified hours per line code, aliases included.

    An empty mapping means no line is covered and every answer falls back to
    the feed.
    """
    if not path.exists():
        return {}
    try:
        import yaml
    except ModuleNotFoundError:  # pragma: no cover - the dependency is declared
        return {}

    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries: dict[str, LineHours] = {}
    for line, entry in (document.get("lines") or {}).items():
        if not isinstance(entry, dict):
            continue
        code = str(line).strip().upper()
        hours = _line_hours(code, entry)
        if hours is None:
            continue
        entries[code] = hours
        # A line runs under several codes in the feed (M2 and its M2A spur,
        # Marmaray and the Marmaray1 fragment). The hours are the line's, so
        # every code it answers under gets them.
        for alias in hours.aliases:
            entries.setdefault(alias, hours)
    return entries
