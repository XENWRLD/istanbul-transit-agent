"""Şehir kaynakları: İSPARK otoparkları, trafik indeksi, hava kalitesi.

Üçü de REST ve üçü de ortak katmanı kullanıyor (`clients/base.py`):
zaman aşımı, geçici hatada yeniden deneme, kaynak bazlı TTL önbellek.

Her kaynağın kendi tuhaflığı ölçüldü ve burada ele alınıyor:

- **İSPARK** koordinatları **metin** olarak veriyor (`"41.0246"`) ve bazı
  otoparklarda boş. `capacity`/`emptyCapacity` tamsayı ama `isOpen` 0/1.
- **Trafik indeksi** 24 saatlik geçmişi 5 dakikalık adımlarla döndürüyor
  (288 kayıt). "Şu anki trafik" demek, **en yeni** kaydı almak ve
  yayımlanma saatini birlikte söylemek demek.
- **Hava kalitesi** istasyon listesinde konumu WKT olarak veriyor
  (`POINT (29.02 41.10)`) ve ölçüm listesinde **çoğu kayıt null**
  (`AQI: null`): 721 kaydın büyük kısmı boş. En yeni **dolu** okumayı
  bulmak gerekiyor, yoksa "veri yok" demek gerekir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from istanbul_agent.clients.base import LiveDataError, fetch_json
from istanbul_agent.config import get_settings
from istanbul_agent.store.geo import haversine_m, parse_coordinate

__all__ = [
    "AirQualityReading",
    "AirQualityStation",
    "CarPark",
    "TrafficIndex",
    "fetch_air_quality",
    "fetch_aqi_stations",
    "fetch_car_parks",
    "fetch_park_detail",
    "fetch_traffic_index",
    "nearest_station",
    "parse_air_quality",
    "parse_aqi_stations",
    "parse_car_parks",
    "parse_traffic_index",
]

ISPARK_LIST = "/ispark/Park"
ISPARK_DETAIL = "/ispark/ParkDetay"
# 1 = İstanbul geneli, 5M = 5 dakikalık adım (Faz 0'da doğrulanan yol).
TRAFFIC_INDEX = "/tkmservices/api/TrafficData/v1/TrafficIndexHistory/1/5M"
AQI_STATIONS = "/havakalitesi/OpenDataPortalHandler/GetAQIStations"
AQI_BY_STATION = "/havakalitesi/OpenDataPortalHandler/GetAQIByStationId"

# `POINT (29.024512 41.100072)` — WKT'de boylam önce gelir.
_WKT_POINT = re.compile(r"POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)", re.IGNORECASE)

# Trafik indeksi eşikleri. İndeks 0-100 arası bir yoğunluk ölçüsü; bu
# etiketler yorumlama kolaylığı için, kaynakta tanımlı değil — bu yüzden
# araç çıktısında ham sayı da veriliyor.
TRAFFIC_LABELS = ((20, "akıcı"), (40, "hafif yoğun"), (60, "yoğun"), (80, "çok yoğun"))


def _url(path: str) -> str:
    return f"{get_settings().ibb_api_base_url.rstrip('/')}{path}"


# ------------------------------------------------------------------ İSPARK


@dataclass(frozen=True)
class CarPark:
    """Bir İSPARK otoparkı."""

    park_id: int
    name: str
    district: str
    lat: float | None
    lon: float | None
    capacity: int | None
    empty: int | None
    park_type: str
    work_hours: str
    free_minutes: int | None
    is_open: bool
    updated_at: str | None = None
    distance_m: float | None = None

    @property
    def reports_occupancy(self) -> bool:
        """Bu otoparkın doluluğu okunabilir mi?

        `emptyCapacity: 0` iki ayrı şey demek olabilir: otopark gerçekten
        dolu, ya da bu otopark doluluk bildirmiyor. Kaynak ikisini
        **ayırt etmiyor** ve ölçüm ikincisine işaret ediyor (11 Kasım
        2026): 239 açık otoparkın 51'i sıfır döndürüyor, sıfırlar ilçeye
        ve tipe göre kümeleniyor (Bayrampaşa 9/10, Bağcılar 7/11, buna
        karşılık Beşiktaş 1/23; kapalı otopark 40/85 sıfır, yol üstü
        0/47). 1310 kapasiteli bir otoparkın tam tamına 0 boş yer
        bildirmesi doluluk değil, suskunluk. Detay ucu da aynı sıfırı
        veriyor; zaman damgası taze çünkü damga tüm partiye basılıyor.

        Bu yüzden sıfır "belirsiz" sayılıyor: `occupancy_percent` None
        döner, ham `empty` değeri yine açıkta durur. Yüzde uydurmak
        yerine bilinmediğini söylemek projenin kuralı (karar 0013).
        """
        if self.capacity is None or self.empty is None:
            return False
        return self.empty > 0

    @property
    def occupancy_percent(self) -> int | None:
        """Doluluk yüzdesi; kapasite yoksa ya da doluluk belirsizse `None`."""
        if not self.capacity or not self.reports_occupancy:
            return None
        used = max(0, self.capacity - (self.empty or 0))
        return round(100 * used / self.capacity)


def parse_car_parks(rows: Any) -> list[CarPark]:
    """`/ispark/Park` yanıtını çevir.

    Koordinatı okunamayan otopark **atılmıyor**: adı ve doluluğu yine
    işe yarar, yalnızca mesafe hesabına girmez.
    """
    parks: list[CarPark] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        parks.append(
            CarPark(
                park_id=int(row.get("parkID") or 0),
                name=str(row.get("parkName") or "").strip(),
                district=str(row.get("district") or "").strip(),
                lat=parse_coordinate(row.get("lat")),
                lon=parse_coordinate(row.get("lng")),
                capacity=_as_int(row.get("capacity")),
                empty=_as_int(row.get("emptyCapacity")),
                park_type=str(row.get("parkType") or "").strip(),
                work_hours=str(row.get("workHours") or "").strip(),
                free_minutes=_as_int(row.get("freeTime")),
                is_open=bool(row.get("isOpen")),
                updated_at=str(row.get("updateDate") or "").strip() or None,
            )
        )
    return parks


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_car_parks() -> list[CarPark]:
    """Bütün otoparklar ve anlık boş kapasiteleri (240 otopark ölçüldü)."""
    return parse_car_parks(fetch_json(_url(ISPARK_LIST), source="ispark"))


def fetch_park_detail(park_id: int) -> CarPark | None:
    """Tek bir otoparkın detayı; `updateDate` bu yanıtta var."""
    rows = fetch_json(_url(ISPARK_DETAIL), source="ispark", params={"id": park_id})
    parks = parse_car_parks(rows)
    return parks[0] if parks else None


# ------------------------------------------------------------------ trafik


@dataclass(frozen=True)
class TrafficIndex:
    """Trafik indeksinin en yeni değeri ve kısa geçmişi."""

    value: int
    measured_at: str
    previous: tuple[tuple[str, int], ...]

    @property
    def label(self) -> str:
        for threshold, name in TRAFFIC_LABELS:
            if self.value < threshold:
                return name
        return "çok yoğun"

    @property
    def trend(self) -> str:
        """Son yarım saatte artıyor mu azalıyor mu?

        Tek bir geçmiş nokta da karşılaştırmaya yeter; ilk sürüm iki nokta
        istiyordu ve kısa geçmişte gereksizce "bilinmiyor" diyordu.
        Hiç geçmiş yoksa eğilim gerçekten bilinmiyor.
        """
        if not self.previous:
            return "bilinmiyor"
        older = self.previous[-1][1]
        if self.value > older + 2:
            return "artıyor"
        if self.value < older - 2:
            return "azalıyor"
        return "sabit"


def parse_traffic_index(rows: Any, *, history: int = 6) -> TrafficIndex:
    """`TrafficIndexHistory` yanıtını çevir.

    Yanıt 24 saatlik geçmişi veriyor ve **en yeni kayıt başta** geliyor
    (ölçüm: 288 kayıt, 5 dakikalık adım). Yine de sıralamaya güvenmek
    yerine tarihe göre sıralanıyor: kaynağın sırası değişirse "şu anki
    trafik" sessizce 24 saat önceki değer olurdu.
    """
    points: list[tuple[str, int]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        value = _as_int(row.get("TrafficIndex"))
        when = str(row.get("TrafficIndexDate") or "").strip()
        if value is None or not when:
            continue
        points.append((when, value))
    if not points:
        raise LiveDataError("traffic", "Trafik indeksi yanıtında kullanılabilir kayıt yok.")

    def key(item: tuple[str, int]) -> datetime:
        try:
            return datetime.fromisoformat(item[0])
        except ValueError:
            return datetime.min

    points.sort(key=key, reverse=True)
    newest_at, newest = points[0]
    return TrafficIndex(
        value=newest, measured_at=newest_at, previous=tuple(points[1 : 1 + history])
    )


def fetch_traffic_index() -> TrafficIndex:
    """İstanbul geneli trafik indeksi."""
    return parse_traffic_index(fetch_json(_url(TRAFFIC_INDEX), source="traffic"))


# ------------------------------------------------------------------ hava kalitesi


@dataclass(frozen=True)
class AirQualityStation:
    """Bir hava kalitesi ölçüm istasyonu."""

    station_id: str
    name: str
    address: str
    lat: float | None
    lon: float | None
    distance_m: float | None = None


@dataclass(frozen=True)
class AirQualityReading:
    """Bir istasyonun en yeni **dolu** okuması."""

    station: AirQualityStation
    aqi: int
    concentration: float | None
    read_at: str
    empty_readings: int

    @property
    def label(self) -> str:
        """AQI bandı (EPA ölçeği)."""
        for threshold, name in (
            (51, "iyi"),
            (101, "orta"),
            (151, "hassas gruplar için sağlıksız"),
            (201, "sağlıksız"),
            (301, "çok sağlıksız"),
        ):
            if self.aqi < threshold:
                return name
        return "tehlikeli"


def parse_aqi_stations(rows: Any) -> list[AirQualityStation]:
    """İstasyon listesini çevir; konum WKT olarak geliyor."""
    stations: list[AirQualityStation] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        lat = lon = None
        match = _WKT_POINT.search(str(row.get("Location") or ""))
        if match:
            # WKT'de boylam önce gelir.
            lon = parse_coordinate(match.group(1))
            lat = parse_coordinate(match.group(2))
        stations.append(
            AirQualityStation(
                station_id=str(row.get("Id") or "").strip(),
                name=str(row.get("Name") or "").strip(),
                address=str(row.get("Adress") or "").strip(),
                lat=lat,
                lon=lon,
            )
        )
    return stations


def fetch_aqi_stations() -> list[AirQualityStation]:
    """Hava kalitesi istasyonları (28 istasyon ölçüldü)."""
    return parse_aqi_stations(fetch_json(_url(AQI_STATIONS), source="air_quality"))


def nearest_station(
    stations: list[AirQualityStation], lat: float, lon: float
) -> AirQualityStation | None:
    """Verilen konuma en yakın, koordinatı bilinen istasyon."""
    located = [
        station for station in stations if station.lat is not None and station.lon is not None
    ]
    if not located:
        return None
    best = min(located, key=lambda s: haversine_m(lat, lon, s.lat or 0, s.lon or 0))
    distance = haversine_m(lat, lon, best.lat or 0, best.lon or 0)
    return AirQualityStation(
        station_id=best.station_id,
        name=best.name,
        address=best.address,
        lat=best.lat,
        lon=best.lon,
        distance_m=round(distance, 1),
    )


def parse_air_quality(rows: Any, station: AirQualityStation) -> AirQualityReading | None:
    """En yeni **dolu** okumayı bul.

    Ölçüm: 721 kaydın büyük kısmında `AQI` ve `Concentration` null. Boş
    kayıtları atlayıp en yeni dolu olanı almak gerekiyor; hiç dolu kayıt
    yoksa `None` dönüyor ve araç "bu istasyonda okuma yok" diyor —
    sıfır ya da son bilinen değeri uydurmak yerine.
    """
    usable: list[tuple[datetime, dict[str, Any]]] = []
    empty = 0
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        aqi = row.get("AQI")
        if aqi is None:
            empty += 1
            continue
        when = str(row.get("ReadTime") or "").strip()
        try:
            stamp = datetime.fromisoformat(when)
        except ValueError:
            empty += 1
            continue
        usable.append((stamp, row))
    if not usable:
        return None
    usable.sort(key=lambda pair: pair[0], reverse=True)
    _, newest = usable[0]
    concentration = newest.get("Concentration")
    return AirQualityReading(
        station=station,
        aqi=int(float(newest["AQI"])),
        concentration=float(concentration) if concentration is not None else None,
        read_at=str(newest.get("ReadTime") or ""),
        empty_readings=empty,
    )


def fetch_air_quality(station: AirQualityStation) -> AirQualityReading | None:
    """Bir istasyonun en yeni dolu okuması."""
    rows = fetch_json(
        _url(AQI_BY_STATION), source="air_quality", params={"StationId": station.station_id}
    )
    return parse_air_quality(rows, station)
