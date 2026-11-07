"""Fit the travel-time model instead of dividing by one average speed.

The first validation showed a systematic -11% bias: a single median speed per
mode makes every estimate too fast. The reason is physical -- a train
accelerates away from one station and brakes into the next, so a hop's
duration is not proportional to its length. Dividing by an average speed
charges nothing for those two manoeuvres.

So fit `duration = overhead + distance / cruise_speed` per mode by least
squares, where `overhead` absorbs the acceleration and braking. Compare three
candidates on the same data:

    A. distance / median_speed            (what we have)
    B. overhead + distance / cruise       (fitted per mode)
    C. overhead + distance / cruise       (fitted per line, where enough data)

Reports which wins and prints the coefficients to paste into rail_travel.py.

    uv run python scripts/fit_travel_model.py
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from itertools import pairwise
from typing import Any

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.store.geo import haversine_m, parse_coordinate
from istanbul_agent.store.gtfs import GtfsFeed, to_seconds
from istanbul_agent.store.rail_reference import load_rail_reference
from istanbul_agent.store.rail_segments import build_segment_index

OUT = PROJECT_ROOT / "data" / "travel_model_fit.json"

ROUTE_TYPES = {
    "0": "tramvay",
    "1": "metro",
    "2": "tren",
    "5": "teleferik",
    "6": "teleferik",
    "7": "funikuler",
}
UNRELIABLE_LINES = {"T3"}
MIN_SEGMENT_S, MAX_SEGMENT_S = 20, 900

# A fit needs enough points to mean anything.
MIN_POINTS_PER_FIT = 8

# Guards on a per-line fit. Least squares on a dozen points whose distances
# barely vary will happily return a cruise speed of 46 m/s (166 km/h, M1A's
# first fit) or a negative overhead (M3's). Such a fit describes its own
# points and nothing else, so it must not be extrapolated to a segment the
# feed never had -- which is the only thing the estimate is ever used for.
# A line failing these falls back to its mode's fit.
MAX_OVERHEAD_S = 90.0
MIN_OVERHEAD_S = 0.0
CRUISE_BAND = (0.6, 1.6)  # multiples of the mode's fitted cruise speed


def line_fit_is_sound(
    intercept: float, cruise: float, mode_cruise: float | None
) -> tuple[bool, str]:
    """Is a per-line fit safe to extrapolate?"""
    if not MIN_OVERHEAD_S <= intercept <= MAX_OVERHEAD_S:
        return False, f"sabit {intercept:.0f}s aralık dışı"
    if mode_cruise is None:
        return False, "mod uydurması yok"
    low, high = CRUISE_BAND[0] * mode_cruise, CRUISE_BAND[1] * mode_cruise
    if not low <= cruise <= high:
        return False, f"seyir {cruise:.1f} m/s, mod bandı {low:.1f}-{high:.1f} dışında"
    return True, "tamam"


def fit_linear(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Least-squares fit of duration = intercept + slope * distance.

    `slope` is seconds per metre, so cruise speed is 1/slope.
    """
    if len(points) < 2:
        return None
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator
    return mean_y - slope * mean_x, slope


def errors_for(
    points: list[tuple[float, float]], predict: Callable[[float], float]
) -> tuple[float, float, float]:
    """Return (mean |error|, max |error|, bias) in seconds."""
    residuals = [predict(x) - y for x, y in points]
    absolute = [abs(r) for r in residuals]
    return statistics.fmean(absolute), max(absolute), statistics.fmean(residuals)


