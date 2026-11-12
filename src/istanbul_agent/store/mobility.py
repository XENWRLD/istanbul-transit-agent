"""Mikromobilite sorguları: park alanları, şarj istasyonları, bisiklet yolları.

`find_mobility_options` bu modül üzerinden okuyor. Tablolar
`store/ingest_mobility.py` ile kuruluyor; depo yoksa sorgular ham
`IOException` değil, çalıştırılacak komutu söyleyen bir durum döndürüyor
(karar 0011).

Mesafe ölçümü `haversine_m` ile Python tarafında yapılıyor, SQL'de değil:
nokta tablosu 4233 satır, düğüm tablosu 132 426 satır ve ikisi de tek
sorguda bellekte rahat dönüyor. Spatial eklentisine bağımlılık eklemenin
karşılığı yok.

Bisiklet yolu mesafesi **düğüm bazlıdır**: bir yol 88 düğüm ortancasıyla
saklandığı için en yakın düğümün mesafesi, yola olan mesafenin iyi bir
yaklaşımı. Yol parçasına dik mesafe hesaplanmıyor; fark düğüm aralığı
mertebesinde ve araç çıktısı bunu `distance_m` olarak kuş uçuşu diye
söylüyor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from istanbul_agent.store.db import read_only_connection
from istanbul_agent.store.geo import haversine_m

# Tablonun gerçekten taşıdığı türler. Eşanlamlı sözlüğü bunun dışına
# açılamaz — karar 0015'in dersi: sözlükle tablo karşılaştırılmayınca
# araç kendi belgelediği bir değeri hiç sonuç döndürmeden kabul ediyor.
STORED_KINDS: frozenset[str] = frozenset({"bisiklet_park", "mikromobilite_park", "sarj"})

KIND_SYNONYMS: dict[str, tuple[str, ...]] = {
    "bisiklet_park": ("bisiklet_park",),
    "bisiklet": ("bisiklet_park",),
    "mikromobilite_park": ("mikromobilite_park",),
    "mikromobilite": ("mikromobilite_park",),
    "scooter": ("mikromobilite_park",),
    "park": ("bisiklet_park", "mikromobilite_park"),
    "sarj": ("sarj",),
    "şarj": ("sarj",),
    "elektrikli": ("sarj",),
}

KIND_LABELS: dict[str, str] = {
    "bisiklet_park": "bisiklet park alanı",
    "mikromobilite_park": "mikromobilite park alanı",
    "sarj": "elektrikli araç şarj istasyonu",
}

# Hata mesajının gösterdiği sözcükler. Mesaj ne yazıyorsa araç onu kabul
# etmek zorunda (karar 0015/4); bir test bunu tutuyor.
KIND_VOCABULARY: tuple[str, ...] = (
    "bisiklet_park",
    "mikromobilite_park",
    "sarj",
    "park",
    "bisiklet",
    "mikromobilite",
    "scooter",
    "elektrikli",
)


@dataclass
class MobilityPoint:
    """Bir park alanı ya da şarj istasyonu, sorulan konuma uzaklığıyla."""

    point_id: str
    kind: str
    name: str
    district: str | None
    detail: str | None
    lat: float
    lon: float
    distance_m: float
    public_access: bool | None
    operator: str | None
    sockets_known: bool
    socket_count_at_least: int | None
    socket_types: str | None
    max_power_kw: float | None
    source: str
    data_as_of: str
    is_stale: bool

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


@dataclass
class MobilityPath:
    """Bir bisiklet yolu kaydı; `exists_now` mevcut mu planlı mı söyler."""

    path_id: str
    name: str
    district: str | None
    stage: str
    exists_now: bool
    length_km: float | None
    distance_m: float
    source: str
    data_as_of: str
    is_stale: bool


@dataclass
class MobilitySearch:
    """Bir aramanın sonucu: noktalar, yollar ve kaynaktan gelen notlar."""

    points: list[MobilityPoint] = field(default_factory=list)
    paths: list[MobilityPath] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    store_ready: bool = True


def resolve_kinds(kind: str | None) -> tuple[tuple[str, ...], str | None]:
    """Tür adını tabloda duran değerlere çevir; `(türler, hata)`.

    Boş demet "kısıt yok" demek. Bilinmeyen bir değer "sonuç yok" değil,
    geçerli sözcükleri sayan bir hata döndürür (karar 0006/7a).
    """
    if kind is None or not str(kind).strip():
        return (), None
    wanted = str(kind).strip().lower()
    resolved = KIND_SYNONYMS.get(wanted)
    if resolved is None:
        valid = ", ".join(KIND_VOCABULARY)
        return (), f"'{kind}' bilinen bir tür değil. Geçerli değerler: {valid}."
    return resolved, None


def tables_present() -> bool:
    """Mikromobilite tabloları kurulu mu?"""
    try:
        with read_only_connection() as connection:
            rows = connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('mobility_points', 'mobility_paths', 'mobility_path_nodes')"
            ).fetchall()
    except Exception:
        return False
    return len({row[0] for row in rows}) == 3


def _point_rows(connection: Any, kinds: tuple[str, ...]) -> list[tuple[Any, ...]]:
    sql = (
        "SELECT point_id, kind, name, district, detail, lat, lon, public_access, operator, "
        "sockets_known, socket_count_at_least, socket_types, max_power_kw, source, "
        "data_as_of, is_stale FROM mobility_points"
    )
    if kinds:
        placeholders = ", ".join("?" for _ in kinds)
        return connection.execute(f"{sql} WHERE kind IN ({placeholders})", list(kinds)).fetchall()
    return connection.execute(sql).fetchall()


def find_mobility_near(
    lat: float,
    lon: float,
    *,
    radius_m: float = 1000.0,
    kinds: tuple[str, ...] = (),
    public_only: bool = True,
    include_planned_paths: bool = False,
    include_paths: bool = True,
    limit: int = 10,
) -> MobilitySearch:
    """Bir koordinatın çevresindeki park, şarj ve bisiklet yolu kayıtları.

    `public_only` yalnızca şarj istasyonlarını etkiler: kaynak 2029 halka
    açık ve 904 özel istasyon ayırıyor, özel olanlar varsayılan olarak
    listelenmiyor ve kaç tanesinin düştüğü nota yazılıyor.

    `include_planned_paths` false iken yalnızca **mevcut** bisiklet yolları
    döner. Kaynağın 477 kaydının 212'si henüz yol değil (UTK kararı, etüt,
    proje, inşaat); planlı bir kaydı mevcut gibi sunmak yanlış cevap olur.
    """
    if not tables_present():
        return MobilitySearch(
            store_ready=False,
            notes=[
                "Mikromobilite tabloları kurulu değil. Çalıştır: "
                "uv run python scripts/download_mobility.py && "
                "uv run python -m istanbul_agent.store.ingest_mobility"
            ],
        )

    result = MobilitySearch()
    excluded_private = 0
    with read_only_connection() as connection:
        for row in _point_rows(connection, kinds):
            distance = haversine_m(lat, lon, row[5], row[6])
            if distance > radius_m:
                continue
            if public_only and row[1] == "sarj" and row[7] is False:
                excluded_private += 1
                continue
            result.points.append(
                MobilityPoint(
                    point_id=row[0],
                    kind=row[1],
                    name=row[2],
                    district=row[3],
                    detail=row[4],
                    lat=row[5],
                    lon=row[6],
                    distance_m=round(distance, 1),
                    public_access=row[7],
                    operator=row[8],
                    sockets_known=bool(row[9]),
                    socket_count_at_least=row[10],
                    socket_types=row[11],
                    max_power_kw=row[12],
                    source=row[13],
                    data_as_of=row[14],
                    is_stale=bool(row[15]),
                )
            )
        result.points.sort(key=lambda point: point.distance_m)

        if include_paths:
            nodes = connection.execute(
                "SELECT path_id, lat, lon FROM mobility_path_nodes"
            ).fetchall()
            nearest: dict[str, float] = {}
            for path_id, node_lat, node_lon in nodes:
                distance = haversine_m(lat, lon, node_lat, node_lon)
                if distance <= radius_m and distance < nearest.get(path_id, float("inf")):
                    nearest[path_id] = distance
            if nearest:
                placeholders = ", ".join("?" for _ in nearest)
                path_rows = connection.execute(
                    "SELECT path_id, name, district, stage, exists_now, length_km, source, "
                    f"data_as_of, is_stale FROM mobility_paths WHERE path_id IN ({placeholders})",
                    list(nearest),
                ).fetchall()
                skipped_planned = 0
                for row in path_rows:
                    if not row[4] and not include_planned_paths:
                        skipped_planned += 1
                        continue
                    result.paths.append(
                        MobilityPath(
                            path_id=row[0],
                            name=row[1],
                            district=row[2],
                            stage=row[3],
                            exists_now=bool(row[4]),
                            length_km=row[5],
                            distance_m=round(nearest[row[0]], 1),
                            source=row[6],
                            data_as_of=row[7],
                            is_stale=bool(row[8]),
                        )
                    )
                result.paths.sort(key=lambda path: path.distance_m)
                if skipped_planned:
                    result.notes.append(
                        f"{skipped_planned} kayıt henüz yol değil (proje/karar aşamasında) ve "
                        "listelenmedi; görmek için include_planned_paths=true ver."
                    )

    # Notlar kırpma **sonrası** listeden üretiliyor (karar 0013/4).
    dropped = max(0, len(result.points) - limit)
    result.points = result.points[:limit]
    result.paths = result.paths[:limit]
    if dropped:
        result.notes.append(f"{dropped} kayıt daha var; en yakın {limit} tanesi listelendi.")
    if excluded_private:
        result.notes.append(
            f"{excluded_private} özel (halka kapalı) şarj istasyonu listelenmedi; "
            "görmek için public_only=false ver."
        )
    unknown_sockets = [
        point for point in result.points if point.kind == "sarj" and not point.sockets_known
    ]
    if unknown_sockets:
        result.notes.append(
            f"{len(unknown_sockets)} şarj istasyonunun soket bilgisi kaynakta yok; "
            "soket sayısı sıfır değil, bilinmiyor."
        )
    if any(point.kind == "sarj" for point in result.points):
        result.notes.append(
            "Şarj soketi sayıları **alt sınırdır**: soket dosyasının 30 073/38 950 satırı "
            "istasyon numarası taşımıyor, o satırlar hiçbir istasyona bağlanamıyor."
        )
    return result
