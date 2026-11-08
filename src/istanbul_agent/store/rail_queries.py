"""The query behind `get_rail_travel_time`.

Loads the station graph from data/transit.duckdb and answers A to B, on one
line or across an interchange. Every answer states which method produced it,
how old the underlying data is, and -- when the duration was estimated -- how
far off it is likely to be, taken from the held-out validation rather than
invented. When no method can answer, it says so instead of guessing.
"""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

from istanbul_agent.store.calibration import Calibration, load_calibrations
from istanbul_agent.store.db import read_only_connection
from istanbul_agent.store.rail_travel import (
    GTFS_METRO_DATA_AS_OF,
    INTERCHANGE_RADIUS_M,
    LINE_WARNINGS,
    TRANSFER_WAIT_S,
    WALK_SPEED_MS,
    ErrorMargin,
    RailNetwork,
    RailStation,
    TravelLeg,
    TravelTimeResult,
    estimate_error,
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

        durations: dict[tuple[str, str, str], int] = {}
        lengths: dict[tuple[str, str, str], float] = {}
        for line, from_name, to_name, duration, track, straight in connection.execute(
            "SELECT line, from_name, to_name, duration_s, track_m, straight_m FROM rail_segments"
        ).fetchall():
            a, b = normalize(from_name), normalize(to_name)
            key = (line, a, b) if a <= b else (line, b, a)
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
                "SELECT line FROM rail_lines WHERE NOT coords_reliable"
            ).fetchall()
        )
        rings = frozenset(
            row[0]
            for row in connection.execute("SELECT line FROM rail_lines WHERE is_ring").fetchall()
        )
        line_dates = {
            row[0]: row[1]
            for row in connection.execute("SELECT line, data_as_of FROM rail_lines").fetchall()
        }
        interchanges = [
            (row[0], row[1], row[2], row[3], row[4])
            for row in connection.execute(
                "SELECT from_line, from_key, to_line, to_key, distance_m FROM rail_interchanges"
            ).fetchall()
        ]

    return RailNetwork(
        stations=stations,
        segment_durations=durations,
        segment_lengths=lengths,
        transfers=transfers,
        unreliable_lines=unreliable,
        ring_lines=rings,
        line_dates=line_dates,
        interchanges=interchanges,
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


def _margin_for(seconds: int, stop_count: int) -> ErrorMargin:
    mean_pct, max_pct = estimate_error(stop_count)
    return ErrorMargin(
        mean_pct=mean_pct,
        max_pct=max_pct,
        mean_seconds=round(seconds * mean_pct / 100),
        max_seconds=round(seconds * max_pct / 100),
    )


def _data_as_of(network: RailNetwork, legs: list[TravelLeg]) -> str:
    """The oldest date among the lines used.

    A journey touching Marmaray is only as current as Marmaray's own feed
    data, which predates the metro's by a year.
    """
    known = [date for date in (network.line_dates.get(leg.line) for leg in legs) if date]
    return min(known) if known else GTFS_METRO_DATA_AS_OF


def _warnings_for(legs: list[TravelLeg]) -> list[str]:
    seen: list[str] = []
    for leg in legs:
        warning = LINE_WARNINGS.get(leg.line.strip().upper())
        if warning and warning not in seen:
            seen.append(warning)
    return seen


def _calibrate(
    leg: TravelLeg, calibrations: dict[str, Calibration]
) -> tuple[TravelLeg, Calibration | None]:
    """Scale a recorded duration by its line's factor, if it has one.

    Only a `gtfs` leg is scaled: an estimate already carries its own error,
    and folding a factor derived from one journey into it would compound two
    uncertainties.
    """
    entry = calibrations.get(leg.line.strip())
    if entry is None or leg.method != "gtfs":
        return leg, None
    scaled = replace(
        leg,
        seconds=round(leg.seconds * entry.factor),
        method="gtfs_calibrated",
        detail=f"{leg.detail}, kalibrasyon ×{entry.factor:.3f}",
    )
    return scaled, entry


def get_rail_travel_time(
    from_station: str,
    to_station: str,
    *,
    network: RailNetwork | None = None,
    calibrate: bool = True,
) -> TravelTimeResult:
    """Travel time between two rail stations.

    Args:
        from_station: origin station name, any spelling the sources use.
        to_station: destination station name.
        network: graph to query; defaults to the store.
        calibrate: apply each line's measured factor. `load_calibrations`
            derives those factors by asking this function what we currently
            return, so it must pass `False` -- otherwise the two call each
            other forever.
    """
    graph = network or _cached_network()
    calibrations = load_calibrations() if calibrate else {}

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

    # ---- same line ----
    best: TravelLeg | None = None
    for line in sorted({o.line for o in origins} & {d.line for d in destinations}):
        origin = next(o for o in origins if o.line == line)
        destination = next(d for d in destinations if d.line == line)
        leg = graph.same_line_leg(origin, destination)
        if leg is not None and (best is None or leg.seconds < best.seconds):
            best = leg

    if best is not None:
        best, calibration = _calibrate(best, calibrations)
        if calibration is not None:
            notes.append(
                f"{best.line} süresi dış kaynakla kalibre edildi "
                f"(×{calibration.factor:.3f}; {calibration.from_station}→"
                f"{calibration.to_station} için bizim {calibration.ours_minutes:.1f} dk, "
                f"dış kaynak {calibration.external_minutes:.1f} dk). "
                f"Kaynak: {calibration.source_url}"
            )
        estimated = best.method == "estimated"
        if best.line in graph.unreliable_lines:
            notes.append(
                f"{best.line} koordinatları güvenilmez (karar 0003); süre beslemenin "
                "kayıtlı değerlerinden alındı, mesafeden türetilmedi."
            )
        if best.line in graph.ring_lines:
            notes.append(f"{best.line} ring hat; iki yönden kısa olanı seçildi.")
        if estimated:
            notes.append(
                "Süre tahmin edildi: bu istasyonların bir kısmı 2020 tarihli GTFS beslemesinde yok."
            )
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
            data_as_of=_data_as_of(graph, [best]),
            legs=[best],
            error_margin=_margin_for(best.seconds, best.stop_count) if estimated else None,
            warnings=_warnings_for([best]),
            notes=notes,
        )

    # ---- across interchanges, however many it takes ----
    legs = graph.plan(origins, destinations)
    if legs:
        calibrated: list[TravelLeg] = []
        for leg in legs:
            scaled, entry = _calibrate(leg, calibrations)
            calibrated.append(scaled)
            if entry is not None:
                notes.append(
                    f"{leg.line} bacağı dış kaynakla kalibre edildi "
                    f"(×{entry.factor:.3f}). Kaynak: {entry.source_url}"
                )
        legs = calibrated
        changes = max(0, len(legs) - 1)
        estimated = any(leg.method == "estimated" for leg in legs)
        # Each change is priced on its own: the walk between two records
        # 463 m apart is not the walk across a platform.
        penalties = [
            graph.transfer_penalty(before.line, before.to_station, after.line, after.from_station)
            for before, after in pairwise(legs)
        ]
        penalty_total = sum(penalties)
        total = sum(leg.seconds for leg in legs) + penalty_total
        stops = sum(leg.stop_count for leg in legs)
        interchanges = [leg.to_station for leg in legs[:-1]]

        if changes:
            detail = ", ".join(
                f"{station} (+{penalty // 60} dk)"
                for station, penalty in zip(interchanges, penalties, strict=True)
            )
            notes.append(
                f"{changes} aktarma: {detail}. Her aktarma {TRANSFER_WAIT_S} s ölçülen "
                "ortalama bekleme + yürüme süresi (istasyon içi geçişte 150 s, "
                f"{INTERCHANGE_RADIUS_M:.0f} m'den uzak çiftlerde mesafe / "
                f"{WALK_SPEED_MS} m/s, karar 0004)."
            )
        if estimated:
            notes.append("Bacakların en az biri tahmin edildi.")
        for line in sorted({leg.line for leg in legs} & graph.unreliable_lines):
            notes.append(
                f"{line} koordinatları güvenilmez (karar 0003); o bacağın süresi "
                "beslemenin kayıtlı değerlerinden alındı, mesafeden türetilmedi."
            )

        return TravelTimeResult(
            from_station=origins[0].name,
            to_station=destinations[0].name,
            seconds=total,
            method="transfer" if changes else legs[0].method,
            estimated=estimated,
            is_stale=True,
            source=(
                "Public Transport GTFS (veri seti 308) + Raylı Sistem Aktarma Bilgileri (313)"
                if changes
                else "Public Transport GTFS (veri seti 308)"
            ),
            data_as_of=_data_as_of(graph, legs),
            legs=legs,
            transfer_station=" → ".join(interchanges) if interchanges else None,
            transfer_penalty_s=penalty_total,
            error_margin=_margin_for(total, stops) if estimated else None,
            warnings=_warnings_for(legs),
            notes=notes,
        )

    origin_lines = ", ".join(sorted({o.line for o in origins}))
    destination_lines = ", ".join(sorted({d.line for d in destinations}))
    return unavailable(
        f"{origins[0].name} ({origin_lines}) ile {destinations[0].name} "
        f"({destination_lines}) arasında raylı bir bağlantı bulunamadı veya "
        "gereken segmentlerin süre/mesafe verisi yok."
    )
