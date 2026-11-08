"""MCP sunucusu: raylı sistem araçları.

Şu an iki araç sunuyor:

- `get_rail_travel_time` — iki istasyon arası süre ve güzergâh
- `get_next_departures` — bir hattın ilk/son seferi ve sefer aralığı

Tarife ve süre verisi yerel depodan (`data/transit.duckdb`) geliyor;
`get_next_departures` buna ek olarak Metro İstanbul duyurularını canlı
çekiyor (çekemezse cevabına bunu yazıyor). Araç açıklamaları agent'ın araç
seçimini belirliyor,
bu yüzden her açıklamada ne yaptığı, parametreleri ve **ne zaman
kullanılmaması gerektiği** yazıyor (CLAUDE.md kuralı).

    uv run python -m istanbul_agent.servers.transit_server
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from istanbul_agent.clients.metro import Announcement, fetch_announcements
from istanbul_agent.store.departures import get_next_departures as _get_next_departures
from istanbul_agent.store.rail_queries import get_rail_travel_time as _get_rail_travel_time
from istanbul_agent.store.rail_queries import load_network
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
    candidates: list[str] = Field(default_factory=list, description="En yakın 3 istasyon adı")
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
    first_departure: str | None
    last_departure: str | None = Field(None, description="Gece boyunca çalışıyorsa null olur")
    runs_all_night: bool = False
    headway_minutes: int | None = Field(None, description="Sorulan saatteki sefer aralığı")
    headway_hour: int | None = Field(None, description="Aralığın ait olduğu saat")
    running_now: bool
    expected_wait_minutes: int | None
    night_service: str | None = Field(None, description="Varsa gece servisi penceresi")


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


# ----------------------------------------------------------------- araçlar


@mcp.tool()
def get_rail_travel_time(from_station: str, to_station: str) -> TravelTimeOut | StationNotFound:
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


@mcp.tool()
def get_next_departures(
    line: str, station: str | None = None, at: str | None = None
) -> DeparturesOut | LineNotFound | StationNotFound:
    """Bir raylı sistem hattının ilk/son seferini ve şu anki sefer aralığını verir.

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
        return LineNotFound(
            error="hat_bulunamadi",
            asked=line,
            candidates=known_lines,
            message=f"'{line}' diye bir raylı sistem hattı yok.",
        )

    if station:
        match = find_station(network, station, line=code)
        if not match.found:
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
        directions=[
            DirectionOut(
                direction=info.label,
                terminal=info.terminal,
                offset_minutes=info.offset_minutes,
                first_departure=info.first,
                last_departure=info.last,
                runs_all_night=info.all_night,
                headway_minutes=info.headway_minutes,
                headway_hour=info.headway_hour,
                running_now=info.running_now,
                expected_wait_minutes=info.expected_wait_minutes,
                night_service=(
                    f"{info.night_first}-{info.night_last}" if info.night_first else None
                ),
            )
            for info in result.directions
        ],
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
