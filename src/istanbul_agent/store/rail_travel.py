"""Station-to-station rail travel times.

The live Metro endpoint for this (`GetStationBetweenTime`) is broken
server-side, so durations come from the 2020 GTFS feed, with an estimate for
the stations that feed never had (decision 0001, K2).

Three ways to answer, in descending order of trust, and the result always
says which was used:

1. **`gtfs`** -- both stations are in the feed on one line, and the feed's own
   stop_times give the duration directly. Median across trips, so one
   delayed run cannot move it.
2. **`estimated`** -- at least one station is missing from the feed. Distance
   comes from dataset 312's track length where it has the segment, else the
   straight line between coordinates; speed is the mode median measured from
   the feed itself (`scripts/analyze_rail_speeds.py`). Measurement showed
   chord and track agree within noise at this scale, so no route-inflation
   factor is applied.
3. **`transfer`** -- the two stations are on different lines; each leg is
   solved as above and a fixed interchange penalty is added.

Everything derived from the feed carries `is_stale: true`: its metro data is
dated 2020-07-09 and the portal will not refresh it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Literal

from istanbul_agent.store.geo import haversine_m
from istanbul_agent.store.rail_reference import _line_family

__all__ = [
    "DWELL_S",
    "GTFS_METRO_DATA_AS_OF",
    "LINE_MODEL",
    "MODE_MODEL",
    "TRANSFER_PENALTY_S",
    "RailNetwork",
    "RailStation",
    "SegmentModel",
    "TravelLeg",
    "TravelTimeResult",
    "model_for",
]

Method = Literal["gtfs", "estimated", "transfer", "unavailable"]

# The feed's metro data, per the portal's own note. Reported as `data_as_of`.
GTFS_METRO_DATA_AS_OF = "2020-07-09"


@dataclass(frozen=True)
class SegmentModel:
    """`duration = overhead + distance / cruise` for one hop.

    A single average speed is not enough: a train accelerates away from one
    station and brakes into the next, so duration is not proportional to
    distance. `overhead` absorbs those two manoeuvres. Fitting this instead of
    dividing by a median speed cut the mean absolute error from 13.7 s to
    11.0 s and removed a systematic -11% bias
    (`scripts/fit_travel_model.py`).
    """

    overhead_s: float
    cruise_ms: float
    source: str

    def seconds(self, metres: float) -> int:
        return round(self.overhead_s + metres / self.cruise_ms)


# Fitted per mode by least squares over the feed's own segments.
MODE_MODEL: dict[str, SegmentModel] = {
    "metro": SegmentModel(33.9, 16.45, "mod uydurması, n=121"),
    "tramvay": SegmentModel(27.5, 5.79, "mod uydurması, n=51"),
    # Too few segments to fit a line; the median speed is all these support.
    "funikuler": SegmentModel(0.0, 4.26, "ortanca hız, n=3"),
    "teleferik": SegmentModel(0.0, 2.16, "ortanca hız, n=2"),
    "tren": SegmentModel(33.9, 16.45, "metro uydurması (bu beslemede tren yok)"),
}

# Per-line fits that passed the plausibility guards in fit_travel_model.py.
# M1A and M3 are deliberately absent: their fits returned a 46 m/s cruise
# speed and a negative overhead respectively, which describe their own points
# and nothing else. Those lines use their mode's fit.
LINE_MODEL: dict[str, SegmentModel] = {
    "M1B": SegmentModel(19.3, 13.40, "hat uydurması, n=12"),
    "M2": SegmentModel(21.2, 13.37, "hat uydurması, n=14"),
    "M4": SegmentModel(23.3, 13.35, "hat uydurması, n=18"),
    "M5": SegmentModel(9.2, 12.38, "hat uydurması, n=15"),
    "M7": SegmentModel(16.1, 14.60, "hat uydurması, n=15"),
    "M8": SegmentModel(9.1, 13.43, "hat uydurması, n=12"),
    "T1": SegmentModel(29.8, 6.09, "hat uydurması, n=30"),
    "T4": SegmentModel(25.6, 5.52, "hat uydurması, n=21"),
}


def model_for(line: str, mode: str) -> SegmentModel:
    """The best available model: the line's own fit, else its mode's."""
    return LINE_MODEL.get(line.strip().upper()) or MODE_MODEL.get(mode) or MODE_MODEL["metro"]


# Median dwell at a station, measured over 162,871 stop_times rows.
DWELL_S = 15

# Fixed interchange penalty, 6 minutes. See
# documents/decisions/0004-aktarma-suresi.md: 210 s is the measured median
# average wait across lines (half of each line's headway), and 150 s is an
# assumed platform-to-platform walk.
TRANSFER_PENALTY_S = 360


@dataclass(frozen=True)
class RailStation:
    """A station as the tools see it, whichever source it came from."""

    name: str
    line: str
    mode: str
    stop_id: str | None = None  # GTFS stop id, when the feed has it
    lat: float | None = None
    lon: float | None = None
    order: int | None = None  # position along the line, when known
    in_gtfs: bool = False
    # True when the live API lists this station mid-line but the feed has its
    # two neighbours as adjacent -- the signature of a branch, not of a new
    # intermediate stop. M2's Seyrantepe is the case: it sits on the M2A spur,
    # so a Yenikapı-Hacıosman journey must not be routed through it.
    is_spur: bool = False

    @property
    def has_position(self) -> bool:
        return self.lat is not None and self.lon is not None


@dataclass(frozen=True)
class TravelLeg:
    """One same-line hop of a journey."""

    line: str
    from_station: str
    to_station: str
    seconds: int
    method: Method
    stop_count: int
    detail: str


@dataclass
class TravelTimeResult:
    """What `get_rail_travel_time` returns."""

    from_station: str
    to_station: str
    seconds: int | None
    method: Method
    estimated: bool
    is_stale: bool
    source: str
    data_as_of: str
    legs: list[TravelLeg] = field(default_factory=list)
    transfer_station: str | None = None
    transfer_penalty_s: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def minutes(self) -> int | None:
        return round(self.seconds / 60) if self.seconds is not None else None


class RailNetwork:
    """The station graph the travel-time tool queries.

    Built by the ingest from three sources: GTFS (durations), dataset 312
    (segment lengths for stations GTFS lacks), dataset 313 (interchanges).
    Kept source-agnostic so tests can construct a small one by hand.
    """

    def __init__(
        self,
        *,
        stations: list[RailStation],
        segment_durations: dict[tuple[str, str], int],
        segment_lengths: dict[tuple[str, str], float],
        transfers: list[tuple[str, str, str]],
        unreliable_lines: frozenset[str] = frozenset(),
    ) -> None:
        """
        Args:
            stations: every known station, one entry per line it serves.
            segment_durations: seconds between adjacent stations, keyed by
                sorted station-key pair, from GTFS stop_times.
            segment_lengths: metres between adjacent stations, same keying,
                from dataset 312.
            transfers: `(station_key, from_line, to_line)` interchanges.
            unreliable_lines: lines whose feed data must not be used
                (decision 0003 excludes T3).
        """
        self.stations = stations
        self.segment_durations = segment_durations
        self.segment_lengths = segment_lengths
        self.transfers = transfers
        self.unreliable_lines = unreliable_lines

        self._by_line: dict[str, list[RailStation]] = {}
        for station in stations:
            self._by_line.setdefault(station.line, []).append(station)
        for line_stations in self._by_line.values():
            line_stations.sort(key=lambda s: s.order if s.order is not None else 1 << 30)

    # ------------------------------------------------------------- lookup

    @staticmethod
    def key(name: str) -> str:
        from istanbul_agent.store.station_names import normalize

        return normalize(name)

    def find(self, name: str, line: str | None = None) -> list[RailStation]:
        """Every station entry matching `name`, optionally on one line."""
        from istanbul_agent.store.station_names import collapsed, normalize

        wanted, wanted_loose = normalize(name), collapsed(name)
        hits = [
            station
            for station in self.stations
            if normalize(station.name) == wanted or collapsed(station.name) == wanted_loose
        ]
        if line is not None:
            family = _line_family(line)
            scoped = [s for s in hits if _line_family(s.line) == family]
            return scoped or []
        return hits

    def lines_at(self, name: str) -> set[str]:
        return {station.line for station in self.find(name)}

    def stations_on(self, line: str) -> list[RailStation]:
        return self._by_line.get(line, [])

    # ------------------------------------------------------- same-line leg

    def _pair_key(self, left: str, right: str) -> tuple[str, str]:
        a, b = self.key(left), self.key(right)
        return (a, b) if a <= b else (b, a)

    def _segment_seconds(self, left: RailStation, right: RailStation) -> tuple[int | None, str]:
        """Duration for one adjacent hop, and how it was obtained."""
        observed = self.segment_durations.get(self._pair_key(left.name, right.name))
        if observed is not None:
            return observed, "gtfs"

        metres = self.segment_lengths.get(self._pair_key(left.name, right.name))
        detail = "312"
        if metres is None and left.has_position and right.has_position:
            metres = haversine_m(left.lat, left.lon, right.lat, right.lon)  # type: ignore[arg-type]
            detail = "koordinat"
        if metres is None:
            return None, "yok"

        return model_for(left.line, left.mode).seconds(metres), detail

    def same_line_leg(self, origin: RailStation, destination: RailStation) -> TravelLeg | None:
        """Sum the hops between two stations on one line."""
        if origin.line != destination.line:
            return None
        if origin.line in self.unreliable_lines:
            return None

        line_stations = self.stations_on(origin.line)
        try:
            start = next(
                i for i, s in enumerate(line_stations) if self.key(s.name) == self.key(origin.name)
            )
            end = next(
                i
                for i, s in enumerate(line_stations)
                if self.key(s.name) == self.key(destination.name)
            )
        except StopIteration:
            return None
        if start == end:
            return TravelLeg(
                line=origin.line,
                from_station=origin.name,
                to_station=destination.name,
                seconds=0,
                method="gtfs",
                stop_count=0,
                detail="aynı istasyon",
            )

        low, high = min(start, end), max(start, end)
        path = line_stations[low : high + 1]
        # Skip branch stations that are merely passed over, but keep one that
        # is itself an endpoint of this journey.
        path = [
            station
            for index, station in enumerate(path)
            if not station.is_spur or index in (0, len(path) - 1)
        ]
        if start > end:
            path = path[::-1]

        total = 0
        details: list[str] = []
        last_index = len(path) - 2
        for index, (left, right) in enumerate(pairwise(path)):
            seconds, how = self._segment_seconds(left, right)
            if seconds is None:
                return None
            total += seconds
            details.append(how)
            # Dwell at every intermediate station, but not on arrival.
            if index < last_index:
                total += DWELL_S

        method: Method = "gtfs" if all(how == "gtfs" for how in details) else "estimated"
        counts = {how: details.count(how) for how in sorted(set(details))}
        return TravelLeg(
            line=origin.line,
            from_station=origin.name,
            to_station=destination.name,
            seconds=total,
            method=method,
            stop_count=len(path) - 1,
            detail=", ".join(f"{how}×{count}" for how, count in counts.items()),
        )

    # ----------------------------------------------------------- transfers

    def transfer_options(
        self, from_line: str, to_line: str
    ) -> list[tuple[RailStation, RailStation]]:
        """Where a passenger can change from one line to the other.

        Returns `(station_on_from_line, station_on_to_line)` pairs. They are
        two objects rather than one because the two lines may call the
        interchange different things: M2 has `Şişli-Mecidiyeköy` where M7 has
        `Mecidiyeköy`. Dataset 313 records each direction as its own row, so
        the pairing comes from matching those rows up and confirming with
        coordinates -- never from matching the names, which do not match.
        """
        from_family, to_family = _line_family(from_line), _line_family(to_line)

        def named_on(line: str, rows_family: str, other_family: str) -> list[RailStation]:
            wanted = {
                station
                for station, left, right in self.transfers
                if _line_family(left) == rows_family and _line_family(right) == other_family
            }
            return [s for s in self.stations_on(line) if self.key(s.name) in wanted]

        origins = named_on(from_line, from_family, to_family)
        destinations = named_on(to_line, to_family, from_family)

        pairs: list[tuple[RailStation, RailStation]] = []

        # The common case: both lines use the same name for the interchange.
        for station in self.stations_on(from_line):
            twin = [
                s for s in self.stations_on(to_line) if self.key(s.name) == self.key(station.name)
            ]
            if twin:
                pairs.append((station, twin[0]))

        # Otherwise pair 313's two sides by proximity: an interchange is one
        # place, so the two records sit within a few hundred metres.
        for origin in origins:
            if any(left is origin for left, _ in pairs):
                continue
            best: RailStation | None = None
            best_distance = None
            for destination in destinations:
                if not (origin.has_position and destination.has_position):
                    continue
                distance = haversine_m(
                    origin.lat,
                    origin.lon,
                    destination.lat,
                    destination.lon,  # type: ignore[arg-type]
                )
                if best_distance is None or distance < best_distance:
                    best, best_distance = destination, distance
            if best is not None and best_distance is not None and best_distance <= 600:
                pairs.append((origin, best))
            elif len(destinations) == 1 and not destinations[0].has_position:
                # No coordinates to confirm with, but 313 names exactly one
                # station on each side, so the pairing is unambiguous.
                pairs.append((origin, destinations[0]))

        return pairs


def median_or_none(values: list[int]) -> int | None:
    return int(statistics.median(values)) if values else None
