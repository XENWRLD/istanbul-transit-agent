"""Second look at the endpoints that failed the first GET probe.

A 405 means the route exists but wants another verb, and a 400 usually means a
required parameter is missing -- neither is the same as "the service is down".
This script works out which, so the inventory records the real status instead
of a first-attempt failure.

    uv run python scripts/probe_failures.py
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx

from istanbul_agent.config import get_settings

METRO = "/MetroIstanbul/api/MetroMobile/V2"

# Endpoints that answered 405 to a GET: retry as POST, with and without a body.
POST_CANDIDATES = [
    ("metro-timetable", f"{METRO}/GetTimeTable"),
    ("metro-station-between-time", f"{METRO}/GetStationBetweenTime"),
    ("metro-faulty-equipment-details", f"{METRO}/GetFaultyEquipmentDetails"),
    ("metro-directions-by-line-and-station", f"{METRO}/GetDirectionsByLineIdAndStationId"),
    ("metro-directions", f"{METRO}/GetDirections"),
]

# Plausible bodies, tried in order until one returns 200 with data.
BODIES: list[dict[str, Any] | None] = [
    None,
    {},
    {"LineId": 1},
    {"LineId": 1, "StationId": 1},
    {"lineId": 1, "stationId": 1},
]


def show(label: str, response: httpx.Response, note: str = "") -> bool:
    body = response.text[:260].replace("\n", " ")
    allow = response.headers.get("allow", "")
    extra = f" allow={allow}" if allow else ""
    ok = response.status_code == 200 and body.strip() not in ("", "null")
    print(f"  {'OK  ' if ok else 'FAIL'} {response.status_code} {label}{extra} {note}")
    print(f"       {' '.join(body.split())[:200]}")
    return ok


def main() -> int:
    settings = get_settings()
    base = settings.ibb_api_base_url.rstrip("/")
    client = httpx.Client(timeout=settings.ibb_request_timeout, follow_redirects=True)
    findings: dict[str, Any] = {}

    print("=== 1. 405 alan Metro uç noktaları: POST denemesi ===")
    for slug, path in POST_CANDIDATES:
        print(f"\n{slug}")
        for body in BODIES:
            try:
                if body is None:
                    response = client.post(f"{base}{path}")
                    label = "POST (gövdesiz)"
                else:
                    response = client.post(f"{base}{path}", json=body)
                    label = f"POST {json.dumps(body)}"
            except Exception as exc:
                print(f"  ERR  {type(exc).__name__}: {exc}")
                continue
            if show(label, response):
                findings[slug] = {"method": "POST", "body": body, "status": 200}
                break

    print("\n=== 2. İsbike GetStationStatus: guid parametresi ===")
    all_stations = client.get(f"{base}/ispark-bike/GetAllStationStatus")
    print(f"  GetAllStationStatus -> {all_stations.status_code}")
    guid = None
    if all_stations.status_code == 200:
        data = all_stations.json()
        items = data.get("dataList") or []
        print(f"  dataList: {len(items)} kayıt")
        if items:
            print(f"  ilk kayıt alanları: {list(items[0])}")
            for key in ("guid", "Guid", "GUID", "id", "stationGuid"):
                if key in items[0]:
                    guid = items[0][key]
                    print(f"  guid alanı '{key}' = {guid}")
                    break
        else:
            print("  dataList BOŞ -> servis açık ama veri dönmüyor")
    for attempt in ([f"?guid={guid}"] if guid else []) + ["?guid=", ""]:
        response = client.get(f"{base}/ispark-bike/GetStationStatus{attempt}")
        show(f"GET {attempt or '(parametresiz)'}", response)

    print("\n=== 3. Yol bakım çalışmaları (teas): yol varyantları ===")
    for path in (
        "/teas/api/open_data",
        "/teas/api/open_data/",
        "/teas/api/open_data?date=2026-11-06",
        "/teas/api/OpenData",
        "/teas/api/open_data/GetRoadMaintenance",
        "/teas/api",
    ):
        try:
            response = client.get(f"{base}{path}")
            show(f"GET {path}", response)
        except Exception as exc:
            print(f"  ERR  {path}: {type(exc).__name__}")

    print("\n=== 4. Şehir Haritası API (406 alan) ===")
    for headers in (
        {"Accept": "text/html,application/xhtml+xml"},
        {"Accept": "*/*"},
    ):
        response = client.get("https://sehirharitasiapi.ibb.gov.tr/developer/", headers=headers)
        show(f"GET Accept={headers['Accept'][:28]}", response)

    print("\n=== Özet ===")
    print(
        json.dumps(findings, ensure_ascii=False, indent=2)
        if findings
        else "  (POST ile açılan uç nokta yok)"
    )
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
