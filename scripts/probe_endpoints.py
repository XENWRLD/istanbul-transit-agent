"""Faz 0 discovery: call every A and A2 endpoint in the inventory for real.

The project rule is not to guess how the IBB API behaves, so this script
records what the services actually return: HTTP status, whether they demand
authentication, the payload shape, how long they took, and the newest
timestamp found anywhere in the response -- the last one tells us whether a
service is still fed with fresh data or just answers with a stale snapshot.

Endpoints needing an id are probed in two passes: the list endpoint runs
first and a real id is harvested from its response for the detail call.

Writes:
    tests/fixtures/<area>/<slug>.json      sample response per working endpoint
    tests/fixtures/_probe_results.json     the full result table

    uv run python scripts/probe_endpoints.py
    uv run python scripts/probe_endpoints.py --only ispark   # filter by slug
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from istanbul_agent.config import PROJECT_ROOT, get_settings

FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
METRO = "/MetroIstanbul/api/MetroMobile/V2"

# How many records of a long list we keep in the fixture. Enough to see the
# shape and some variety, small enough to read in a diff.
FIXTURE_MAX_RECORDS = 5
POLITE_DELAY_S = 0.6


@dataclass
class Endpoint:
    """One inventory row to probe."""

    dataset_no: int
    group: str
    slug: str
    area: str
    path: str
    note: str = ""
    # Resolve a "{placeholder}" in `path` from a previously probed response.
    needs: dict[str, str] = field(default_factory=dict)
    # Several Metro endpoints answer 405 to GET and want a POST with a body;
    # the required fields were derived from their own error messages.
    method: str = "GET"
    body: dict[str, Any] | None = None

    def url(self, base: str) -> str:
        return self.path if self.path.startswith("http") else f"{base}{self.path}"


# --------------------------------------------------------------------------
# Pass 1: no parameters needed. Pass 2 reuses ids harvested from these.
# --------------------------------------------------------------------------
PASS1: list[Endpoint] = [
    Endpoint(502, "A", "ispark-park-list", "ispark", "/ispark/Park"),
    Endpoint(
        548,
        "A",
        "traffic-index-history",
        "traffic",
        "/tkmservices/api/TrafficData/v1/TrafficIndexHistory/1/5M",
    ),
    Endpoint(520, "A", "metro-service-statuses", "metro", f"{METRO}/GetServiceStatuses"),
    Endpoint(512, "A", "metro-announcements-tr", "metro", f"{METRO}/GetAnnouncements/TR"),
    Endpoint(
        552,
        "A",
        "metro-timetable",
        "metro",
        f"{METRO}/GetTimeTable",
        method="POST",
        body={"LineId": 4, "StationId": 188, "DirectionId": 13},
        note="POST; her hat/yön kombinasyonunda sunucu içi IndexOutOfRange",
    ),
    Endpoint(
        513,
        "A",
        "metro-station-between-time",
        "metro",
        f"{METRO}/GetStationBetweenTime",
        method="POST",
        body={"LineId": 4, "DirectionId": 13},
        note="POST; her hat/yön kombinasyonunda sunucu içi NullReference",
    ),
    Endpoint(168, "A", "metro-stations", "metro", f"{METRO}/GetStations"),
    Endpoint(508, "A", "metro-lines", "metro", f"{METRO}/GetLines"),
    Endpoint(
        515,
        "A",
        "metro-faulty-equipment-details",
        "metro",
        f"{METRO}/GetFaultyEquipmentDetails",
        method="POST",
        body={"EquipmentGroupName": "Yürüyen Merdiven"},
        note="POST; EquipmentGroupName zorunlu, değerler GetFaultyEquipments.GroupName'den",
    ),
    Endpoint(518, "A", "metro-faulty-equipments", "metro", f"{METRO}/GetFaultyEquipments"),
    Endpoint(527, "A", "metro-ticket-price-tr", "metro", f"{METRO}/GetTicketPrice/TR"),
    Endpoint(
        407,
        "A",
        "aqi-stations",
        "air_quality",
        "/havakalitesi/OpenDataPortalHandler/GetAQIStations",
    ),
    Endpoint(501, "A", "road-maintenance-works", "road_works", "/teas/api/open_data"),
    Endpoint(
        169,
        "A2",
        "isbike-all-station-status",
        "bike",
        "/ispark-bike/GetAllStationStatus",
        note="portal notu: geçici olarak kapalı",
    ),
    Endpoint(
        170,
        "A2",
        "isbike-station-status",
        "bike",
        "/ispark-bike/GetStationStatus",
        note="portal notu: geçici olarak kapalı",
    ),
    Endpoint(504, "A2", "metro-railway-groups", "metro", f"{METRO}/GetRailwayGroups"),
    Endpoint(507, "A2", "metro-directions", "metro", f"{METRO}/GetDirections"),
    Endpoint(516, "A2", "metro-failure-types", "metro", f"{METRO}/GetFailureTypes"),
    Endpoint(
        517,
        "A2",
        "metro-technical-object-types",
        "metro",
        f"{METRO}/GetTechnicalObjectTypes",
    ),
    Endpoint(519, "A2", "metro-failures-types", "metro", f"{METRO}/GetFailuresTypes"),
    Endpoint(511, "A2", "metro-news-tr", "metro", f"{METRO}/GetNews/TR"),
    Endpoint(510, "A2", "metro-activities", "metro", f"{METRO}/GetActivities"),
    Endpoint(549, "A2", "metro-maps", "metro", f"{METRO}/GetMaps"),
    Endpoint(550, "A2", "metro-addresses-tr", "metro", f"{METRO}/GetAddresses/TR"),
    Endpoint(551, "A2", "metro-line-projects", "metro", f"{METRO}/GetLineProjects"),
    Endpoint(382, "A2", "signalized-junctions", "traffic", "/web/api/junction"),
    Endpoint(
        384,
        "A2",
        "city-map-api-portal",
        "geocoding",
        "https://sehirharitasiapi.ibb.gov.tr/developer/",
        note="developer portalı, JSON uç noktası değil",
    ),
]

# --------------------------------------------------------------------------
# Pass 2: needs an id harvested from a pass-1 response.
# --------------------------------------------------------------------------
PASS2: list[Endpoint] = [
    Endpoint(
        163,
        "A",
        "ispark-park-detail",
        "ispark",
        "/ispark/ParkDetay?id={park_id}",
        needs={"park_id": "ispark-park-list"},
    ),
    Endpoint(
        514,
        "A",
        "metro-stations-by-line",
        "metro",
        f"{METRO}/GetStationById/{{line_id}}",
        needs={"line_id": "metro-lines"},
    ),
    Endpoint(
        408,
        "A",
        "aqi-by-station",
        "air_quality",
        "/havakalitesi/OpenDataPortalHandler/GetAQIByStationId?StationId={station_id}",
        needs={"station_id": "aqi-stations"},
    ),
    Endpoint(
        506,
        "A2",
        "metro-direction-by-line",
        "metro",
        f"{METRO}/GetDirectionById/{{line_id}}",
        needs={"line_id": "metro-lines"},
    ),
    Endpoint(
        505,
        "A2",
        "metro-directions-by-line-and-station",
        "metro",
        f"{METRO}/GetDirectionsByLineIdAndStationId",
        method="POST",
        body={"LineId": 4, "StationId": 188},
        note="POST; LineId ve StationId aynı hatta ait olmalı",
    ),
]

# Field names to look at when harvesting an id out of a list response.
ID_KEYS = ("parkID", "parkId", "Id", "id", "ID", "StationId", "stationId", "LineId", "lineId")

LIST_KEYS = ("Data", "data", "result", "Result", "records")

# --------------------------------------------------------------------------
# Timestamp sniffing
# --------------------------------------------------------------------------
DATE_RE = re.compile(r"(20\d{2})[-/.](\d{2})[-/.](\d{2})(?:[T ](\d{2}):(\d{2}))?")


def newest_timestamp(payload: Any) -> str | None:
    """Return the latest date-looking string found anywhere inside `payload`.

    Deliberately crude: field names differ wildly across these services, so we
    scan values rather than trust a schema. The point is only to see whether a
    service still carries recent data.
    """
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and len(node) >= 8:
            match = DATE_RE.search(node)
            if match:
                year, month, day = match.group(1), match.group(2), match.group(3)
                hour, minute = match.group(4) or "00", match.group(5) or "00"
                if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
                    found.append(f"{year}-{month}-{day} {hour}:{minute}")

    walk(payload)
    return max(found) if found else None


def as_list(payload: Any) -> list[Any] | None:
    """Return the record list in `payload`, whether top level or wrapped."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in LIST_KEYS:
            if isinstance(payload.get(key), list):
                return payload[key]
    return None


