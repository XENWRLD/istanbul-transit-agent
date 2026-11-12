"""Mikromobilite katmanlarını DuckDB'ye al: park alanları, şarj, bisiklet yolları.

Üç tablo kuruluyor:

- `mobility_points`  -- nokta katmanları (bisiklet/mikromobilite parkı, şarj istasyonu)
- `mobility_paths`   -- bisiklet yolu kayıtları (ad, ilçe, **aşama**, uzunluk)
- `mobility_path_nodes` -- yolların düğümleri; "en yakın yol" mesafesi buradan

Kaynakların ölçülmüş ağzı (karar 0019; ölçüm `scripts/inspect_mobility_sources.py`):

**16 — park alanları (1300 nokta, 2026-11-04).** `Park_Tipi` iki değer
taşıyor: `Mikromobilite Park Alanı` (1099) ve `Bisiklet Park Alanı` (201).
İkisi ayrı tür olarak saklanıyor, çünkü scooter parkı ile bisiklet parkı
aynı soruya cevap vermiyor. Koordinat hem geometride hem `X`/`Y`
alanlarında var; geometri esas alınıp `X`/`Y` yedek olarak okunuyor.

**276 — şarj istasyonları (2933 nokta, 2025-05-13).** Portal tarihi bir
buçuk yıldan eski, bu yüzden `is_stale = true`. `HIZMET_SEKLI` alanı
2029 `HALKA_ACIK` ve 904 `OZEL` ayırıyor; ikisi de saklanıyor, aracın
varsayılanı halka açık olanlar.

**275 — soketler (38 950 satır, CSV, virgül, başlıkta BOM).** Üç ölçülmüş
tuzak:

1. Başlık `\\ufeffISTASYON_NO` olarak geliyor; BOM temizlenmeden alan adı
   hiçbir şeyle eşleşmiyor.
2. Satırların **yalnızca %23'ü** (8877) istasyon numarası taşıyor.
   Kalan 30 073 satır 8406. satırdan sonra gelip kendi `SOKET_NO`'sunu
   taşıyor, yani bu bir "birleştirilmiş hücre" artığı **değil**: aşağı
   doldurma yapmak 29 487 soketi tek istasyona yazardı. Bağlanamayan
   satırlar atılıyor ve sayısı `ingest_meta`'ya not olarak yazılıyor.
3. Dolu satırlar 2962 ayrı istasyona dağılıyor ve bunların 2931'i 276'daki
   istasyonlarla eşleşiyor. Yani neredeyse her istasyonun **bir miktar**
   soketi biliniyor, ama toplamı bilinmiyor: tablo bu yüzden
   `socket_count_at_least` adını taşıyor. Karar 0013/1'in kuralı burada da
   geçerli — sıfır "bilinmiyor" demek değil, eksik de "tamam" demek değil.

**1 — bisiklet yolları (477 çizgi, 132 426 düğüm, 2026-11-06).** `PRJ_ASAMA`
alanı kaydın **mevcut mu planlı mı** olduğunu söylüyor: 238 `Mevcut
Ayrılmış` + 27 `Mevcut Paylaşımlı` mevcut; 204 `UTK kararı alınan`, 3 etüt,
2 proje, 2 inşaat, 1 projesi tamamlanan **henüz yol değil** — kayıtların
%44'ü. `exists` sütunu bu ayrımı taşıyor ve araç planlı olanı mevcut gibi
sunmuyor.

    uv run python -m istanbul_agent.store.ingest_mobility
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output
from istanbul_agent.store.db import connect, database_path
from istanbul_agent.store.geo import parse_coordinate
from istanbul_agent.store.station_names import collapsed, normalize, token_key

DATA_DIR = PROJECT_ROOT / "data" / "mobility"
MANIFEST_PATH = DATA_DIR / "_mobility_manifest.json"

# Portal tarihi bundan eskiyse veri `is_stale` sayılır. Şarj verisi
# 2025-05, park ve yol verisi 2026-11: eşik ikisini doğru ayırıyor.
STALE_BEFORE = "2026-01-01"

# `Park_Tipi` → tablodaki tür. Kaynak iki değer taşıyor; tahmin edilen
# üçüncü bir değer yok, bilinmeyen gelirse ingest onu sayıp bildirir.
# Anahtarlar **normalize edilmiş** biçimde: `normalize()` Türkçe harfleri de
# katlıyor ("Park Alanı" → "park alani"). İlk yazımda anahtarları olduğu gibi
# yazdım ve ingest 1300 park alanının tamamını "bilinmeyen tip" diye attı;
# bir test artık her anahtarın normalize'dan geçmiş olmasını tutuyor.
PARK_KINDS: dict[str, str] = {
    "mikromobilite park alani": "mikromobilite_park",
    "bisiklet park alani": "bisiklet_park",
}

# Mevcut sayılan aşamalar, yine normalize edilmiş biçimde
# ("Mevcut Ayrılmış" → "mevcut ayrilmis"). Geri kalan her değer planlı ya da
# yapım aşamasıdır; aynı yazım hatası burada 265 mevcut yolu planlı
# göstermişti.
EXISTING_STAGES: frozenset[str] = frozenset({"mevcut ayrilmis", "mevcut paylasimli"})

SCHEMA = """
DROP TABLE IF EXISTS mobility_points;
DROP TABLE IF EXISTS mobility_paths;
DROP TABLE IF EXISTS mobility_path_nodes;

