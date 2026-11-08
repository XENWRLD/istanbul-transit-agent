"""Finding the station a user actually typed.

The tools are called with whatever the user wrote: `kadıköy`, `KADIKOY`,
`taksim`, `sabiha gökçen`, `ayrılık çeşmesi`. The store keys stations on a
normalised name, which already folds case and Turkish letters, but a user's
spelling can still miss: a suffix (`Ayrılıkçeşme` vs `Ayrılık Çeşmesi`), a
missing word (`Sabiha Gökçen` for `Sabiha Gökçen Havalimanı`), a typo.

So lookup goes through four steps, strictest first, and when none of them
lands it returns the three closest names rather than an empty answer -- a
caller that gets candidates can ask the user which one they meant, and the
agent can say "böyle bir istasyon yok, şunlar var".
"""

from __future__ import annotations

from dataclasses import dataclass

from istanbul_agent.store.rail_travel import RailNetwork, RailStation
from istanbul_agent.store.station_names import collapsed, normalize, similarity, token_key

__all__ = ["CANDIDATE_COUNT", "StationMatch", "find_station", "suggest_stations"]

# How many alternatives to offer when nothing matched.
CANDIDATE_COUNT = 3

# Below this a name is not a plausible misspelling of the other, it is a
# different station. Measured against the station list: real typos and
# suffix differences score above 0.6, unrelated names below it.
SUGGESTION_FLOOR = 0.45


@dataclass(frozen=True)
class StationMatch:
    """What a lookup found: either a station, or what to ask about."""

    station: RailStation | None
    name: str | None
    lines: tuple[str, ...] = ()
    how: str = ""
    candidates: tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return self.station is not None


def _entries(network: RailNetwork, line: str | None) -> list[RailStation]:
    """Stations to search: one line's, or the whole network's.

    The line code is matched case-insensitively, because a caller passes
    what the user typed (`marmaray`) while the feed spells it `Marmaray`.
    """
    if not line:
        return list(network.stations)
    wanted = line.strip().upper()
    return [station for station in network.stations if station.line.upper() == wanted]


def find_station(network: RailNetwork, name: str, *, line: str | None = None) -> StationMatch:
    """The station `name` refers to, or three candidates.

    `line` restricts the search to one line, which is what disambiguates the
    stations that exist twice under one name (M4's Göztepe on the E-5 and
    Marmaray's on the coast).
    """
    wanted = (name or "").strip()
    if not wanted:
        return StationMatch(station=None, name=None, candidates=())

    entries = _entries(network, line)
    if not entries:
        return StationMatch(station=None, name=None, candidates=())

    def hit(station: RailStation, how: str) -> StationMatch:
        same = [s.line for s in entries if network.key(s.name) == network.key(station.name)]
        return StationMatch(
            station=station,
            name=station.name,
            lines=tuple(dict.fromkeys(same)),
            how=how,
        )

    # 1. The normalised name, which is how the store keys stations.
    key = normalize(wanted)
    exact = next((s for s in entries if network.key(s.name) == key), None)
    if exact is not None:
        return hit(exact, "tam ad")

    # 2. Spacing and punctuation ignored, then word order ignored.
    tight = collapsed(wanted)
    loose = next((s for s in entries if collapsed(s.name) == tight), None)
    if loose is not None:
        return hit(loose, "yazım farkı")
    tokens = token_key(wanted)
    reordered = next((s for s in entries if token_key(s.name) == tokens), None)
    if reordered is not None:
        return hit(reordered, "kelime sırası")

    # 3. A unique prefix: "sabiha gökçen" for "Sabiha Gökçen Havalimanı".
    if len(tight) >= 4:
        prefixed = {network.key(s.name): s for s in entries if collapsed(s.name).startswith(tight)}
        if len(prefixed) == 1:
            return hit(next(iter(prefixed.values())), "ön ek")

    # 4. Closest by similarity, but only if it is clearly the same name.
    scored = sorted(
        ((similarity(wanted, s.name), s) for s in entries),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if scored and scored[0][0] >= 0.85:
        return hit(scored[0][1], f"benzerlik {scored[0][0]:.2f}")

    # Nothing matched. Offer the closest names anyway: the caller has to say
    # something to the user, and "bulunamadı" with no alternative is the one
    # answer that helps nobody.
    return StationMatch(
        station=None,
        name=None,
        candidates=_top_names(scored) or _closest_names(scored),
    )


def _closest_names(scored: list[tuple[float, RailStation]]) -> tuple[str, ...]:
    """The nearest names regardless of how far off they are."""
    names: list[str] = []
    for _, station in scored:
        if station.name not in names:
            names.append(station.name)
        if len(names) == CANDIDATE_COUNT:
            break
    return tuple(names)


def _top_names(scored: list[tuple[float, RailStation]]) -> tuple[str, ...]:
    names: list[str] = []
    for score, station in scored:
        if score < SUGGESTION_FLOOR:
            break
        if station.name not in names:
            names.append(station.name)
        if len(names) == CANDIDATE_COUNT:
            break
    return tuple(names)


def suggest_stations(
    network: RailNetwork, name: str, *, line: str | None = None
) -> tuple[str, ...]:
    """The closest station names to `name`, however badly it is spelled.

    Used when a lookup fails: unlike `find_station` this never refuses, so
    the caller always has something to offer the user.
    """
    entries = _entries(network, line)
    scored = sorted(
        ((similarity(name or "", s.name), s) for s in entries),
        key=lambda pair: pair[0],
        reverse=True,
    )
    return _top_names(scored) or _closest_names(scored)