def describe(payload: Any) -> str:
    """One-line summary of a JSON payload's shape."""
    items = as_list(payload)
    if isinstance(payload, list):
        if not items:
            return "boş liste []"
        if isinstance(items[0], dict):
            return f"{len(items)} kayıtlı liste; alanlar: {', '.join(list(items[0])[:6])}"
        return f"{len(items)} kayıtlı liste ({type(items[0]).__name__})"
    if isinstance(payload, dict):
        summary = f"nesne; anahtarlar: {', '.join(list(payload)[:8])}"
        if items is not None:
            summary += f"; iç liste=[{len(items)} kayıt]"
            if items and isinstance(items[0], dict):
                summary += f" alanlar: {', '.join(list(items[0])[:6])}"
        return summary
    return type(payload).__name__


def record_count(payload: Any) -> int | None:
    items = as_list(payload)
    return len(items) if items is not None else None


def truncate(payload: Any) -> tuple[Any, bool]:
    """Shrink long lists so fixtures stay readable. Returns (payload, trimmed)."""
    if isinstance(payload, list) and len(payload) > FIXTURE_MAX_RECORDS:
        return payload[:FIXTURE_MAX_RECORDS], True
    if isinstance(payload, dict):
        for key in LIST_KEYS:
            items = payload.get(key)
            if isinstance(items, list) and len(items) > FIXTURE_MAX_RECORDS:
                clone = dict(payload)
                clone[key] = items[:FIXTURE_MAX_RECORDS]
                return clone, True
    return payload, False


