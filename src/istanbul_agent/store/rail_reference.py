"""Readers for the rail reference spreadsheets (datasets 312 and 313).

Both are hand-maintained XLSX files with no schema, and both carry defects
that have to be filtered rather than trusted:

- **312, inter-station lengths.** Three rows are Excel `#N/A`. Eleven M7 rows
  separate the station pair with an en-dash and a non-breaking space instead
  of a hyphen, and would be dropped by a naive split. Three values are
  implausible: M9 reports 929,542 m and 854,850 m between stations that
  neighbour each other, and M1A reports 49 m between Ulubatlı and Emniyet.
  The M9 pair looks like a factor-of-1000 slip, but guessing that would be
  inventing data, so all three are excluded and counted.
- **313, transfers.** Entries read `M1A Zeytinburnu` → `T1 Zeytinburnu`: a
  line code glued to a station name. A quarter of the targets are Metrobüs,
  ferry or the nostalgic tram rather than rail.

Everything returned here carries its provenance, and anything dropped is
reported in `excluded`, so the ingest can log what it refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.store.station_names import normalize

__all__ = [
    "PLAUSIBLE_MAX_M",
    "PLAUSIBLE_MIN_M",
    "RailReference",
    "SegmentLength",
    "Transfer",
    "load_rail_reference",
]

REFERENCE_DIR = PROJECT_ROOT / "data" / "rail_reference"

# No Istanbul rail station pair is closer than ~100 m or further than ~12 km;
# the M4 airport spur is the longest real gap at about 11 km.
PLAUSIBLE_MIN_M = 100.0
PLAUSIBLE_MAX_M = 12_000.0

# Hyphen, en-dash, em-dash and minus sign all appear as the pair separator.
_SEPARATOR_RE = re.compile(r"\s*[-‐‑‒–—−]\s*")

# Line codes that denote rail in the transfer table's target column.
_RAIL_PREFIX_RE = re.compile(r"^(M\d+[AB]?|T\d+|F\d+|TF\d+|Marmaray)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SegmentLength:
    """Track length between two consecutive stations, in metres."""

    line: str
    from_station: str
    to_station: str
    metres: float

    @property
    def key(self) -> tuple[str, str]:
        """Direction-insensitive key on normalised station names."""
        a, b = normalize(self.from_station), normalize(self.to_station)
        return (a, b) if a <= b else (b, a)


@dataclass(frozen=True)
class Transfer:
    """A place where a passenger can change between two services."""

    line_family: str
    from_line: str
    from_station: str
    to_line: str
    to_station: str
    to_is_rail: bool


@dataclass
class RailReference:
    segments: list[SegmentLength] = field(default_factory=list)
    transfers: list[Transfer] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)

    def segment_metres(self, left: str, right: str, line: str | None = None) -> float | None:
        """Track length between two stations, either direction."""
        a, b = normalize(left), normalize(right)
        wanted = (a, b) if a <= b else (b, a)
        matches = [s for s in self.segments if s.key == wanted]
        if line is not None:
            family = _line_family(line)
            same_line = [s for s in matches if _line_family(s.line) == family]
            if same_line:
                matches = same_line
        return matches[0].metres if matches else None

    def rail_transfers(self) -> list[Transfer]:
        return [t for t in self.transfers if t.to_is_rail]


def _line_family(line: str) -> str:
    """`M1A` and `M1B` both belong to family `M1`.

    The length table files segments under the family code while GTFS and the
    live API use the branch code, so comparisons go through this.
    """
    match = re.match(r"^([A-Za-z]+\d+)", line.strip())
    return match.group(1).upper() if match else line.strip().upper()


def _clean(text: object) -> str:
    """Strip, and fold the non-breaking spaces this file is littered with."""
    return str(text).replace("\xa0", " ").strip() if text is not None else ""


def _split_pair(segment: str) -> tuple[str, str] | None:
    parts = [p for p in (_clean(p) for p in _SEPARATOR_RE.split(segment)) if p]
    if len(parts) < 2:
        return None
    if len(parts) == 2:
        return parts[0], parts[1]
    # More than one separator: a station name contains one itself. Taking the
    # ends is the only reading that does not require knowing which.
    return parts[0], parts[-1]


def _sheet_rows(path: Path) -> list[tuple]:
    workbook = openpyxl.load_workbook(path, data_only=True)
    sheet = workbook.worksheets[0]
    return [row for row in sheet.iter_rows(values_only=True) if any(row)]


def _load_segments(path: Path, reference: RailReference) -> None:
    for raw in _sheet_rows(path)[1:]:
        line, segment, length = (tuple(raw) + (None,) * 3)[:3]
        line_text, segment_text = _clean(line), _clean(segment)

        if not line_text or not segment_text or length is None:
            reference.excluded.append({"source": "312", "row": list(raw), "reason": "boş alan"})
            continue
        try:
            metres = float(_clean(length).replace(",", "."))
        except ValueError:
            reference.excluded.append(
                {"source": "312", "row": list(raw), "reason": f"sayı değil: {_clean(length)!r}"}
            )
            continue

        pair = _split_pair(segment_text)
        if pair is None:
            reference.excluded.append(
                {"source": "312", "row": list(raw), "reason": "istasyon çifti ayrıştırılamadı"}
            )
            continue
        if not PLAUSIBLE_MIN_M <= metres <= PLAUSIBLE_MAX_M:
            reference.excluded.append(
                {
                    "source": "312",
                    "row": list(raw),
                    "reason": f"makul aralık dışı ({metres:,.0f} m)",
                }
            )
            continue

        reference.segments.append(
            SegmentLength(line=line_text, from_station=pair[0], to_station=pair[1], metres=metres)
        )


def _split_line_and_station(text: str) -> tuple[str, str]:
    """`M1A Zeytinburnu` -> (`M1A`, `Zeytinburnu`); unprefixed -> ("", text)."""
    cleaned = _clean(text)
    match = _RAIL_PREFIX_RE.match(cleaned)
    if match:
        code = match.group(1)
        return code.upper(), cleaned[len(code) :].strip()
    # "M1Yenikapı" -- the code runs into the name with no space.
    tight = re.match(r"^(M\d+[AB]?|T\d+|TF\d+|F\d+)(\S.*)$", cleaned)
    if tight:
        return tight.group(1).upper(), tight.group(2).strip()
    return "", cleaned


def _load_transfers(path: Path, reference: RailReference) -> None:
    for raw in _sheet_rows(path)[1:]:
        family, source, target = (tuple(raw) + (None,) * 3)[:3]
        source_text, target_text = _clean(source), _clean(target)
        if not source_text or not target_text:
            reference.excluded.append({"source": "313", "row": list(raw), "reason": "boş alan"})
            continue

        from_line, from_station = _split_line_and_station(source_text)
        to_line, to_station = _split_line_and_station(target_text)
        reference.transfers.append(
            Transfer(
                line_family=_clean(family),
                from_line=from_line,
                from_station=from_station,
                to_line=to_line,
                to_station=to_station,
                to_is_rail=bool(to_line),
            )
        )


def load_rail_reference(directory: Path | None = None) -> RailReference:
    """Read datasets 312 and 313 from `directory` (default data/rail_reference)."""
    base = directory or REFERENCE_DIR
    reference = RailReference()

    lengths = base / "hat_uzunluklari.xlsx"
    if lengths.exists():
        _load_segments(lengths, reference)

    transfers = base / "aktarma_bilgileri.xlsx"
    if transfers.exists():
        _load_transfers(transfers, reference)

    return reference