def main() -> int:
    feed = GtfsFeed.load("public_transport", agency_filter="Metro")
    reference = load_rail_reference()

    stations_by_line: dict[str, list[dict[str, str]]] = {}
    mode_by_line: dict[str, str] = {}
    for route in feed.routes:
        line = route["route_short_name"].strip()
        if line in UNRELIABLE_LINES:
            continue
        sequence = feed.longest_trip_sequence(route["route_id"])
        stations_by_line[line] = [
            feed.stops[row["stop_id"]] for row in sequence if row["stop_id"] in feed.stops
        ]
        mode_by_line[line] = ROUTE_TYPES.get(route["route_type"], "bilinmiyor")

    index = build_segment_index(reference, stations_by_line)

    observed: dict[tuple[str, str], list[int]] = defaultdict(list)
    for route in feed.routes:
        line = route["route_short_name"].strip()
        if line in UNRELIABLE_LINES:
            continue
        for trip in feed.trips_for_route(route["route_id"]):
            sequence = feed.stop_times.get(trip["trip_id"], [])
            for left, right in pairwise(sequence):
                depart = to_seconds(left["departure_time"]) or to_seconds(left["arrival_time"])
                arrive = to_seconds(right["arrival_time"]) or to_seconds(right["departure_time"])
                if depart is None or arrive is None:
                    continue
                delta = arrive - depart
                if MIN_SEGMENT_S <= delta <= MAX_SEGMENT_S:
                    observed[(left["stop_id"], right["stop_id"])].append(delta)
    duration_of = {key: int(statistics.median(v)) for key, v in observed.items()}

    # ---------- build (distance, duration) points ----------
    by_mode: dict[str, list[tuple[float, float]]] = defaultdict(list)
    by_line: dict[str, list[tuple[float, float]]] = defaultdict(list)
    rows: list[dict[str, Any]] = []

    for line, stations in stations_by_line.items():
        for left, right in pairwise(stations):
            actual = duration_of.get((left["stop_id"], right["stop_id"])) or duration_of.get(
                (right["stop_id"], left["stop_id"])
            )
            if actual is None:
                continue
            metres = index.track_m(left["stop_id"], right["stop_id"])
            source = "312"
            if metres is None:
                a = (parse_coordinate(left["stop_lat"]), parse_coordinate(left["stop_lon"]))
                b = (parse_coordinate(right["stop_lat"]), parse_coordinate(right["stop_lon"]))
                if None in a or None in b:
                    continue
                metres = haversine_m(a[0], a[1], b[0], b[1])  # type: ignore[arg-type]
                source = "koordinat"
            by_mode[mode_by_line[line]].append((metres, float(actual)))
            by_line[line].append((metres, float(actual)))
            rows.append(
                {
                    "line": line,
                    "mode": mode_by_line[line],
                    "from": left["stop_name"],
                    "to": right["stop_name"],
                    "metres": round(metres, 1),
                    "duration_s": actual,
                    "distance_source": source,
                }
            )

    print(f"{len(rows)} segment ölçümü\n")

    # ---------- candidate A: single median speed ----------
    print("=" * 76)
    print("A. mesafe / ortanca hız   (mevcut model)")
    print("=" * 76)
    print(f"{'mod':12} {'n':>4} {'hız m/s':>8} {'ort|hata|':>10} {'max':>7} {'yanlılık':>9}")
    median_speeds: dict[str, float] = {}
    for mode in sorted(by_mode):
        points = by_mode[mode]
        speeds = [d / t for d, t in points if t > 0]
        speed = statistics.median(speeds)
        median_speeds[mode] = speed
        mean_abs, worst, bias = errors_for(points, lambda d, s=speed: d / s)
        print(
            f"{mode:12} {len(points):>4} {speed:>8.2f} {mean_abs:>9.1f}s "
            f"{worst:>6.0f}s {bias:>+8.1f}s"
        )

    # ---------- candidate B: fitted per mode ----------
    print("\n" + "=" * 76)
    print("B. sabit + mesafe / seyir hızı   (mod bazında uydurulmuş)")
    print("=" * 76)
    print(
        f"{'mod':12} {'n':>4} {'sabit':>7} {'seyir m/s':>10} {'ort|hata|':>10} {'max':>7} {'yanlılık':>9}"
    )
    mode_fits: dict[str, dict[str, float]] = {}
    for mode in sorted(by_mode):
        points = by_mode[mode]
        fit = fit_linear(points)
        if fit is None or len(points) < MIN_POINTS_PER_FIT:
            print(f"{mode:12} {len(points):>4}  (yeterli veri yok)")
            continue
        intercept, slope = fit
        if slope <= 0:
            print(f"{mode:12} {len(points):>4}  (eğim pozitif değil, atlandı)")
            continue
        mean_abs, worst, bias = errors_for(points, lambda d, i=intercept, s=slope: i + d * s)
        cruise = 1 / slope
        mode_fits[mode] = {
            "overhead_s": round(intercept, 1),
            "cruise_ms": round(cruise, 2),
            "n": len(points),
            "mean_abs_error_s": round(mean_abs, 1),
            "max_error_s": round(worst, 1),
            "bias_s": round(bias, 2),
        }
        print(
            f"{mode:12} {len(points):>4} {intercept:>6.1f}s {cruise:>10.2f} "
            f"{mean_abs:>9.1f}s {worst:>6.0f}s {bias:>+8.1f}s"
        )

    # ---------- candidate C: fitted per line ----------
    print("\n" + "=" * 76)
    print("C. sabit + mesafe / seyir hızı   (hat bazında uydurulmuş)")
    print("=" * 76)
    print(
        f"{'hat':6} {'mod':11} {'n':>4} {'sabit':>7} {'seyir m/s':>10} "
        f"{'ort|hata|':>10} {'max':>7}  kabul"
    )
    line_fits: dict[str, dict[str, float]] = {}
    for line in sorted(by_line):
        points = by_line[line]
        if len(points) < MIN_POINTS_PER_FIT:
            continue
        fit = fit_linear(points)
        if fit is None or fit[1] <= 0:
            continue
        intercept, slope = fit
        cruise = 1 / slope
        mode_fit = mode_fits.get(mode_by_line[line])
        sound, reason = line_fit_is_sound(
            intercept, cruise, mode_fit["cruise_ms"] if mode_fit else None
        )
        mean_abs, worst, _ = errors_for(points, lambda d, i=intercept, s=slope: i + d * s)
        print(
            f"{line:6} {mode_by_line[line]:11} {len(points):>4} {intercept:>6.1f}s "
            f"{cruise:>10.2f} {mean_abs:>9.1f}s {worst:>6.0f}s  "
            f"{'evet' if sound else 'HAYIR — ' + reason}"
        )
        if sound:
            line_fits[line] = {
                "overhead_s": round(intercept, 1),
                "cruise_ms": round(cruise, 2),
                "n": len(points),
                "mean_abs_error_s": round(mean_abs, 1),
            }

    # ---------- head to head on all points ----------
    print("\n" + "=" * 76)
    print("Karşılaştırma (tüm segmentler)")
    print("=" * 76)

    def score(
        predict_for_row: Callable[[dict[str, Any], float], float | None],
    ) -> tuple[float, float, float]:
        residuals = []
        for row, (metres, duration) in zip(
            rows, [(r["metres"], r["duration_s"]) for r in rows], strict=False
        ):
            predicted = predict_for_row(row, metres)
            if predicted is None:
                continue
            residuals.append(predicted - duration)
        absolute = [abs(r) for r in residuals]
        return statistics.fmean(absolute), max(absolute), statistics.fmean(residuals)

    def predict_a(row: dict, metres: float) -> float | None:
        speed = median_speeds.get(row["mode"])
        return metres / speed if speed else None

    def predict_b(row: dict, metres: float) -> float | None:
        fit = mode_fits.get(row["mode"])
        return fit["overhead_s"] + metres / fit["cruise_ms"] if fit else None

    def predict_c(row: dict, metres: float) -> float | None:
        fit = line_fits.get(row["line"]) or mode_fits.get(row["mode"])
        return fit["overhead_s"] + metres / fit["cruise_ms"] if fit else None

    print(f"{'model':46} {'ort|hata|':>10} {'max':>8} {'yanlılık':>10}")
    for label, predict in (
        ("A  mesafe / ortanca hız", predict_a),
        ("B  sabit + mesafe/seyir (mod)", predict_b),
        ("C  sabit + mesafe/seyir (hat, moda düşer)", predict_c),
    ):
        mean_abs, worst, bias = score(predict)
        print(f"{label:46} {mean_abs:>9.1f}s {worst:>7.0f}s {bias:>+9.2f}s")

    OUT.write_text(
        json.dumps(
            {
                "fitted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "median_speeds_ms": {k: round(v, 3) for k, v in median_speeds.items()},
                "mode_fits": mode_fits,
                "line_fits": line_fits,
                "segments": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n-> {OUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