def looks_like_auth_problem(status: int, body: str) -> str:
    if status in (401, 403):
        return "EVET"
    lowered = body.lower()
    for needle in ("subscription key", "api key", "unauthorized", "access denied"):
        if needle in lowered:
            return f"MUHTEMEL ({needle})"
    return "hayır"


def harvest_id(payload: Any, wanted: str) -> str | None:
    """Pull a usable id out of a list response."""
    items = as_list(payload)
    if not items or not isinstance(items[0], dict):
        return None
    first = items[0]
    preferred = {
        "park_id": ("parkID", "parkId", "ParkID", "Id", "id"),
        "line_id": ("Id", "id", "LineId", "lineId"),
        "station_id": ("Id", "id", "StationId", "stationId"),
    }.get(wanted, ID_KEYS)
    for key in (*preferred, *ID_KEYS):
        if key in first and first[key] not in (None, ""):
            return str(first[key])
    return None


def probe(
    client: httpx.Client, endpoint: Endpoint, base: str, resolved: dict[str, Any]
) -> dict[str, Any]:
    """Call one endpoint and build its result row. Never raises."""
    url = endpoint.url(base)
    substitutions: dict[str, str] = {}

    for placeholder, source_slug in endpoint.needs.items():
        value = harvest_id(resolved.get(source_slug), placeholder)
        if value is None:
            return {
                "no": endpoint.dataset_no,
                "group": endpoint.group,
                "slug": endpoint.slug,
                "url": url,
                "status": "ATLANDI",
                "works": False,
                "auth": "-",
                "format": "-",
                "elapsed_ms": None,
                "newest_timestamp": None,
                "records": None,
                "summary": f"{placeholder} değeri {source_slug} yanıtından alınamadı",
                "note": endpoint.note,
            }
        substitutions[placeholder] = value

    for placeholder, value in substitutions.items():
        url = url.replace("{" + placeholder + "}", value)

    row: dict[str, Any] = {
        "no": endpoint.dataset_no,
        "group": endpoint.group,
        "slug": endpoint.slug,
        "url": url,
        "method": endpoint.method,
        "body": endpoint.body,
        "params_used": substitutions or None,
        "note": endpoint.note,
    }

    started = time.perf_counter()
    try:
        if endpoint.method == "POST":
            response = client.post(url, json=endpoint.body or {})
        else:
            response = client.get(url)
    except Exception as exc:
        row.update(
            status="BAĞLANTI HATASI",
            works=False,
            auth="-",
            format="-",
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            newest_timestamp=None,
            records=None,
            summary=f"{type(exc).__name__}: {str(exc)[:160]}",
        )
        return row

    elapsed_ms = round((time.perf_counter() - started) * 1000)
    content_type = response.headers.get("content-type", "").split(";")[0] or "?"
    body_preview = response.text[:4000]
    row.update(
        status=str(response.status_code),
        elapsed_ms=elapsed_ms,
        content_type=content_type,
        bytes=len(response.content),
        auth=looks_like_auth_problem(response.status_code, body_preview),
    )

    if response.status_code != 200:
        row.update(
            works=False,
            format=content_type,
            newest_timestamp=None,
            records=None,
            summary=" ".join(body_preview[:200].split()) or "(boş gövde)",
        )
        return row

    try:
        payload = response.json()
    except Exception:
        is_html = "html" in content_type or body_preview.lstrip().lower().startswith("<!doctype")
        row.update(
            works=not is_html,
            format="HTML" if is_html else content_type,
            newest_timestamp=None,
            records=None,
            summary="HTML sayfa, JSON değil" if is_html else " ".join(body_preview[:200].split()),
            payload=None,
        )
        return row

    # The Metro API answers 200 even when it failed: Success=false carries an
    # internal exception. That is not a working endpoint.
    if isinstance(payload, dict) and payload.get("Success") is False:
        error = (payload.get("Error") or {}).get("Message", "")
        row.update(
            works=False,
            format="JSON",
            newest_timestamp=None,
            records=None,
            summary=f"HTTP 200 ama Success=false: {' '.join(error.split())[:150]}",
        )
        return row

    row.update(
        works=True,
        format="JSON",
        payload=payload,
        newest_timestamp=newest_timestamp(payload),
        records=record_count(payload),
        summary=describe(payload),
    )
    return row


