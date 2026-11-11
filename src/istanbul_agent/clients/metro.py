"""Metro İstanbul REST client, only what the timetable tool needs.

Two things about this API, both measured in Faz 0 (decision 0001):

- It answers **HTTP 200 even when it failed**. The body's `Success` field is
  the only reliable signal, so every response goes through `unwrap`.
- `GetAnnouncements/TR` is a plain GET and returns HTML in `Content`.

Announcements matter here because a schedule change is announced, not
reflected in the 2020 GTFS feed: if Metro İstanbul says it is running M7 on
an adjusted timetable, the headway this tool computed is wrong and the
answer has to say so.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

import httpx

from istanbul_agent.config import get_settings

__all__ = [
    "ACCESSIBILITY_GROUPS",
    "SCHEDULE_KEYWORDS",
    "Announcement",
    "EquipmentCount",
    "FaultyEquipment",
    "ServiceStatus",
    "StationFacilities",
    "fetch_announcements",
    "fetch_equipment_counts",
    "fetch_faulty_equipment",
    "fetch_service_statuses",
    "fetch_station_facilities",
    "parse_announcements",
    "parse_equipment_counts",
    "parse_faulty_equipment",
    "parse_service_statuses",
    "parse_station_facilities",
    "schedule_alerts",
    "unwrap",
]

ANNOUNCEMENTS_PATH = "/MetroIstanbul/api/MetroMobile/V2/GetAnnouncements/TR"

# Words that mark an announcement as being about the timetable rather than
# about a lift, a campaign or an event. Chosen from the live announcement
# titles in tests/fixtures/metro/metro-announcements-tr.json.
SCHEDULE_KEYWORDS = (
    "sefer",
    "tarife",
    "çalışma saat",
    "ilk sefer",
    "son sefer",
    "servis saat",
    "düzenleme",
    "ek sefer",
    "seferler",
)

_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v]+")
# A line code as a standalone token: `M7`, `T1`, `F4`, `M1A`, `TF2`.
_LINE_TOKEN = re.compile(r"\b(?:TF|[MTF])\d+[AB]?\b", re.IGNORECASE)


@dataclass(frozen=True)
class Announcement:
    """One announcement, with its markup stripped."""

    id: int
    title: str
    text: str
    lines: frozenset[str]

    @property
    def is_schedule_related(self) -> bool:
        haystack = f"{self.title} {self.text}".casefold()
        return any(keyword in haystack for keyword in SCHEDULE_KEYWORDS)

    def summary(self, limit: int = 240) -> str:
        text = self.text if self.text else self.title
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def unwrap(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The `Data` list of a Metro İstanbul response, or an error.

    Raises on `Success: false` because the status code does not report it.
    """
    if not payload.get("Success"):
        raise RuntimeError(f"Metro İstanbul API başarısız: {payload.get('Error')!r}")
    data = payload.get("Data")
    return data if isinstance(data, list) else []


def _plain_text(markup: str | None) -> str:
    """Announcement bodies are HTML; this is the readable text in them."""
    if not markup:
        return ""
    text = _TAGS.sub(" ", markup)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("•", " ")
    text = _SPACES.sub(" ", text)
    return " ".join(part.strip() for part in text.split("\n") if part.strip()).strip()


def parse_announcements(payload: dict[str, Any]) -> list[Announcement]:
    """Turn a `GetAnnouncements` response into announcements.

    The payload may be either the raw response or a saved fixture, which
    wraps the response under `response`.
    """
    body = payload.get("response", payload)
    announcements: list[Announcement] = []
    for row in unwrap(body):
        title = _plain_text(row.get("Title"))
        text = _plain_text(row.get("Content"))
        lines = frozenset(
            match.group(0).upper() for match in _LINE_TOKEN.finditer(f"{title} {text}")
        )
        announcements.append(
            Announcement(
                id=int(row.get("Id") or 0),
                title=title,
                text=text,
                lines=lines,
            )
        )
    return announcements


