"""MCP sunucusu: toplu taşıma araçları.

Şu an sekiz araç sunuyor:

- `geocode_place` — ad → konum (durak, istasyon, iskele)
- `find_nearby_stops` — bir yerin çevresindeki duraklar
- `get_lines_at_stop` — bir duraktan geçen hatlar
- `get_line_route` — bir hattın sıralı durakları ve servis penceresi
- `get_rail_travel_time` — iki istasyon arası süre ve güzergâh
- `get_next_departures` — bir hattın ilk/son seferi ve sefer aralığı
- `get_service_alerts` — canlı sefer duyuruları (İETT + Metro İstanbul)
- `get_station_accessibility` — asansör/yürüyen merdiven sayısı ve anlık arızalar

Tarife ve süre verisi yerel depodan (`data/transit.duckdb`) geliyor;
son üç araç canlı API'den (`clients/`) okuyor: ortak zaman aşımı, geçici
hatada yeniden deneme, kaynak bazlı TTL önbellek. Canlı kaynağa
ulaşılamazsa araç bunu **açıkça** döner; boş liste döndürmek "duyuru yok"
demekle aynı şey olurdu (karar 0012). Araç açıklamaları agent'ın araç
seçimini belirliyor,
bu yüzden her açıklamada ne yaptığı, parametreleri ve **ne zaman
kullanılmaması gerektiği** yazıyor (CLAUDE.md kuralı).

    uv run python -m istanbul_agent.servers.transit_server
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from istanbul_agent.clients.metro import Announcement, fetch_announcements
from istanbul_agent.store.departures import DepartureInfo
from istanbul_agent.store.departures import get_next_departures as _get_next_departures
from istanbul_agent.store.line_queries import get_line_route as _get_line_route
from istanbul_agent.store.line_queries import get_lines_at_stop as _get_lines_at_stop
from istanbul_agent.store.places import (
    MODE_LABELS,
    MODE_VOCABULARY,
    find_nearby_places,
    find_places,
    resolve_mode,
)
from istanbul_agent.store.rail_queries import get_rail_travel_time as _get_rail_travel_time
from istanbul_agent.store.rail_queries import load_network
from istanbul_agent.store.station_names import normalize
from istanbul_agent.store.station_search import find_station, suggest_stations

# log_level: the server runs as a subprocess of the agent and its INFO
# lines ("Processing request of type CallToolRequest") land in the user's
# terminal, mixed into the answer.
mcp = FastMCP("istanbul-transit", log_level="WARNING")

# Duyurular canlı API'den geliyor, tarife ise yerel depodan. Duyuru
# çekilemezse araç yine cevap verir ama bunu nota yazar: API kapalıyken
# sessizce "duyuru yok" demek, tarife değişikliğini gizlemek olur.
_ANNOUNCEMENT_TTL_S = 300.0
_announcement_cache: dict[str, object] = {"at": 0.0, "items": [], "error": None}


def _announcements() -> tuple[list[Announcement], str | None]:
    now = time.monotonic()
    if now - float(_announcement_cache["at"]) < _ANNOUNCEMENT_TTL_S and (
        _announcement_cache["items"] or _announcement_cache["error"]
    ):
        return list(_announcement_cache["items"]), _announcement_cache["error"]  # type: ignore[arg-type]
    try:
        items = fetch_announcements()
        error = None
    except Exception as problem:  # httpx errors, Success: false, bad JSON
        items = []
        error = (
            "Metro İstanbul duyuruları alınamadı "
            f"({type(problem).__name__}); tarife değişikliği olup olmadığı bilinmiyor."
        )
    _announcement_cache.update({"at": now, "items": items, "error": error})
    return items, error


# --------------------------------------------------------------- modeller


class StationNotFound(BaseModel):
    """Aranan istasyon bulunamadı; en yakın adaylar döner."""

    ok: Literal[False] = False
    error: Literal["istasyon_bulunamadi"] = "istasyon_bulunamadi"
    asked: str = Field(description="Kullanıcının yazdığı ad")
    candidates: list[str] = Field(
        default_factory=list,
        description=(
            "En yakın 3 istasyon adı; istasyon başka bir hatta varsa onun yerine o hatların kodları"
        ),
    )
    message: str


class LiveDataUnavailable(BaseModel):
    """Canlı kaynağa ulaşılamadı.

    Boş liste yerine hata dönüyor: "duyuru yok" ile "duyurulara
    ulaşamadım" aynı şey değil (karar 0012).
    """

    ok: Literal[False] = False
    error: Literal["canli_veri_yok"] = "canli_veri_yok"
    source: str
    message: str


class LineNotFound(BaseModel):
    """Aranan hat bulunamadı veya o hat için tarife verisi yok."""

    ok: Literal[False] = False
    error: Literal["hat_bulunamadi", "tarife_yok"] = "hat_bulunamadi"
    asked: str
    candidates: list[str] = Field(default_factory=list, description="Bilinen hat kodları")
    message: str
    notes: list[str] = Field(default_factory=list)


class TravelLegOut(BaseModel):
    """Güzergâhın tek bir hattaki bölümü."""

    line: str
    from_station: str
    to_station: str
    stop_count: int
    minutes: int
    method: str = Field(description="gtfs | gtfs_calibrated | estimated")


class TravelTimeOut(BaseModel):
    """`get_rail_travel_time` cevabı."""

    ok: Literal[True] = True
    from_station: str
    to_station: str
    minutes: int
    legs: list[TravelLegOut]
    transfers: int = Field(description="Aktarma sayısı")
    transfer_stations: list[str] = Field(default_factory=list)
    transfer_minutes: int = Field(0, description="Aktarmalar için eklenen toplam süre")
    method: str = Field(description="gtfs | gtfs_calibrated | estimated | transfer")
    estimated: bool = Field(description="Süre tahmin edildiyse true")
    error_margin: str | None = Field(
        None, description="Tahminlerde hata payı, ör. '±2 dk (en kötü ±5 dk)'"
    )
    is_stale: bool = Field(description="Süreler 2020 tarihli GTFS beslemesinden ise true")
    source: str
    data_as_of: str
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DirectionOut(BaseModel):
    """Bir hattın tek yönündeki servis bilgisi."""

    direction: str = Field(description="Yönün son durağı")
    terminal: str | None = Field(None, description="Saatlerin ölçüldüğü uç istasyon")
    offset_minutes: int | None = Field(
        None, description="Uç istasyondan sorulan istasyona kadar kaydırma (dk)"
    )
    first_departure: str | None = Field(
        None,
        description=(
            "Bu yönde sorulan istasyondan ilk kalkış. Sorulan istasyon bu yönün son "
            "durağıysa kalkış yoktur, null olur ve saat first_arrival'a yazılır."
        ),
    )
    last_departure: str | None = Field(
        None,
        description=(
            "Bu yönde son kalkış. Gece boyunca çalışıyorsa veya istasyon bu yönün son "
            "durağıysa null olur."
        ),
    )
    first_arrival: str | None = Field(
        None, description="Sorulan istasyon bu yönün son durağıysa oraya ilk varış"
    )
    last_arrival: str | None = Field(
        None, description="Sorulan istasyon bu yönün son durağıysa oraya son varış"
    )
    runs_all_night: bool = False
    headway_minutes: int | None = Field(None, description="Sorulan saatteki sefer aralığı")
    headway_hour: int | None = Field(None, description="Aralığın ait olduğu saat")
    running_now: bool
    expected_wait_minutes: int | None
    night_service: str | None = Field(None, description="Varsa gece servisi penceresi")
    is_arrival_at_station: bool = Field(
        False,
        description=(
            "Sorulan istasyon bu yönün son durağı: saatler oraya VARIŞ saatidir, "
            "o istasyondan kalkış saati değildir. 'X'ten son sefer kaçta' sorusunda "
            "bu yönü kullanma, diğer yönü kullan."
        ),
    )


class BusWindowOut(BaseModel):
    """Bir otobüs hattının bir yön + gün tipindeki servis penceresi."""

    direction: str = Field(description="Yön kodu (D: gidiş, G: dönüş — beslemenin kodu)")
    day_type: str = Field(description="hafta_ici | cumartesi | pazar")
    first_departure: str = Field(
        description="İlk duraktan ilk **gündüz** kalkışı (04:00 öncesi gece servisidir)"
    )
    last_departure: str = Field(description="İlk duraktan son gündüz kalkışı")
    night_service: str | None = Field(
        None,
        description=(
            'Varsa 04:00 öncesi gece servisi penceresi (`"00:00-00:20"`). '
            "Bu saatler ilk sefer DEĞİLDİR; 'ilk sefer kaçta' sorusunda "
            "`first_departure` kullanılır"
        ),
    )
    night_only: bool = Field(
        False, description="Bu yönde gündüz servisi yok, yalnızca gece seferi var"
    )
    trips: int = Field(description="O yön ve gün tipindeki sefer sayısı (gece dahil)")
    headway_minutes: int | None = Field(
        None, description="Kalkışlar arası ortanca süre; 4 seferden az veriden üretilmez"
    )
    route_variants: int = Field(description="Bu yönde kaç güzergâh varyantı var")


class BusDeparturesOut(BaseModel):
    """`get_next_departures` cevabı, otobüs hatları için."""

    ok: Literal[True] = True
    line: str
    line_name: str | None = None
    mode: Literal["otobus"] = "otobus"
    today: str = Field(description="Bugünün gün tipi")
    windows: list[BusWindowOut]
    source: str = "İETT Planlanan Sefer Saati (veri seti 350, canlı SOAP)"
    data_as_of: str
    notes: list[str] = Field(default_factory=list)


class DeparturesOut(BaseModel):
    """`get_next_departures` cevabı."""

    ok: Literal[True] = True
    line: str
    station: str | None
    day_type: str = Field(description="pzt_pers | cuma | cumartesi | pazar (veya hafta_ici)")
    asked_at: str
    directions: list[DirectionOut]
    hours_source: str
    hours_is_stale: bool = Field(description="İlk/son sefer saatleri eski veriden ise true")
    hours_as_of: str
    headway_source: str
    headway_is_stale: bool = Field(description="Sefer aralığı eski veriden ise true")
    headway_as_of: str
    alerts: list[str] = Field(
        default_factory=list, description="Tarifeyle ilgili Metro İstanbul duyuruları"
    )
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PlaceNotFound(BaseModel):
    """Aranan yer bulunamadı; en yakın adaylar ve ilgili adlar döner."""

    ok: Literal[False] = False
    error: Literal["yer_bulunamadi"] = "yer_bulunamadi"
    asked: str = Field(description="Kullanıcının yazdığı ad")
    candidates: list[str] = Field(default_factory=list, description="En yakın 3 durak/istasyon adı")
    related: list[str] = Field(
        default_factory=list,
        description=(
            "Sorguyla başlayan tam adlar, ör. 'Üsküdar' için 'Üsküdar ŞH.', "
            "'Üsküdar Turyol'. İskele adları işletmeci ekiyle kayıtlı."
        ),
    )
    message: str


class InvalidMode(BaseModel):
    """Verilen `mode` tabloda yok; geçerli değerler döner.

    Boş sonuç dönmek yerine hata dönmesi bilinçli: model ilk denemede
    `mode="iskele"` verdi, arama boş döndü ve model kullanıcıya "böyle bir
    yer yok" dedi. Geçerli değerleri sayan bir hata, modelin aracı doğru
    parametreyle yeniden çağırmasını sağlıyor.
    """

    ok: Literal[False] = False
    error: Literal["gecersiz_mod"] = "gecersiz_mod"
    asked: str
    valid_modes: list[str] = Field(default_factory=list)
    message: str


class PlaceOut(BaseModel):
    """Tek bir yer: aynı adı taşıyan ve birbirine yakın noktalar birleştirilmiş."""

    name: str
    mode: str = Field(
        description="metro | tramvay | funikuler | teleferik | tren | vapur | otobus | minibus | dolmus"
    )
    mode_label: str = Field(description="Modun Türkçe adı, ör. 'otobüs durağı'")
    lat: float
    lon: float
    point_count: int = Field(
        description="Bu yere katılan nokta sayısı (yönler ve peronlar ayrı kayıtlı)"
    )
    codes: list[str] = Field(default_factory=list, description="Durak kodları veya hat kodları")
    details: list[str] = Field(default_factory=list)
    distance_m: float | None = Field(None, description="Sorulan konuma uzaklık (metre)")
    source: str
    data_as_of: str
    is_stale: bool


class GeocodeOut(BaseModel):
    """`geocode_place` cevabı."""

    ok: Literal[True] = True
    asked: str
    places: list[PlaceOut]
    match_method: str | None = Field(
        None, description="tam ad | yazım farkı | kelime sırası | ön ek | benzerlik N"
    )
    total_points: int = Field(description="Ada uyan toplam nokta sayısı")
    related: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class NearbyOut(BaseModel):
    """`find_nearby_stops` cevabı."""

    ok: Literal[True] = True
    around: str = Field(description="Çevresi arananan yerin adı veya koordinatı")
    lat: float
    lon: float
    radius_m: float
    stops: list[PlaceOut]
    notes: list[str] = Field(default_factory=list)


class LineAtStopOut(BaseModel):
    """Bir duraktan geçen tek hat."""

    line: str
    mode: str
    long_name: str | None = None
    directions: list[str] = Field(
        default_factory=list, description="Seferlerin son durakları (yön levhası)"
    )
    variant_count: int = Field(
        description="Bu hattın kaç ayrı güzergâh varyantı bu duraktan geçiyor"
    )
    trip_count: int = Field(description="Haftalık tarifede bu duraktan geçen sefer sayısı")
    is_stale: bool


class StopLinesOut(BaseModel):
    """`get_lines_at_stop` cevabı."""

    ok: Literal[True] = True
    stop: str
    mode: str
    mode_label: str
    lines: list[LineAtStopOut]
    line_count: int
    source: str | None = None
    data_as_of: str | None = None
    notes: list[str] = Field(default_factory=list)


class RouteStopOut(BaseModel):
    """Güzergâhtaki bir durak."""

    sequence: int
    name: str
    lat: float | None = None
    lon: float | None = None


class ServiceWindowOut(BaseModel):
    """Bir yöndeki servis penceresi."""

    service: str = Field(description="Servis günü: WEEKDAYS | SATURDAY | SUNDAY")
    trips: int
    first_departure: str | None = Field(None, description="İlk duraktan ilk kalkış (HH:MM)")
    last_departure: str | None = Field(None, description="İlk duraktan son kalkış (HH:MM)")
    median_duration_minutes: int | None = Field(
        None, description="Baştan sona yolculuk süresinin ortancası"
    )


class LineRouteOut(BaseModel):
    """Bir hattın tek yönü."""

    line: str
    mode: str
    direction_id: str | None = None
    direction_name: str | None = Field(None, description="Yönün son durağı")
    stops: list[RouteStopOut]
    stop_count: int
    variant_count: int = Field(description="Bu yöndeki güzergâh varyantı sayısı")
    trip_count: int
    service_windows: list[ServiceWindowOut] = Field(default_factory=list)
    source: str
    data_as_of: str
    is_stale: bool
    notes: list[str] = Field(default_factory=list)


class LineRoutesOut(BaseModel):
    """`get_line_route` cevabı: hattın her yönü ayrı."""

    ok: Literal[True] = True
    line: str
    directions: list[LineRouteOut]
    notes: list[str] = Field(default_factory=list)


class StoreNotReady(BaseModel):
    """Yerel veri deposu kurulmamış.

    Araçların hepsi `data/transit.duckdb` üzerinden çalışıyor. Depo yokken
    DuckDB ham bir `IOException` atıyordu ve bu, kurulum adımını atlayan
    kullanıcıya yığın izi olarak dönüyordu. Artık ne çalıştıracağını
    söyleyen tipli bir hata dönüyor (karar 0011).
    """

    ok: Literal[False] = False
    error: Literal["depo_yok"] = "depo_yok"
    message: str


def _store_guard() -> StoreNotReady | None:
    """Depo kurulu değilse araçların döndüreceği hata."""
    from istanbul_agent.store.db import missing_store_message, store_ready

    if store_ready():
        return None
    return StoreNotReady(message=missing_store_message())


# ----------------------------------------------------------------- araçlar


@mcp.tool()
def get_rail_travel_time(
    from_station: str, to_station: str
) -> TravelTimeOut | StationNotFound | StoreNotReady:
    """İki raylı sistem istasyonu arasındaki yolculuk süresini ve güzergâhı verir.

    Metro, tramvay, füniküler, teleferik ve Marmaray'ı kapsar. Gerekirse
    aktarmalı güzergâh kurar (ör. Kadıköy → Taksim: M4 + Marmaray + M2) ve
    her bacağın hattını, durak sayısını ve süresini döner. Aktarma süresi
    (bekleme + yürüme) toplama dahildir.

    Parametreler:
        from_station: kalkış istasyonu adı. Kullanıcının yazdığı hali
            yeterli ("kadıköy", "uskudar"); bulunamazsa en yakın 3 aday
            döner, o zaman kullanıcıya hangisini kastettiğini sor.
        to_station: varış istasyonu adı, aynı kurallar.

    Cevapta `estimated: true` varsa süre 2020 beslemesinde olmayan
    istasyonlar için koordinat ve ortalama hızdan tahmin edilmiştir;
    `error_margin` hata payını verir. `is_stale` süre verisinin eski
    olduğunu söyler — ikisini de kullanıcıya aktar.

    NE ZAMAN KULLANMA:
    - Otobüs, dolmuş, vapur veya yürüme süresi için kullanma; bu araç
      yalnızca raylı sistemi bilir.
    - İlk/son sefer saati veya "kaçta kalkıyor / ne sıklıkla geliyor"
      sorularında kullanma — `get_next_departures` kullan. Bu araç yolculuk
      süresini verir, saat vermez.
    - Adres veya semt arası yol tarifi için kullanma; parametreler istasyon
      adı olmalı.
    - Anlık gecikme veya arıza durumu için kullanma; süreler tarife
      verisinden gelir, canlı değildir.
    """
    guard = _store_guard()
    if guard is not None:
        return guard
    network = load_network()
    for raw in (from_station, to_station):
        match = find_station(network, raw)
        if not match.found:
            return StationNotFound(
                asked=raw,
                candidates=list(match.candidates or suggest_stations(network, raw)),
                message=(
                    f"'{raw}' adlı bir raylı sistem istasyonu bulunamadı. "
                    "Adaylar arasından doğru olanı kullanıcıya sor."
                ),
            )

    result = _get_rail_travel_time(from_station, to_station, network=network)
    if result.seconds is None:
        return StationNotFound(
            asked=f"{from_station} → {to_station}",
            candidates=[],
            message="; ".join(result.notes) or "Bu iki istasyon arasında raylı bağlantı yok.",
        )

    return TravelTimeOut(
        from_station=result.from_station,
        to_station=result.to_station,
        minutes=result.minutes or 0,
        legs=[
            TravelLegOut(
                line=leg.line,
                from_station=leg.from_station,
                to_station=leg.to_station,
                stop_count=leg.stop_count,
                minutes=round(leg.seconds / 60),
                method=leg.method,
            )
            for leg in result.legs
        ],
        transfers=max(0, len(result.legs) - 1),
        transfer_stations=(result.transfer_station.split(" → ") if result.transfer_station else []),
        transfer_minutes=round(result.transfer_penalty_s / 60),
        method=result.method,
        estimated=result.estimated,
        error_margin=result.error_margin.phrase() if result.error_margin else None,
        is_stale=result.is_stale,
        source=result.source,
        data_as_of=result.data_as_of,
        warnings=list(result.warnings),
        notes=list(result.notes),
    )


def _bus_departures(line: str) -> BusDeparturesOut | LiveDataUnavailable | None:
    """İETT'den otobüs sefer saatleri; hat bilinmiyorsa `None`.

    `None` dönmesi "bu bir otobüs hattı değil" demek; çağıran o zaman
    raylı sistem için hat bulunamadı hatası döndürüyor.
    """
    from istanbul_agent.clients import iett
    from istanbul_agent.clients.base import LiveDataError

    code = line.strip().upper()
    try:
        departures = iett.fetch_planned_departures(code)
    except LiveDataError as problem:
        return _live_error(problem)
    if not departures:
        return None

    today = iett.today_day_type()
    windows = iett.summarise_departures(departures)
    notes = [
        "Saatler hattın **ilk durağından** kalkışı gösterir; ara duraklardan geçiş "
        "saatini bu kaynak vermiyor.",
    ]
    night = [window for window in windows if window.night_service]
    if night:
        notes.append(
            "04:00 öncesi kalkışlar gece servisi sayıldı ve `night_service` alanına "
            "ayrıldı; `first_departure` günün ilk seferidir. Besleme gece seferini "
            "`24:20` değil `00:20` diye yazdığı için bunlar en erken kalkış gibi "
            "görünüyor ama değil."
        )
    thin = [window for window in windows if window.headway_minutes is None]
    if thin:
        notes.append(
            f"{len(thin)} yön/gün tipinde 4 seferden az kayıt var, sefer aralığı üretilmedi."
        )
    return BusDeparturesOut(
        line=code,
        line_name=departures[0].line_name or None,
        today=today,
        windows=[
            BusWindowOut(
                direction=window.direction,
                day_type=window.day_type,
                first_departure=window.first,
                last_departure=window.last,
                night_service=window.night_service,
                night_only=window.night_only,
                trips=window.trips,
                headway_minutes=window.headway_minutes,
                route_variants=len(window.route_codes),
            )
            for window in windows
        ],
        data_as_of=time.strftime("%Y-%m-%d %H:%M"),
        notes=notes,
    )


def _direction_out(info: DepartureInfo, station: str | None) -> DirectionOut:
    """One direction of the answer, as the tool reports it.

    When the queried station is this direction's last stop, the shifted
    times are arrivals: the train terminates there, so there is no departure
    to report. They go into `first_arrival` / `last_arrival` and the
    departure fields stay empty -- a number in `last_departure` is a number
    a caller will quote as "son kalkış", which is how a model answered
    "Kadıköy'den son M4" with 00:49, the minute the last train from the
    other end *reaches* Kadıköy.
    """
    arrival = bool(station and normalize(info.direction_name) == normalize(station))
    return DirectionOut(
        direction=info.label,
        terminal=info.terminal,
        offset_minutes=info.offset_minutes,
        first_departure=None if arrival else info.first,
        last_departure=None if arrival else info.last,
        first_arrival=info.first if arrival else None,
        last_arrival=info.last if arrival else None,
        runs_all_night=info.all_night,
        headway_minutes=info.headway_minutes,
        headway_hour=info.headway_hour,
        running_now=info.running_now,
        expected_wait_minutes=info.expected_wait_minutes,
        night_service=(f"{info.night_first}-{info.night_last}" if info.night_first else None),
        is_arrival_at_station=arrival,
    )


@mcp.tool()
def get_next_departures(
    line: str, station: str | None = None, at: str | None = None
) -> (
    DeparturesOut
    | BusDeparturesOut
    | LineNotFound
    | StationNotFound
    | StoreNotReady
    | LiveDataUnavailable
):
    """Bir hattın ilk/son seferini ve sefer aralığını verir (raylı sistem ve otobüs).

    Yön bazında cevap verir: her yön için ilk sefer, son sefer, o saatteki
    sefer aralığı, servisin şu an çalışıp çalışmadığı ve ortalama bekleme.
    Gün tipini (Pazartesi-Perşembe / Cuma / Cumartesi / Pazar) kendisi
    belirler. Gece metrosu olan hatlarda Cuma ve Cumartesi için "son sefer
    yok, gece boyunca çalışıyor" döner.

    Parametreler:
        line: hat kodu — `M2`, `M4`, `T1`, `F1`, `Marmaray` gibi.
        station: istasyon adı (opsiyonel). Verilirse saatler o istasyona
            göre kaydırılır; verilmezse hattın uç istasyon saatleri döner.
            "Kadıköy'den son metro kaçta" gibi sorularda mutlaka ver.
        at: "YYYY-MM-DD HH:MM" biçiminde an (opsiyonel). Verilmezse şu an
            kullanılır. Kullanıcı "cumartesi gecesi" gibi bir zaman
            söylüyorsa bunu doldur.

    Cevapta `hours_is_stale` ve `headway_is_stale` **ayrı** alanlardır:
    saatler elle doğrulanmış olabilirken sefer aralığı hâlâ 2020 tarihli
    GTFS beslemesinden gelebilir. Hangisi eskiyse kullanıcıya söyle.
    `alerts` doluysa duyuru hesaplanan tarifenin önüne geçer.

    NE ZAMAN KULLANMA:
    - Yolculuk süresi için kullanma ("kaç dakika sürer") —
      `get_rail_travel_time` kullan. Bu araç saat ve sıklık verir, süre
      vermez.
    - Belirli bir trenin tam kalkış saatini sormak için kullanma; cevap
      ilk/son sefer ve aralıktır, tek tek sefer saatleri değildir.
    - Otobüs, vapur veya dolmuş hatları için kullanma; yalnızca raylı
      sistem hatlarını bilir.
    - İki istasyon arası gidiş için kullanma; tek hat hakkında bilgi verir,
      güzergâh kurmaz.
    """
    guard = _store_guard()
    if guard is not None:
        return guard
    moment: datetime | None = None
    if at:
        for shape in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
            try:
                moment = datetime.strptime(at.strip(), shape)
                break
            except ValueError:
                continue

    network = load_network()
    known_lines = sorted({s.line for s in network.stations})
    # Answer with the feed's own spelling of the line ("Marmaray", not
    # "MARMARAY"), so station lookups and the answer agree with the store.
    by_code = {known.upper(): known for known in known_lines}
    code = by_code.get(line.strip().upper(), line.strip().upper())
    if code not in known_lines:
        # Raylı sistemde yok: otobüs hattı olabilir. Otobüs tarifesi yerel
        # depoda değil, İETT'nin canlı servisinde (karar 0007: GTFS
        # beslemesi ara duraklara saat vermiyor, yalnızca sefer başına iki
        # saat veriyor).
        bus = _bus_departures(line)
        if bus is not None:
            return bus
        return LineNotFound(
            error="hat_bulunamadi",
            asked=line,
            candidates=known_lines,
            message=(
                f"'{line}' ne raylı sistem hattı ne de İETT'de sefer saati olan bir otobüs "
                "hattı. Raylı sistemde bilinen hatlar adaylarda listeli."
            ),
        )

    if station:
        match = find_station(network, station, line=code)
        if not match.found:
            # The station may be real but on another line -- "Üsküdar" is on
            # M5 and Marmaray, not M4. Saying only "not found on M4" sends
            # the caller down a dead end, so the lines that do serve it come
            # back as the candidates to retry with.
            elsewhere = find_station(network, station)
            if elsewhere.found and elsewhere.lines:
                return StationNotFound(
                    asked=station,
                    candidates=list(elsewhere.lines),
                    message=(
                        f"'{elsewhere.name}' {code} hattında değil; şu hatlarda var: "
                        f"{', '.join(elsewhere.lines)}. Aynı aracı bu hatlardan biriyle "
                        "yeniden çağır."
                    ),
                )
            return StationNotFound(
                asked=station,
                candidates=list(match.candidates or suggest_stations(network, station, line=code)),
                message=f"'{station}' {code} hattında bulunamadı.",
            )
        station = match.name

    announcements, announcement_error = _announcements()
    result = _get_next_departures(code, station, at=moment, announcements=announcements)
    if announcement_error:
        result.warnings.append(announcement_error)
    if not result.available:
        return LineNotFound(
            error="tarife_yok",
            asked=line,
            candidates=[],
            message=f"{code} için tarife verisi yok.",
            notes=list(result.notes),
        )

    return DeparturesOut(
        line=result.line,
        station=result.station,
        day_type=result.day_type,
        asked_at=result.asked_at,
        directions=[_direction_out(info, result.station) for info in result.directions],
        hours_source=result.hours_source,
        hours_is_stale=result.hours_is_stale,
        hours_as_of=result.hours_as_of,
        headway_source=result.headway_source,
        headway_is_stale=result.headway_is_stale,
        headway_as_of=result.headway_as_of,
        alerts=list(result.alerts),
        warnings=list(result.warnings),
        notes=list(result.notes),
    )


def _place_out(cluster: object) -> PlaceOut:
    """Bir yer kümesini araç cevabına çevir."""
    return PlaceOut(
        name=cluster.name,  # type: ignore[attr-defined]
        mode=cluster.mode,  # type: ignore[attr-defined]
        mode_label=cluster.mode_label,  # type: ignore[attr-defined]
        lat=round(cluster.lat, 6),  # type: ignore[attr-defined]
        lon=round(cluster.lon, 6),  # type: ignore[attr-defined]
        point_count=cluster.point_count,  # type: ignore[attr-defined]
        codes=list(cluster.codes),  # type: ignore[attr-defined]
        details=list(cluster.details),  # type: ignore[attr-defined]
        distance_m=cluster.distance_m,  # type: ignore[attr-defined]
        source=cluster.source,  # type: ignore[attr-defined]
        data_as_of=cluster.data_as_of,  # type: ignore[attr-defined]
        is_stale=cluster.is_stale,  # type: ignore[attr-defined]
    )


@mcp.tool()
def geocode_place(
    query: str, mode: str | None = None
) -> GeocodeOut | PlaceNotFound | InvalidMode | StoreNotReady:
    """Bir durak, istasyon veya iskele adını konuma (enlem/boylam) çevirir.

    Otobüs durakları, metro/tramvay/füniküler/teleferik istasyonları,
    iskeleler, minibüs ve dolmuş durakları aranır. Aynı adı taşıyan ve
    birbirine 300 metreden yakın noktalar tek yer olarak birleştirilir;
    `point_count` kaç kaydın birleştiğini söyler (bir kavşağın iki yönü ayrı
    kayıt). Aynı adlı ama farklı semtteki duraklar ayrı yerler olarak döner.

    Parametreler:
        query: aranan ad. Kullanıcının yazdığı hali yeterli ("kadikoy",
            "ÜSKÜDAR"); bulunamazsa en yakın 3 aday ve sorguyla başlayan
            tam adlar (`related`) döner.
        mode: aramayı bir moda kısıtlar — `metro`, `tramvay`, `funikuler`,
            `teleferik`, `vapur`, `otobus`, `minibus`, `dolmus`. `rayli`
            dördünü (metro + tramvay + füniküler + teleferik) birlikte arar;
            `marmaray` ve `tren` metro istasyonlarına bakar, çünkü Marmaray
            istasyonları ayrı bir mod değil metro kaydı olarak duruyor.
            "Üsküdar" hem metro istasyonu hem otobüs durağı hem iskele
            olduğu için kullanıcı birini kastediyorsa bunu ver.

    İskele adları işletmeci ekiyle kayıtlıdır ("Kadıköy ŞH.", "Üsküdar
    Turyol"); `vapur` modunda arama ad tam tutmazsa `related` listesine bak.

    NE ZAMAN KULLANMA:
    - Adres, semt, mahalle veya cadde aramak için kullanma; bu araç yalnızca
      durak/istasyon/iskele adlarını bilir, adres çözemez.
    - Bir yerin çevresindeki durakları listelemek için kullanma —
      `find_nearby_stops` kullan. Bu araç adı konuma çevirir, çevre aramaz.
    - Hangi hatların geçtiğini sormak için kullanma — `get_lines_at_stop`
      kullan.
    - Yolculuk süresi veya sefer saati için kullanma; bu araç saat ve süre
      vermez.
    """
    guard = _store_guard()
    if guard is not None:
        return guard
    resolved_modes, mode_error = resolve_mode(mode)
    if mode_error:
        return InvalidMode(
            asked=mode or "",
            valid_modes=list(MODE_VOCABULARY),
            message=mode_error,
        )
    search = find_places(query, modes=resolved_modes or None, limit=5)
    if not search.found:
        return PlaceNotFound(
            asked=query,
            candidates=list(search.candidates),
            related=list(search.related),
            message=(
                f"'{query}' adlı bir durak, istasyon veya iskele bulunamadı. "
                "Adaylar ya da ilgili adlar arasından doğru olanı kullanıcıya sor."
            ),
        )
    notes: list[str] = []
    if len(search.clusters) > 1:
        notes.append(
            f"Bu ada uyan {len(search.clusters)} ayrı yer var; mod ve konuma bakarak "
            "hangisini kullandığını cevapta belirt."
        )
    return GeocodeOut(
        asked=query,
        places=[_place_out(cluster) for cluster in search.clusters],
        match_method=search.how,
        total_points=search.total_points,
        related=list(search.related),
        notes=notes,
    )


@mcp.tool()
def find_nearby_stops(
    around: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_m: float = 500.0,
    mode: str | None = None,
    limit: int = 10,
) -> NearbyOut | PlaceNotFound | InvalidMode | StoreNotReady:
    """Bir yerin çevresindeki durak, istasyon ve iskeleleri yakından uzağa listeler.

    Parametreler:
        around: çevresi aranacak yerin adı ("Kadıköy", "Taksim"). Ad önce
            konuma çevrilir; `lat`/`lon` verilmişse bu alan göz ardı edilir.
        lat: enlem. `around` yerine koordinat vermek için `lon` ile birlikte
            kullan.
        lon: boylam, `lat` ile birlikte.
        radius_m: arama yarıçapı, metre. Varsayılan 500, en çok 2000.
        mode: sonucu bir moda kısıtlar — `metro`, `tramvay`, `funikuler`,
            `teleferik`, `vapur`, `otobus`, `minibus`, `dolmus`. `rayli`
            dört raylı modu birlikte verir; `marmaray`/`tren` metro
            istasyonlarına bakar.
        limit: en çok kaç yer dönecek. Varsayılan 10, en çok 50.

    Aynı adlı yakın noktalar tek satırda birleşir; `distance_m` sorulan
    konuma uzaklıktır (kuş uçuşu, yürüme mesafesi değil). `mode` verildiğinde
    merkez de önce o modda aranır.

    NE ZAMAN KULLANMA:
    - Bir adresin çevresini aramak için kullanma; başlangıç noktası bir
      durak/istasyon/iskele adı ya da koordinat olmalı.
    - Hangi hatların geçtiğini öğrenmek için kullanma — bulduğun durağı
      `get_lines_at_stop` ile sor. Bu araç yer listeler, hat listelemez.
    - İki yer arası süre için kullanma — `get_rail_travel_time` kullan;
      buradaki mesafe kuş uçuşudur.
    - Otopark, şarj istasyonu veya bisiklet noktası için kullanma; bu araç
      yalnızca toplu taşıma noktalarını bilir.
    """
    guard = _store_guard()
    if guard is not None:
        return guard
    resolved_modes, mode_error = resolve_mode(mode)
    if mode_error:
        return InvalidMode(
            asked=mode or "",
            valid_modes=list(MODE_VOCABULARY),
            message=mode_error,
        )
    radius = max(50.0, min(float(radius_m), 2000.0))
    count = max(1, min(int(limit), 50))

    if lat is not None and lon is not None:
        origin_name = f"{lat:.5f},{lon:.5f}"
        origin_lat, origin_lon = float(lat), float(lon)
        notes: list[str] = []
    else:
        if not around:
            return PlaceNotFound(
                asked="",
                message="Ya `around` (yer adı) ya da `lat` ve `lon` birlikte verilmeli.",
            )
        # Mod verildiyse merkez **önce o modda** aranıyor (karar 0017).
        # Modsuz arama "Bostancı"yı metro istasyonu olarak buluyor, iskeleler
        # ise 1.7 km uzakta: vapur sorusu varsayılan 500 m yarıçapta boş
        # dönüyordu. `geocode_place` modu arama aşamasında zaten geçiriyordu,
        # bu çağrı yeri geçirmiyordu.
        search = None
        if resolved_modes:
            scoped = find_places(around, modes=resolved_modes, limit=3)
            if scoped.found:
                search = scoped
        if search is None:
            search = find_places(around, limit=3)
        if not search.found:
            return PlaceNotFound(
                asked=around,
                candidates=list(search.candidates),
                related=list(search.related),
                message=(
                    f"'{around}' adlı bir yer bulunamadı, bu yüzden çevresi aranamadı. "
                    "Adaylar arasından doğru olanı kullanıcıya sor."
                ),
            )
        anchor = search.clusters[0]
        origin_name = anchor.name
        origin_lat, origin_lon = anchor.lat, anchor.lon
        notes = [f"Merkez olarak '{anchor.name}' ({anchor.mode_label}) alındı."]
        if resolved_modes and anchor.mode not in resolved_modes:
            notes.append(
                "Sorulan moda uyan bu adda bir yer yok; merkez başka moddan alındı, "
                "sonuçlar yine sorulan modda."
            )
        if len(search.clusters) > 1:
            notes.append("Bu ada uyan başka yerler de var; merkezin doğru olduğunu cevapta belirt.")

    clusters = find_nearby_places(
        origin_lat,
        origin_lon,
        radius_m=radius,
        modes=resolved_modes or None,
        limit=count,
    )
    if not clusters:
        notes.append(f"{radius:.0f} m içinde kayıtlı durak bulunamadı; yarıçapı artırmayı dene.")
    return NearbyOut(
        around=origin_name,
        lat=round(origin_lat, 6),
        lon=round(origin_lon, 6),
        radius_m=radius,
        stops=[_place_out(cluster) for cluster in clusters],
        notes=notes,
    )


@mcp.tool()
def get_lines_at_stop(
    stop: str, mode: str | None = None
) -> StopLinesOut | PlaceNotFound | InvalidMode | StoreNotReady:
    """Bir duraktan veya istasyondan geçen hatları listeler.

    Otobüste hat kodu, güzergâh adı, yön levhaları ve haftalık tarifedeki
    sefer sayısı döner; raylı sistemde o istasyonu işleten hat kodları
    döner.

    Parametreler:
        stop: durak veya istasyon adı. Kullanıcının yazdığı hali yeterli.
            Bulunamazsa en yakın 3 aday döner.
        mode: durağı bir modda ara — `otobus`, `metro`, `tramvay`,
            `funikuler`, `teleferik`, `vapur`, `minibus`, `dolmus`. `rayli`
            dört raylı modu birlikte arar; `marmaray`/`tren` metro
            istasyonlarına bakar.
            "Üsküdar" hem metro istasyonu hem otobüs durağı olduğu için,
            kullanıcı otobüs soruyorsa `otobus` ver.

    Minibüs, dolmuş ve iskeleler için **hat bilgisi yoktur**: yerel veride
    yalnızca durak konumları var. Araç bunu söyler, hat uydurmaz.
    Raylı sistem hat bilgisi 2020 beslemesinden gelir (`is_stale: true`).

    NE ZAMAN KULLANMA:
    - Bir hattın güzergâhındaki durakları öğrenmek için kullanma —
      `get_line_route` kullan. Bu araç duraktan hatta gider, hattan durağa
      değil.
    - Sefer saati veya aralığı için kullanma — metro/Marmaray için
      `get_next_departures` kullan; otobüste ilk/son kalkış
      `get_line_route` içindedir.
    - Otobüsün şu an nerede olduğunu sormak için kullanma; bu araç tarife
      verisini okur, canlı konum vermez.
    """
    guard = _store_guard()
    if guard is not None:
        return guard
    resolved_modes, mode_error = resolve_mode(mode)
    if mode_error:
        return InvalidMode(
            asked=mode or "",
            valid_modes=list(MODE_VOCABULARY),
            message=mode_error,
        )
    result = _get_lines_at_stop(stop, modes=resolved_modes or None)
    if not result.found:
        return PlaceNotFound(
            asked=stop,
            candidates=list(result.candidates),
            related=list(result.related),
            message="; ".join(result.notes) or f"'{stop}' bulunamadı.",
        )
    lines = sorted(result.lines, key=lambda line: (-line.trip_count, line.line))
    return StopLinesOut(
        stop=result.stop_name or stop,
        mode=result.mode or "",
        mode_label=MODE_LABELS.get(result.mode or "", result.mode or ""),
        lines=[
            LineAtStopOut(
                line=line.line,
                mode=line.mode,
                long_name=line.long_name,
                directions=list(line.headsigns),
                variant_count=line.variant_count,
                trip_count=line.trip_count,
                is_stale=line.is_stale,
            )
            for line in lines
        ],
        line_count=len(lines),
        source=lines[0].source if lines else None,
        data_as_of=lines[0].data_as_of if lines else None,
        notes=list(result.notes),
    )


@mcp.tool()
def get_line_route(
    line: str, direction: str | None = None
) -> LineRoutesOut | LineNotFound | StoreNotReady:
    """Bir hattın sıralı duraklarını, yönlerini ve servis penceresini verir.

    Otobüs hatlarında her yön ayrı döner: sıralı durak listesi, yön levhası,
    servis günü başına sefer sayısı, ilk duraktan ilk/son kalkış saati ve
    baştan sona yolculuk süresinin ortancası. Raylı sistemde tek sonuç döner
    ve duraklar uç istasyondan başlar.

    Parametreler:
        line: hat kodu — otobüste `500T`, `34`, `12A`; raylı sistemde `M4`,
            `T1`, `Marmaray`. Büyük/küçük harf önemsiz.
        direction: yalnızca tek yön istenirse `"0"` veya `"1"`. Yön kodları
            beslemeden gelir; hangi yönün hangi son durağa gittiği cevaptaki
            `direction_name` alanında yazar. Raylı sistemde yok sayılır.

    **Saatler ilk duraktan kalkışı gösterir**, ara duraklardan geçiş saatini
    göstermez: İETT beslemesi sefer başına yalnızca iki saat veriyor (ilk
    duraktan kalkış, son durağa varış). Bir hattın birden çok güzergâh
    varyantı varsa en çok durağa uğrayan listelenir ve `variant_count` kaç
    varyant olduğunu söyler.

    NE ZAMAN KULLANMA:
    - Bir **duraktan** geçen hatları sormak için kullanma —
      `get_lines_at_stop` kullan. Bu araç hattan durağa gider.
    - Ara bir duraktan otobüsün kaçta geçeceğini sormak için kullanma; o
      bilgi bu veride yok, saatler yalnızca ilk duraktan kalkışı gösterir.
    - Metro/Marmaray sefer aralığı veya "şu an ne sıklıkta geliyor" için
      kullanma — `get_next_departures` kullan.
    - İki durak arası süre için kullanma — raylı sistemde
      `get_rail_travel_time` kullan; buradaki süre hattın tamamını gezmenin
      süresidir.
    """
    guard = _store_guard()
    if guard is not None:
        return guard
    routes = _get_line_route(line, direction=direction)
    if not routes:
        return LineNotFound(
            asked=line,
            message=(
                f"'{line}' adlı bir hat bulunamadı. Otobüste hat kodu ('500T', '34'), "
                "raylı sistemde hat kodu ('M4', 'T1', 'Marmaray') ver."
            ),
        )
    notes: list[str] = []
    if direction is not None and len(routes) == 1 and routes[0].direction_id != direction:
        notes.append(f"'{direction}' yönü bulunamadı; hattın bilinen yönü listelendi.")
    return LineRoutesOut(
        line=routes[0].line,
        directions=[
            LineRouteOut(
                line=route.line,
                mode=route.mode,
                direction_id=route.direction_id,
                direction_name=route.direction_name,
                stops=[
                    RouteStopOut(
                        sequence=stop.sequence,
                        name=stop.name,
                        lat=round(stop.lat, 6) if stop.lat is not None else None,
                        lon=round(stop.lon, 6) if stop.lon is not None else None,
                    )
                    for stop in route.stops
                ],
                stop_count=len(route.stops),
                variant_count=route.variant_count,
                trip_count=route.trip_count,
                service_windows=[
                    ServiceWindowOut(
                        service=window.service_name or window.service_id or "bilinmiyor",
                        trips=window.trips,
                        first_departure=window.first_departure,
                        last_departure=window.last_departure,
                        median_duration_minutes=window.median_duration_min,
                    )
                    for window in route.service_windows
                ],
                source=route.source,
                data_as_of=route.data_as_of,
                is_stale=route.is_stale,
                notes=list(route.notes),
            )
            for route in routes
        ],
        notes=notes,
    )


# ------------------------------------------------- canlı araçlar (Faz 2)


class AlertOut(BaseModel):
    """Tek bir duyuru."""

    source: str = Field(description="İETT | Metro İstanbul")
    line: str | None = Field(None, description="Duyurunun ilgili olduğu hat kodu")
    kind: str = Field(description="Duyuru tipi (Sefer, servis durumu vb.)")
    text: str
    recorded_at: str | None = None
    recorded_day_known: bool = Field(
        True,
        description=(
            "`recorded_at` gün bilgisi taşıyor mu? İETT yalnızca saat yazıyor, "
            "tarih yazmıyor — o duyurularda false"
        ),
    )
    from_earlier_day: bool = Field(
        False,
        description=(
            "Kayıt saati şu andan ileride: duyuru bugüne değil, önceki bir güne ait. "
            "Bu duyuruya 'şu an' deme"
        ),
    )
    age_days: int | None = Field(
        None,
        description=(
            "Duyurunun kaç gün önce güncellendiği (yalnızca tarihli kaynaklarda). "
            "Büyük bir sayı duyurunun hâlâ yürürlükte olmayabileceğini gösterir"
        ),
    )
    is_cancellation: bool = Field(False, description="Sefer iptali bildiren duyuru")


class AlertsOut(BaseModel):
    """`get_service_alerts` cevabı."""

    ok: Literal[True] = True
    asked_line: str | None = None
    asked_mode: str | None = None
    alerts: list[AlertOut]
    total: int = Field(description="Filtre öncesi toplam duyuru sayısı")
    sources_ok: list[str] = Field(default_factory=list)
    sources_failed: list[str] = Field(
        default_factory=list, description="Ulaşılamayan kaynaklar; cevapta belirt"
    )
    data_as_of: str
    notes: list[str] = Field(default_factory=list)


class StationFacilitiesOut(BaseModel):
    """Bir istasyonun erişilebilirlik donanımı ve arızaları."""

    station: str
    lines: list[str] = Field(default_factory=list)
    lifts: int = Field(description="İstasyondaki asansör sayısı")
    escalators: int = Field(description="Yürüyen merdiven sayısı")
    wc: bool
    baby_room: bool
    faults: list[str] = Field(
        default_factory=list, description="Bu istasyonda **şu an** arızalı ekipmanlar"
    )


class AccessibilityOut(BaseModel):
    """`get_station_accessibility` cevabı."""

    ok: Literal[True] = True
    asked: str | None = None
    stations: list[StationFacilitiesOut]
    network_faults: dict[str, int] = Field(
        default_factory=dict, description="Ağ genelinde arızalı ekipman sayısı (grup bazında)"
    )
    data_as_of: str
    source: str = "Metro İstanbul (GetStations.DetailInfo + GetFaultyEquipmentDetails)"
    notes: list[str] = Field(default_factory=list)


# Raylı sistem hat kodları: M2, M1A, T1, F4, TF2, Marmaray. Otobüs kodları
# sayıyla başlıyor (34, 500T, 12A) ya da harf+sayı dışında bir desen.
_RAIL_LINE = re.compile(r"^(?:M\d+[AB]?|T\d+|F\d+|TF\d+|MARMARAY\d?)$", re.IGNORECASE)


def _looks_like_rail_line(line: str) -> bool:
    return bool(_RAIL_LINE.match(line.strip()))


def _live_error(problem: Any) -> LiveDataUnavailable:
    return LiveDataUnavailable(source=problem.source, message=problem.message)


# Bu kadar gün geçmiş bir servis durumu "anlık" sayılmaz; hâlâ yürürlükte
# olabilir ama model ona "şu an" demeden önce yaşını görmeli. Ölçüm
# (11 Kasım 2026): yayındaki iki servis durumu 26 ve 23 gün önce
# güncellenmişti (karar 0013).
STALE_STATUS_DAYS = 7

# Cevaba en çok bu kadar duyuru giriyor. Sınır şart: filtresiz sorguda
# kaynaklar 219 İETT duyurusu döndürüyor ve hepsini modele vermek bağlam
# penceresini yiyor. Ama **sıra** şart: ilk sürüm ekleme sırasına göre
# kırpıyordu ve metro servis durumu listeye en son eklendiği için
# filtresiz sorguda hiç dönmüyordu — "M7'de mekik seferi yapılıyor"
# bilgisi, yani listedeki en işletmesel kayıt, 219 güzergâh duyurusunun
# arkasında kalıyordu (ölçüldü, karar 0013).
MAX_ALERTS = 40

# Küçük sayı önce kırpılmaktan kurtulur: hat bazlı servis durumu ağ
# genelini anlatır ve sayısı birkaç tanedir, İETT güzergâh duyuruları ise
# yüzlercedir.
_ALERT_PRIORITY = {"servis durumu": 0}


def _alert_rank(alert: AlertOut) -> int:
    """Kırpma sırası: servis durumu, sonra iptal, sonra geri kalanı."""
    if alert.kind in _ALERT_PRIORITY:
        return _ALERT_PRIORITY[alert.kind]
    if alert.source != "İETT":
        return 1
    return 2 if alert.is_cancellation else 3


def _now_clock() -> str:
    """Şu anın `"HH:MM"` hali; İETT duyurularının saatiyle karşılaştırmak için."""
    return time.strftime("%H:%M")


def _age_days(stamp: str | None) -> int | None:
    """ISO zaman damgasının kaç gün önce olduğu; çözülemezse `None`."""
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp.strip().replace("Z", ""))
    except ValueError:
        return None
    return max(0, (datetime.now() - when).days)


@mcp.tool()
def get_service_alerts(
    line: str | None = None, mode: str | None = None
) -> AlertsOut | LiveDataUnavailable:
    """Otobüs ve raylı sistemdeki **canlı** sefer duyurularını ve arıza bildirimlerini verir.

    İki kaynağı birleştirir: İETT duyuruları (sefer iptalleri, güzergâh
    değişiklikleri) ve Metro İstanbul duyuruları + hat bazlı servis
    durumu. Duyurular tarifeden **daha günceldir**: tarife 23:00'e kadar
    sefer diyorken duyuru iptal diyorsa, duyuru geçerlidir.

    **Her duyuru aynı yaşta değil.** İETT duyurularında yalnızca kayıt
    saati var, tarih yok; saati şu andan ileride olan bir duyuru önceki
    bir güne aittir (`from_earlier_day`) ve ona "şu an" denemez. Metro
    servis durumları tarihli ama kaynakta aylarca kalabiliyor (ölçüm: 26
    ve 23 gün); `age_days` büyükse güncellenme tarihini de söyle.

    Parametreler:
        line: hat kodu ile süz — otobüste `500T`, `34`; raylı sistemde
            `M7`, `T1`, `Marmaray`. Verilmezse bütün duyurular döner
            (çok olabilir; `mode` ile daralt).
        mode: kaynağa göre süz — `otobus` yalnızca İETT duyurularını,
            `rayli` yalnızca Metro İstanbul duyurularını getirir.

    Kaynaklardan biri erişilemezse araç **yine cevap verir** ve
    `sources_failed` içinde hangisine ulaşılamadığını söyler; o kaynağın
    duyuruları eksik demektir ve bunu cevapta belirt.

    NE ZAMAN KULLANMA:
    - Sefer saati sormak için kullanma — `get_next_departures` kullan.
      Bu araç tarife vermez, tarifedeki **değişikliği** verir.
    - Asansör/yürüyen merdiven arıza listesi için kullanma —
      `get_station_accessibility` kullan.
    - Trafik durumu için kullanma — `get_traffic_status` kullan.
    - Geçmiş duyuruları aramak için kullanma; kaynak yalnızca güncel
      duyuruları yayımlıyor.
    """
    import time

    from istanbul_agent.clients import iett
    from istanbul_agent.clients.base import LiveDataError

    notes: list[str] = []
    wanted_line = (line or "").strip().upper() or None
    wanted_mode = (mode or "").strip().casefold() or None
    if wanted_mode in {"otobüs", "bus", "iett"}:
        wanted_mode = "otobus"
    if wanted_mode in {"raylı", "metro", "tren", "tramvay"}:
        wanted_mode = "rayli"

    # Mod verilmediyse hat kodundan çıkar. Olmazsa otobüs hattı sorulurken
    # ağ geneli bir metro duyurusu da listeye giriyor ("tüm hatlarımızda
    # kış tarifesi"), çünkü o duyuru hiçbir hat adı taşımıyor ve "her hat"
    # sayılıyor — ölçümde 500T sorgusunda tam bu oldu.
    if wanted_mode is None and wanted_line:
        wanted_mode = "rayli" if _looks_like_rail_line(wanted_line) else "otobus"
        notes.append(
            f"{wanted_line} bir {'raylı sistem' if wanted_mode == 'rayli' else 'otobüs'} "
            "hattı sayıldı; yalnızca o kaynağın duyuruları tarandı."
        )

    alerts: list[AlertOut] = []
    ok_sources: list[str] = []
    failed: list[str] = []
    total = 0

    if wanted_mode in (None, "otobus"):
        try:
            bus_alerts = iett.fetch_announcements()
            ok_sources.append("İETT duyuruları")
            total += len(bus_alerts)
            for item in bus_alerts:
                if wanted_line and item.line.upper() != wanted_line:
                    continue
                alerts.append(
                    AlertOut(
                        source="İETT",
                        line=item.line or None,
                        kind=item.kind or "duyuru",
                        text=item.message,
                        recorded_at=item.recorded_at or None,
                        recorded_day_known=False,
                        from_earlier_day=item.recorded_after(_now_clock()),
                        is_cancellation=item.is_cancellation,
                    )
                )
        except LiveDataError as problem:
            failed.append(f"İETT duyuruları ({problem.source})")

    if wanted_mode in (None, "rayli"):
        try:
            metro_alerts = fetch_announcements()
            ok_sources.append("Metro İstanbul duyuruları")
            total += len(metro_alerts)
            for item in metro_alerts:
                if wanted_line and item.lines and wanted_line not in item.lines:
                    continue
                alerts.append(
                    AlertOut(
                        source="Metro İstanbul",
                        line=", ".join(sorted(item.lines)) or None,
                        kind="tarife" if item.is_schedule_related else "duyuru",
                        text=item.summary(),
                        recorded_at=None,
                    )
                )
        except LiveDataError as problem:
            failed.append(f"Metro İstanbul duyuruları ({problem.source})")
        except Exception as problem:
            failed.append(f"Metro İstanbul duyuruları ({type(problem).__name__})")

        try:
            from istanbul_agent.clients.metro import fetch_service_statuses

            statuses = fetch_service_statuses()
            ok_sources.append("Metro İstanbul servis durumu")
            total += len(statuses)
            for status in statuses:
                if wanted_line and status.line.upper() != wanted_line:
                    continue
                alerts.append(
                    AlertOut(
                        source="Metro İstanbul",
                        line=status.line or None,
                        kind="servis durumu",
                        text=status.description,
                        recorded_at=status.updated_at or None,
                        age_days=_age_days(status.updated_at),
                    )
                )
        except LiveDataError as problem:
            failed.append(f"Metro İstanbul servis durumu ({problem.source})")
        except Exception as problem:
            failed.append(f"Metro İstanbul servis durumu ({type(problem).__name__})")

    if not ok_sources:
        return LiveDataUnavailable(
            source="duyurular",
            message=(
                "Hiçbir duyuru kaynağına ulaşılamadı: "
                + "; ".join(failed)
                + ". Duyuru olup olmadığı bilinmiyor."
            ),
        )
    dropped = max(0, len(alerts) - MAX_ALERTS)
    alerts = sorted(alerts, key=_alert_rank)[:MAX_ALERTS]
    if dropped:
        notes.append(
            f"Kaynaklarda {total} duyuru var, cevaba en önemli {len(alerts)} tanesi girdi "
            f"({dropped} duyuru listelenmedi). Servis durumları ve sefer iptalleri önce "
            "geliyor. Belirli bir hattı soruyorsan `line` ver, o hattın hepsi gelir."
        )
    undated = [alert for alert in alerts if not alert.recorded_day_known]
    if undated:
        earlier = [alert for alert in undated if alert.from_earlier_day]
        note = (
            f"{len(undated)} İETT duyurusunda yalnızca kayıt **saati** var, tarih yok: "
            "bu duyurunun hangi güne ait olduğu kaynaktan bilinemiyor."
        )
        if earlier:
            note += (
                f" Bunların {len(earlier)} tanesinin saati şu andan ileride, yani "
                "önceki bir güne ait (`from_earlier_day: true`); onlara 'şu an' deme."
            )
        notes.append(note)
    old_statuses = [
        alert for alert in alerts if alert.age_days and alert.age_days > STALE_STATUS_DAYS
    ]
    if old_statuses:
        notes.append(
            f"{len(old_statuses)} servis durumu {STALE_STATUS_DAYS} günden eski "
            f"(en eskisi {max(alert.age_days or 0 for alert in old_statuses)} gün). "
            "Kaynak bunları yayından kaldırmamış; hâlâ yürürlükte olabilir ama "
            "kesin değil — cevapta güncellenme tarihini birlikte söyle."
        )
    if failed:
        notes.append(
            "Şu kaynaklara ulaşılamadı, o kaynakların duyuruları bu listede yok: "
            + "; ".join(failed)
        )
    if wanted_line and not alerts:
        notes.append(f"{wanted_line} için güncel duyuru yok (kaynaklarda {total} duyuru tarandı).")

    return AlertsOut(
        asked_line=wanted_line,
        asked_mode=wanted_mode,
        alerts=alerts,
        total=total,
        sources_ok=ok_sources,
        sources_failed=failed,
        data_as_of=time.strftime("%Y-%m-%d %H:%M"),
        notes=notes,
    )


@mcp.tool()
def get_station_accessibility(
    station: str | None = None, line: str | None = None
) -> AccessibilityOut | StationNotFound | LiveDataUnavailable:
    """Raylı sistem istasyonlarındaki asansör/yürüyen merdiven sayısını ve **anlık arızaları** verir.

    İki şeyi birleştirir: istasyonun donanımı (kaç asansör, kaç yürüyen
    merdiven, tuvalet, bebek bakım odası) ve Metro İstanbul'un **şu anki**
    arıza listesi. Tekerlekli sandalye ya da bebek arabası sorularında
    asansör arızası belirleyicidir.

    Parametreler:
        station: istasyon adı ("Kadıköy", "Yenikapı"). Verilmezse ağ
            genelindeki arıza sayıları döner.
        line: hat kodu ile süz (`M4`, `T1`). İstasyon adı birden çok hatta
            geçiyorsa bunu ver.

    Arıza listesi yalnızca **bildirilmiş** arızaları içerir; listede
    olmaması "çalışıyor" garantisi değildir, bunu cevapta söyle.

    NE ZAMAN KULLANMA:
    - Sefer saati veya süre için kullanma — `get_next_departures` ve
      `get_rail_travel_time` kullan.
    - Otobüs/vapur erişilebilirliği için kullanma; kaynak yalnızca raylı
      sistemi kapsıyor.
    - İstasyonun nerede olduğunu sormak için kullanma — `geocode_place`
      kullan.
    - Tekerlekli sandalyeyle **rota planı** için kullanma; bu araç
      istasyon donanımını söyler, rota kurmaz.
    """
    import time

    from istanbul_agent.clients.base import LiveDataError
    from istanbul_agent.clients.metro import (
        ACCESSIBILITY_GROUPS,
        fetch_equipment_counts,
        fetch_faulty_equipment,
        fetch_station_facilities,
    )
    from istanbul_agent.store.station_names import normalize

    try:
        facilities = fetch_station_facilities()
        counts = fetch_equipment_counts()
        faults = [
            fault for group in ACCESSIBILITY_GROUPS for fault in fetch_faulty_equipment(group)
        ]
    except LiveDataError as problem:
        return _live_error(problem)
    except Exception as problem:
        return LiveDataUnavailable(
            source="metro_equipment",
            message=(
                f"Metro İstanbul erişilebilirlik verisine ulaşılamadı ({type(problem).__name__}); "
                "arıza durumu bilinmiyor."
            ),
        )

    wanted_line = (line or "").strip().upper() or None
    network = {
        count.name: count.inactive
        for count in counts
        if count.group in ACCESSIBILITY_GROUPS and count.inactive
    }
    notes = [
        "Arıza listesi yalnızca bildirilmiş arızaları içerir; listede olmaması "
        "ekipmanın çalıştığı garantisi değildir."
    ]

    if not station:
        return AccessibilityOut(
            asked=None,
            stations=[],
            network_faults=network,
            data_as_of=time.strftime("%Y-%m-%d %H:%M"),
            notes=[*notes, "İstasyon adı verilmedi; yalnızca ağ geneli arıza sayıları döndü."],
        )

    key = normalize(station)
    matched = [
        item
        for item in facilities
        if normalize(item.station) == key and (not wanted_line or item.line.upper() == wanted_line)
    ]
    if not matched:
        candidates = sorted(
            {item.station for item in facilities if key and key[:4] in normalize(item.station)}
        )[:3]
        return StationNotFound(
            asked=station,
            candidates=candidates,
            message=(
                f"'{station}' adlı istasyon Metro İstanbul istasyon listesinde bulunamadı"
                + (f" ({wanted_line} hattında)" if wanted_line else "")
                + ". Adaylar arasından doğru olanı kullanıcıya sor."
            ),
        )

    grouped: dict[str, StationFacilitiesOut] = {}
    for item in matched:
        existing = grouped.get(normalize(item.station))
        station_faults = [
            f"{fault.group} arızası ({fault.date[:10]})"
            for fault in faults
            if normalize(fault.station) == normalize(item.station)
            and (not wanted_line or fault.line.upper() == wanted_line)
        ]
        if existing is None:
            grouped[normalize(item.station)] = StationFacilitiesOut(
                station=item.station,
                lines=[item.line] if item.line else [],
                lifts=item.lifts,
                escalators=item.escalators,
                wc=item.wc,
                baby_room=item.baby_room,
                faults=sorted(set(station_faults)),
            )
        else:
            if item.line and item.line not in existing.lines:
                existing.lines.append(item.line)
            existing.lifts = max(existing.lifts, item.lifts)
            existing.escalators = max(existing.escalators, item.escalators)
            existing.faults = sorted(set([*existing.faults, *station_faults]))

    stations = list(grouped.values())
    if any(item.lifts == 0 for item in stations):
        notes.append(
            "Asansör sayısı 0 olan istasyonda asansör bulunmuyor; tekerlekli sandalye "
            "erişimi için bunu belirt."
        )
    return AccessibilityOut(
        asked=station,
        stations=stations,
        network_faults=network,
        data_as_of=time.strftime("%Y-%m-%d %H:%M"),
        notes=notes,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
