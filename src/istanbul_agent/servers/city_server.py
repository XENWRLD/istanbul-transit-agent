"""MCP sunucusu: şehir araçları (otopark, trafik).

İki araç sunuyor:

- `find_parking` — İSPARK otoparkları ve **anlık** boş kapasite
- `get_traffic_status` — İstanbul geneli trafik indeksi ve eğilimi

**`get_air_quality` yok ve bu bir ölçüm sonucu** (karar 0012): hava
kalitesi uç noktası cevap veriyor, 28 istasyonu listeliyor, tarih
parametresini bile kabul ediyor — ama ölçüm değerlerinin **tamamı boş**.
12 istasyonun 12'sinde, 721 kaydın hepsinde `AQI: null`. Veri olmayan bir
kaynağın üstüne araç koymak, agent'a "bilmiyorum" demenin daha karmaşık
bir yolunu eklemek olurdu; Faz 0'da İsbike için verilen karar da buydu.

Araçlar canlı API'ye `clients/city.py` üzerinden gidiyor: ortak zaman
aşımı, geçici hatada yeniden deneme, kaynak bazlı TTL önbellek. Kaynağa
ulaşılamazsa araç bunu **açıkça** söylüyor — boş liste döndürmek
"otopark yok" demekle aynı şey olurdu.

    uv run python -m istanbul_agent.servers.city_server
"""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from istanbul_agent.clients.base import LiveDataError
from istanbul_agent.clients.city import (
    CarPark,
    fetch_car_parks,
    fetch_traffic_index,
)
from istanbul_agent.store.geo import haversine_m

mcp = FastMCP("istanbul-city", log_level="WARNING")

# Otopark aramasında varsayılan ve en geniş yarıçap. 1500 m yürüme
# mesafesinin üstü; daha geniş arama "yakında otopark var" demeyi anlamsız
# kılıyor.
DEFAULT_RADIUS_M = 1000.0
MAX_RADIUS_M = 5000.0


# --------------------------------------------------------------- modeller


class LiveDataUnavailable(BaseModel):
    """Canlı kaynağa ulaşılamadı.

    Boş sonuç yerine hata dönüyor: "otopark yok" ile "otopark verisine
    ulaşamadım" aynı şey değil ve ikincisini birincisi gibi sunmak
    kullanıcıyı yanıltır.
    """

    ok: Literal[False] = False
    error: Literal["canli_veri_yok"] = "canli_veri_yok"
    source: str
    message: str


class PlaceNotFound(BaseModel):
    """Çevresi aranacak yer bulunamadı."""

    ok: Literal[False] = False
    error: Literal["yer_bulunamadi"] = "yer_bulunamadi"
    asked: str
    candidates: list[str] = Field(default_factory=list)
    message: str


class CarParkOut(BaseModel):
    """Tek bir otopark."""

    name: str
    district: str
    park_type: str = Field(description="KAPALI OTOPARK | AÇIK OTOPARK | YOL ÜSTÜ vb.")
    capacity: int | None
    empty: int | None = Field(description="Anlık boş kapasite")
    occupancy_known: bool = Field(
        True,
        description=(
            "Doluluk okunabiliyor mu? `empty: 0` dönen otoparklarda false: "
            "kaynak 'dolu' ile 'doluluk bildirmiyor' arasını ayırmıyor"
        ),
    )
    occupancy_percent: int | None = Field(
        None, description="Doluluk yüzdesi; `occupancy_known` false ise None"
    )
    is_open: bool
    work_hours: str
    free_minutes: int | None = Field(None, description="Ücretsiz süre (dakika)")
    lat: float | None = None
    lon: float | None = None
    distance_m: float | None = Field(None, description="Sorulan konuma kuş uçuşu uzaklık")