def schedule_alerts(announcements: list[Announcement], line: str) -> list[Announcement]:
    """Announcements about `line`'s timetable.

    An announcement has to be about the timetable -- a lift breakdown on M2
    is no reason to doubt M2's headway -- and it has to concern the line.
    An announcement naming no line at all concerns every line: "Metrolarda
    Kış Tarifesine Geçiliyor" changes first and last trains across the
    network without mentioning a single code.
    """
    wanted = line.strip().upper()
    return [
        announcement
        for announcement in announcements
        if announcement.is_schedule_related
        and (not announcement.lines or wanted in announcement.lines)
    ]


def fetch_announcements(*, timeout: float = 10.0) -> list[Announcement]:
    """Live announcements. Never called from unit tests; fixtures are used."""
    settings = get_settings()
    url = f"{settings.ibb_api_base_url.rstrip('/')}{ANNOUNCEMENTS_PATH}"
    with httpx.Client(timeout=timeout) as client:
        response = client.get(url)
        response.raise_for_status()
        return parse_announcements(response.json())


# --------------------------------------------------- servis durumu ve arızalar
#
# Faz 2'de eklenen dört uç nokta (karar 0012). Hepsi `Success` alanına
# bakıyor (HTTP durumuna değil) ve ortak katmanın TTL önbelleğini kullanıyor.

SERVICE_STATUS_PATH = "/MetroIstanbul/api/MetroMobile/V2/GetServiceStatuses"
FAULTY_EQUIPMENT_PATH = "/MetroIstanbul/api/MetroMobile/V2/GetFaultyEquipments"
FAULTY_DETAILS_PATH = "/MetroIstanbul/api/MetroMobile/V2/GetFaultyEquipmentDetails"
STATIONS_PATH = "/MetroIstanbul/api/MetroMobile/V2/GetStations"

# `GetFaultyEquipmentDetails` POST ve `EquipmentGroupName` istiyor; grup
# adları `GetFaultyEquipments` çıktısındaki `GroupName` alanından geliyor
# (ölçüm: "Hat", "Yürüyen Merdiven", "Asansör", "Turnike").
ACCESSIBILITY_GROUPS = ("Asansör", "Yürüyen Merdiven")


@dataclass(frozen=True)
class ServiceStatus:
    """Bir hattın servis durumu duyurusu."""

    line: str
    description: str
    is_active: bool
    updated_at: str


@dataclass(frozen=True)
class EquipmentCount:
    """Bir ekipman grubunun çalışan/arızalı sayısı."""

    group: str
    name: str
    active: int
    inactive: int

    @property
    def has_faults(self) -> bool:
        return self.inactive > 0


@dataclass(frozen=True)
class FaultyEquipment:
    """Tek bir arızalı ekipman."""

    group: str
    line: str
    station: str
    location: str
    kind: str
    date: str
    description: str


@dataclass(frozen=True)
class StationFacilities:
    """Bir istasyonun erişilebilirlik donanımı."""

    station: str
    line: str
    escalators: int
    lifts: int
    wc: bool
    baby_room: bool


