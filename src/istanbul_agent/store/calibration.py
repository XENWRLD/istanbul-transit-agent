"""Per-line calibration against independently checked travel times.

Our pipeline reproduces the 2020 feed's own end-to-end trip times exactly, so
the gap against external figures is the feed diverging from how the lines run
today -- and the direction differs per line: M2 is about 9% optimistic while
T1 is about 8% pessimistic. A single global correction would fix one and
worsen the other, so the correction is a per-line ratio.

A factor is only used when all four gates pass:

1. **A source link.** `source_url` must be a real URL. A figure nobody can
   check is not evidence, so a row whose link is still a placeholder leaves
   its line uncalibrated -- the code ships inactive and turns itself on when
   the link arrives.
2. **Deviation above MIN_DEVIATION_PCT.** M4's 0.9% is noise; scaling a line
   by it would be fitting to rounding.
3. **A long enough journey.** Below MIN_STOPS / MIN_MINUTES the external
   figure's own rounding dominates: F1's 2.0 vs 2.5 minutes is a 30-second
   difference that would scale the whole funicular by 25%.
4. **Our figure must be `gtfs`.** Calibrating an estimate against an external
   number would fold the estimate's error into the factor and then apply it
   back to the estimate.

Thresholds and the reasoning behind them: documents/decisions/0004.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from istanbul_agent.config import PROJECT_ROOT

__all__ = [
    "MAX_FACTOR",
    "MIN_DEVIATION_PCT",
    "MIN_FACTOR",
    "MIN_MINUTES",
    "MIN_STOPS",
    "Calibration",
    "load_calibrations",
]

EXTERNAL_CSV = PROJECT_ROOT / "datasets" / "external_travel_times.csv"

# Gate 2: below this the difference is rounding, not drift.
MIN_DEVIATION_PCT = 5.0

# Gate 3: a factor from a shorter journey is a factor from rounding.
MIN_STOPS = 5
MIN_MINUTES = 10.0

# Beyond these the two numbers are probably describing different journeys.
MIN_FACTOR = 0.67
MAX_FACTOR = 1.50

# A `source_url` that is still a placeholder rather than a link.
_PLACEHOLDER_MARKERS = ("<", ">", "link", "tbd", "todo")


@dataclass(frozen=True)
class Calibration:
    """One line's accepted factor, with what justifies it."""

    line: str
    factor: float
    from_station: str
    to_station: str
    ours_minutes: float
    external_minutes: float
    source_url: str

    @property
    def deviation_pct(self) -> float:
        return (self.external_minutes - self.ours_minutes) / self.ours_minutes * 100


def has_real_source(source_url: str) -> bool:
    """Is this a link someone can follow, or still a placeholder?"""
    value = (source_url or "").strip().lower()
    if not value:
        return False
    if any(marker in value for marker in _PLACEHOLDER_MARKERS):
        return False
    return value.startswith(("http://", "https://"))


def evaluate_row(
    row: dict[str, str],
    ours_minutes: float | None,
    stop_count: int,
    method: str,
) -> tuple[Calibration | None, str]:
    """Decide whether one external row may calibrate its line.

    Returns (calibration or None, reason). The reason is always populated so
    a rejection can be explained rather than just happening.
    """
    line = (row.get("line") or "").strip()
    notes = row.get("notes") or ""

    if "DOĞRULANMADI" in notes:
        return None, "dış kaynak doğrulanmadı"
    if not has_real_source(row.get("source_url", "")):
        return None, f"kaynak linki yok ({row.get('source_url', '')!r})"
    if ours_minutes is None:
        return None, "bizim sonucumuz yok"
    if method != "gtfs":
        return None, f"bizim sonucumuz {method}; tahmini kalibre etmek döngüsel olur"
    if stop_count < MIN_STOPS:
        return None, f"{stop_count} durak < {MIN_STOPS}"
    if ours_minutes < MIN_MINUTES:
        return None, f"{ours_minutes:.1f} dk < {MIN_MINUTES}"

    try:
        external_minutes = float(row["minutes"])
    except (KeyError, TypeError, ValueError):
        return None, "dış süre okunamadı"
    if ours_minutes <= 0:
        return None, "bizim süremiz sıfır"

    factor = external_minutes / ours_minutes
    deviation = abs(external_minutes - ours_minutes) / ours_minutes * 100
    if deviation < MIN_DEVIATION_PCT:
        return None, f"sapma %{deviation:.1f} < %{MIN_DEVIATION_PCT:.0f} (gürültü)"
    if not MIN_FACTOR <= factor <= MAX_FACTOR:
        return None, f"oran {factor:.3f} makul aralık dışı"

    return (
        Calibration(
            line=line,
            factor=factor,
            from_station=row["from"],
            to_station=row["to"],
            ours_minutes=ours_minutes,
            external_minutes=external_minutes,
            source_url=row["source_url"].strip(),
        ),
        "kabul",
    )


def read_external(path: Path | None = None) -> list[dict[str, str]]:
    target = path or EXTERNAL_CSV
    if not target.exists():
        return []
    with target.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@lru_cache(maxsize=1)
def load_calibrations() -> dict[str, Calibration]:
    """Accepted factors per line, measured against the live store.

    Returns an empty mapping while no row passes the gates, which is the
    state until the source links are filled in.
    """
    from istanbul_agent.store.db import database_path

    rows = read_external()
    if not rows or not database_path().exists():
        return {}

    # Imported here so this module stays importable without a built store.
    from istanbul_agent.store.rail_queries import get_rail_travel_time, load_network

    network = load_network()
    accepted: dict[str, Calibration] = {}
    for row in rows:
        # Uncalibrated on purpose: the factor is the ratio between the
        # external figure and our *raw* reading, and asking for a calibrated
        # one here would make the two functions call each other forever.
        result = get_rail_travel_time(row["from"], row["to"], network=network, calibrate=False)
        calibration, _ = evaluate_row(
            row,
            ours_minutes=result.seconds / 60 if result.seconds is not None else None,
            stop_count=result.stop_count,
            method=result.method,
        )
        if calibration is not None:
            accepted[calibration.line] = calibration
    return accepted


def factor_for(line: str, calibrations: dict[str, Calibration] | None = None) -> float:
    """The multiplier for `line`; 1.0 when it has no accepted factor."""
    table = calibrations if calibrations is not None else load_calibrations()
    entry = table.get(line.strip())
    return entry.factor if entry else 1.0
