"""Two blockers from the worked examples, examined before being fixed.

1. **Marmaray.** Kadıköy to Taksim is the project's headline question and it
   cannot be answered, because the only rail interchange between M4 and M2
   runs through Marmaray -- which sits in the same GTFS feed under TCDD
   rather than Metro İstanbul, so the ingest's agency filter drops it. Check
   what the feed holds for it and where it meets the metro lines.
2. **Sultanbeyli's missing coordinates.** M5's last three stations have none,
   which is why that journey is refused. Find out whether the live API omits
   the field or returns it empty, and whether the rail-station GeoJSON
   (dataset 6) can supply them.

    uv run python scripts/inspect_marmaray_and_coords.py
"""

from __future__ import annotations

import json
import sys
from itertools import pairwise

import httpx

from istanbul_agent.config import get_settings
from istanbul_agent.console import enable_unicode_output
from istanbul_agent.store.gtfs import GtfsFeed, to_seconds
from istanbul_agent.store.station_names import normalize

METRO_API = "/MetroIstanbul/api/MetroMobile/V2"
MISSING = ("Veysel Karani", "Hasanpaşa", "Sultanbeyli")


def main() -> int:
    settings = get_settings()
    base = settings.ibb_api_base_url.rstrip("/")

    # ---------------- 1. Marmaray in the feed ----------------
    print("=" * 80)
    print("1. GTFS'te TCDD / Marmaray")
    print("=" * 80)

    feed = GtfsFeed.load("public_transport", agency_filter="TCDD")
    print(f"  ajanslar: {feed.agencies}")
    print(f"  {len(feed.routes)} hat, {len(feed.trips)} sefer, {len(feed.stops)} durak\n")

    for route in feed.routes:
        sequence = feed.longest_trip_sequence(route["route_id"])
        stations = [feed.stops[r["stop_id"]] for r in sequence if r["stop_id"] in feed.stops]
        trips = feed.trips_for_route(route["route_id"])
        print(
            f"  {route['route_short_name']:12} tip={route['route_type']} "
            f"{len(stations):>3} istasyon {len(trips):>5} sefer  "
            f"{route['route_long_name'][:40]}"
        )
        if stations:
            print(f"      ilk: {stations[0]['stop_name']}  ...  son: {stations[-1]['stop_name']}")

    # Are consecutive durations usable?
    main_route = max(feed.routes, key=lambda r: len(feed.longest_trip_sequence(r["route_id"])))
    sequence = feed.longest_trip_sequence(main_route["route_id"])
    print(f"\n  {main_route['route_short_name']} sıralı istasyonlar ve süreler:")
    usable = 0
    for left, right in pairwise(sequence):
        depart = to_seconds(left["departure_time"])
        arrive = to_seconds(right["arrival_time"])
        left_name = feed.stops.get(left["stop_id"], {}).get("stop_name", "?")
        right_name = feed.stops.get(right["stop_id"], {}).get("stop_name", "?")
        delta = (arrive - depart) if (depart is not None and arrive is not None) else None
        if delta is not None and 20 <= delta <= 900:
            usable += 1
        print(
            f"    {left_name[:24]:26} → {right_name[:24]:26} {delta if delta is not None else '—'}"
        )
    print(f"\n  kullanılabilir segment: {usable}/{len(sequence) - 1}")

    # ---------------- where does it meet the metro? ----------------
    print("\n  Metro ağıyla ortak istasyon adları:")
    metro = GtfsFeed.load("public_transport", agency_filter="Metro")
    metro_names: dict[str, list[str]] = {}
    for route in metro.routes:
        line = route["route_short_name"].strip()
        for row in metro.longest_trip_sequence(route["route_id"]):
            stop = metro.stops.get(row["stop_id"])
            if stop:
                metro_names.setdefault(normalize(stop["stop_name"]), []).append(line)

    marmaray_names = {
        normalize(feed.stops[r["stop_id"]]["stop_name"])
        for route in feed.routes
        for r in feed.longest_trip_sequence(route["route_id"])
        if r["stop_id"] in feed.stops
    }
    shared = sorted(marmaray_names & set(metro_names))
    for name in shared:
        print(f"    {name:28} metro hatları: {', '.join(sorted(set(metro_names[name])))}")
    print(f"  {len(shared)} ortak istasyon")

    # ---------------- 2. missing coordinates ----------------
    print("\n" + "=" * 80)
    print("2. M5'in son istasyonlarında koordinat")
    print("=" * 80)

    with httpx.Client(timeout=settings.ibb_request_timeout) as client:
        lines = client.get(f"{base}{METRO_API}/GetLines").json()["Data"]
        m5 = next(line for line in lines if line["Name"] == "M5")
        payload = client.get(f"{base}{METRO_API}/GetStationById/{m5['Id']}").json()

    stations = sorted(payload.get("Data") or [], key=lambda s: s.get("Order") or 0)
    print(f"  M5: {len(stations)} istasyon\n")
    print("  Ham DetailInfo (son 5 istasyon):")
    for station in stations[-5:]:
        detail = station.get("DetailInfo")
        name = station.get("Description") or station["Name"]
        print(f"    {name[:28]:30} DetailInfo={json.dumps(detail, ensure_ascii=False)}")

    print("\n  Eksik koordinatlı istasyonların tüm alanları:")
    for station in stations:
        name = station.get("Description") or station["Name"]
        if any(normalize(target) == normalize(name) for target in MISSING):
            print(f"    --- {name} ---")
            print(f"    {json.dumps(station, ensure_ascii=False, indent=6)[:700]}")

    # Does GetStations (the whole-network endpoint) have them?
    print("\n  GetStations (tüm ağ) aynı istasyonlar için ne diyor?")
    with httpx.Client(timeout=settings.ibb_request_timeout) as client:
        everything = client.get(f"{base}{METRO_API}/GetStations").json()
    for station in everything.get("Data") or []:
        name = station.get("Description") or station.get("Name") or ""
        if any(normalize(target) == normalize(name) for target in MISSING):
            detail = station.get("DetailInfo") or {}
            print(
                f"    {name[:28]:30} line={station.get('LineName')} "
                f"lat={detail.get('Latitude')} lon={detail.get('Longitude')}"
            )
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
