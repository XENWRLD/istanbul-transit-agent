"""Scope analysis: how much of today's metro network does the 2020 GTFS cover?

`get_next_departures` (metro) and `get_rail_travel_time` have to fall back to
the Public Transport GTFS feed (dataset 308) because the live Metro endpoints
are broken server-side. That feed's metro data is dated 09.07.2020 and will
not be updated, so before building on it we need to know exactly what it is
missing: which lines are absent, which are present but short of their current
extensions, and which stations cannot be matched by name at all.

Compares the GTFS feed against the live GetLines / GetStationById responses
and writes:

    datasets/station_name_map.csv      unmatched + fuzzy pairs for human review
    data/metro_coverage.json           the full machine-readable comparison

    uv run python scripts/analyze_metro_coverage.py
"""

from __future__ import annotations

import csv
import json
import sys
import time
from collections import defaultdict
from typing import Any

import httpx

from istanbul_agent.config import PROJECT_ROOT, get_settings
from istanbul_agent.store.station_names import collapsed, normalize, similarity

GTFS_DIR = PROJECT_ROOT / "data" / "gtfs" / "public_transport"
OUT_JSON = PROJECT_ROOT / "data" / "metro_coverage.json"
NAME_MAP = PROJECT_ROOT / "datasets" / "station_name_map.csv"
METRO = "/MetroIstanbul/api/MetroMobile/V2"

# The IBB GTFS CSVs are Windows-1254 (Turkish), not UTF-8.
GTFS_ENCODING = "cp1254"

# Agencies in this feed that run rail. Metro İstanbul is the one we compare
# against the API; the others are listed so the report can say what else the
# feed carries.
RAIL_ROUTE_TYPES = {
    "0": "tramvay",
    "1": "metro",
    "2": "tren",
    "4": "vapur",
    "5": "teleferik",
    "6": "teleferik",
    "7": "funikuler",
}

# A fuzzy pair at or above this score is offered as a suggestion; below it the
# station is reported as genuinely absent.
SUGGEST_THRESHOLD = 0.72


