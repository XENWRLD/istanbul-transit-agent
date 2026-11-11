"""Canlı kaynakları gerçekten çağır: taze mi, tutarlı mı, ne kadar gecikmeli?

Birim testler kural gereği ağa çıkmıyor, fixture okuyor — yani kodun kendi
beklentisini doğruluyor, kaynağın gerçekte ne gönderdiğini doğrulamıyor. Bu
betik tam tersini yapıyor: her canlı kaynağa gerçek istek atar ve dönen
verinin zaman damgasını şimdiyle karşılaştırır.

Karar 0013'te ölçtüğü dört sessiz yanlışı bu betik ortaya çıkardı. Yeni bir
canlı kaynak eklenince çalıştır.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime

from istanbul_agent.console import enable_unicode_output

enable_unicode_output()

from istanbul_agent.clients import city, iett, metro  # noqa: E402
from istanbul_agent.clients.base import LiveDataError  # noqa: E402

NOW = datetime.now()


def lag(stamp: str | None) -> str:
    if not stamp:
        return "damga yok"
    text = stamp.strip().replace("Z", "")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y %H:%M:%S",
    ):
        try:
            when = datetime.strptime(text, fmt)
        except ValueError:
            continue
        minutes = (NOW - when).total_seconds() / 60
        return f"{stamp} ({minutes:.0f} dk once)"
    return f"{stamp} (cozulemedi)"


def run(name: str, fn: Callable[[], str]) -> None:
    start = time.perf_counter()
    try:
        detail = fn()
    except LiveDataError as problem:
        print(f"[HATA]  {name:26} {problem}")
        return
    except Exception as problem:
        print(f"[COKTU] {name:26} {type(problem).__name__}: {problem}")
        return
    print(f"[OK]    {name:26} {time.perf_counter() - start:5.2f}s  {detail}")


def parking() -> str:
    parks = city.fetch_car_parks()
    with_counts = [p for p in parks if p.capacity and p.empty is not None]
    stamps = [p.updated_at for p in parks if p.updated_at]
    busiest = sorted(
        (p for p in with_counts if p.capacity and p.capacity > 50),
        key=lambda p: (p.empty or 0) / (p.capacity or 1),
    )[:3]
    sample = ", ".join(
        f"{p.name[:20]} %{100 * (1 - (p.empty or 0) / (p.capacity or 1)):.0f} dolu" for p in busiest
    )
    return (
        f"{len(parks)} otopark, {len(with_counts)} kapasiteli | "
        f"damga: {lag(stamps[0] if stamps else None)} | {sample}"
    )


def parking_detail() -> str:
    parks = [p for p in city.fetch_car_parks() if p.capacity and p.capacity > 100]
    if not parks:
        return "kapasiteli otopark yok"
    one = parks[0]
    detail = city.fetch_park_detail(one.park_id)
    if detail is None:
        return f"{one.name}: detay yok"
    return (
        f"{one.name}: liste={one.empty}/{one.capacity}, "
        f"detay={detail.empty}/{detail.capacity}, damga={lag(detail.updated_at)}"
    )


def traffic() -> str:
    index = city.fetch_traffic_index()
    return (
        f"indeks={index.value} ({index.label}), egilim={index.trend}, "
        f"olcum={lag(index.measured_at)}, gecmis={len(index.previous)} nokta"
    )


def iett_alerts() -> str:
    alerts = iett.fetch_announcements()
    cancels = [a for a in alerts if a.is_cancellation]
    stamps = {a.recorded_at for a in alerts if a.recorded_at}
    return (
        f"{len(alerts)} duyuru, {len(cancels)} iptal, {len(stamps)} ayri kayit saati | "
        f"ornek: {alerts[0].line if alerts else '-'} {alerts[0].recorded_at if alerts else ''}"
    )


def iett_timetable() -> str:
    departures = iett.fetch_planned_departures("500T")
    today = iett.today_day_type()
    windows = iett.summarise_departures(departures, day_type=today)
    first = windows[0] if windows else None
    tail = (
        f"{first.direction} {first.first}-{first.last}, {first.trips} sefer, "
        f"aralik={first.headway_minutes} dk"
        if first
        else "bugun icin pencere yok"
    )
    return f"500T: {len(departures)} kalkis, bugun ({today}) {len(windows)} pencere | {tail}"


def metro_alerts() -> str:
    alerts = metro.fetch_announcements()
    return f"{len(alerts)} duyuru | ornek: {(alerts[0].title[:44] if alerts else '-')}"


def metro_status() -> str:
    statuses = metro.fetch_service_statuses()
    return f"{len(statuses)} hat durumu | ornek: {statuses[0] if statuses else '-'}"


def metro_equipment() -> str:
    counts = metro.fetch_equipment_counts()
    return (
        ", ".join(f"{c.name[:16]}: {c.active} aktif/{c.inactive} arizali" for c in counts)
        or "sayim yok"
    )


def metro_faults() -> str:
    faults = metro.fetch_faulty_equipment("Asansör")
    stamps = [f.date for f in faults if f.date]
    return f"{len(faults)} asansor arizasi | en yeni: {lag(max(stamps) if stamps else None)}"


def metro_stations() -> str:
    facilities = metro.fetch_station_facilities()
    lifts = sum(f.lifts for f in facilities)
    return f"{len(facilities)} istasyon kaydi, toplam {lifts} asansor"


def air_quality() -> str:
    stations = city.fetch_aqi_stations()
    filled = checked = 0
    for station in stations[:6]:
        checked += 1
        try:
            if city.fetch_air_quality(station) is not None:
                filled += 1
        except LiveDataError:
            continue
    return f"{len(stations)} istasyon, {checked} denendi, {filled} dolu okuma"


for name, fn in [
    ("ispark liste", parking),
    ("ispark detay", parking_detail),
    ("trafik indeksi", traffic),
    ("iett duyurular", iett_alerts),
    ("iett sefer saati", iett_timetable),
    ("metro duyurular", metro_alerts),
    ("metro servis durumu", metro_status),
    ("metro ekipman sayimi", metro_equipment),
    ("metro asansor arizalari", metro_faults),
    ("metro istasyon donanimi", metro_stations),
    ("hava kalitesi", air_quality),
]:
    run(name, fn)

print(f"\nsaat: {NOW:%Y-%m-%d %H:%M:%S}")
