"""Why do T3 and M7 produce same-name-different-place matches?

Five of the six suspicious matches are on T3, and `Rızapaşa` landed on
`Altıyol` at 29 m while `Altıyol` itself matched its namesake 763 m away.
That pattern says the names are shifted along the line rather than simply
imprecise, which would make any distance-only match on T3 wrong. M7's
`Yeşilpınar` is 1444 m out and needs its own look.

Prints both sequences side by side so the shift, if any, is visible.

    uv run python scripts/inspect_t3_m7.py
"""

from __future__ import annotations

import csv
import sys

import httpx

from istanbul_agent.config import PROJECT_ROOT, get_settings
from istanbul_agent.store.geo import haversine_m, parse_coordinate

GTFS_DIR = PROJECT_ROOT / "data" / "gtfs" / "public_transport"
METRO = "/MetroIstanbul/api/MetroMobile/V2"
LINES = ("T3", "M7")


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

    settings = get_settings()
    base = settings.ibb_api_base_url.rstrip("/")

    with httpx.Client(timeout=settings.ibb_request_timeout) as client:
        live_lines = client.get(f"{base}{METRO}/GetLines").json()["Data"]

        for name in LINES:
            route = next((r for r in routes if r["route_short_name"] == name), None)
            gtfs_sequence: list[dict[str, str]] = []
            if route is not None:
                best: list[dict[str, str]] = []
                for trip in (t for t in trips if t["route_id"] == route["route_id"]):
                    sequence = sorted(
                        times.get(trip["trip_id"], []),
                        key=lambda r: int(r["stop_sequence"] or 0),
                    )
                    if len(sequence) > len(best):
                        best = sequence
                gtfs_sequence = [stops[r["stop_id"]] for r in best if r["stop_id"] in stops]

            line = next((line for line in live_lines if line["Name"] == name), None)
            api_stations: list[dict] = []
            if line is not None:
                payload = client.get(f"{base}{METRO}/GetStationById/{line['Id']}").json()
                api_stations = sorted(payload.get("Data") or [], key=lambda s: s.get("Order") or 0)

            print(f"\n{'=' * 92}")
            print(
                f"{name}:  GTFS {len(gtfs_sequence)} durak   ·   canlı API {len(api_stations)} istasyon"
            )
            print(f"{'=' * 92}")
            print(f"{'#':>3}  {'GTFS durağı':32} {'canlı API istasyonu':32} {'aradaki mesafe':>14}")

            for index in range(max(len(gtfs_sequence), len(api_stations))):
                gtfs_stop = gtfs_sequence[index] if index < len(gtfs_sequence) else None
                api_station = api_stations[index] if index < len(api_stations) else None

                gtfs_name = gtfs_stop["stop_name"][:31] if gtfs_stop else "—"
                api_name = (
                    (api_station.get("Description") or api_station["Name"])[:31]
                    if api_station
                    else "—"
                )

                distance_label = "—"
                if gtfs_stop and api_station:
                    detail = api_station.get("DetailInfo") or {}
                    coords = (
                        parse_coordinate(gtfs_stop["stop_lat"]),
                        parse_coordinate(gtfs_stop["stop_lon"]),
                        parse_coordinate(detail.get("Latitude")),
                        parse_coordinate(detail.get("Longitude")),
                    )
                    if None not in coords:
                        distance_label = f"{haversine_m(*coords):.0f} m"  # type: ignore[arg-type]

                print(f"{index + 1:>3}  {gtfs_name:32} {api_name:32} {distance_label:>14}")

            # Is the GTFS name list the API list rotated or shifted?
            if gtfs_sequence and api_stations:
                gtfs_names = [s["stop_name"].strip().lower() for s in gtfs_sequence]
                api_names = [
                    (s.get("Description") or s["Name"]).strip().lower() for s in api_stations
                ]
                print("\n  Kayma kontrolü (GTFS adı, API'de kaç pozisyon ötede?):")
                for gtfs_index, gtfs_name in enumerate(gtfs_names):
                    if gtfs_name in api_names:
                        api_index = api_names.index(gtfs_name)
                        offset = api_index - gtfs_index
                        print(
                            f"    {gtfs_name[:30]:32} GTFS #{gtfs_index + 1} -> API #{api_index + 1} "
                            f"(kayma {offset:+d})"
                        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
