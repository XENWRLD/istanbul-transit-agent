"""Matching dataset 312's inter-station lengths onto the GTFS station graph.

Dataset 312 turned out to be sound: its segment chains follow the real
station order (verified against M1A and M3). The difficulty is joining its
names to the feed's, and two collisions make a global name lookup unsafe:

- **Qualified names.** The feed writes `Ataköy - Şirinevler`, `Bakırköy -
  İncirli`, `Davutpaşa - YTÜ`; dataset 312 writes `Atakoy`, `Bakırkoy`,
  `Davutpasa`. A subset-of-tokens match resolves these, but only within one
  line -- globally it would merge distinct stations.
- **Repeated names.** `Yenimahalle` exists on both M3 and M7, `Çarşı` on a
  tram line and a minibus route. Matching without scoping to a line picks
  whichever was seen first and silently reports the wrong distance.

So every lookup here is line-scoped, and each resulting segment carries how
it was matched plus the straight-line distance between the pair, letting the
caller reject a match whose length and geometry disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

from istanbul_agent.store.geo import haversine_m, parse_coordinate
from istanbul_agent.store.rail_reference import RailReference, _line_family
from istanbul_agent.store.station_names import collapsed, normalize

__all__ = ["MatchedSegment", "SegmentIndex", "build_segment_index"]

# Bounds for believing a 312 length, read off the measured distribution
# rather than assumed (scripts/inspect_route_factor.py).
#
# All 148 line-scoped matches are GTFS-adjacent pairs, and their
# track-to-chord ratio forms a smooth bell centred on 0.934, spanning roughly
# 0.6 to 1.4. A ratio below 1 is not physically possible for real track, so
# the centre being under 1 says the two sources disagree by about 7% on where
# a station is -- GTFS places its point at an entrance, 312 measures between
# platforms -- not that anyone is wrong. In other words, at the scale of one
# metro hop the chord *is* the track length, within noise.
#
# So the bounds only have to catch genuine data errors, and the distribution
# shows exactly two: M3 Başak Konutları-Siteler at 0.22 and M4
# Maltepe-Küçükyalı at 5.29 (10,994 m between stations 2 km apart).
MIN_ROUTE_FACTOR = 0.6
MAX_ROUTE_FACTOR = 2.0


@dataclass(frozen=True)
class MatchedSegment:
    """A 312 length successfully tied to a pair of GTFS stations."""

    line: str
    from_stop_id: str
    to_stop_id: str
    from_station: str
    to_station: str
    track_m: float
    straight_m: float | None
    matched_by: str

    @property
    def route_factor(self) -> float | None:
        if not self.straight_m or self.straight_m < 50:
            return None
        return self.track_m / self.straight_m


class SegmentIndex:
    """Track lengths for station pairs, keyed by line and stop ids."""

    def __init__(self) -> None:
        self._by_stop_pair: dict[tuple[str, str], MatchedSegment] = {}
        self.matched: list[MatchedSegment] = []
        self.rejected: list[dict[str, object]] = []
        self.unmatched_rows: list[dict[str, object]] = []

    def _key(self, left: str, right: str) -> tuple[str, str]:
        return (left, right) if left <= right else (right, left)

    def add(self, segment: MatchedSegment) -> None:
        self._by_stop_pair[self._key(segment.from_stop_id, segment.to_stop_id)] = segment
        self.matched.append(segment)

    def track_m(self, from_stop_id: str, to_stop_id: str) -> float | None:
        segment = self._by_stop_pair.get(self._key(from_stop_id, to_stop_id))
        return segment.track_m if segment else None

    def get(self, from_stop_id: str, to_stop_id: str) -> MatchedSegment | None:
        return self._by_stop_pair.get(self._key(from_stop_id, to_stop_id))


def _line_station_keys(stations: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """Lookup keys for one line's stations: exact, collapsed, and per token."""
    exact: dict[str, dict[str, str]] = {}
    loose: dict[str, dict[str, str]] = {}
    for stop in stations:
        name = stop["stop_name"]
        exact.setdefault(normalize(name), stop)
        loose.setdefault(collapsed(name), stop)
    return {"exact": exact, "collapsed": loose}  # type: ignore[dict-item]


