"""What exactly is wrong with T3, and is it the ring shape or the data?

Two separate questions got tangled together earlier:

* T3 was refused because all 10 of its feed stations carry a name that
  disagrees with the live station nearest their coordinates. Is that a clean
  one-position rotation, or something messier?
* T3 is a ring line, and `same_line_leg` walks a linear array. Even with
  perfect data it would miss the shorter way round. That is a code gap,
  independent of T3's defect.

This measures both: the distance between same-name pairs against the line's
own station spacing, and whether the two sequences are the same cycle.

    uv run python scripts/inspect_t3_ring.py
"""

from __future__ import annotations

import statistics
import sys
from itertools import pairwise

import httpx

from istanbul_agent.config import get_settings
from istanbul_agent.store.geo import haversine_m, parse_coordinate
from istanbul_agent.store.gtfs import GtfsFeed
from istanbul_agent.store.station_names import collapsed

METRO_API = "/MetroIstanbul/api/MetroMobile/V2"
LINE = "T3"


def main() -> int:
    feed = GtfsFeed.load("public_transport", agency_filter="Metro")
    route = feed.route_by_short_name(LINE)
    if route is None:
        print(f"{LINE} GTFS'te yok")
        return 1
    gtfs = [
        feed.stops[row["stop_id"]]
        for row in feed.longest_trip_sequence(route["route_id"])
        if row["stop_id"] in feed.stops
    ]

    settings = get_settings()
    base = settings.ibb_api_base_url.rstrip("/")
    with httpx.Client(timeout=settings.ibb_request_timeout) as client:
        line = next(
            line
            for line in client.get(f"{base}{METRO_API}/GetLines").json()["Data"]
            if line["Name"] == LINE
        )
        api = sorted(
            client.get(f"{base}{METRO_API}/GetStationById/{line['Id']}").json()["Data"],
            key=lambda s: s.get("Order") or 0,
        )

    def api_point(station: dict) -> tuple[float, float] | None:
        detail = station.get("DetailInfo") or {}
        lat = parse_coordinate(detail.get("Latitude"))
        lon = parse_coordinate(detail.get("Longitude"))
        return (lat, lon) if lat is not None and lon is not None else None

    api_names = [s.get("Description") or s["Name"] for s in api]
    gtfs_names = [s["stop_name"] for s in gtfs]

    print("=== İki kaynaktaki istasyon sırası ===")
    print(f"{'#':>3}  {'GTFS':28} {'canlı API':28}")
    for index in range(max(len(gtfs_names), len(api_names))):
        left = gtfs_names[index] if index < len(gtfs_names) else ""
        right = api_names[index] if index < len(api_names) else ""
        print(f"{index + 1:>3}  {left[:27]:28} {right[:27]:28}")

    # ---- are the two sequences the same cycle? ----
    print("\n=== Aynı döngü mü? (GTFS sırası, API sırasının döndürülmüş hali mi?) ===")
    api_keys = [collapsed(name) for name in api_names]
    gtfs_keys = [collapsed(name) for name in gtfs_names]
    extra = [name for name, key in zip(api_names, api_keys, strict=False) if key not in gtfs_keys]
    print(f"  API'de olup GTFS'te olmayan: {extra or 'yok'}")

    trimmed = [key for key in api_keys if key in gtfs_keys]
    rotations = [
        offset for offset in range(len(trimmed)) if trimmed[offset:] + trimmed[:offset] == gtfs_keys
    ]
    if rotations:
        print(
            f"  EVET: GTFS ad sırası, API sırasının {rotations[0]} pozisyon "
            "döndürülmüş hali. Ad listeleri tutarlı."
        )
    else:
        print("  HAYIR: ad sıraları aynı döngü değil.")

    # ---- how far apart are same-name pairs, relative to station spacing? ----
    print("\n=== Aynı adlı çiftler ne kadar uzakta? ===")
    api_by_key = {collapsed(name): station for name, station in zip(api_names, api, strict=False)}
    offsets: list[float] = []
    for stop in gtfs:
        twin = api_by_key.get(collapsed(stop["stop_name"]))
        if twin is None:
            continue
        gtfs_point = (parse_coordinate(stop["stop_lat"]), parse_coordinate(stop["stop_lon"]))
        twin_point = api_point(twin)
        if None in gtfs_point or twin_point is None:
            continue
        distance = haversine_m(gtfs_point[0], gtfs_point[1], twin_point[0], twin_point[1])  # type: ignore[arg-type]
        offsets.append(distance)
        print(f"  {stop['stop_name'][:26]:28} {distance:>7.0f} m")

    spacings = []
    for left, right in pairwise(api):
        a, b = api_point(left), api_point(right)
        if a and b:
            spacings.append(haversine_m(a[0], a[1], b[0], b[1]))

    if offsets and spacings:
        print(
            f"\n  aynı ad kayması : ortanca {statistics.median(offsets):.0f} m "
            f"(min {min(offsets):.0f}, max {max(offsets):.0f})"
        )
        print(
            f"  istasyon aralığı: ortanca {statistics.median(spacings):.0f} m "
            f"(min {min(spacings):.0f}, max {max(spacings):.0f})"
        )
        ratio = statistics.median(offsets) / statistics.median(spacings)
        print(f"  oran            : {ratio:.2f} istasyon aralığı")
        print(
            "\n  SONUÇ: adlar koordinatlardan yaklaşık bir istasyon kaymış. "
            "Ad listeleri doğru, koordinat ataması kaymış."
            if 0.6 <= ratio <= 1.6
            else "\n  SONUÇ: kayma bir istasyon aralığına denk gelmiyor."
        )

    # ---- is it a ring? ----
    print("\n=== Ring hat mı? ===")
    first, last = api_point(api[0]), api_point(api[-1])
    if first and last and spacings:
        closing = haversine_m(first[0], first[1], last[0], last[1])
        typical = statistics.median(spacings)
        print(f"  ilk ↔ son istasyon: {closing:.0f} m")
        print(f"  tipik aralık      : {typical:.0f} m")
        print(
            f"  → {'RING' if closing <= 2.5 * typical else 'doğrusal hat'} "
            f"(kapanma / aralık = {closing / typical:.2f})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
