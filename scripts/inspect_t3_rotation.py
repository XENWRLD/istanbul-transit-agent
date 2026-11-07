"""Confirm the T3 defect: do its GTFS names sit on the wrong coordinates?

Two observations from the side-by-side listing do not fit mere imprecision:

    GTFS "İskele Cami"   is 5 m from the API's "Kadıköy İDO"
    GTFS "Moda İlkokulu" is 29 m from the API's "Kilise"

while each of those GTFS names matched its own namesake 500-800 m away. That
is the signature of names shifted along the line relative to their positions,
not of a coarse survey. If true, T3's stop_times cannot be trusted for
station-to-station durations, because the name a caller asks for is attached
to a different place.

For every GTFS T3 stop this prints the nearest API station by distance and
the offset between their positions in the two sequences. A constant offset
confirms the shift.

    uv run python scripts/inspect_t3_rotation.py
"""

from __future__ import annotations

import csv
import sys
from collections import Counter

import httpx

from istanbul_agent.config import PROJECT_ROOT, get_settings
from istanbul_agent.store.geo import haversine_m, parse_coordinate

GTFS_DIR = PROJECT_ROOT / "data" / "gtfs" / "public_transport"
METRO = "/MetroIstanbul/api/MetroMobile/V2"
LINE = "T3"


def read_csv(name: str) -> list[dict[str, str]]:
    with (GTFS_DIR / name).open(encoding="cp1254", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    routes = read_csv("routes.csv")
    stops = {r["stop_id"]: r for r in read_csv("stops.csv")}
    trips = read_csv("trips.csv")
    times: dict[str, list[dict[str, str]]] = {}
    for row in read_csv("stop_times.csv"):
        times.setdefault(row["trip_id"], []).append(row)

    route = next(r for r in routes if r["route_short_name"] == LINE)
    best: list[dict[str, str]] = []
    for trip in (t for t in trips if t["route_id"] == route["route_id"]):
        sequence = sorted(
            times.get(trip["trip_id"], []), key=lambda r: int(r["stop_sequence"] or 0)
        )
        if len(sequence) > len(best):
            best = sequence
    gtfs_stops = [stops[r["stop_id"]] for r in best if r["stop_id"] in stops]

    settings = get_settings()
    base = settings.ibb_api_base_url.rstrip("/")
    with httpx.Client(timeout=settings.ibb_request_timeout) as client:
        line = next(
            line
            for line in client.get(f"{base}{METRO}/GetLines").json()["Data"]
            if line["Name"] == LINE
        )
        api_stations = sorted(
            client.get(f"{base}{METRO}/GetStationById/{line['Id']}").json()["Data"],
            key=lambda s: s.get("Order") or 0,
        )

    api_points = []
    for index, station in enumerate(api_stations):
        detail = station.get("DetailInfo") or {}
        lat = parse_coordinate(detail.get("Latitude"))
        lon = parse_coordinate(detail.get("Longitude"))
        if lat is not None and lon is not None:
            api_points.append(
                {
                    "index": index,
                    "name": station.get("Description") or station["Name"],
                    "lat": lat,
                    "lon": lon,
                }
            )

    print(f"GTFS {LINE}: {len(gtfs_stops)} durak   ·   canlı API: {len(api_points)} istasyon\n")
    print(
        f"{'GTFS #':>6} {'GTFS adı':24} {'en yakın API istasyonu':24} {'mesafe':>8} "
        f"{'API #':>6} {'kayma':>6}  ad aynı mı?"
    )

    offsets: Counter[int] = Counter()
    same_name = 0

    for gtfs_index, stop in enumerate(gtfs_stops):
        lat = parse_coordinate(stop["stop_lat"])
        lon = parse_coordinate(stop["stop_lon"])
        if lat is None or lon is None:
            continue
        nearest = min(
            api_points, key=lambda point: haversine_m(lat, lon, point["lat"], point["lon"])
        )
        distance = haversine_m(lat, lon, nearest["lat"], nearest["lon"])
        offset = nearest["index"] - gtfs_index
        offsets[offset] += 1
        names_agree = stop["stop_name"].strip().lower() == nearest["name"].strip().lower()
        same_name += names_agree
        print(
            f"{gtfs_index + 1:>6} {stop['stop_name'][:23]:24} {nearest['name'][:23]:24} "
            f"{distance:>7.0f}m {nearest['index'] + 1:>6} {offset:>+6}  "
            f"{'EVET' if names_agree else 'HAYIR'}"
        )

    print("\nKayma dağılımı (konuma göre en yakın API istasyonunun pozisyon farkı):")
    for offset, count in offsets.most_common():
        print(f"  kayma {offset:+d}: {count} durak")

    dominant, dominant_count = offsets.most_common(1)[0]
    print(
        f"\nKonuma göre eşleşen ama adı farklı olan durak: {len(gtfs_stops) - same_name}/{len(gtfs_stops)}"
    )
    if dominant != 0 and dominant_count >= len(gtfs_stops) * 0.6:
        print(
            f"SONUÇ: T3'te GTFS adları koordinatlara göre sabit {dominant:+d} pozisyon kaymış. "
            "Ad ile konum tutarsız; bu hat süre hesabı için kullanılamaz."
        )
    else:
        print("SONUÇ: sabit bir kayma yok; tutarsızlık dağınık.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