def _match_station(name: str, stations: list[dict[str, str]]) -> tuple[dict[str, str] | None, str]:
    """Find `name` among one line's stations. Returns (stop, how)."""
    wanted = normalize(name)
    for stop in stations:
        if normalize(stop["stop_name"]) == wanted:
            return stop, "ad"
    wanted_collapsed = collapsed(name)
    for stop in stations:
        if collapsed(stop["stop_name"]) == wanted_collapsed:
            return stop, "boşluksuz"

    # Qualified-name case: "Atakoy" vs "Ataköy - Şirinevler". Safe only
    # because we are already inside a single line, where a given place name
    # occurs once. Require a unique hit so an ambiguous one is refused.
    wanted_tokens = set(wanted.split())
    if wanted_tokens:
        subset_hits = [
            stop
            for stop in stations
            if wanted_tokens and wanted_tokens <= set(normalize(stop["stop_name"]).split())
        ]
        if len(subset_hits) == 1:
            return subset_hits[0], "alt küme"
    return None, "bulunamadı"


def build_segment_index(
    reference: RailReference,
    stations_by_line: dict[str, list[dict[str, str]]],
) -> SegmentIndex:
    """Tie every 312 row to a GTFS station pair, line by line.

    Args:
        reference: parsed datasets 312/313.
        stations_by_line: GTFS stop rows per line short name, in order.
    """
    index = SegmentIndex()

    # 312 files segments under the family code (M1), the feed under the branch
    # (M1A, M1B), so one 312 row may apply to several branches.
    branches_by_family: dict[str, list[str]] = {}
    for line in stations_by_line:
        branches_by_family.setdefault(_line_family(line), []).append(line)

    for segment in reference.segments:
        family = _line_family(segment.line)
        candidates = branches_by_family.get(family, [])
        if not candidates:
            index.unmatched_rows.append(
                {
                    "line": segment.line,
                    "from": segment.from_station,
                    "to": segment.to_station,
                    "metres": segment.metres,
                    "reason": f"GTFS'te {family} hattı yok",
                }
            )
            continue

        placed = False
        for line in candidates:
            stations = stations_by_line[line]
            from_stop, from_how = _match_station(segment.from_station, stations)
            to_stop, to_how = _match_station(segment.to_station, stations)
            if from_stop is None or to_stop is None:
                continue
            if from_stop["stop_id"] == to_stop["stop_id"]:
                continue

            coords = (
                parse_coordinate(from_stop["stop_lat"]),
                parse_coordinate(from_stop["stop_lon"]),
                parse_coordinate(to_stop["stop_lat"]),
                parse_coordinate(to_stop["stop_lon"]),
            )
            straight = haversine_m(*coords) if None not in coords else None  # type: ignore[arg-type]

            matched = MatchedSegment(
                line=line,
                from_stop_id=from_stop["stop_id"],
                to_stop_id=to_stop["stop_id"],
                from_station=from_stop["stop_name"],
                to_station=to_stop["stop_name"],
                track_m=segment.metres,
                straight_m=round(straight, 1) if straight is not None else None,
                matched_by=f"{from_how}/{to_how}",
            )

            factor = matched.route_factor
            if factor is not None and not MIN_ROUTE_FACTOR <= factor <= MAX_ROUTE_FACTOR:
                index.rejected.append(
                    {
                        "line": line,
                        "from": matched.from_station,
                        "to": matched.to_station,
                        "track_m": matched.track_m,
                        "straight_m": matched.straight_m,
                        "route_factor": round(factor, 3),
                        "matched_by": matched.matched_by,
                        "reason": (
                            "ray düz çizgiden kısa"
                            if factor < MIN_ROUTE_FACTOR
                            else "ray düz çizgiden aşırı uzun"
                        ),
                    }
                )
                placed = True  # accounted for; do not also report as unmatched
                continue

            index.add(matched)
            placed = True

        if not placed:
            index.unmatched_rows.append(
                {
                    "line": segment.line,
                    "from": segment.from_station,
                    "to": segment.to_station,
                    "metres": segment.metres,
                    "reason": "istasyon adları hatta bulunamadı",
                }
            )

    return index
