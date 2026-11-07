"""Confirm every station match by distance, not by name alone.

A name match is a hypothesis. Two stations can share a name and be different
places, and a renamed station keeps its location, so every pair the name
matcher produced is checked against the coordinates.

Three passes, in order:

1. **Line integrity.** For each GTFS station, which live station is actually
   closest, and does it carry the same name? A line where most names land on
   someone else's coordinates has its names scrambled relative to its
   positions, and no per-station rule can rescue it -- the whole line has to
   be refused. (T3 fails exactly this way.)
2. **Name matches.** Every name-based pair is measured. Over MATCH_RADIUS_M
   is reported loudly: those are the dangerous ones, since they would
   silently corrupt a duration.
3. **Open cases.** A station with no name match is accepted only if a
   name candidate exists *and* sits within the radius. No candidate, or a
   candidate too far away, means genuinely missing, so
   `get_rail_travel_time` falls back to its estimate. A station is never
   matched on distance alone.

    uv run python scripts/validate_station_matches.py
    uv run python scripts/validate_station_matches.py --radius 300
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from typing import Any

import httpx

from istanbul_agent.config import PROJECT_ROOT, get_settings
from istanbul_agent.store.geo import haversine_m, parse_coordinate
from istanbul_agent.store.station_names import (
    collapsed,
    normalize,
    similarity,
    token_key,
)

GTFS_DIR = PROJECT_ROOT / "data" / "gtfs" / "public_transport"
NAME_MAP = PROJECT_ROOT / "datasets" / "station_name_map.csv"
INTEGRITY = PROJECT_ROOT / "datasets" / "metro_line_integrity.csv"
OUT = PROJECT_ROOT / "data" / "station_match_validation.json"
METRO = "/MetroIstanbul/api/MetroMobile/V2"
GTFS_ENCODING = "cp1254"

# Two records this close apart are the same station. Istanbul metro stations
# sit several hundred metres apart at minimum, so a platform-centroid vs
# entrance-point disagreement between two sources stays well inside it.
MATCH_RADIUS_M = 300.0

# Below this name similarity we do not offer a candidate at all.
SUGGEST_THRESHOLD = 0.72

# A line whose names agree with their coordinates less often than this has
# its name list scrambled; refuse the whole line rather than guess.
INTEGRITY_THRESHOLD = 0.6


def read_csv(name: str) -> list[dict[str, str]]:
    with (GTFS_DIR / name).open(encoding=GTFS_ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def load_gtfs_metro() -> dict[str, dict[str, Any]]:
    """Metro routes with their stations in sequence, carrying coordinates."""
    agencies = {r["agency_id"]: r["agency_name"] for r in read_csv("agency.csv")}
    metro_ids = [aid for aid, name in agencies.items() if "Metro" in name]
    routes = {r["route_id"]: r for r in read_csv("routes.csv") if r["agency_id"] in metro_ids}
    stops = {r["stop_id"]: r for r in read_csv("stops.csv")}
    trips = [t for t in read_csv("trips.csv") if t["route_id"] in routes]

    times_by_trip: dict[str, list[dict[str, str]]] = {}
    for row in read_csv("stop_times.csv"):
        times_by_trip.setdefault(row["trip_id"], []).append(row)

    by_line: dict[str, dict[str, Any]] = {}
    for route in routes.values():
        best: list[dict[str, str]] = []
        for trip in (t for t in trips if t["route_id"] == route["route_id"]):
            sequence = sorted(
                times_by_trip.get(trip["trip_id"], []),
                key=lambda r: int(r["stop_sequence"] or 0),
            )
            if len(sequence) > len(best):
                best = sequence
        stations = []
        for row in best:
            stop = stops.get(row["stop_id"])
            if stop is None:
                continue
            stations.append(
                {
                    "stop_id": stop["stop_id"],
                    "name": stop["stop_name"],
                    "lat": parse_coordinate(stop["stop_lat"]),
                    "lon": parse_coordinate(stop["stop_lon"]),
                }
            )
        by_line[route["route_short_name"]] = {
            "route_id": route["route_id"],
            "stations": stations,
        }
    return by_line


def load_live(client: httpx.Client, base: str) -> dict[str, list[dict[str, Any]]]:
    """Current stations per line, in order, with coordinates from DetailInfo."""
    response = client.get(f"{base}{METRO}/GetLines")
    response.raise_for_status()
    out: dict[str, list[dict[str, Any]]] = {}
    for line in response.json()["Data"]:
        stations_response = client.get(f"{base}{METRO}/GetStationById/{line['Id']}")
        rows = []
        if stations_response.status_code == 200:
            payload = sorted(
                stations_response.json().get("Data") or [],
                key=lambda s: s.get("Order") or 0,
            )
            for station in payload:
                detail = station.get("DetailInfo") or {}
                rows.append(
                    {
                        "id": station["Id"],
                        "name": station.get("Description") or station["Name"],
                        "lat": parse_coordinate(detail.get("Latitude")),
                        "lon": parse_coordinate(detail.get("Longitude")),
                    }
                )
        out[line["Name"]] = rows
        time.sleep(0.2)
    return out


def distance_between(left: dict[str, Any], right: dict[str, Any]) -> float | None:
    if None in (left.get("lat"), left.get("lon"), right.get("lat"), right.get("lon")):
        return None
    return haversine_m(left["lat"], left["lon"], right["lat"], right["lon"])


def nearest(
    station: dict[str, Any], candidates: list[dict[str, Any]]
) -> tuple[dict | None, float | None]:
    best, best_distance = None, None
    for candidate in candidates:
        distance = distance_between(station, candidate)
        if distance is None:
            continue
        if best_distance is None or distance < best_distance:
            best, best_distance = candidate, distance
    return best, best_distance


def check_integrity(
    gtfs_stations: list[dict[str, Any]], api_stations: list[dict[str, Any]], radius: float
) -> dict[str, Any]:
    """Do the GTFS names sit on the coordinates the live API gives them?"""
    agree = disagree = unknown = 0
    examples: list[str] = []
    for stop in gtfs_stations:
        near, distance = nearest(stop, api_stations)
        if near is None or distance is None:
            unknown += 1
            continue
        if distance > radius:
            # Nothing of ours is nearby at all; that is a coverage gap, not a
            # naming defect, so it does not count either way.
            unknown += 1
            continue
        if normalize(stop["name"]) == normalize(near["name"]) or collapsed(
            stop["name"]
        ) == collapsed(near["name"]):
            agree += 1
        else:
            disagree += 1
            if len(examples) < 3:
                examples.append(f"{stop['name']} → {near['name']} ({distance:.0f}m)")

    checked = agree + disagree
    score = agree / checked if checked else None
    return {
        "checked": checked,
        "agree": agree,
        "disagree": disagree,
        "unknown": unknown,
        "score": round(score, 3) if score is not None else None,
        "reliable": score is None or score >= INTEGRITY_THRESHOLD,
        "examples": examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radius", type=float, default=MATCH_RADIUS_M)
    args = parser.parse_args()
    radius = args.radius

    settings = get_settings()
    base = settings.ibb_api_base_url.rstrip("/")

    gtfs = load_gtfs_metro()
    with httpx.Client(timeout=settings.ibb_request_timeout) as client:
        live = load_live(client, base)

    # ---------------- pass 1: line integrity ----------------
    print(f"=== 1. Hat bütünlüğü: GTFS adları doğru koordinatta mı? (yarıçap {radius:.0f} m) ===")
    print(f"{'Hat':6} {'kontrol':>8} {'uyumlu':>7} {'UYUMSUZ':>8} {'skor':>6}  durum")

    integrity: dict[str, dict[str, Any]] = {}
    for line in sorted(live):
        gtfs_line = gtfs.get(line)
        if gtfs_line is None or not gtfs_line["stations"]:
            integrity[line] = {
                "checked": 0,
                "agree": 0,
                "disagree": 0,
                "unknown": 0,
                "score": None,
                "reliable": False,
                "examples": [],
                "note": "GTFS'te hat yok",
            }
            print(f"{line:6} {'—':>8} {'—':>7} {'—':>8} {'—':>6}  GTFS'te hat yok")
            continue
        result = check_integrity(gtfs_line["stations"], live[line], radius)
        integrity[line] = result
        label = "güvenilir" if result["reliable"] else "❌ ADLAR KARIŞIK — HAT KULLANILAMAZ"
        score = f"{result['score']:.2f}" if result["score"] is not None else "—"
        print(
            f"{line:6} {result['checked']:>8} {result['agree']:>7} {result['disagree']:>8} "
            f"{score:>6}  {label}"
        )
        for example in result["examples"]:
            print(f"       ↳ {example}")

    unreliable = sorted(
        line
        for line, result in integrity.items()
        if not result["reliable"] and not result.get("note")
    )

    # ---------------- passes 2 and 3 ----------------
    name_matches: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []

    for line, api_stations in sorted(live.items()):
        gtfs_line = gtfs.get(line)
        gtfs_stations = gtfs_line["stations"] if gtfs_line else []
        line_reliable = integrity[line]["reliable"] and bool(gtfs_stations)

        by_norm = {normalize(s["name"]): s for s in gtfs_stations}
        by_collapsed = {collapsed(s["name"]): s for s in gtfs_stations}
        by_tokens = {token_key(s["name"]): s for s in gtfs_stations}

        for api_station in api_stations:
            if not line_reliable:
                resolved.append(
                    {
                        "line": line,
                        "api_station": api_station["name"],
                        "name_candidate": "",
                        "name_score": 0.0,
                        "candidate_distance_m": None,
                        "gtfs_stop_id": "",
                        "verdict": (
                            "HAT GÜVENİLMEZ (adlar karışık)"
                            if gtfs_stations
                            else "GERÇEKTEN EKSİK (hat GTFS'te yok)"
                        ),
                    }
                )
                continue

            hit = (
                by_norm.get(normalize(api_station["name"]))
                or by_collapsed.get(collapsed(api_station["name"]))
                or by_tokens.get(token_key(api_station["name"]))
            )

            if hit is not None:
                distance = distance_between(api_station, hit)
                name_matches.append(
                    {
                        "line": line,
                        "api_station": api_station["name"],
                        "gtfs_station": hit["name"],
                        "gtfs_stop_id": hit["stop_id"],
                        "distance_m": round(distance, 1) if distance is not None else None,
                        "verdict": (
                            "koordinat yok"
                            if distance is None
                            else "doğrulandı"
                            if distance <= radius
                            else "ŞÜPHELİ"
                        ),
                    }
                )
                continue

            best_candidate, best_score = None, 0.0
            for candidate in gtfs_stations:
                score = similarity(api_station["name"], candidate["name"])
                if score > best_score:
                    best_candidate, best_score = candidate, score

            candidate = best_candidate if best_score >= SUGGEST_THRESHOLD else None
            candidate_distance = distance_between(api_station, candidate) if candidate else None
            near, near_distance = nearest(api_station, gtfs_stations)

            if (
                candidate is not None
                and candidate_distance is not None
                and candidate_distance <= radius
            ):
                verdict = "EŞLEŞTİ (ad adayı + mesafe onayladı)"
                stop_id = candidate["stop_id"]
            elif candidate is not None:
                verdict = "REDDEDİLDİ (ad benziyor, mesafe uzak)"
                stop_id = ""
            else:
                verdict = "GERÇEKTEN EKSİK"
                stop_id = ""

            resolved.append(
                {
                    "line": line,
                    "api_station": api_station["name"],
                    "name_candidate": candidate["name"] if candidate else "",
                    "name_score": round(best_score, 3),
                    "candidate_distance_m": (
                        round(candidate_distance, 1) if candidate_distance is not None else None
                    ),
                    "nearest_gtfs": near["name"] if near else "",
                    "nearest_distance_m": (
                        round(near_distance, 1) if near_distance is not None else None
                    ),
                    "gtfs_stop_id": stop_id,
                    "verdict": verdict,
                }
            )

    suspicious = [r for r in name_matches if r["verdict"] == "ŞÜPHELİ"]
    verified = [r for r in name_matches if r["verdict"] == "doğrulandı"]

    print("\n\n=== 2. Ad bazlı eşleşmelerin mesafe kontrolü ===")
    print(f"  doğrulandı : {len(verified)}")
    print(f"  ŞÜPHELİ    : {len(suspicious)}")
    if suspicious:
        print(f"\n  ⚠️  {radius:.0f} m ÜSTÜNDE EŞLEŞENLER — ad aynı, yer farklı:")
        for row in sorted(suspicious, key=lambda r: -(r["distance_m"] or 0)):
            print(
                f"    {row['line']:6} {row['api_station'][:28]:30} ↔ "
                f"{row['gtfs_station'][:28]:30} {row['distance_m']:>8.0f}m"
            )

    print("\n\n=== 3. Açık kalan istasyonlar ===")
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in resolved:
        groups.setdefault(row["verdict"], []).append(row)

    for verdict in sorted(groups, key=lambda v: (v.startswith("GERÇEKTEN"), v)):
        rows = groups[verdict]
        print(f"\n  {verdict}  ({len(rows)})")
        show = rows if len(rows) <= 6 or not verdict.startswith(("GERÇEKTEN", "HAT")) else rows[:6]
        for row in show:
            if verdict.startswith("EŞLEŞTİ") or verdict.startswith("REDDEDİLDİ"):
                print(
                    f"    {row['line']:6} {row['api_station'][:28]:30} ~ "
                    f"{row['name_candidate'][:26]:28} ad={row['name_score']:.2f} "
                    f"mesafe={row['candidate_distance_m']:.0f}m"
                )
            else:
                print(f"    {row['line']:6} {row['api_station']}")
        if len(show) < len(rows):
            print(f"    ... (+{len(rows) - len(show)} daha)")

    # ---------------- write outputs ----------------
    rows_out: list[dict[str, str]] = []
    for row in name_matches:
        rows_out.append(
            {
                "line": row["line"],
                "api_station": row["api_station"],
                "gtfs_station": row["gtfs_station"],
                "gtfs_stop_id": row["gtfs_stop_id"],
                "name_score": "1.000",
                "distance_m": f"{row['distance_m']:.1f}" if row["distance_m"] is not None else "",
                "verdict": row["verdict"],
                "decision": "AD EŞLEŞMESİ"
                if row["verdict"] == "doğrulandı"
                else "ŞÜPHELİ — KULLANMA",
            }
        )
    for row in resolved:
        distance = row["candidate_distance_m"]
        if distance is None:
            distance = row.get("nearest_distance_m")
        rows_out.append(
            {
                "line": row["line"],
                "api_station": row["api_station"],
                "gtfs_station": row["name_candidate"] or row.get("nearest_gtfs", ""),
                "gtfs_stop_id": row["gtfs_stop_id"],
                "name_score": f"{row['name_score']:.3f}",
                "distance_m": f"{distance:.1f}" if distance is not None else "",
                "verdict": row["verdict"],
                "decision": (
                    "ELLE EŞLEME"
                    if row["verdict"].startswith("EŞLEŞTİ")
                    else "HAT GÜVENİLMEZ"
                    if row["verdict"].startswith("HAT")
                    else "GERÇEKTEN EKSİK"
                ),
            }
        )

    with NAME_MAP.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "line",
                "api_station",
                "gtfs_station",
                "gtfs_stop_id",
                "name_score",
                "distance_m",
                "verdict",
                "decision",
            ],
        )
        writer.writeheader()
        writer.writerows(sorted(rows_out, key=lambda r: (r["line"], r["api_station"])))

    with INTEGRITY.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["line", "checked", "agree", "disagree", "score", "reliable", "note"],
        )
        writer.writeheader()
        for line in sorted(integrity):
            result = integrity[line]
            writer.writerow(
                {
                    "line": line,
                    "checked": result["checked"],
                    "agree": result["agree"],
                    "disagree": result["disagree"],
                    "score": result["score"] if result["score"] is not None else "",
                    "reliable": "evet" if result["reliable"] else "hayır",
                    "note": result.get("note", "")
                    or ("adlar koordinatlarla uyuşmuyor" if not result["reliable"] else ""),
                }
            )

    OUT.write_text(
        json.dumps(
            {
                "validated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "match_radius_m": radius,
                "integrity_threshold": INTEGRITY_THRESHOLD,
                "line_integrity": integrity,
                "unreliable_lines": unreliable,
                "name_matches": name_matches,
                "resolved": resolved,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    recovered = sum(1 for r in resolved if r["verdict"].startswith("EŞLEŞTİ"))
    print("\n\n=== Özet ===")
    print(f"  ad eşleşmesi mesafeyle doğrulandı : {len(verified)}/{len(name_matches)}")
    print(f"  ŞÜPHELİ ad eşleşmesi              : {len(suspicious)}")
    print(f"  ad adayı + mesafe ile kurtarılan   : {recovered}")
    print(f"  güvenilmez hat                     : {len(unreliable)} {unreliable or ''}")
    print(f"\n-> {NAME_MAP.relative_to(PROJECT_ROOT)}")
    print(f"-> {INTEGRITY.relative_to(PROJECT_ROOT)}")
    print(f"-> {OUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