class ParkingOut(BaseModel):
    """`find_parking` cevabı."""

    ok: Literal[True] = True
    around: str
    lat: float
    lon: float
    radius_m: float
    parks: list[CarParkOut]
    total_in_radius: int = Field(description="Yarıçap içindeki otopark sayısı")
    source: str = "İSPARK (api.ibb.gov.tr/ispark/Park)"
    data_as_of: str = Field(description="Verinin çekildiği an; doluluk dakikalar içinde değişir")
    notes: list[str] = Field(default_factory=list)


class TrafficOut(BaseModel):
    """`get_traffic_status` cevabı."""

    ok: Literal[True] = True
    index: int = Field(description="Trafik indeksi, 0-100")
    label: str = Field(description="akıcı | hafif yoğun | yoğun | çok yoğun")
    trend: str = Field(description="artıyor | azalıyor | sabit | bilinmiyor")
    measured_at: str = Field(description="İndeksin ölçüm saati (kaynaktan)")
    recent: list[dict[str, object]] = Field(
        default_factory=list, description="Son ölçümler (saat, değer)"
    )
    scope: str = "İstanbul geneli"
    source: str = "İBB Trafik Yoğunluk İndeksi (tkmservices)"
    notes: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------- araçlar


def _unavailable(problem: LiveDataError) -> LiveDataUnavailable:
    return LiveDataUnavailable(source=problem.source, message=problem.message)


class MobilityPointOut(BaseModel):
    """Bir park alanı ya da şarj istasyonu."""

    name: str = Field(description="Yerin adı")
    kind: str = Field(description="bisiklet_park | mikromobilite_park | sarj")
    kind_label: str = Field(description="Türün Türkçe adı")
    district: str | None = Field(default=None, description="İlçe, kaynakta varsa")
    detail: str | None = Field(default=None, description="Park tipi ya da şarj istasyonu adresi")
    lat: float
    lon: float
    distance_m: float = Field(description="Sorulan konuma kuş uçuşu mesafe")
    public_access: bool | None = Field(
        default=None, description="Halka açık mı; şarj istasyonlarında kaynaktan gelir"
    )
    operator: str | None = Field(default=None, description="Şarj istasyonunun işletmecisi")
    sockets_known: bool = Field(
        description="Soket bilgisi kaynakta var mı; false ise soket sayısı bilinmiyor (sıfır değil)"
    )
    socket_count_at_least: int | None = Field(
        default=None, description="Bilinen soket sayısı — **alt sınır**, toplam değil"
    )
    socket_types: str | None = Field(
        default=None, description="Soket türleri, ör. DC_CCS, AC_TYPE2"
    )
    max_power_kw: float | None = Field(default=None, description="Bilinen en yüksek soket gücü")


class MobilityPathOut(BaseModel):
    """Bir bisiklet yolu kaydı; mevcut olabilir ya da henüz proje olabilir."""

    name: str = Field(description="Proje adı")
    district: str | None = Field(default=None, description="İlçe")
    stage: str = Field(description="Kaynaktaki aşama, ör. 'Mevcut Ayrılmış', 'UTK kararı alınan'")
    exists_now: bool = Field(description="Yol fiilen var mı; false ise henüz yapılmamış")
    length_km: float | None = Field(default=None, description="Proje uzunluğu, km")
    distance_m: float = Field(description="Yolun en yakın düğümüne kuş uçuşu mesafe")


class MobilityOut(BaseModel):
    """`find_mobility_options` çıktısı."""

    ok: bool = True
    around: str = Field(description="Arama merkezi olarak alınan yer")
    lat: float
    lon: float
    radius_m: float
    points: list[MobilityPointOut] = Field(default_factory=list)
    paths: list[MobilityPathOut] = Field(default_factory=list)
    source: str = Field(description="Veri kaynakları")
    data_as_of: str = Field(description="Kaynakların portal tarihi")
    is_stale: bool = Field(description="Sonuçlarda eski veri var mı")
    notes: list[str] = Field(default_factory=list)


class StoreNotReady(BaseModel):
    """Depo kurulmamış: ne çalıştırılacağını söyler (karar 0011)."""

    ok: bool = False
    error: str = "depo_yok"
    message: str


