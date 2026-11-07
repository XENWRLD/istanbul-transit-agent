"""The query behind `get_rail_travel_time`.

Loads the station graph from data/transit.duckdb and answers A-to-B, on one
line or across an interchange. Every answer states which method produced it
and how old the underlying data is; when no method can answer, it says so
rather than guessing -- M5's last three stations have no coordinates in any
source, so a journey through them is genuinely unanswerable.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from istanbul_agent.store.db import read_only_connection
from istanbul_agent.store.rail_travel import (
    GTFS_METRO_DATA_AS_OF,
    TRANSFER_PENALTY_S,
    RailNetwork,
    RailStation,
    TravelLeg,
    TravelTimeResult,
)
from istanbul_agent.store.station_names import collapsed, normalize

__all__ = ["get_rail_travel_time", "load_network"]


def load_network(path: Path | None = None) -> RailNetwork:
    """Build a RailNetwork from the store."""
    with read_only_connection(path) as connection:
        stations = [
            RailStation(
                name=row[0],
                line=row[1],
                mode=row[2],
                stop_id=row[3],
                lat=row[4],
                lon=row[5],
                order=row[6],
                in_gtfs=bool(row[7]),
                is_spur=bool(row[8]),
            )
            for row in connection.execute(
                """
                SELECT name, line, mode, gtfs_stop_id, lat, lon, stop_order,
                       in_gtfs, is_spur
                FROM rail_stations
                """
            ).fetchall()
        ]

        durations: dict[tuple[str, str], int] = {}
        lengths: dict[tuple[str, str], float] = {}
        for from_name, to_name, duration, track, straight in connection.execute(
            "SELECT from_name, to_name, duration_s, track_m, straight_m FROM rail_segments"
        ).fetchall():
            a, b = normalize(from_name), normalize(to_name)
            key = (a, b) if a <= b else (b, a)
            if duration is not None:
                durations[key] = int(duration)
            metres = track if track is not None else straight
            if metres is not None:
                lengths[key] = float(metres)

        transfers = [
            (normalize(row[0]), row[1], row[2])
            for row in connection.execute(
                "SELECT station_name, from_line, to_line FROM rail_transfers WHERE to_is_rail"
            ).fetchall()
        ]

        unreliable = frozenset(
            row[0]
            for row in connection.execute(
                "SELECT line FROM rail_lines WHERE in_gtfs AND NOT gtfs_reliable"
            ).fetchall()
        )

    return RailNetwork(
        stations=stations,
        segment_durations=durations,
        segment_lengths=lengths,
        transfers=transfers,
        unreliable_lines=unreliable,
    )


@lru_cache(maxsize=1)
def _cached_network() -> RailNetwork:
    return load_network()


def _resolve(network: RailNetwork, name: str) -> list[RailStation]:
    hits = network.find(name)
    if hits:
        return hits
    # Last resort: a unique prefix, so "Sabiha Gökçen" finds "Sabiha Gökçen
    # Havalimanı". Ambiguous prefixes are refused rather than guessed.
    wanted = collapsed(name)
    if len(wanted) < 4:
        return []
    prefixed = [s for s in network.stations if collapsed(s.name).startswith(wanted)]
    unique_names = {normalize(s.name) for s in prefixed}
    return prefixed if len(unique_names) == 1 else []


def get_rail_travel_time(
    from_station: str,
    to_station: str,
    *,
    network: RailNetwork | None = None,
) -> TravelTimeResult:
    """Travel time between two rail stations.

    Args:
        from_station: origin station name, any spelling the sources use.
        to_station: destination station name.
        network: graph to query; defaults to the store.
    """
    graph = network or _cached_network()

    def unavailable(note: str) -> TravelTimeResult:
        return TravelTimeResult(
            from_station=from_station,
            to_station=to_station,
            seconds=None,
            method="unavailable",
            estimated=False,
            is_stale=False,
            source="—",
            data_as_of=GTFS_METRO_DATA_AS_OF,
            notes=[note],
        )

    origins = _resolve(graph, from_station)
    destinations = _resolve(graph, to_station)
    if not origins:
        return unavailable(f"{from_station!r} istasyonu bulunamadı.")
    if not destinations:
        return unavailable(f"{to_station!r} istasyonu bulunamadı.")

    notes: list[str] = []

    # ---- same line, if any line serves both ----
    shared = {o.line for o in origins} & {d.line for d in destinations}
    usable_shared = shared - graph.unreliable_lines
    if shared and not usable_shared:
        notes.append(
            f"{', '.join(sorted(shared))} hattının GTFS verisi güvenilmez "
            "(adlar koordinatlarla uyuşmuyor), kullanılmadı."
        )

    best: TravelLeg | None = None
    for line in sorted(usable_shared):
        origin = next(o for o in origins if o.line == line)
        destination = next(d for d in destinations if d.line == line)
        leg = graph.same_line_leg(origin, destination)
        if leg is not None and (best is None or leg.seconds < best.seconds):
            best = leg

    if best is not None:
        estimated = best.method == "estimated"
        return TravelTimeResult(
            from_station=origins[0].name,
            to_station=destinations[0].name,
            seconds=best.seconds,
            method=best.method,
            estimated=estimated,
            is_stale=True,
            source=(
                "Public Transport GTFS (veri seti 308)"
                + (" + Raylı Sistem Hat Uzunlukları (312)" if estimated else "")
            ),
            data_as_of=GTFS_METRO_DATA_AS_OF,
            legs=[best],
            notes=notes
            + (
                [
                    "Süre tahmin edildi: bu istasyonların bir kısmı 2020 tarihli "
                    "GTFS beslemesinde yok. Ortalama hata %6.6 (karar 0004)."
                ]
                if estimated
                else []
            ),
        )

    # ---- one interchange ----
    best_pair: tuple[TravelLeg, TravelLeg, str] | None = None
    for origin in origins:
        for destination in destinations:
            if origin.line == destination.line:
                continue
            if origin.line in graph.unreliable_lines or destination.line in graph.unreliable_lines:
                continue
            for change_from, change_to in graph.transfer_options(origin.line, destination.line):
                first = graph.same_line_leg(origin, change_from)
                second = graph.same_line_leg(change_to, destination)
                if first is None or second is None:
                    continue
                total = first.seconds + second.seconds
                if best_pair is None or total < best_pair[0].seconds + best_pair[1].seconds:
                    label = change_from.name
                    if graph.key(change_from.name) != graph.key(change_to.name):
                        label = f"{change_from.name} / {change_to.name}"
                    best_pair = (first, second, label)

    if best_pair is not None:
        first, second, interchange = best_pair
        estimated = "estimated" in (first.method, second.method)
        return TravelTimeResult(
            from_station=origins[0].name,
            to_station=destinations[0].name,
            seconds=first.seconds + second.seconds + TRANSFER_PENALTY_S,
            method="transfer",
            estimated=estimated,
            is_stale=True,
            source=("Public Transport GTFS (veri seti 308) + Raylı Sistem Aktarma Bilgileri (313)"),
            data_as_of=GTFS_METRO_DATA_AS_OF,
            legs=[first, second],
            transfer_station=interchange,
            transfer_penalty_s=TRANSFER_PENALTY_S,
            notes=notes
            + [
                f"{interchange} aktarmalı. Aktarma için {TRANSFER_PENALTY_S // 60} dakika "
                "eklendi (210 s ölçülen ortalama bekleme + 150 s varsayılan yürüme, "
                "karar 0004)."
            ]
            + (["Bacakların en az biri tahmin edildi."] if estimated else []),
        )

    origin_lines = ", ".join(sorted({o.line for o in origins}))
    destination_lines = ", ".join(sorted({d.line for d in destinations}))
    return unavailable(
        f"{origins[0].name} ({origin_lines}) ile {destinations[0].name} "
        f"({destination_lines}) arasında tek aktarmalı bir bağlantı bulunamadı "
        "veya gereken segmentlerin mesafe verisi yok."
    )
