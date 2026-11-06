"""Work out the required POST body for the Metro endpoints that answer 405 to GET.

Those endpoints return HTTP 200 with ``Success: false`` and an error message
naming the missing field, so the body can be derived instead of guessed: this
script walks candidate bodies built from ids harvested from the working list
endpoints (LineId, StationId, DirectionId, GroupName) and reports the first
one that yields real data. Successful responses are saved as fixtures.

    uv run python scripts/probe_post_params.py
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import httpx

from istanbul_agent.config import PROJECT_ROOT, get_settings

METRO = "/MetroIstanbul/api/MetroMobile/V2"
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "metro"

# Real values taken from the GetLines / GetStations / GetDirectionById fixtures.
LINE_ID = 4  # F1 Taksim-Kabataş funicular: only two stations, so responses stay small
LINE_ID_METRO = 1  # M2, a full metro line
STATION_ID = 188  # TAKSIM on line 4
DIRECTION_ID = 13  # Kabataş->Taksim
GROUP_NAMES = ["Yürüyen Merdiven", "Asansör", "Hat"]

# slug -> (path, [candidate bodies in order])
CANDIDATES: dict[str, tuple[str, list[dict[str, Any]]]] = {
    "metro-faulty-equipment-details": (
        f"{METRO}/GetFaultyEquipmentDetails",
        [{"EquipmentGroupName": name} for name in GROUP_NAMES]
        + [{"GroupName": name} for name in GROUP_NAMES],
    ),
    "metro-station-between-time": (
        f"{METRO}/GetStationBetweenTime",
        [
            {"LineId": LINE_ID},
            {"LineId": LINE_ID, "DirectionId": DIRECTION_ID},
            {"LineId": LINE_ID_METRO, "DirectionId": 1},
            {"LineId": LINE_ID_METRO},
            {"LineId": LINE_ID, "StationId": STATION_ID, "DirectionId": DIRECTION_ID},
        ],
    ),
    "metro-directions-by-line-and-station": (
        f"{METRO}/GetDirectionsByLineIdAndStationId",
        [
            {"LineId": LINE_ID, "StationId": STATION_ID},
            {"LineId": LINE_ID_METRO, "StationId": 20},
            {"lineId": LINE_ID, "stationId": STATION_ID},
        ],
    ),
    "metro-timetable": (
        f"{METRO}/GetTimeTable",
        [
            {"LineId": LINE_ID, "StationId": STATION_ID, "DirectionId": DIRECTION_ID},
            {"LineId": LINE_ID, "DirectionId": DIRECTION_ID},
            {
                "LineId": LINE_ID,
                "StationId": STATION_ID,
                "DirectionId": DIRECTION_ID,
                "DayType": 1,
            },
            {"LineId": LINE_ID_METRO, "StationId": 20, "DirectionId": 1},
            {"LineId": LINE_ID, "StationId": STATION_ID},
        ],
    ),
}


def save(slug: str, url: str, body: dict[str, Any], payload: Any) -> str:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    target = FIXTURES / f"{slug}.json"
    document = {
        "_fixture_meta": {
            "source_url": url,
            "http_method": "POST",
            "request_body": body,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "http_status": "200",
        },
        "response": payload,
    }
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target.relative_to(PROJECT_ROOT)).replace("\\", "/")


def main() -> int:
    settings = get_settings()
    base = settings.ibb_api_base_url.rstrip("/")
    found: dict[str, Any] = {}

    with httpx.Client(timeout=settings.ibb_request_timeout) as client:
        for slug, (path, bodies) in CANDIDATES.items():
            print(f"\n=== {slug} ===")
            url = f"{base}{path}"
            for body in bodies:
                try:
                    response = client.post(url, json=body)
                except Exception as exc:
                    print(f"  ERR  {type(exc).__name__} {body}")
                    continue

                if response.status_code != 200:
                    print(f"  {response.status_code}  {json.dumps(body, ensure_ascii=False)}")
                    continue

                payload = response.json()
                if payload.get("Success") is True and payload.get("Data"):
                    data = payload["Data"]
                    count = len(data) if isinstance(data, list) else 1
                    print(f"  OK   {json.dumps(body, ensure_ascii=False)} -> {count} kayıt")
                    sample = data[0] if isinstance(data, list) and data else data
                    print(f"       {json.dumps(sample, ensure_ascii=False)[:220]}")
                    found[slug] = {
                        "method": "POST",
                        "body": body,
                        "records": count,
                        "fixture": save(slug, url, body, payload),
                    }
                    break

                error = (payload.get("Error") or {}).get("Message", "")
                state = "Success=false" if payload.get("Success") is False else "Data boş"
                print(f"  --   {json.dumps(body, ensure_ascii=False)} -> {state}: {error[:110]}")
                time.sleep(0.4)

    print("\n=== Özet ===")
    for slug in CANDIDATES:
        if slug in found:
            print(f"  ✅ {slug}: POST {json.dumps(found[slug]['body'], ensure_ascii=False)}")
        else:
            print(f"  ❌ {slug}: çalışan gövde bulunamadı")

    out = PROJECT_ROOT / "tests" / "fixtures" / "_post_params.json"
    out.write_text(json.dumps(found, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