class InvalidKind(BaseModel):
    """Geçersiz tür: boş sonuç yerine geçerli değerleri sayar."""

    ok: bool = False
    error: str = "gecersiz_tur"
    asked: str
    valid_kinds: list[str]
    message: str


@mcp.tool()
def find_parking(
    around: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_m: float = DEFAULT_RADIUS_M,
    limit: int = 5,
    only_open: bool = True,
) -> ParkingOut | PlaceNotFound | LiveDataUnavailable:
    """Bir yerin çevresindeki İSPARK otoparklarını ve **anlık boş kapasitesini** verir.

    İSPARK'ın 240 otoparkı arasından verilen konuma en yakın olanları,
    doluluk oranı ve çalışma saatleriyle döner. Doluluk **canlı** veridir
    ve dakikalar içinde değişir; `data_as_of` verinin çekildiği anı söyler.

    Parametreler:
        around: çevresi aranacak yerin adı ("Taksim", "Kadıköy"). Ad önce
            durak/istasyon tablosunda konuma çevrilir; `lat`/`lon`
            verilmişse bu alan göz ardı edilir.
        lat: enlem; `lon` ile birlikte kullan.
        lon: boylam; `lat` ile birlikte.
        radius_m: arama yarıçapı, metre. Varsayılan 1000, en çok 5000.
        limit: en çok kaç otopark dönecek. Varsayılan 5, en çok 20.
        only_open: varsayılan true — kapalı otoparklar listelenmez.
            Kapalıları da görmek için false ver.

    `distance_m` kuş uçuşu mesafedir, yürüme mesafesi değil.

    **Doluluk her otoparkta okunamıyor.** `empty: 0` dönen bir otopark ya
    gerçekten dolu ya da doluluk bildirmiyor; kaynak ikisini ayırt etmiyor
    (ölçüm: 239 açık otoparkın 51'i sıfır döndürüyor ve sıfırlar ilçeye
    göre kümeleniyor, karar 0013). Böyle otoparklarda `occupancy_known`
    false ve `occupancy_percent` None gelir — kullanıcıya "dolu" değil
    "doluluk bilinmiyor" denmeli.

    NE ZAMAN KULLANMA:
    - Toplu taşıma durağı aramak için kullanma — `find_nearby_stops`
      kullan. Bu araç yalnızca otopark bilir.
    - Otopark ücret tarifesi için kullanma; bu veri ücret içermiyor,
      yalnızca ücretsiz süreyi (`free_minutes`) veriyor.
    - Yol üstü parkmetre doluluğu için kullanma; kaynak İSPARK
      otoparklarıdır.
    - Trafik durumu için kullanma — `get_traffic_status` kullan.
    """
    import time

    radius = max(50.0, min(float(radius_m), MAX_RADIUS_M))
    count = max(1, min(int(limit), 20))
    notes: list[str] = []

    if lat is not None and lon is not None:
        origin_name = f"{lat:.5f},{lon:.5f}"
        origin_lat, origin_lon = float(lat), float(lon)
    else:
        if not around:
            return PlaceNotFound(
                asked="",
                message="Ya `around` (yer adı) ya da `lat` ve `lon` birlikte verilmeli.",
            )
        from istanbul_agent.store.places import find_places

        search = find_places(around, limit=3)
        if not search.found:
            return PlaceNotFound(
                asked=around,
                candidates=list(search.candidates),
                message=(
                    f"'{around}' adlı bir yer bulunamadı, bu yüzden çevresindeki otoparklar "
                    "aranamadı. Adaylar arasından doğru olanı kullanıcıya sor."
                ),
            )
        anchor = search.clusters[0]
        origin_name, origin_lat, origin_lon = anchor.name, anchor.lat, anchor.lon
        notes.append(f"Merkez olarak '{anchor.name}' ({anchor.mode_label}) alındı.")

    try:
        parks = fetch_car_parks()
    except LiveDataError as problem:
        return _unavailable(problem)

    located: list[CarPark] = []
    for park in parks:
        if park.lat is None or park.lon is None:
            continue
        if only_open and not park.is_open:
            continue
        distance = haversine_m(origin_lat, origin_lon, park.lat, park.lon)
        if distance > radius:
            continue
        located.append(
            CarPark(
                park_id=park.park_id,
                name=park.name,
                district=park.district,
                lat=park.lat,
                lon=park.lon,
                capacity=park.capacity,
                empty=park.empty,
                park_type=park.park_type,
                work_hours=park.work_hours,
                free_minutes=park.free_minutes,
                is_open=park.is_open,
                updated_at=park.updated_at,
                distance_m=round(distance, 1),
            )
        )
    located.sort(key=lambda park: park.distance_m or 0.0)

    if not located:
        notes.append(
            f"{radius:.0f} m içinde {'açık ' if only_open else ''}İSPARK otoparkı yok; "
            "yarıçapı artırmayı dene."
        )
    missing_capacity = sum(1 for park in located if park.capacity is None)
    if missing_capacity:
        notes.append(f"{missing_capacity} otoparkta kapasite bilgisi yok, doluluk hesaplanamadı.")
    unknown = [park for park in located[:count] if park.capacity and not park.reports_occupancy]
    if unknown:
        notes.append(
            f"{len(unknown)} otopark 0 boş yer bildiriyor ({', '.join(p.name for p in unknown)}). "
            "Bu ya gerçekten dolu olduğu ya da o otoparkın doluluk bildirmediği anlamına gelir; "
            "İSPARK ikisini ayırt etmiyor. Bu otoparklar için 'dolu' deme, "
            "doluluğun bilinmediğini söyle."
        )

    return ParkingOut(
        around=origin_name,
        lat=round(origin_lat, 6),
        lon=round(origin_lon, 6),
        radius_m=radius,
        parks=[
            CarParkOut(
                name=park.name,
                district=park.district,
                park_type=park.park_type,
                capacity=park.capacity,
                empty=park.empty,
                occupancy_known=park.reports_occupancy,
                occupancy_percent=park.occupancy_percent,
                is_open=park.is_open,
                work_hours=park.work_hours,
                free_minutes=park.free_minutes,
                lat=park.lat,
                lon=park.lon,
                distance_m=park.distance_m,
            )
            for park in located[:count]
        ],
        total_in_radius=len(located),
        data_as_of=time.strftime("%Y-%m-%d %H:%M"),
        notes=notes,
    )


