"""İETT SOAP istemcisi: elle kurulan zarf.

`zeep` kullanılmıyor ve bu bir ölçüm sonucu (karar 0012). `duyurular`
servisi `zeep` ile açılabiliyor ama:

| Servis | `zeep` | elle zarf |
|---|---|---|
| `duyurular` (351) | ✓ 0.90 s, 192 kayıt | ✓ **0.45 s**, 192 kayıt |
| `planlanan-sefer-saati` (350) | ✗ `No definition '...AuthHeaderʼ in messages` | ✓ 0.17 s, 267 kayıt |

İki servisten biri `zeep` ile hiç açılmıyor (WSDL `AuthHeader` bildirmiş
ama eşleşen `<message>` tanımı yok) ve açılan serviste bile `zeep` iki kat
yavaş, çünkü her istemci kurulumunda WSDL'i indirip ayrıştırıyor. Tek
protokol için iki kod yolu tutmak yerine her çağrı elle kurulan SOAP 1.1
zarfıyla gidiyor.

Servis JSON'ı **XML gövdesinin içinde metin olarak** döndürüyor; bu yüzden
yanıt önce zarftan çıkarılıyor, sonra JSON olarak ayrıştırılıyor.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from xml.etree import ElementTree

from istanbul_agent.clients.base import LiveDataError, fetch_text
from istanbul_agent.config import get_settings

__all__ = [
    "SERVICES",
    "Announcement",
    "BusServiceWindow",
    "PlannedDeparture",
    "call",
    "fetch_announcements",
    "fetch_planned_departures",
    "parse_announcements",
    "parse_planned_departures",
    "summarise_departures",
    "today_day_type",
]

TEMPURI = "http://tempuri.org/"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"

SERVICES: dict[str, str] = {
    "duyurular": "/iett/UlasimDinamikVeri/Duyurular.asmx",
    "planlanan-sefer-saati": "/iett/UlasimAnaVeri/PlanlananSeferSaati.asmx",
    "hat-durak-guzergah": "/iett/UlasimAnaVeri/HatDurakGuzergah.asmx",
}

# Gün tipi kodları. Ölçüm (hat 34, 267 kayıt): `SGUNTIPI` alanı `I`, `C`,
# `P` değerlerini alıyor — hafta içi, Cumartesi, Pazar.
DAY_TYPES = {"I": "hafta_ici", "C": "cumartesi", "P": "pazar"}
DAY_TYPE_LABELS = {
    "hafta_ici": "hafta içi",
    "cumartesi": "cumartesi",
    "pazar": "pazar",
}

# Duyuru metinlerindeki saat kalıbı ("Saat 19:10 de hareket etmesi planlanan").
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def endpoint(service: str) -> str:
    base = get_settings().ibb_api_base_url.rstrip("/")
    try:
        return f"{base}{SERVICES[service]}"
    except KeyError:
        known = ", ".join(sorted(SERVICES))
        raise KeyError(f"bilinmeyen İETT servisi: {service!r} (bilinenler: {known})") from None


def build_envelope(method: str, params: dict[str, str] | None = None) -> bytes:
    """SOAP 1.1 zarfı."""
    body = "".join(f"<{key}>{value}</{key}>" for key, value in (params or {}).items())
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        ' xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
        f' xmlns:soap="{SOAP_NS}">'
        "<soap:Body>"
        f'<{method} xmlns="{TEMPURI}">{body}</{method}>'
        "</soap:Body></soap:Envelope>"
    )
    return envelope.encode("utf-8")


def extract_payload(xml_text: str, *, source: str) -> list[dict[str, Any]]:
    """Zarfın içindeki JSON listesini çıkar.

    Önce SOAP `Fault` aranıyor: servis hatayı 200 ile de döndürebiliyor ve
    gövdeyi JSON sanıp ayrıştırmaya çalışmak hatayı gizler.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as problem:
        raise LiveDataError(
            source, f"{source}: SOAP yanıtı XML olarak ayrıştırılamadı.", cause=problem
        ) from problem

    fault = root.find(f".//{{{SOAP_NS}}}Fault")
    if fault is not None:
        reason = (fault.findtext("faultstring") or "bilinmeyen SOAP hatası").strip()
        raise LiveDataError(source, f"{source}: servis hata döndürdü — {reason}")

    text = "".join(root.itertext()).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise LiveDataError(
            source, f"{source}: yanıtın içinde beklenen JSON listesi yok ({len(text)} karakter)."
        )
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as problem:
        raise LiveDataError(
            source, f"{source}: yanıttaki JSON ayrıştırılamadı.", cause=problem
        ) from problem
    return [row for row in payload if isinstance(row, dict)]


def call(
    service: str,
    method: str,
    params: dict[str, str] | None = None,
    *,
    source: str,
) -> list[dict[str, Any]]:
    """Bir İETT SOAP metodunu çağır ve JSON listesini döndür."""
    text = fetch_text(
        endpoint(service),
        source=source,
        content=build_envelope(method, params),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{TEMPURI}{method}"',
        },
    )
    return extract_payload(text, source=source)


# ------------------------------------------------------------------ duyurular


@dataclass(frozen=True)
class Announcement:
    """Bir İETT duyurusu."""

    line: str
    line_name: str
    kind: str
    recorded_at: str
    message: str

    @property
    def is_cancellation(self) -> bool:
        """Sefer iptali mi?

        Duyuruların çoğu "yapılamayacaktır" diyen sefer iptalleri; tarife
        sorusunda bunları ayırmak gerekiyor.
        """
        text = self.message.casefold()
        return "yapilamayacak" in text or "yapılamayacak" in text or "iptal" in text

    @property
    def times(self) -> tuple[str, ...]:
        """Mesajda anılan saatler."""
        return tuple(f"{hour.zfill(2)}:{minute}" for hour, minute in _TIME.findall(self.message))


