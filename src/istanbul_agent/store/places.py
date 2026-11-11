"""Yer arama ve yakındaki duraklar: `transit_places` üzerine sorgular.

`geocode_place` ve `find_nearby_stops` araçlarının veri katmanı. İki şeyi
raylı istasyon aramasından (karar 0006/3) devralıyor: dört adımlı ad
eşleştirme ve bulunamadığında üç aday döndürme. Bir şeyi ise devralmıyor —
**teklik**. Raylı sistemde bir ad genellikle bir istasyon demek; durak
tablosunda öyle değil:

- "KADIKÖY İSKELE" adını taşıyan onlarca otobüs durağı var (her yön ve her
  peron ayrı kayıt),
- "D-100 Karayolu" adlı 100'den fazla minibüs durağı şehrin her yerine
  dağılmış.

Bu yüzden arama sonucu **küme** döndürür: aynı adı taşıyan ve birbirine 300
metreden yakın noktalar tek yer sayılır, uzaktakiler ayrı yerler olarak
listelenir. Aynı adlı noktaların ortalamasını almak yanlış olurdu — şehrin
iki yakasındaki iki "D-100 Karayolu"nun ortası Boğaz'ın içine düşer.

Mod önceliği sabit: kullanıcı "Kadıköy" dediğinde kastettiği büyük
olasılıkla iskele ya da metro istasyonudur, yüz metre ötedeki bir otobüs
bayrağı değil.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from istanbul_agent.store.db import read_only_connection
from istanbul_agent.store.geo import haversine_m
from istanbul_agent.store.station_names import collapsed, normalize, similarity, token_key

__all__ = [
    "MODE_LABELS",
    "MODE_PRIORITY",
    "MODE_SYNONYMS",
    "MODE_VOCABULARY",
    "RAIL_MODES",
    "STORED_MODES",
    "Place",
    "PlaceCluster",
    "PlaceSearch",
    "find_nearby_places",
    "find_places",
    "load_places",
    "resolve_mode",
]

# Aynı adı taşıyan noktalar bu yarıçap içinde tek yer sayılır. 300 m, bir
# kavşağın iki yönündeki durakları ve bir iskelenin peronlarını birleştirir
# ama iki ayrı semtteki aynı adlı durakları birleştirmez.
CLUSTER_RADIUS_M = 300.0

# Ön ek bu kadar ayrı ada kadar uyuyorsa hepsi sonuç sayılır; üstünde
# belirsizlik kullanıcıya bırakılır (`related` listesinde görünürler).
PREFIX_FANOUT_LIMIT = 5

# Benzerlik eşiği ve eşitlik bandı: bu bandın içindeki puanlar eşit
# sayılır, aralarından biri seçilmez.
SIMILARITY_THRESHOLD = 0.85
SIMILARITY_TIE_BAND = 0.01

# `transit_places` tablosunun gerçekten taşıdığı mod değerleri; `ingest`
# başka değer üretmiyor. Bir eşanlamlı bunların dışına açılırsa arama
# **sessizce boş** döner, o yüzden eşanlamlı sözlüğü buna karşı test edilir.
STORED_MODES: frozenset[str] = frozenset(
    {
        "metro",
        "tramvay",
        "funikuler",
        "teleferik",
        "vapur",
        "otobus",
        "minibus",
        "dolmus",
    }
)

# Kullanıcının bir ad söylediğinde kastettiği mod sırası.
MODE_PRIORITY: tuple[str, ...] = (
    "metro",
    "tramvay",
    "funikuler",
    "teleferik",
    "vapur",
    "otobus",
    "minibus",
    "dolmus",
)

MODE_LABELS: dict[str, str] = {
    "metro": "metro istasyonu",
    "tramvay": "tramvay durağı",
    "funikuler": "füniküler istasyonu",
    "teleferik": "teleferik istasyonu",
    "vapur": "iskele",
    "otobus": "otobüs durağı",
    "minibus": "minibüs durağı",
    "dolmus": "dolmuş durağı",
}

# Modu olmayan bir şey sorulduğunda hata mesajında gösterilecek sözcükler:
# her saklanan mod için bir yazım, artı şemsiye `rayli` ve `marmaray`.
MODE_VOCABULARY: tuple[str, ...] = (
    "dolmus",
    "funikuler",
    "marmaray",
    "metro",
    "minibus",
    "otobus",
    "rayli",
    "teleferik",
    "tramvay",
    "vapur",
)


# Modun kullanıcı (ve modelin) yazabileceği halleri. Buraya gerek var çünkü
# ortak `normalize()` tam bu kelimeleri tesis eki sayıp atıyor: normalize
# ("metro") boş dize döner (station_names rule 5). Model ilk denemede
# `mode="iskele"` verdi, mod listesinde o değer yoktu ve arama sessizce boş
# döndü — araç "geçersiz mod" demek yerine "yer bulunamadı" demiş oldu.
# Her eşanlamlı **saklanan** modlara açılır, kendi adına değil. İki tuzak
# buradan çıktı (karar 0015): `rayli` kendine eşleniyordu ama tabloda o
# modda tek satır yok, `tren`/`marmaray` da öyle — ikisi de her sorguda
# "yer bulunamadı" döndürüyordu. Raylı sistem bir şemsiye: dört moda
# açılır. Marmaray ayrı bir mod değil, istasyonları `metro` satırı olarak
# duruyor (hat kodu `code` alanında "Marmaray" yazar).
RAIL_MODES: tuple[str, ...] = ("metro", "tramvay", "funikuler", "teleferik")

MODE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "metro": ("metro",),
    "tren": ("metro",),
    "banliyo": ("metro",),
    "banliyö": ("metro",),
    "marmaray": ("metro",),
    "tramvay": ("tramvay",),
    "tram": ("tramvay",),
    "funikuler": ("funikuler",),
    "füniküler": ("funikuler",),
    "teleferik": ("teleferik",),
    "rayli": RAIL_MODES,
    "raylı": RAIL_MODES,
    "vapur": ("vapur",),
    "iskele": ("vapur",),
    "feribot": ("vapur",),
    "deniz": ("vapur",),
    "otobus": ("otobus",),
    "otobüs": ("otobus",),
    "bus": ("otobus",),
    "iett": ("otobus",),
    "minibus": ("minibus",),
    "minibüs": ("minibus",),
    "dolmus": ("dolmus",),
    "dolmuş": ("dolmus",),
}


def resolve_mode(value: str | None) -> tuple[tuple[str, ...], str | None]:
    """Mod adını tabloda kullanılan değerlere çevir.

    `(modlar, hata)` döner; mod verilmemişse modlar boştur (kısıt yok).
    Tanınmayan bir mod **sessizce boş sonuç değil**, geçerli değerleri sayan
    bir hata üretir: boş sonuç, modelin "böyle bir yer yok" diye
    cevaplamasına yol açıyor. Bir eşanlamlı birden çok moda açılabilir
    (`rayli` → metro + tramvay + füniküler + teleferik).
    """
    if value is None:
        return (), None
    wanted = value.strip().lower()
    if not wanted:
        return (), None
    resolved = MODE_SYNONYMS.get(wanted)
    if resolved is None:
        valid = ", ".join(MODE_VOCABULARY)
        return (), f"'{value}' geçerli bir mod değil. Geçerli değerler: {valid}."
    return resolved, None


def mode_rank(mode: str) -> int:
    try:
        return MODE_PRIORITY.index(mode)
    except ValueError:
        return len(MODE_PRIORITY)


@dataclass(frozen=True)
class Place:
    """`transit_places` tablosunun bir satırı."""

    place_id: str
    mode: str
    name: str
    name_key: str
    name_key_flat: str
    name_key_sorted: str
    code: str | None
    detail: str | None
    lat: float
    lon: float
    source: str
    data_as_of: str
    is_stale: bool

    @property
    def mode_label(self) -> str:
        return MODE_LABELS.get(self.mode, self.mode)


@dataclass
class PlaceCluster:
    """Aynı adı taşıyan ve birbirine yakın noktaların tek yer olarak hali."""

    name: str
    mode: str
    lat: float
    lon: float
    point_count: int
    codes: tuple[str, ...]
    details: tuple[str, ...]
    source: str
    data_as_of: str
    is_stale: bool
    distance_m: float | None = None

    @property
    def mode_label(self) -> str:
        return MODE_LABELS.get(self.mode, self.mode)


@dataclass
class PlaceSearch:
    """Arama sonucu: bulunan kümeler, nasıl bulunduğu, bulunamadıysa adaylar."""

    query: str
    clusters: list[PlaceCluster] = field(default_factory=list)
    how: str | None = None
    candidates: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    total_points: int = 0

    @property
    def found(self) -> bool:
        return bool(self.clusters)


def load_places(path: Path | None = None, *, modes: Sequence[str] | None = None) -> list[Place]:
    """Nokta tablosunu oku.

    Tablo 23 bin satır; tamamını okumak milisaniyeler sürüyor ve mesafe
    hesabını Python'da yapmak DuckDB'ye spatial eklentisi yüklemekten basit.
    Tablo büyürse burası sınır kutusu ön filtresiyle değiştirilir.
    """
    query = """
        SELECT place_id, mode, name, name_key, name_key_flat, name_key_sorted,
               code, detail, lat, lon, source, data_as_of, is_stale
        FROM transit_places
    """
    parameters: list[object] = []
    if modes:
        placeholders = ", ".join("?" * len(modes))
        query += f" WHERE mode IN ({placeholders})"
        parameters.extend(modes)
    with read_only_connection(path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [Place(*row) for row in rows]


def _cluster(places: Sequence[Place]) -> list[PlaceCluster]:
    """Aynı ad + mod altındaki noktaları yakınlığa göre kümele."""
    buckets: dict[tuple[str, str], list[Place]] = {}
    for place in places:
        buckets.setdefault((place.mode, place.name_key), []).append(place)

    clusters: list[PlaceCluster] = []
    for group in buckets.values():
        remaining = list(group)
        while remaining:
            seed = remaining.pop(0)
            members = [seed]
            rest: list[Place] = []
            for other in remaining:
                if haversine_m(seed.lat, seed.lon, other.lat, other.lon) <= CLUSTER_RADIUS_M:
                    members.append(other)
                else:
                    rest.append(other)
            remaining = rest
            clusters.append(
                PlaceCluster(
                    name=members[0].name,
                    mode=members[0].mode,
                    lat=sum(member.lat for member in members) / len(members),
                    lon=sum(member.lon for member in members) / len(members),
                    point_count=len(members),
                    codes=tuple(dict.fromkeys(member.code for member in members if member.code)),
                    details=tuple(
                        dict.fromkeys(member.detail for member in members if member.detail)
                    ),
                    source=members[0].source,
                    data_as_of=members[0].data_as_of,
                    is_stale=any(member.is_stale for member in members),
                )
            )
    clusters.sort(key=lambda cluster: (mode_rank(cluster.mode), -cluster.point_count, cluster.name))
    return clusters


def _related(
    query: str, places: Sequence[Place], exclude: set[str], limit: int = 5
) -> tuple[str, ...]:
    """Sorguyla başlayan ama tam eşleşmeyen adlar.

    Ortak `normalize()` "iskele", "metro", "durağı" gibi tesis eklerini
    attığı için (rule 5) "Kadıköy iskelesi" sorgusu "Kadıköy" adına
    eşleşiyor; oysa gerçek iskelenin adı "Kadıköy ŞH.". İşletmeci ekleri
    (ŞH., İDO, Turyol, Dentur) normalizasyonda yok ve oraya eklemek raylı
    sistem eşleştirmesini de değiştirmek demek (karar 0003 yeniden
    doğrulanmalı olurdu). Onun yerine bu adlar **ayrıca** döndürülüyor:
    agent kullanıcıya "Kadıköy ŞH. iskelesini mi kastettiniz" diyebilsin.
    """
    flat = collapsed(query)
    if len(flat) < 3:
        return ()
    names: list[str] = []
    for place in places:
        if place.name_key in exclude or place.name in names:
            continue
        if place.name_key_flat.startswith(flat) and place.name_key_flat != flat:
            names.append(place.name)
    return tuple(sorted(names, key=len)[:limit])


def _candidates(query: str, places: Sequence[Place], limit: int = 3) -> tuple[str, ...]:
    """Hiçbir adım tutmadığında en yakın adlar."""
    scored = sorted(
        ({place.name: similarity(query, place.name) for place in places}).items(),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return tuple(name for name, _ in scored[:limit])


def find_places(
    query: str,
    *,
    modes: Sequence[str] | None = None,
    limit: int = 5,
    places: Sequence[Place] | None = None,
) -> PlaceSearch:
    """`query` adının karşılık geldiği yerler.

    Eşleştirme en katıdan en gevşeğe dört adım (raylı sistemdeki ile aynı,
    karar 0006/3): tam ad, yazım farkı, kelime sırası, tek adaya götüren
    ön ek. Hiçbiri tutmazsa benzerlik ≥ 0.85 denenir, o da tutmazsa üç aday
    döner — boş cevap kimseye yardım etmiyor.
    """
    wanted = (query or "").strip()
    if not wanted:
        return PlaceSearch(query=query or "")

    pool = list(places) if places is not None else load_places(modes=modes)
    if modes and places is not None:
        allowed = set(modes)
        pool = [place for place in pool if place.mode in allowed]
    if not pool:
        return PlaceSearch(query=wanted)

    key, flat, tokens = normalize(wanted), collapsed(wanted), token_key(wanted)

    steps: list[tuple[str, list[Place]]] = [
        ("tam ad", [place for place in pool if place.name_key == key]),
        ("yazım farkı", [place for place in pool if place.name_key_flat == flat]),
        ("kelime sırası", [place for place in pool if place.name_key_sorted == tokens]),
    ]
    if len(flat) >= 4:
        prefixed = [place for place in pool if place.name_key_flat.startswith(flat)]
        distinct = {place.name_key for place in prefixed}
        if len(distinct) == 1:
            steps.append(("ön ek", prefixed))
        elif 1 < len(distinct) <= PREFIX_FANOUT_LIMIT:
            # Birkaç ada uyan ön ek de kabul edilir ve **hepsi** döner.
            # "Kadıköy" + mod=vapur dört iskeleye uyuyor (İDO, ŞH. Yeni,
            # ŞH. Eski, Balon Turyol) ve dördü de gerçekten Kadıköy
            # iskelesi; reddetmek çıkmaz sokaktı — model "böyle bir iskele
            # yok" diye cevaplıyordu. Onlarca ada uyan ön ek (mod
            # verilmeden "kadikoy") hâlâ reddedilir, çünkü orada seçim
            # kullanıcının.
            steps.append((f"ön ek ({len(distinct)} aday)", prefixed))

    for how, matched in steps:
        if matched:
            clusters = _cluster(matched)
            return PlaceSearch(
                query=wanted,
                clusters=clusters[:limit],
                how=how,
                related=_related(wanted, pool, {place.name_key for place in matched}),
                total_points=len(matched),
            )

    # Benzerlik adımı: eşit puanlı adlar arasından **keyfî seçim yapmaz**.
    # "Ortaköy" ile "ORTAKÖY A".."ORTAKÖY F" adlarının altısı da 0.88
    # alıyor; birini seçmek, kullanıcının sormadığı durağı seçmek demek.
    scores: dict[str, float] = {}
    for place in pool:
        score = similarity(wanted, place.name)
        if score > scores.get(place.name_key, 0.0):
            scores[place.name_key] = score
    best_score = max(scores.values(), default=0.0)
    if best_score >= SIMILARITY_THRESHOLD:
        tied = [key for key, score in scores.items() if score >= best_score - SIMILARITY_TIE_BAND]
        if len(tied) <= PREFIX_FANOUT_LIMIT:
            matched = [place for place in pool if place.name_key in set(tied)]
            clusters = _cluster(matched)
            how = f"benzerlik {best_score:.2f}"
            if len(tied) > 1:
                how = f"{how} ({len(tied)} aday)"
            return PlaceSearch(
                query=wanted,
                clusters=clusters[:limit],
                how=how,
                related=_related(wanted, pool, set(tied)),
                total_points=len(matched),
            )

    return PlaceSearch(
        query=wanted,
        candidates=_candidates(wanted, pool),
        related=_related(wanted, pool, set()),
    )


def find_nearby_places(
    lat: float,
    lon: float,
    *,
    radius_m: float = 500.0,
    modes: Sequence[str] | None = None,
    limit: int = 10,
    places: Sequence[Place] | None = None,
) -> list[PlaceCluster]:
    """Bir noktanın çevresindeki duraklar, yakından uzağa.

    Kümeleme burada da geçerli: bir kavşağın iki yönündeki aynı adlı iki
    durak tek satır olur, yoksa yarıçap içindeki ilk on sonucun hepsi aynı
    durağın kopyaları olabiliyor.
    """
    pool = list(places) if places is not None else load_places(modes=modes)
    if modes and places is not None:
        allowed = set(modes)
        pool = [place for place in pool if place.mode in allowed]

    near = [place for place in pool if haversine_m(lat, lon, place.lat, place.lon) <= radius_m]
    clusters = _cluster(near)
    for cluster in clusters:
        cluster.distance_m = round(haversine_m(lat, lon, cluster.lat, cluster.lon), 1)
    clusters.sort(key=lambda cluster: (cluster.distance_m or 0.0, mode_rank(cluster.mode)))
    return clusters[:limit]