@mcp.tool()
def get_traffic_status() -> TrafficOut | LiveDataUnavailable:
    """İstanbul genelinin **anlık** trafik yoğunluk indeksini ve eğilimini verir.

    İndeks 0-100 arası bir yoğunluk ölçüsüdür ve 5 dakikalık adımlarla
    yayımlanır. Cevapta hem ham sayı hem de yorumlanmış etiket
    (`akıcı`/`hafif yoğun`/`yoğun`/`çok yoğun`) var; etiketler bu projenin
    yorumu, kaynakta tanımlı değil — sayıyı da aktar.

    `trend` son yarım saatteki değişimi söyler. `measured_at` indeksin
    ölçüm saatidir; indeks 5 dakikada bir yenilendiği için cevabın "şu an"
    dediği şey bu saattir.

    Parametre almaz: kaynak **yalnızca İstanbul geneli** için indeks
    yayımlıyor.

    NE ZAMAN KULLANMA:
    - Belirli bir yol, köprü veya ilçe için kullanma; indeks şehir
      genelindir ve "Boğaz köprüsünde trafik" sorusunu cevaplayamaz.
      Bunu açıkça söyle.
    - Yolculuk süresi tahmini için kullanma; indeks süreye çevrilemez.
    - Metro/otobüs sefer durumu için kullanma — `get_service_alerts`
      kullan.
    - Otopark doluluğu için kullanma — `find_parking` kullan.
    """
    try:
        index = fetch_traffic_index()
    except LiveDataError as problem:
        return _unavailable(problem)

    return TrafficOut(
        index=index.value,
        label=index.label,
        trend=index.trend,
        measured_at=index.measured_at,
        recent=[{"saat": when, "indeks": value} for when, value in index.previous],
        notes=[
            "İndeks İstanbul genelidir; belirli bir yol veya köprü için değer vermez.",
            "Etiketler (akıcı/yoğun) bu aracın yorumudur, kaynakta tanımlı değildir.",
        ],
    )


