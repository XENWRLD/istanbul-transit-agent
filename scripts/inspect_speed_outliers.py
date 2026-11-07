"""Why does the speed profile contain impossible values?

The first profile run reported a median route factor of 0.9 -- track shorter
than the straight line between the same two stations, which cannot happen --
and a top metro speed of 65.8 m/s, or 237 km/h. Both point at dataset 312
lengths being attached to the wrong GTFS pairs rather than at the feed.

Lists the worst segments so the cause is visible before the estimator is
built on these numbers.

    uv run python scripts/inspect_speed_outliers.py
"""

from __future__ import annotations

import json
import sys

from istanbul_agent.config import PROJECT_ROOT

PROFILE = PROJECT_ROOT / "data" / "rail_speed_profile.json"

# A metro segment faster than this is not a metro segment.
IMPLAUSIBLE_SPEED_MS = 25.0


def main() -> int:
    if not PROFILE.exists():
        print("Önce: uv run python scripts/analyze_rail_speeds.py")
        return 1

    segments = json.loads(PROFILE.read_text(encoding="utf-8"))["segments"]
    with_both = [s for s in segments if s["track_m"] and s["straight_m"]]

    for segment in with_both:
        segment["factor"] = segment["track_m"] / segment["straight_m"]
        segment["speed"] = segment["track_m"] / segment["duration_s"]

    print(f"{len(with_both)} segmentin hem ray hem düz çizgi uzunluğu var\n")

    print("=== Güzergâh katsayısı < 0.95 (ray, düz çizgiden kısa — imkansız) ===")
    impossible = sorted((s for s in with_both if s["factor"] < 0.95), key=lambda s: s["factor"])
    print(f"{len(impossible)} segment\n")
    print(f"{'Hat':5} {'A':24} {'B':24} {'ray':>8} {'düz':>8} {'kat':>6}")
    for segment in impossible[:20]:
        print(
            f"{segment['line']:5} {segment['from_station'][:23]:24} "
            f"{segment['to_station'][:23]:24} {segment['track_m']:>8.0f} "
            f"{segment['straight_m']:>8.0f} {segment['factor']:>6.2f}"
        )

    print(f"\n=== Hız > {IMPLAUSIBLE_SPEED_MS:.0f} m/s ===")
    fast = sorted(
        (s for s in with_both if s["speed"] > IMPLAUSIBLE_SPEED_MS),
        key=lambda s: -s["speed"],
    )
    print(f"{len(fast)} segment\n")
    print(f"{'Hat':5} {'A':24} {'B':24} {'ray':>8} {'süre':>6} {'m/s':>7}")
    for segment in fast:
        print(
            f"{segment['line']:5} {segment['from_station'][:23]:24} "
            f"{segment['to_station'][:23]:24} {segment['track_m']:>8.0f} "
            f"{segment['duration_s']:>6} {segment['speed']:>7.1f}"
        )

    print("\n=== Sağlıklı görünen segmentler (karşılaştırma için) ===")
    healthy = sorted(
        (s for s in with_both if 1.0 <= s["factor"] <= 1.6 and s["speed"] <= IMPLAUSIBLE_SPEED_MS),
        key=lambda s: -s["track_m"],
    )
    print(f"{len(healthy)} segment\n")
    for segment in healthy[:12]:
        print(
            f"{segment['line']:5} {segment['from_station'][:23]:24} "
            f"{segment['to_station'][:23]:24} {segment['track_m']:>8.0f} "
            f"{segment['straight_m']:>8.0f} {segment['factor']:>6.2f} "
            f"{segment['speed']:>6.1f} m/s"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