CREATE TABLE mobility_points (
    point_id      VARCHAR NOT NULL,
    kind          VARCHAR NOT NULL,
    name          VARCHAR NOT NULL,
    name_key      VARCHAR NOT NULL,
    name_key_flat VARCHAR NOT NULL,
    name_key_sorted VARCHAR NOT NULL,
    district      VARCHAR,
    region        VARCHAR,
    detail        VARCHAR,
    public_access BOOLEAN,
    operator      VARCHAR,
    station_no    VARCHAR,
    sockets_known BOOLEAN NOT NULL,
    socket_count_at_least INTEGER,
    socket_types  VARCHAR,
    max_power_kw  DOUBLE,
    lat           DOUBLE NOT NULL,
    lon           DOUBLE NOT NULL,
    source        VARCHAR NOT NULL,
    data_as_of    VARCHAR NOT NULL,
    is_stale      BOOLEAN NOT NULL
);

CREATE TABLE mobility_paths (
    path_id       VARCHAR NOT NULL,
    name          VARCHAR NOT NULL,
    district      VARCHAR,
    region        VARCHAR,
    stage         VARCHAR NOT NULL,
    exists_now    BOOLEAN NOT NULL,
    length_km     DOUBLE,
    node_count    INTEGER NOT NULL,
    source        VARCHAR NOT NULL,
    data_as_of    VARCHAR NOT NULL,
    is_stale      BOOLEAN NOT NULL
);