def read_csv(name: str) -> list[dict[str, str]]:
    path = GTFS_DIR / name
    with path.open(encoding=GTFS_ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def fetch_live(client: httpx.Client, base: str) -> tuple[list[dict], dict[int, list[dict]]]:
    """Current lines, and the stations on each, from the working endpoints."""
    response = client.get(f"{base}{METRO}/GetLines")
    response.raise_for_status()
    lines = response.json()["Data"]

    stations: dict[int, list[dict]] = {}
    for line in lines:
        response = client.get(f"{base}{METRO}/GetStationById/{line['Id']}")
        if response.status_code != 200:
            stations[line["Id"]] = []
            continue
        payload = response.json()
        stations[line["Id"]] = payload.get("Data") or []
        time.sleep(0.25)
    return lines, stations


def main() -> int:
    if not (GTFS_DIR / "routes.csv").exists():
        print("GTFS indirilmemiş. Önce: uv run python scripts/download_gtfs.py")
        return 1

    settings = get_settings()
    base = settings.ibb_api_base_url.rstrip("/")

    agencies = {row["agency_id"]: row["agency_name"] for row in read_csv("agency.csv")}
    routes = read_csv("routes.csv")
    trips = read_csv("trips.csv")
    stops = {row["stop_id"]: row for row in read_csv("stops.csv")}
    stop_times = read_csv("stop_times.csv")
    calendar = read_csv("calendar.csv")

    print("=== GTFS beslemesi (veri seti 308) ===")
    print(f"  ajans      {len(agencies)}: {', '.join(agencies.values())}")
    print(f"  hat        {len(routes)}")
    print(f"  sefer      {len(trips)}")
    print(f"  durak      {len(stops)}")
    print(f"  stop_times {len(stop_times)}")

    # --- calendar validity: is this feed even in its service window? ---
    print("\n=== calendar.csv geçerlilik penceresi ===")
    starts = sorted({row["start_date"] for row in calendar})
    ends = sorted({row["end_date"] for row in calendar})
    today = time.strftime("%Y%m%d")
    print(f"  start_date: {starts[0]} .. {starts[-1]}")
    print(f"  end_date:   {ends[0]} .. {ends[-1]}")
    expired = max(ends) < today
    print(f"  bugün {today} -> besleme {'SÜRESİ GEÇMİŞ' if expired else 'geçerli'}")

    # --- which agency owns the metro routes ---
    metro_agency_ids = [aid for aid, name in agencies.items() if "Metro" in name]
    print(f"\n=== Metro İstanbul ajansı: {metro_agency_ids} ===")

    by_agency: dict[str, list[dict]] = defaultdict(list)
    for route in routes:
        by_agency[route.get("agency_id") or "(boş)"].append(route)
    for agency_id, agency_routes in sorted(by_agency.items(), key=lambda kv: -len(kv[1])):
        label = agencies.get(agency_id) or agency_id
        types = {
            RAIL_ROUTE_TYPES.get(r["route_type"], f"tip {r['route_type']}") for r in agency_routes
        }
        print(f"  {label:28} {len(agency_routes):4} hat  tipler: {', '.join(sorted(types))}")

    metro_routes = [r for r in routes if r["agency_id"] in metro_agency_ids]
    print(f"\n  Metro İstanbul hatları: {len(metro_routes)}")

    # Marmaray runs under TCDD, not Metro İstanbul, and the live GetLines list
    # does not include it. Report it so the gap is visible, but keep it out of
    # the line-by-line comparison.
    tcdd_routes = [
        r for r in routes if (agencies.get(r["agency_id"]) or "").upper().startswith("TCDD")
    ]
    if tcdd_routes:
        print(f"  TCDD (Marmaray) hatları: {len(tcdd_routes)} — canlı Metro API'sinde yok")
        for route in tcdd_routes:
            print(f"    {route['route_short_name']:10} {route['route_long_name'][:60]}")

    # --- stations per GTFS route, in stop_sequence order ---
    trips_by_route: dict[str, list[dict]] = defaultdict(list)
    for trip in trips:
        trips_by_route[trip["route_id"]].append(trip)

    times_by_trip: dict[str, list[dict]] = defaultdict(list)
    for row in stop_times:
        times_by_trip[row["trip_id"]].append(row)

    def route_stations(route_id: str) -> list[dict[str, str]]:
        """The longest stop sequence across the route's trips."""
        best: list[dict] = []
        for trip in trips_by_route.get(route_id, []):
            sequence = sorted(
                times_by_trip.get(trip["trip_id"], []),
                key=lambda r: int(r["stop_sequence"] or 0),
            )
            if len(sequence) > len(best):
                best = sequence
        return [stops[r["stop_id"]] for r in best if r["stop_id"] in stops]

    gtfs_lines: dict[str, dict[str, Any]] = {}
    for route in metro_routes:
        station_rows = route_stations(route["route_id"])
        gtfs_lines[route["route_id"]] = {
            "route_id": route["route_id"],
            "short_name": route["route_short_name"],
            "long_name": route["route_long_name"],
            "route_type": route["route_type"],
            "trip_count": len(trips_by_route.get(route["route_id"], [])),
            "stations": [
                {"stop_id": s["stop_id"], "name": s["stop_name"], "norm": normalize(s["stop_name"])}
                for s in station_rows
            ],
        }

    print("\n=== GTFS'teki Metro İstanbul hatları ===")
    for info in sorted(gtfs_lines.values(), key=lambda i: i["short_name"]):
        kind = RAIL_ROUTE_TYPES.get(info["route_type"], info["route_type"])
        print(
            f"  {info['short_name']:8} {kind:10} {len(info['stations']):3} istasyon  "
            f"{info['trip_count']:4} sefer  {info['long_name'][:48]}"
        )

    # --- live network ---
    # The feed splits some lines into branch routes the live API does not list
    # separately (M2A, M3A, F2, F3). A station "missing" from M2 may well sit
    # on M2A, so keep a network-wide index to tell a real gap from a station
    # filed under a sibling route.
    network_index: dict[str, list[str]] = defaultdict(list)
    for info in gtfs_lines.values():
        for station in info["stations"]:
            for key in {station["norm"], collapsed(station["name"])}:
                if info["short_name"] not in network_index[key]:
                    network_index[key].append(info["short_name"])

    print("\n=== Canlı API'den bugünkü ağ ===")
    with httpx.Client(timeout=settings.ibb_request_timeout) as client:
        live_lines, live_stations = fetch_live(client, base)
    print(f"  {len(live_lines)} hat, {sum(len(v) for v in live_stations.values())} istasyon kaydı")

    # --- compare line by line ---
    gtfs_by_short = {normalize(info["short_name"]): info for info in gtfs_lines.values()}

    comparison: list[dict[str, Any]] = []
    unmatched_rows: list[dict[str, str]] = []

    for line in sorted(live_lines, key=lambda line: line["Name"]):
        live_name = line["Name"]
        api_stations = live_stations.get(line["Id"], [])
        gtfs_info = gtfs_by_short.get(normalize(live_name))

        if gtfs_info is None:
            comparison.append(
                {
                    "line": live_name,
                    "line_id": line["Id"],
                    "api_station_count": len(api_stations),
                    "gtfs_station_count": 0,
                    "coverage": "yok",
                    "matched": [],
                    "missing": [s["Description"] or s["Name"] for s in api_stations],
                    "extra_in_gtfs": [],
                }
            )
            for station in api_stations:
                api_name = station["Description"] or station["Name"]
                key = normalize(api_name)
                elsewhere = network_index.get(key) or network_index.get(collapsed(api_name)) or []
                unmatched_rows.append(
                    {
                        "line": live_name,
                        "api_station": api_name,
                        "api_station_raw": station["Name"],
                        "api_norm": key,
                        "gtfs_candidate": "",
                        "gtfs_norm": "",
                        "score": "",
                        "on_other_routes": ", ".join(elsewhere),
                        "reason": (
                            f"hat GTFS'te yok; istasyon {', '.join(elsewhere)} hattında var"
                            if elsewhere
                            else "hat GTFS'te yok"
                        ),
                        "decision": "",
                    }
                )
            continue

        # Two keys per GTFS station: the normalised name, and the same with
        # spaces removed for sources that disagree on word boundaries.
        gtfs_norms = {s["norm"]: s for s in gtfs_info["stations"]}
        gtfs_collapsed = {collapsed(s["name"]): s for s in gtfs_info["stations"]}
        matched: list[dict[str, str]] = []
        missing: list[dict[str, Any]] = []

        for station in api_stations:
            api_name = station["Description"] or station["Name"]
            key = normalize(api_name)
            hit = gtfs_norms.get(key) or gtfs_collapsed.get(collapsed(api_name))
            if hit is not None:
                matched.append(
                    {
                        "api": api_name,
                        "gtfs": hit["name"],
                        "via": "ad" if key in gtfs_norms else "boşluksuz",
                    }
                )
                continue
            # Not on this route. Is it anywhere in the feed's metro network?
            elsewhere = network_index.get(key) or network_index.get(collapsed(api_name)) or []

            # Otherwise offer the closest GTFS station on this line.
            best_name, best_score = "", 0.0
            for candidate in gtfs_info["stations"]:
                score = similarity(api_name, candidate["name"])
                if score > best_score:
                    best_name, best_score = candidate["name"], score

            if elsewhere:
                reason = f"GTFS'te var ama başka hatta: {', '.join(elsewhere)}"
            elif best_score >= SUGGEST_THRESHOLD:
                reason = "benzer ad bulundu, onay gerekiyor"
            else:
                reason = "GTFS'te karşılığı yok"

            missing.append(
                {
                    "api": api_name,
                    "suggestion": best_name,
                    "score": round(best_score, 3),
                    "on_other_routes": elsewhere,
                }
            )
            unmatched_rows.append(
                {
                    "line": live_name,
                    "api_station": api_name,
                    "api_station_raw": station["Name"],
                    "api_norm": key,
                    "gtfs_candidate": best_name if best_score >= SUGGEST_THRESHOLD else "",
                    "gtfs_norm": normalize(best_name) if best_score >= SUGGEST_THRESHOLD else "",
                    "score": f"{best_score:.3f}",
                    "on_other_routes": ", ".join(elsewhere),
                    "reason": reason,
                    "decision": "",
                }
            )

        api_norms = {normalize(s["Description"] or s["Name"]) for s in api_stations}
        api_collapsed = {collapsed(s["Description"] or s["Name"]) for s in api_stations}
        extra = [
            s["name"]
            for s in gtfs_info["stations"]
            if s["norm"] not in api_norms and collapsed(s["name"]) not in api_collapsed
        ]

        if not missing:
            coverage = "tam"
        elif len(matched) >= 2:
            coverage = "kısmen"
        else:
            coverage = "yok"

        comparison.append(
            {
                "line": live_name,
                "line_id": line["Id"],
                "api_station_count": len(api_stations),
                "gtfs_station_count": len(gtfs_info["stations"]),
                "gtfs_route_id": gtfs_info["route_id"],
                "gtfs_trip_count": gtfs_info["trip_count"],
                "coverage": coverage,
                "matched": matched,
                "missing": missing,
                "extra_in_gtfs": extra,
            }
        )

    # --- report ---
    print("\n" + "=" * 78)
    print("HAT BAZINDA KAPSAM")
    print("=" * 78)
    print(f"{'Hat':7} {'Kapsam':8} {'API':>4} {'GTFS':>5} {'Eşleşen':>8} {'Eksik':>6}  Not")
    for row in comparison:
        note = ""
        if row["coverage"] == "yok" and row["gtfs_station_count"] == 0:
            note = "GTFS'te bu hat hiç yok"
        elif row["missing"]:
            names = ", ".join(m["api"] if isinstance(m, dict) else m for m in row["missing"][:3])
            note = f"eksik: {names}"
            if len(row["missing"]) > 3:
                note += f" (+{len(row['missing']) - 3})"
        if row["extra_in_gtfs"]:
            note += f" | GTFS fazlası: {', '.join(row['extra_in_gtfs'][:2])}"
        print(
            f"{row['line']:7} {row['coverage']:8} {row['api_station_count']:4} "
            f"{row['gtfs_station_count']:5} {len(row['matched']):8} {len(row['missing']):6}  {note[:70]}"
        )

    tally = defaultdict(int)
    for row in comparison:
        tally[row["coverage"]] += 1
    print(f"\n  tam: {tally['tam']}   kısmen: {tally['kısmen']}   yok: {tally['yok']}")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(
            {
                "analyzed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "gtfs_feed": "public_transport (veri seti 308)",
                "gtfs_encoding": GTFS_ENCODING,
                "calendar_window": {"start": starts[0], "end": max(ends), "expired": expired},
                "gtfs_agencies": agencies,
                "metro_agency_ids": metro_agency_ids,
                "gtfs_metro_routes": list(gtfs_lines.values()),
                "comparison": comparison,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    NAME_MAP.parent.mkdir(parents=True, exist_ok=True)
    with NAME_MAP.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "line",
                "api_station",
                "api_station_raw",
                "api_norm",
                "gtfs_candidate",
                "gtfs_norm",
                "score",
                "on_other_routes",
                "reason",
                "decision",
            ],
        )
        writer.writeheader()
        writer.writerows(unmatched_rows)

    print(f"\n{len(unmatched_rows)} eşleşmeyen istasyon -> {NAME_MAP.relative_to(PROJECT_ROOT)}")
    print(f"Tam karşılaştırma -> {OUT_JSON.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