@mcp.tool()
def find_mobility_options(
    around: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_m: float = DEFAULT_RADIUS_M,
    kind: str | None = None,
    public_only: bool = True,
    include_planned_paths: bool = False,
    limit: int = 10,
) -> MobilityOut | PlaceNotFound | InvalidKind | StoreNotReady:
    """Bir yerin çevresindeki bisiklet/scooter park alanlarını, elektrikli araç şarj istasyonlarını ve bisiklet yollarını verir.

    Üç katman tek araçta: 1300 park alanı (1099 mikromobilite + 201
    bisiklet), 2933 şarj istasyonu ve 477 bisiklet yolu kaydı. Hepsi
    **dosya** verisi, canlı servis değil; boş park yeri ya da müsait soket
    sayısı bu veride **yok**.

    Parametreler:
        around: çevresi aranacak yerin adı ("Kadıköy", "Taksim"). Ad önce
            durak/istasyon tablosunda konuma çevrilir; `lat`/`lon`
            verilmişse bu alan göz ardı edilir.
        lat: enlem; `lon` ile birlikte kullan.
        lon: boylam; `lat` ile birlikte.
        radius_m: arama yarıçapı, metre. Varsayılan 1000, en çok 5000.
        kind: tek türe kısıtlar — `bisiklet_park`, `mikromobilite_park`,
            `sarj`. `park` iki park türünü birlikte verir; `scooter`
            mikromobiliteye, `elektrikli` şarja eşlenir. Boş bırakılırsa
            hepsi döner.
        public_only: varsayılan true — kaynaktaki 904 özel (halka kapalı)
            şarj istasyonu listelenmez ve kaç tanesinin düştüğü nota yazılır.
        include_planned_paths: varsayılan false — yalnızca **mevcut**
            bisiklet yolları döner.
        limit: en çok kaç kayıt dönecek. Varsayılan 10, en çok 50.

    `distance_m` kuş uçuşu mesafedir. Bisiklet yolunda mesafe yolun en
    yakın düğümüne ölçülür.

    **Üç şeyi karıştırmamak gerekiyor, araç bunları ayrı alanlarda söyler:**

    - **Bisiklet yolu kayıtlarının %44'ü henüz yol değil.** 477 kaydın
      212'si proje, karar ya da inşaat aşamasında; `exists_now` bunu
      söyler ve varsayılan olarak yalnızca mevcut olanlar listelenir.
      Planlı bir yolu "var" diye sunmak yanlış cevaptır.
    - **Şarj soketi sayıları alt sınırdır.** Soket dosyasının 30 073/38 950
      satırı istasyon numarası taşımıyor; `socket_count_at_least` bilinen
      kısmı verir, toplamı vermez. `sockets_known` false ise soket sayısı
      **sıfır değil, bilinmiyor**.
    - **Şarj verisi eski.** Portal tarihi 2025-05; sonuç `is_stale: true`
      taşır ve cevapta bunu söylemek gerekir. Park alanları ve bisiklet
      yolu verisi 2026-11 tarihli, taze.

    NE ZAMAN KULLANMA:
    - Otopark (araba) aramak için kullanma — `find_parking` kullan. Bu araç
      bisiklet/scooter parkı ve şarj istasyonu bilir, İSPARK otoparkını değil.
    - Toplu taşıma durağı için kullanma — `find_nearby_stops` kullan.
    - **Müsait soket ya da boş park yeri sormak için kullanma**; bu veri
      anlık doluluk içermiyor, yalnızca noktanın varlığını söylüyor.
    - Bisiklet kiralama (İsbike) için kullanma: servis kapalı ve veri yok.
    - Şarj ücreti ya da ödeme yöntemi için kullanma; kaynakta fiyat yok.
    - İki nokta arası bisiklet rotası için kullanma; araç yol kayıtlarını
      listeler, rota planlamaz.
    """
    from istanbul_agent.store.mobility import (
        KIND_VOCABULARY,
        find_mobility_near,
        resolve_kinds,
    )

    radius = max(50.0, min(float(radius_m), MAX_RADIUS_M))
    count = max(1, min(int(limit), 50))
    kinds, kind_error = resolve_kinds(kind)
    if kind_error:
        return InvalidKind(asked=kind or "", valid_kinds=list(KIND_VOCABULARY), message=kind_error)

    notes: list[str] = []
    if lat is not None and lon is not None:
        origin_name = f"{lat:.5f},{lon:.5f}"
        origin_lat, origin_lon = float(lat), float(lon)
    else:
        if not around:
            return PlaceNotFound(
                asked="",
                message="Ya `around` (yer adı) ya da `lat` ve `lon` birlikte verilmeli.",
            )
        from istanbul_agent.store.places import find_places

        search = find_places(around, limit=3)
        if not search.found:
            return PlaceNotFound(
                asked=around,
                candidates=list(search.candidates),
                message=(
                    f"'{around}' adlı bir yer bulunamadı, bu yüzden çevresi aranamadı. "
                    "Adaylar arasından doğru olanı kullanıcıya sor."
                ),
            )
        anchor = search.clusters[0]
        origin_name, origin_lat, origin_lon = anchor.name, anchor.lat, anchor.lon
        notes.append(f"Merkez olarak '{anchor.name}' ({anchor.mode_label}) alındı.")

    result = find_mobility_near(
        origin_lat,
        origin_lon,
        radius_m=radius,
        kinds=kinds,
        public_only=public_only,
        include_planned_paths=include_planned_paths,
        limit=count,
    )
    if not result.store_ready:
        return StoreNotReady(message=result.notes[0])

    notes.extend(result.notes)
    if not result.points and not result.paths:
        notes.append(
            f"{radius:.0f} m içinde kayıtlı bisiklet/scooter parkı, şarj istasyonu ya da "
            "bisiklet yolu bulunamadı; yarıçapı artırmayı dene."
        )

    sources = sorted(
        {point.source for point in result.points} | {path.source for path in result.paths}
    )
    dates = sorted(
        {point.data_as_of for point in result.points} | {path.data_as_of for path in result.paths}
    )
    return MobilityOut(
        around=origin_name,
        lat=round(origin_lat, 6),
        lon=round(origin_lon, 6),
        radius_m=radius,
        points=[
            MobilityPointOut(
                name=point.name,
                kind=point.kind,
                kind_label=point.kind_label,
                district=point.district,
                detail=point.detail,
                lat=round(point.lat, 6),
                lon=round(point.lon, 6),
                distance_m=point.distance_m,
                public_access=point.public_access,
                operator=point.operator,
                sockets_known=point.sockets_known,
                socket_count_at_least=point.socket_count_at_least,
                socket_types=point.socket_types,
                max_power_kw=point.max_power_kw,
            )
            for point in result.points
        ],
        paths=[
            MobilityPathOut(
                name=path.name,
                district=path.district,
                stage=path.stage,
                exists_now=path.exists_now,
                length_km=path.length_km,
                distance_m=path.distance_m,
            )
            for path in result.paths
        ],
        source=" | ".join(sources) if sources else "İBB Açık Veri 1, 16, 275, 276",
        data_as_of=", ".join(dates) if dates else "-",
        is_stale=any(point.is_stale for point in result.points)
        or any(path.is_stale for path in result.paths),
        notes=notes,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()


__all__ = ["find_mobility_options", "find_parking", "get_traffic_status", "mcp"]
