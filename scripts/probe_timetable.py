"""Last check on the two Metro endpoints that still fail: is it us or them?

GetTimeTable and GetStationBetweenTime answer 200 with ``Success: false`` and
an internal .NET exception ("Index was out of range", "Object reference not
set", "Unexpected character ... <" -- the last one means the service parsed an
HTML error page from a dependency of its own). None of those name a missing
parameter, which points at a server-side fault rather than a wrong request.

Before writing them off, sweep every line and direction the API reports, so
the decision note can say the failure is not line-specific.

    uv run python scripts/probe_timetable.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from typing import Any

import httpx

from istanbul_agent.config import PROJECT_ROOT, get_settings

METRO = "/MetroIstanbul/api/MetroMobile/V2"
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "metro"


def load_lines(client: httpx.Client, base: str) -> list[dict[str, Any]]:
    """Fetch the full line list live -- the saved fixture is trimmed to 5 rows."""
    response = client.get(f"{base}{METRO}/GetLines")
    response.raise_for_status()
    return response.json()["Data"]


def short(message: str) -> str:
    """Collapse an exception message to a comparable label."""
    for needle, label in (
        ("Index was out of range", "IndexOutOfRange"),
        ("Object reference not set", "NullReference"),
        ("Unexpected character", "UpstreamHtmlParse"),
        ("required column", "MissingSqlColumn"),
    ):
        if needle in message:
            return label
    return message[:60] or "(mesaj yok)"


def main() -> int:
    settings = get_settings()
    base = settings.ibb_api_base_url.rstrip("/")
    tallies: dict[str, Counter[str]] = {
        "GetTimeTable": Counter(),
        "GetStationBetweenTime": Counter(),
    }
    successes: list[str] = []

    with httpx.Client(timeout=settings.ibb_request_timeout) as client:
        lines = load_lines(client, base)
        print(f"{len(lines)} hat üzerinde deneniyor\n")
        for line in lines:
            line_id, line_name = line["Id"], line["Name"]

            # Directions actually defined for this line.
            directions: list[int] = []
            try:
                response = client.get(f"{base}{METRO}/GetDirectionById/{line_id}")
                if response.status_code == 200:
                    directions = [d["DirectionId"] for d in (response.json().get("Data") or [])]
            except Exception:
                pass

            # Stations actually on this line.
            station_id = None
            try:
                response = client.get(f"{base}{METRO}/GetStationById/{line_id}")
                if response.status_code == 200:
                    stations = response.json().get("Data") or []
                    if stations:
                        station_id = stations[0]["Id"]
            except Exception:
                pass

            for endpoint in ("GetTimeTable", "GetStationBetweenTime"):
                bodies: list[dict[str, Any]] = []
                for direction_id in directions or [None]:
                    body: dict[str, Any] = {"LineId": line_id}
                    if direction_id is not None:
                        body["DirectionId"] = direction_id
                    if endpoint == "GetTimeTable" and station_id is not None:
                        body["StationId"] = station_id
                    bodies.append(body)

                for body in bodies:
                    try:
                        response = client.post(f"{base}{METRO}/{endpoint}", json=body)
                    except Exception as exc:
                        tallies[endpoint][type(exc).__name__] += 1
                        continue
                    if response.status_code != 200:
                        tallies[endpoint][f"HTTP {response.status_code}"] += 1
                        continue
                    payload = response.json()
                    if payload.get("Success") and payload.get("Data"):
                        tallies[endpoint]["OK"] += 1
                        successes.append(
                            f"{endpoint} {line_name} {json.dumps(body, ensure_ascii=False)}"
                        )
                        (FIXTURES / f"metro-{endpoint.lower()}-working.json").write_text(
                            json.dumps(
                                {
                                    "_fixture_meta": {
                                        "source_url": f"{base}{METRO}/{endpoint}",
                                        "http_method": "POST",
                                        "request_body": body,
                                    },
                                    "response": payload,
                                },
                                ensure_ascii=False,
                                indent=2,
                            ),
                            encoding="utf-8",
                        )
                    else:
                        error = (payload.get("Error") or {}).get("Message", "")
                        tallies[endpoint][short(error)] += 1

            print(f"  {line_name:6} yön={len(directions)} istasyon={station_id}")

    print("\n=== Sonuç dağılımı ===")
    for endpoint, counter in tallies.items():
        total = sum(counter.values())
        print(f"\n{endpoint}  ({total} deneme)")
        for label, count in counter.most_common():
            print(f"    {count:4} x {label}")

    print("\n=== Çalışan kombinasyon ===")
    print("\n".join(f"  {s}" for s in successes) if successes else "  YOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