def parse_announcements(rows: list[dict[str, Any]]) -> list[Announcement]:
    announcements: list[Announcement] = []
    for row in rows:
        message = str(row.get("MESAJ") or "").strip()
        if not message:
            continue
        announcements.append(
            Announcement(
                line=str(row.get("HATKODU") or "").strip(),
                line_name=str(row.get("HAT") or "").strip(),
                kind=str(row.get("TIP") or "").strip(),
                recorded_at=str(row.get("GUNCELLEME_SAATI") or "").strip(),
                message=message,
            )
        )
    return announcements


def fetch_announcements() -> list[Announcement]:
    """Canlı İETT duyuruları (291 kayıt ölçüldü, sayı gün içinde değişir)."""
    rows = call("duyurular", "GetDuyurular_json", source="iett_alerts")
    return parse_announcements(rows)


# --------------------------------------------------------- planlanan sefer saati


@dataclass(frozen=True)
class PlannedDeparture:
    """Bir hattın bir güzergâhındaki planlanmış tek kalkış."""

    line: str
    line_name: str
    route_code: str
    direction: str
    day_type: str
    service_kind: str
    time: str

    @property
    def day_label(self) -> str:
        return DAY_TYPE_LABELS.get(self.day_type, self.day_type)


def parse_planned_departures(rows: list[dict[str, Any]]) -> list[PlannedDeparture]:
    """`GetPlanlananSeferSaati_json` satırlarını çevir.

    Bilinmeyen bir gün tipi kodu **atılmıyor**, olduğu gibi taşınıyor:
    sessizce düşürmek hattın o gün hiç seferi yokmuş gibi görünmesine yol
    açardı.
    """
    departures: list[PlannedDeparture] = []
    for row in rows:
        time_text = str(row.get("DT") or "").strip()
        if not time_text:
            continue
        raw_day = str(row.get("SGUNTIPI") or "").strip().upper()
        departures.append(
            PlannedDeparture(
                line=str(row.get("SHATKODU") or "").strip(),
                line_name=str(row.get("HATADI") or "").strip(),
                route_code=str(row.get("SGUZERAH") or "").strip(),
                direction=str(row.get("SYON") or "").strip(),
                day_type=DAY_TYPES.get(raw_day, raw_day or "bilinmiyor"),
                service_kind=str(row.get("SSERVISTIPI") or "").strip(),
                time=time_text,
            )
        )
    return departures


def fetch_planned_departures(line: str) -> list[PlannedDeparture]:
    """Bir otobüs hattının planlanan sefer saatleri."""
    rows = call(
        "planlanan-sefer-saati",
        "GetPlanlananSeferSaati_json",
        {"HatKodu": line.strip().upper()},
        source="iett_timetable",
    )
    return parse_planned_departures(rows)


# ------------------------------------------------- sefer saatlerinin özeti


@dataclass(frozen=True)
class BusServiceWindow:
    """Bir hattın bir yön + gün tipindeki servis penceresi."""

    direction: str
    day_type: str
    first: str
    last: str
    trips: int
    headway_minutes: int | None
    route_codes: tuple[str, ...]

    @property
    def day_label(self) -> str:
        return DAY_TYPE_LABELS.get(self.day_type, self.day_type)


def _minutes(value: str) -> int | None:
    """`"05:35"` -> 335. Gece yarısını geçen saatler (`24:10`) korunur."""
    parts = value.split(":")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None


def summarise_departures(
    departures: list[PlannedDeparture], *, day_type: str | None = None
) -> list[BusServiceWindow]:
    """Kalkışları yön + gün tipine göre özetle.

    Aralık (headway) saat farklarının **ortancası**: ortalama, gün içindeki
    uzun gece boşluklarından etkileniyor. Dört kalkıştan az veriden aralık
    üretilmiyor — karar 0005'te metro için konan kuralın aynısı; üç
    kalkıştan "her 40 dakikada bir" çıkarmak uydurma olur.
    """
    import statistics
    from itertools import pairwise

    grouped: dict[tuple[str, str], list[PlannedDeparture]] = {}
    for departure in departures:
        if day_type and departure.day_type != day_type:
            continue
        grouped.setdefault((departure.direction, departure.day_type), []).append(departure)

    windows: list[BusServiceWindow] = []
    for (direction, day), rows in sorted(grouped.items()):
        times = sorted({row.time for row in rows}, key=lambda value: _minutes(value) or 0)
        if not times:
            continue
        minutes = [value for value in (_minutes(time) for time in times) if value is not None]
        headway: int | None = None
        if len(minutes) >= 4:
            gaps = [
                later - earlier
                for earlier, later in pairwise(minutes)
                if 0 < later - earlier <= 180
            ]
            if gaps:
                headway = round(statistics.median(gaps))
        windows.append(
            BusServiceWindow(
                direction=direction,
                day_type=day,
                first=times[0],
                last=times[-1],
                trips=len(times),
                headway_minutes=headway,
                route_codes=tuple(sorted({row.route_code for row in rows if row.route_code})),
            )
        )
    return windows


def today_day_type(when: datetime | None = None) -> str:
    """Bugünün gün tipi: `hafta_ici`, `cumartesi`, `pazar`."""
    moment = when or datetime.now()
    weekday = moment.weekday()
    if weekday == 5:
        return "cumartesi"
    if weekday == 6:
        return "pazar"
    return "hafta_ici"
