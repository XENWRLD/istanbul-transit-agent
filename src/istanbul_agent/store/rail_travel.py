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
4. **`gtfs_calibrated`** -- a `gtfs` duration scaled by the line's measured
   factor, where an independently checked travel time showed the 2020 feed
   drifting from today's operation by more than 5%. See store/calibration.py.

Everything derived from the feed carries `is_stale: true`: its metro data is
dated 2020-07-09 and the portal will not refresh it.
"""

from __future__ import annotations

import heapq
import statistics
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Literal

from istanbul_agent.store.geo import haversine_m
from istanbul_agent.store.rail_reference import _line_family

__all__ = [
    "DWELL_S",
    "ESTIMATE_ERROR_BY_STOPS",
    "GTFS_METRO_DATA_AS_OF",
    "INTERCHANGE_RADIUS_M",
    "LINE_MODEL",
    "LINE_WARNINGS",
    "MARMARAY_DATA_AS_OF",
    "MODE_MODEL",
    "SANCTIONED_INTERCHANGE_RADIUS_M",
    "TRANSFER_PENALTY_S",
    "TRANSFER_WAIT_S",
    "TRANSFER_WALK_S",
    "WALK_SPEED_MS",
    "ErrorMargin",
    "RailNetwork",
    "RailStation",
    "SegmentModel",
    "TravelLeg",
    "TravelTimeResult",
    "estimate_error",
    "model_for",
    "transfer_penalty_seconds",
    "walk_seconds",
]

Method = Literal["gtfs", "gtfs_calibrated", "estimated", "transfer", "unavailable"]

# The feed's metro data, per the portal's own note. Reported as `data_as_of`.
GTFS_METRO_DATA_AS_OF = "2020-07-09"

# Marmaray sits in the same feed under TCDD and carries its own date.
MARMARAY_DATA_AS_OF = "2019-10-16"


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


# Dwell at a station. The feed records 15 s on 95.7% of its stop_times rows
# and 0 s at terminals, so this is the feed's own figure rather than ours; our
# end-to-end sums reproduce the feed's trip times exactly because of it.
DWELL_S = 15

# Held-out error of the estimate, by how many stops the journey covers
# (scripts/validate_travel_estimates.py). Reported so a caller can say
# "about 50 minutes, give or take 2" instead of implying false precision.
# Keys are the lower bound of each band.
ESTIMATE_ERROR_BY_STOPS: tuple[tuple[int, float, float], ...] = (
    # (minimum stops, mean |error| %, max |error| %)
    (11, 1.4, 5.3),
    (7, 2.2, 8.1),
    (4, 3.3, 14.2),
    (1, 10.9, 29.1),
)

# Lines whose estimate is known to be biased beyond the table above. M6 runs
# 662 m hops in 125 s -- a third of the metro median -- and carries only three
# segments, too few to fit its own model, so it falls back to the mode fit and
# comes out about 29% low. Fitting three points is the mistake that M1A's
# 46 m/s cruise speed already demonstrated.
LINE_WARNINGS: dict[str, str] = {
    "M6": (
        "M6 segmentleri metro ortalamasının üçte biri hızında; bu hattın "
        "tahmini yaklaşık %29 düşük çıkıyor, gerçek süre daha uzun olabilir."
    ),
}


def estimate_error(stop_count: int) -> tuple[float, float]:
    """Mean and maximum held-out error, as percentages, for a journey length."""
    for minimum, mean_error, max_error in ESTIMATE_ERROR_BY_STOPS:
        if stop_count >= minimum:
            return mean_error, max_error
    return ESTIMATE_ERROR_BY_STOPS[-1][1], ESTIMATE_ERROR_BY_STOPS[-1][2]


# An interchange costs a wait plus a walk. The wait, 210 s, is the measured
# median average wait across lines (half of each line's headway). The walk is
# 150 s for a normal platform-to-platform change -- see decision 0004.
TRANSFER_WAIT_S = 210
TRANSFER_WALK_S = 150
TRANSFER_PENALTY_S = TRANSFER_WAIT_S + TRANSFER_WALK_S

# Two stations within this far of each other are one interchange. Pairs that
# dataset 313 names as an interchange are allowed out to the wider radius:
# 313 is the operator saying the change exists, which outweighs our distance
# heuristic, and F4 -- a two-station funicular whose only link is M6, 463 m
# away -- is otherwise cut off the network entirely.
INTERCHANGE_RADIUS_M = 400.0
SANCTIONED_INTERCHANGE_RADIUS_M = 1000.0

# Above the normal radius, 150 s is no longer a platform change but a walk
# along a street, so it is derived from the distance instead. 1.2 m/s is a
# conventional pedestrian speed.
WALK_SPEED_MS = 1.2


def walk_seconds(distance_m: float | None) -> int:
    """Walking time for an interchange, from its distance.

    Inside `INTERCHANGE_RADIUS_M` the change is within a station complex and
    the flat `TRANSFER_WALK_S` stands; beyond it the distance decides. The
    two rules do not meet continuously -- 400 m at 1.2 m/s is 333 s, so the
    first metre past the radius more than doubles the walk. That is
    deliberate: a pair only gets past the radius by being named in 313, and
    such a pair is a street-level change, not a corridor.
    """
    if distance_m is None or distance_m <= INTERCHANGE_RADIUS_M:
        return TRANSFER_WALK_S
    return round(distance_m / WALK_SPEED_MS)


def transfer_penalty_seconds(distance_m: float | None) -> int:
    """Total cost of one line change: average wait plus the walk."""
    return TRANSFER_WAIT_S + walk_seconds(distance_m)


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


@dataclass(frozen=True)
class ErrorMargin:
    """How far off an estimate is likely to be.

    Taken from the held-out validation rather than invented, so a caller can
    phrase the answer honestly: "about 50 minutes (±2)".
    """

    mean_pct: float
    max_pct: float
    mean_seconds: int
    max_seconds: int

    @property
    def mean_minutes(self) -> int:
        return max(1, round(self.mean_seconds / 60))

    @property
    def max_minutes(self) -> int:
        return max(1, round(self.max_seconds / 60))

    def phrase(self) -> str:
        return f"±{self.mean_minutes} dk (en kötü ±{self.max_minutes} dk)"


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
    error_margin: ErrorMargin | None = None
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def minutes(self) -> int | None:
        return round(self.seconds / 60) if self.seconds is not None else None

    @property
    def stop_count(self) -> int:
        return sum(leg.stop_count for leg in self.legs)


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
        segment_durations: dict[tuple[str, str, str], int],
        segment_lengths: dict[tuple[str, str, str], float],
        transfers: list[tuple[str, str, str]],
        unreliable_lines: frozenset[str] = frozenset(),
        ring_lines: frozenset[str] = frozenset(),
        line_dates: dict[str, str] | None = None,
        interchanges: list[tuple[str, str, str, str]] | None = None,
    ) -> None:
        """
        Args:
            stations: every known station, one entry per line it serves.
            segment_durations: seconds between adjacent stations, keyed by
                `(line, station_a, station_b)` with the pair sorted, from GTFS
                stop_times. The line has to be part of the key: Marmaray and
                M4 both run Bostancı to Küçükyalı on separate tracks, and
                keying on names alone let one overwrite the other.
            segment_lengths: metres between adjacent stations, same keying,
                from dataset 312.
            transfers: `(station_key, from_line, to_line)` interchanges.
            unreliable_lines: lines whose **coordinates** cannot be trusted,
                so a distance-based estimate on them is refused. Their
                recorded durations are still used; see decision 0003.
            ring_lines: lines that close on themselves, where the shorter of
                the two ways round is the answer.
            line_dates: `data_as_of` per line. Marmaray's feed data is a year
                older than the metro's, so a journey using it must report the
                older of the dates rather than the metro's.
            interchanges: `(from_line, from_station_key, to_line,
                to_station_key[, distance_m])` walkable pairs, built from
                coordinates by the ingest. The distance, when given, sets
                that change's walking time. When omitted, stations that two lines share by exact
                key are used -- enough for a hand-built test network, never
                for the real one.
        """
        self.stations = stations
        self.segment_durations = segment_durations
        self.segment_lengths = segment_lengths
        self.transfers = transfers
        self.unreliable_lines = unreliable_lines
        self.ring_lines = ring_lines
        self.line_dates = line_dates or {}

        # The per-line index has to exist before interchanges can be resolved
        # against it.
        self._by_line: dict[str, list[RailStation]] = {}
        for station in stations:
            self._by_line.setdefault(station.line, []).append(station)
        for line_stations in self._by_line.values():
            line_stations.sort(key=lambda s: s.order if s.order is not None else 1 << 30)

        self._interchanges: dict[tuple[str, str], list[tuple[RailStation, RailStation]]] = {}
        # Walking distance per pair, so a change beyond the normal radius
        # costs what it actually takes to walk rather than a flat 150 s.
        self._interchange_distances: dict[tuple[str, str, str, str], float] = {}
        for record in (
            interchanges if interchanges is not None else self._shared_station_interchanges()
        ):
            from_line, from_key, to_line, to_key = record[:4]
            distance_m = record[4] if len(record) > 4 else None
            origin = self._station(from_line, from_key)
            destination = self._station(to_line, to_key)
            if origin is None or destination is None:
                continue
            self._interchanges.setdefault((from_line, to_line), []).append((origin, destination))
            if distance_m is not None:
                self._interchange_distances[(from_line, from_key, to_line, to_key)] = float(
                    distance_m
                )

    # ------------------------------------------------------------- lookup

    def _station(self, line: str, station_key: str) -> RailStation | None:
        for station in self._by_line.get(line, []):
            if self.key(station.name) == station_key:
                return station
        return None

    def _shared_station_interchanges(self) -> list[tuple[str, str, str, str]]:
        """Fallback for a network built without an interchange table.

        Pairs stations that two lines share by exact key. Only safe because a
        hand-built test network has no same-name-different-place traps; the
        real network always passes its measured table.
        """
        pairs: list[tuple[str, str, str, str]] = []
        for from_line, line_stations in self._by_line.items():
            for station in line_stations:
                station_key = self.key(station.name)
                for to_line, other_stations in self._by_line.items():
                    if to_line == from_line:
                        continue
                    if any(self.key(s.name) == station_key for s in other_stations):
                        pairs.append((from_line, station_key, to_line, station_key))
        return pairs

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

    def _pair_key(self, line: str, left: str, right: str) -> tuple[str, str, str]:
        a, b = self.key(left), self.key(right)
        return (line, a, b) if a <= b else (line, b, a)

    def _segment_seconds(self, left: RailStation, right: RailStation) -> tuple[int | None, str]:
        """Duration for one adjacent hop, and how it was obtained."""
        observed = self.segment_durations.get(self._pair_key(left.line, left.name, right.name))
        if observed is not None:
            return observed, "gtfs"

        metres = self.segment_lengths.get(self._pair_key(left.line, left.name, right.name))
        detail = "312"
        if metres is None:
            # A line whose coordinates are untrustworthy must not have a
            # distance derived from them: on T3 the error (164-763 m) exceeds
            # the 239 m station spacing, so the result would be arbitrary.
            if left.line in self.unreliable_lines:
                return None, "koordinat güvenilmez"
            if left.has_position and right.has_position:
                metres = haversine_m(left.lat, left.lon, right.lat, right.lon)  # type: ignore[arg-type]
                detail = "koordinat"
        if metres is None:
            return None, "yok"

        return model_for(left.line, left.mode).seconds(metres), detail

    def _walk(self, line: str, path: list[RailStation]) -> TravelLeg | None:
        """Sum one ordered run of stations into a leg."""
        if len(path) < 2:
            return None
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
            line=line,
            from_station=path[0].name,
            to_station=path[-1].name,
            seconds=total,
            method=method,
            stop_count=len(path) - 1,
            detail=", ".join(f"{how}×{count}" for how, count in counts.items()),
        )

    def same_line_leg(self, origin: RailStation, destination: RailStation) -> TravelLeg | None:
        """Shortest run between two stations on one line.

        On a ring line both ways round are tried and the quicker wins; on a
        linear line there is only one run.
        """
        if origin.line != destination.line:
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

        def trim_spurs(path: list[RailStation]) -> list[RailStation]:
            """Drop branch stations merely passed over, keep them as endpoints."""
            return [
                station
                for index, station in enumerate(path)
                if not station.is_spur or index in (0, len(path) - 1)
            ]

        low, high = min(start, end), max(start, end)
        forward = line_stations[low : high + 1]
        if start > end:
            forward = forward[::-1]
        candidates = [trim_spurs(forward)]

        if origin.line in self.ring_lines:
            # The other way round: out past the end of the list, over the join,
            # back to the other station. T3's ring closes with 122 m between
            # its listed first and last stations, so this run is often quicker.
            # The slice runs high -> low, so it needs reversing exactly when
            # the origin is the `low` end.
            around = line_stations[high:] + line_stations[: low + 1]
            if start <= end:
                around = around[::-1]
            candidates.append(trim_spurs(around))

        legs = [leg for path in candidates if (leg := self._walk(origin.line, path))]
        return min(legs, key=lambda leg: leg.seconds) if legs else None

    # ----------------------------------------------------------- transfers

    def transfer_options(
        self, from_line: str, to_line: str
    ) -> list[tuple[RailStation, RailStation]]:
        """Where a passenger can change from one line to the other.

        Reads the interchange table the ingest built from coordinates. Names
        are deliberately not consulted: matching on them invented 26 false
        interchanges (M5 and Marmaray2 each have an "Altınşehir", 33 km
        apart; M4 shares six names with Marmaray that are 1-2 km apart
        because one runs the E-5 and the other the coast) while missing the
        real M4-Marmaray interchange, two records 7 m apart spelled
        `Ayrılık Çeşmesi` and `Ayrılıkçeşme`.
        """
        return self._interchanges.get((from_line, to_line), [])

    def transfer_penalty(
        self, from_line: str, from_station: str, to_line: str, to_station: str
    ) -> int:
        """Seconds to add for this particular change.

        The walk comes from the measured distance between the two records,
        so the 463 m F4-M6 change costs more than a cross-platform one. An
        unmeasured pair (a hand-built test network) falls back to the flat
        penalty.
        """
        distance = self._interchange_distances.get(
            (from_line, self.key(from_station), to_line, self.key(to_station))
        )
        return transfer_penalty_seconds(distance)

    # ----------------------------------------------------- whole journeys

    def plan(
        self, origins: list[RailStation], destinations: list[RailStation]
    ) -> list[TravelLeg] | None:
        """Quickest run of legs from any origin to any destination.

        Dijkstra over `(line, station)` nodes. Edges are of two kinds: riding
        one hop to the adjacent station on the same line, and changing line at
        an interchange for its own penalty. One interchange is not enough
        for this network -- Kadıköy to Taksim needs M4, Marmaray and M2,
        because the metro lines do not meet each other across the Bosphorus.

        Consecutive hops on one line are merged back into a single leg, and
        each merged leg's duration is recomputed with `same_line_leg` so the
        reported figure is the same one a direct query would give. The search
        cost is only used to pick the route.
        """
        if not origins or not destinations:
            return None

        goals = {(d.line, self.key(d.name)) for d in destinations}
        station_of: dict[tuple[str, str], RailStation] = {}
        for line, line_stations in self._by_line.items():
            for station in line_stations:
                station_of.setdefault((line, self.key(station.name)), station)

        # Riding edges: adjacent pairs only, branch stations skipped.
        rides: dict[tuple[str, str], list[tuple[tuple[str, str], int]]] = {}
        for line, line_stations in self._by_line.items():
            usable = [s for s in line_stations if not s.is_spur]
            pairs = list(pairwise(usable))
            if line in self.ring_lines and len(usable) > 2:
                pairs.append((usable[-1], usable[0]))
            for left, right in pairs:
                seconds, _ = self._segment_seconds(left, right)
                if seconds is None:
                    continue
                cost = seconds + DWELL_S
                a = (line, self.key(left.name))
                b = (line, self.key(right.name))
                rides.setdefault(a, []).append((b, cost))
                rides.setdefault(b, []).append((a, cost))

        # Interchange edges, from the measured table, each with its own cost.
        changes: dict[tuple[str, str], list[tuple[tuple[str, str], int]]] = {}
        for line in self._by_line:
            for other in self._by_line:
                if line == other:
                    continue
                for change_from, change_to in self.transfer_options(line, other):
                    node = (line, self.key(change_from.name))
                    target = (other, self.key(change_to.name))
                    penalty = self.transfer_penalty(line, change_from.name, other, change_to.name)
                    changes.setdefault(node, []).append((target, penalty))

        starts = {(o.line, self.key(o.name)) for o in origins}
        best: dict[tuple[str, str], int] = dict.fromkeys(starts, 0)
        # node -> (previous node, was this a line change?)
        came_from: dict[tuple[str, str], tuple[tuple[str, str], bool]] = {}
        queue: list[tuple[int, int, tuple[str, str]]] = [
            (0, index, node) for index, node in enumerate(sorted(starts))
        ]
        heapq.heapify(queue)
        counter = len(queue)
        settled: set[tuple[str, str]] = set()
        reached: tuple[str, str] | None = None

        while queue:
            cost, _, node = heapq.heappop(queue)
            if node in settled:
                continue
            settled.add(node)
            if node in goals:
                reached = node
                break

            for target, edge_cost in rides.get(node, []):
                if target in settled:
                    continue
                total = cost + edge_cost
                if total < best.get(target, 1 << 60):
                    best[target] = total
                    came_from[target] = (node, False)
                    counter += 1
                    heapq.heappush(queue, (total, counter, target))

            for target, penalty in changes.get(node, []):
                if target in settled or target not in station_of:
                    continue
                total = cost + penalty
                if total < best.get(target, 1 << 60):
                    best[target] = total
                    came_from[target] = (node, True)
                    counter += 1
                    heapq.heappush(queue, (total, counter, target))

        if reached is None:
            return None

        # Walk back to the start, then group the ride edges by line.
        path: list[tuple[tuple[str, str], bool]] = []
        node = reached
        while node in came_from:
            previous, was_change = came_from[node]
            path.append((node, was_change))
            node = previous
        path.append((node, False))
        path.reverse()

        legs: list[TravelLeg] = []
        run: list[tuple[str, str]] = [path[0][0]]
        for current, was_change in path[1:]:
            if was_change:
                legs.extend(self._leg_from_run(run))
                run = [current]
            else:
                run.append(current)
        legs.extend(self._leg_from_run(run))
        return legs or None

    def _leg_from_run(self, run: list[tuple[str, str]]) -> list[TravelLeg]:
        """Turn a run of same-line nodes into at most one leg."""
        if len(run) < 2:
            return []
        line = run[0][0]
        origin = next((s for s in self.stations_on(line) if self.key(s.name) == run[0][1]), None)
        destination = next(
            (s for s in self.stations_on(line) if self.key(s.name) == run[-1][1]), None
        )
        if origin is None or destination is None:
            return []
        leg = self.same_line_leg(origin, destination)
        return [leg] if leg is not None and leg.seconds > 0 else []


def median_or_none(values: list[int]) -> int | None:
    return int(statistics.median(values)) if values else None