CREATE TABLE mobility_path_nodes (
    path_id       VARCHAR NOT NULL,
    seq           INTEGER NOT NULL,
    lat           DOUBLE NOT NULL,
    lon           DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_meta (
    table_name   VARCHAR NOT NULL,
    source       VARCHAR NOT NULL,
    data_as_of   VARCHAR NOT NULL,
    rows         BIGINT NOT NULL,
    is_stale     BOOLEAN NOT NULL,
    note         VARCHAR,
    ingested_at  VARCHAR NOT NULL
);
"""


def load_manifest() -> dict[str, dict[str, Any]]:
    """İndirme manifest'ini slug'a göre indeksle.

    Manifest yoksa ham `FileNotFoundError` yerine çalıştırılacak komutu
    söyleyen bir hata veriliyor (karar 0011).
    """
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(
            "Mikromobilite kaynakları indirilmemiş. Önce şunu çalıştır:\n"
            "  uv run python scripts/download_mobility.py"
        )
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {entry["slug"]: entry for entry in payload.get("layers", [])}


def is_stale(data_as_of: str) -> bool:
    return (data_as_of or "") < STALE_BEFORE


def name_keys(name: str) -> tuple[str, str, str]:
    return normalize(name), collapsed(name), token_key(name)


def first_coordinate(geometry: dict[str, Any]) -> tuple[float, float] | None:
    """Geometrinin ilk (lon, lat) çiftini çöz."""
    coords = geometry.get("coordinates")
    while isinstance(coords, list) and coords and isinstance(coords[0], list):
        coords = coords[0]
    if not (isinstance(coords, list) and len(coords) >= 2):
        return None
    lon = parse_coordinate(coords[0])
    lat = parse_coordinate(coords[1])
    if lat is None or lon is None:
        return None
    return lat, lon


def read_sockets(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """İstasyon numarasına göre soket özeti + atılanların sayımı.

    `utf-8-sig` ile okunuyor: başlıktaki BOM temizlenmezse alan adı
    `\\ufeffISTASYON_NO` kalıyor ve hiçbir şeyle eşleşmiyor.
    """
    counts: dict[str, int] = {"rows": 0, "without_station": 0}
    powers: dict[str, list[float]] = defaultdict(list)
    types: dict[str, Counter[str]] = defaultdict(Counter)
    sockets: dict[str, int] = Counter()

    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            counts["rows"] += 1
            station = (row.get("ISTASYON_NO") or "").strip()
            if not station:
                # Aşağı doldurma **yapılmıyor**: ölçümde bu satırlar
                # 8406'dan sonra blok halinde geliyor ve kendi
                # `SOKET_NO`'larını taşıyor, yani önceki istasyona ait
                # değiller. Doldurmak 29 487 soketi tek istasyona yazardı.
                counts["without_station"] += 1
                continue
            sockets[station] += 1
            kind = (row.get("SOKET_TURU") or row.get("SOKET_TIPI") or "").strip()
            if kind:
                types[station][kind] += 1
            power = parse_coordinate(row.get("SOKET_GUCU"))
            if power is not None:
                powers[station].append(power)

    summary: dict[str, dict[str, Any]] = {}
    for station, count in sockets.items():
        summary[station] = {
            "count": count,
            "types": ", ".join(name for name, _ in types[station].most_common()),
            "max_power_kw": max(powers[station]) if powers[station] else None,
        }
    return summary, counts


def build_points(
    manifest: dict[str, dict[str, Any]],
) -> tuple[list[tuple[Any, ...]], dict[str, Any]]:
    """Park alanları + şarj istasyonlarından `mobility_points` satırları."""
    rows: list[tuple[Any, ...]] = []
    notes: dict[str, Any] = {}

    # --- 16: park alanları ---
    entry = manifest["bike_parking"]
    data_as_of = entry["data_as_of"]
    stale = is_stale(data_as_of)
    payload = json.loads((DATA_DIR / entry["file"]).read_text(encoding="utf-8"))
    unknown_types: Counter[str] = Counter()
    skipped = 0
    for index, feature in enumerate(payload.get("features") or []):
        props = feature.get("properties") or {}
        position = first_coordinate(feature.get("geometry") or {})
        if position is None:
            # Yedek: kaynak koordinatı ayrı alanlarda da taşıyor.
            lat = parse_coordinate(props.get("Y"))
            lon = parse_coordinate(props.get("X"))
            position = (lat, lon) if lat is not None and lon is not None else None
        if position is None:
            skipped += 1
            continue
        raw_type = str(props.get("Park_Tipi") or "").strip()
        kind = PARK_KINDS.get(normalize(raw_type))
        if kind is None:
            unknown_types[raw_type or "(boş)"] += 1
            continue
        name = str(props.get("Park_Alani") or "").strip() or f"Park alanı {index + 1}"
        key, flat, sorted_key = name_keys(name)
        rows.append(
            (
                f"park-{index + 1}",
                kind,
                name,
                key,
                flat,
                sorted_key,
                (str(props.get("Ilce") or "").strip() or None),
                (str(props.get("Bolge") or "").strip() or None),
                raw_type or None,
                True,  # park alanları halka açık
                None,
                None,
                False,  # soket bilgisi park alanı için anlamsız
                None,
                None,
                None,
                position[0],
                position[1],
                f"İBB Açık Veri {entry['dataset_no']}: {entry['title']}",
                data_as_of,
                stale,
            )
        )
    notes["bike_parking"] = {
        "rows": sum(1 for row in rows if row[1] in PARK_KINDS.values()),
        "skipped_no_coordinate": skipped,
        "unknown_park_types": dict(unknown_types),
        "data_as_of": data_as_of,
        "is_stale": stale,
    }

    # --- 275 + 276: şarj istasyonları ve soketleri ---
    sockets, socket_counts = read_sockets(DATA_DIR / manifest["ev_sockets"]["file"])
    entry = manifest["ev_charging"]
    data_as_of = entry["data_as_of"]
    stale = is_stale(data_as_of)
    payload = json.loads((DATA_DIR / entry["file"]).read_text(encoding="utf-8"))
    matched = 0
    skipped_ev = 0
    public = 0
    for index, feature in enumerate(payload.get("features") or []):
        props = feature.get("properties") or {}
        position = first_coordinate(feature.get("geometry") or {})
        if position is None:
            lat = parse_coordinate(props.get("LATITUDE"))
            lon = parse_coordinate(props.get("LONGITUDE"))
            position = (lat, lon) if lat is not None and lon is not None else None
        if position is None:
            skipped_ev += 1
            continue
        station_no = str(props.get("ISTASYON_NO") or "").strip() or None
        name = str(props.get("AD") or "").strip() or station_no or f"Şarj istasyonu {index + 1}"
        key, flat, sorted_key = name_keys(name)
        socket = sockets.get(station_no or "")
        if socket:
            matched += 1
        access = str(props.get("HIZMET_SEKLI") or "").strip()
        is_public = access.upper() == "HALKA_ACIK"
        public += int(is_public)
        rows.append(
            (
                f"ev-{index + 1}",
                "sarj",
                name,
                key,
                flat,
                sorted_key,
                None,  # kaynakta ilçe alanı yok, yalnızca açık adres var
                None,
                (str(props.get("ADRES") or "").strip() or None),
                is_public,
                (str(props.get("AGIL_ISLETMECISI_UNVAN") or "").strip() or None),
                station_no,
                bool(socket),
                socket["count"] if socket else None,
                socket["types"] if socket else None,
                socket["max_power_kw"] if socket else None,
                position[0],
                position[1],
                (
                    f"İBB Açık Veri {entry['dataset_no']}: {entry['title']}"
                    f" + {manifest['ev_sockets']['dataset_no']} (soketler)"
                ),
                data_as_of,
                stale,
            )
        )
    notes["ev_charging"] = {
        "stations": matched + (len(payload.get("features") or []) - matched - skipped_ev),
        "with_socket_detail": matched,
        "public_access": public,
        "skipped_no_coordinate": skipped_ev,
        "socket_rows": socket_counts["rows"],
        "socket_rows_without_station": socket_counts["without_station"],
        "data_as_of": data_as_of,
        "is_stale": stale,
    }
    return rows, notes


def build_paths(
    manifest: dict[str, dict[str, Any]],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], dict[str, Any]]:
    """Bisiklet yolu kayıtları ve düğümleri."""
    entry = manifest["bike_paths"]
    data_as_of = entry["data_as_of"]
    stale = is_stale(data_as_of)
    payload = json.loads((DATA_DIR / entry["file"]).read_text(encoding="utf-8"))

    paths: list[tuple[Any, ...]] = []
    nodes: list[tuple[Any, ...]] = []
    stages: Counter[str] = Counter()
    existing = 0
    for index, feature in enumerate(payload.get("features") or []):
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        lines = coordinates if geometry.get("type") == "MultiLineString" else [coordinates]

        path_id = f"path-{index + 1}"
        sequence = 0
        for line in lines:
            for point in line or []:
                if not (isinstance(point, list) and len(point) >= 2):
                    continue
                lon = parse_coordinate(point[0])
                lat = parse_coordinate(point[1])
                if lat is None or lon is None:
                    continue
                nodes.append((path_id, sequence, lat, lon))
                sequence += 1
        if sequence == 0:
            continue

        stage = str(props.get("PRJ_ASAMA") or "").strip() or "bilinmiyor"
        stages[stage] += 1
        exists_now = normalize(stage) in EXISTING_STAGES
        existing += int(exists_now)
        name = str(props.get("PROJE_ADI") or "").strip() or f"Bisiklet yolu {index + 1}"
        paths.append(
            (
                path_id,
                name,
                (str(props.get("ILCE_1") or "").strip() or None),
                (str(props.get("BOLGE") or "").strip() or None),
                stage,
                exists_now,
                parse_coordinate(props.get("PRJ_UZUNLUGU")),
                sequence,
                f"İBB Açık Veri {entry['dataset_no']}: {entry['title']}",
                data_as_of,
                stale,
            )
        )
    notes = {
        "paths": len(paths),
        "nodes": len(nodes),
        "existing": existing,
        "planned": len(paths) - existing,
        "stages": dict(stages.most_common()),
        "data_as_of": data_as_of,
        "is_stale": stale,
    }
    return paths, nodes, notes


def insert(connection: Any, table: str, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    placeholders = ", ".join("?" for _ in rows[0])
    connection.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)


def record_meta(
    connection: Any, table: str, *, source: str, data_as_of: str, rows: int, stale: bool, note: str
) -> None:
    connection.execute("DELETE FROM ingest_meta WHERE table_name = ?", [table])
    connection.execute(
        "INSERT INTO ingest_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            table,
            source,
            data_as_of,
            rows,
            stale,
            note,
            time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        ],
    )


def main() -> int:
    enable_unicode_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=None)
    args = parser.parse_args()

    manifest = load_manifest()
    missing = [
        slug
        for slug in ("bike_parking", "ev_charging", "ev_sockets", "bike_paths")
        if slug not in manifest
    ]
    if missing:
        print(f"manifest'te eksik katman: {missing}")
        print("  uv run python scripts/download_mobility.py --force")
        return 1

    started = time.perf_counter()
    points, point_notes = build_points(manifest)
    paths, nodes, path_notes = build_paths(manifest)

    target = args.database or database_path()
    connection = connect(target)
    try:
        for statement in SCHEMA.strip().split(";\n"):
            if statement.strip():
                connection.execute(statement)
        insert(connection, "mobility_points", points)
        insert(connection, "mobility_paths", paths)
        insert(connection, "mobility_path_nodes", nodes)

        ev = point_notes["ev_charging"]
        park = point_notes["bike_parking"]
        record_meta(
            connection,
            "mobility_points",
            source="İBB Açık Veri 16, 275, 276",
            data_as_of=min(park["data_as_of"], ev["data_as_of"]),
            rows=len(points),
            stale=bool(ev["is_stale"]),
            note=(
                f"park {park['rows']} (taze {park['data_as_of']}), "
                f"şarj {ev['with_socket_detail']}/{len(points) - park['rows']} istasyonda soket detayı; "
                f"{ev['socket_rows_without_station']}/{ev['socket_rows']} soket satırı istasyonsuz, "
                f"bu yüzden soket sayıları alt sınır"
            ),
        )
        record_meta(
            connection,
            "mobility_paths",
            source="İBB Açık Veri 1",
            data_as_of=path_notes["data_as_of"],
            rows=len(paths),
            stale=bool(path_notes["is_stale"]),
            note=(
                f"{path_notes['existing']} mevcut, {path_notes['planned']} planlı/yapımda; "
                f"{path_notes['nodes']} düğüm"
            ),
        )
        connection.commit()
    finally:
        connection.close()

    print(f"veritabanı: {target}")
    print(f"  mobility_points      {len(points):>7}")
    print(f"    park alanları      {park['rows']:>7} (data_as_of {park['data_as_of']})")
    print(
        f"    şarj istasyonları  {len(points) - park['rows']:>7} (data_as_of {ev['data_as_of']}, "
        f"is_stale={ev['is_stale']})"
    )
    print(f"    soket detayı olan  {ev['with_socket_detail']:>7}")
    print(f"    halka açık         {ev['public_access']:>7}")
    print(
        f"    atılan soket satırı{ev['socket_rows_without_station']:>7}"
        f"/{ev['socket_rows']} (istasyon numarası yok)"
    )
    print(f"  mobility_paths       {len(paths):>7}")
    print(f"    mevcut             {path_notes['existing']:>7}")
    print(f"    planlı/yapımda     {path_notes['planned']:>7}")
    print(f"  mobility_path_nodes  {len(nodes):>7}")
    if park["unknown_park_types"]:
        print(f"  BİLİNMEYEN park tipi: {park['unknown_park_types']}")
    print(f"  süre: {time.perf_counter() - started:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