def save_fixture(area: str, slug: str, row: dict[str, Any]) -> str | None:
    """Write a sample response, with provenance, under tests/fixtures/."""
    payload = row.get("payload")
    if payload is None:
        return None
    trimmed_payload, trimmed = truncate(payload)
    target = FIXTURES / area / f"{slug}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "_fixture_meta": {
            "source_url": row["url"],
            "http_method": row.get("method", "GET"),
            "request_body": row.get("body"),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "http_status": row["status"],
            "total_records": row.get("records"),
            "trimmed_to": FIXTURE_MAX_RECORDS if trimmed else None,
            "params_used": row.get("params_used"),
        },
        "response": trimmed_payload,
    }
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target.relative_to(PROJECT_ROOT)).replace("\\", "/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="probe only slugs containing this substring")
    args = parser.parse_args()

    settings = get_settings()
    base = settings.ibb_api_base_url.rstrip("/")
    areas = {endpoint.slug: endpoint.area for endpoint in (*PASS1, *PASS2)}

    rows: list[dict[str, Any]] = []
    resolved: dict[str, Any] = {}
    headers = {
        "Accept": "application/json",
        "User-Agent": "istanbul-agent/0.1 (Faz 0 discovery)",
    }

    with httpx.Client(
        timeout=settings.ibb_request_timeout, headers=headers, follow_redirects=True
    ) as client:
        for phase, endpoints in (("1", PASS1), ("2", PASS2)):
            print(f"\n===== Pass {phase} =====")
            for endpoint in endpoints:
                if args.only and args.only not in endpoint.slug:
                    continue
                row = probe(client, endpoint, base, resolved)
                if row.get("works") and row.get("payload") is not None:
                    resolved[endpoint.slug] = row["payload"]
                row["fixture"] = save_fixture(endpoint.area, endpoint.slug, row)
                row.pop("payload", None)
                rows.append(row)

                mark = "OK  " if row.get("works") else "FAIL"
                stamp = row.get("newest_timestamp") or "-"
                print(
                    f"  {mark} {row['status']:>6} {row.get('elapsed_ms')!s:>6}ms "
                    f"{endpoint.slug:42} ts={stamp:16} {row['summary'][:70]}"
                )
                time.sleep(POLITE_DELAY_S)

    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "_probe_results.json").write_text(
        json.dumps(
            {
                "probed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "api_base_url": base,
                "areas": areas,
                "results": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    working = sum(1 for row in rows if row.get("works"))
    print(
        f"\n{working}/{len(rows)} uç nokta çalışıyor. Sonuçlar: tests/fixtures/_probe_results.json"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