def _get(path: str, *, source: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Metro API'sinden GET; `Success` alanı kontrol edilir."""
    from istanbul_agent.clients.base import LiveDataError, fetch_json

    url = f"{get_settings().ibb_api_base_url.rstrip('/')}{path}"
    payload = fetch_json(url, source=source, params=params)
    if not isinstance(payload, dict) or not payload.get("Success"):
        error = payload.get("Error") if isinstance(payload, dict) else None
        raise LiveDataError(
            source,
            f"Metro Istanbul servisi basarisiz dondu ({error!r}). HTTP durumu 200 olsa da "
            "govdedeki Success alani false.",
        )
    return payload


def _post(path: str, body: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Metro API'sine POST (`GetFaultyEquipmentDetails` GET'e 405 veriyor)."""
    import json as _json

    from istanbul_agent.clients.base import LiveDataError, fetch_text

    url = f"{get_settings().ibb_api_base_url.rstrip('/')}{path}"
    text = fetch_text(
        url,
        source=source,
        content=_json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        payload = _json.loads(text)
    except ValueError as problem:
        raise LiveDataError(source, "Metro Istanbul yaniti JSON degil.", cause=problem) from problem
    if not isinstance(payload, dict) or not payload.get("Success"):
        raise LiveDataError(
            source, f"Metro Istanbul servisi basarisiz dondu ({payload.get('Error')!r})."
        )
    return payload


def parse_service_statuses(payload: dict[str, Any]) -> list[ServiceStatus]:
    body = payload.get("response", payload)
    statuses: list[ServiceStatus] = []
    for row in unwrap(body):
        description = _plain_text(row.get("Description"))
        if not description:
            continue
        statuses.append(
            ServiceStatus(
                line=str(row.get("LineName") or "").strip(),
                description=description,
                is_active=bool(row.get("IsActive")),
                updated_at=str(row.get("UpdateDate") or "").strip(),
            )
        )
    return statuses


def fetch_service_statuses() -> list[ServiceStatus]:
    """Hat bazlı servis durumu duyuruları."""
    return parse_service_statuses(_get(SERVICE_STATUS_PATH, source="metro_alerts"))


def parse_equipment_counts(payload: dict[str, Any]) -> list[EquipmentCount]:
    """`GetFaultyEquipments` toplamlarını çevir.

    Yanıt tek bir kayıt içinde iki liste veriyor; `EquipmentServiceStatus`
    ekipman gruplarının çalışan/arızalı sayılarını tutuyor.
    """
    body = payload.get("response", payload)
    counts: list[EquipmentCount] = []
    for row in unwrap(body):
        for item in row.get("EquipmentServiceStatus") or []:
            if not isinstance(item, dict):
                continue
            counts.append(
                EquipmentCount(
                    group=str(item.get("GroupName") or "").strip(),
                    name=str(item.get("Name") or "").strip(),
                    active=int(item.get("Active") or 0),
                    inactive=int(item.get("Inactive") or 0),
                )
            )
    return counts


def fetch_equipment_counts() -> list[EquipmentCount]:
    """Ekipman gruplarının çalışan/arızalı sayıları."""
    return parse_equipment_counts(_get(FAULTY_EQUIPMENT_PATH, source="metro_equipment"))


def parse_faulty_equipment(payload: dict[str, Any]) -> list[FaultyEquipment]:
    body = payload.get("response", payload)
    faults: list[FaultyEquipment] = []
    for row in unwrap(body):
        for item in row.get("Equipments") or []:
            if not isinstance(item, dict):
                continue
            faults.append(
                FaultyEquipment(
                    group=str(item.get("Group") or "").strip(),
                    line=str(item.get("LineName") or "").strip(),
                    station=str(item.get("StationName") or "").strip(),
                    location=str(item.get("Location") or "").strip(),
                    kind=str(item.get("Type") or "").strip(),
                    date=str(item.get("Date") or "").strip(),
                    description=str(item.get("Description") or "").strip(),
                )
            )
    return faults


def fetch_faulty_equipment(group: str) -> list[FaultyEquipment]:
    """Bir ekipman grubundaki arızalı ekipmanlar (istasyon adıyla)."""
    payload = _post(FAULTY_DETAILS_PATH, {"EquipmentGroupName": group}, source="metro_equipment")
    return parse_faulty_equipment(payload)


def parse_station_facilities(payload: dict[str, Any]) -> list[StationFacilities]:
    body = payload.get("response", payload)
    facilities: list[StationFacilities] = []
    for row in unwrap(body):
        detail = row.get("DetailInfo") or {}
        if not isinstance(detail, dict):
            continue
        facilities.append(
            StationFacilities(
                station=str(row.get("Description") or row.get("Name") or "").strip(),
                line=str(row.get("LineName") or "").strip(),
                escalators=int(detail.get("Escolator") or 0),
                lifts=int(detail.get("Lift") or 0),
                wc=bool(detail.get("WC")),
                baby_room=bool(detail.get("BabyRoom")),
            )
        )
    return facilities


def fetch_station_facilities() -> list[StationFacilities]:
    """İstasyonların asansör/yürüyen merdiven sayıları (248 istasyon)."""
    return parse_station_facilities(_get(STATIONS_PATH, source="metro_stations"))
