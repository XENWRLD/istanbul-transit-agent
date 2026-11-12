"""Mikromobilite kaynaklarının ağzını ölç: `find_mobility_options` yazılmadan önce.

Proje kuralı: İBB API'sinin davranışını tahmin etme, önce gerçek yanıtı
incele. Bu betik dört kaynağı indirip **ne döndüklerini** rapor eder; araç
kodu bu rapordan sonra yazılır.

| No  | Kaynak                                   | Biçim   |
|-----|------------------------------------------|---------|
| 1   | Bisiklet yolları                         | GeoJSON |
| 16  | Bisiklet/mikromobilite park alanları     | GeoJSON |
| 276 | Elektrikli araç şarj istasyonları        | GeoJSON |
| 275 | Elektrikli araç şarj soketleri           | CSV     |

Ölçülen şeyler, hepsi araç tasarımını doğrudan etkilediği için:

- kayıt sayısı ve geometri tipi (nokta mı çizgi mi — nokta araması çizgi
  katmanında farklı davranmak zorunda),
- alan adları ve her alanın **doldurulma oranı** (boş alan araç çıktısında
  yer kaplamamalı),
- koordinatların biçimi ve İstanbul sınırları içinde olup olmadığı
  (karar 0007: 75 beslemesinde koordinatlar parçalanmış çıkmıştı),
- ilçe alanı var mı (araç ilçeye göre filtreleyecekse kaynağın bunu
  söylemesi gerekiyor),
- portal tarihi (`data_as_of`) ve verinin yaşı: 275/276 Mayıs 2025, yani
  `is_stale` kararını bu veriyle vermek gerekiyor.

    uv run python scripts/inspect_mobility_sources.py [--save]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from collections import Counter
from typing import Any

import httpx

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output

CATALOG_PATH = PROJECT_ROOT / "datasets" / "ibb_dataset.json"
REPORT_PATH = PROJECT_ROOT / "data" / "mobility_probe.json"

# slug -> (veri seti no, katalog paket adı, beklenen biçim)
SOURCES: dict[str, tuple[int, str, str]] = {
    "bike_paths": (1, "istanbul-bisiklet-yollari-verisi", "GEOJSON"),
    "bike_parking": (16, "bisiklet-ve-mikromobilite-park-alanlari", "GEOJSON"),
    "ev_charging": (276, "elektrikli-arac-sarj-istasyonlari-verisi", "GEOJSON"),
    "ev_sockets": (275, "elektrikli-arac-sarj-istasyonlari-soket-verileri", "CSV"),
}

# İstanbul'un kaba sınırları: koordinatın yer değiştirmiş (lat/lon ters) ya
# da bozuk olup olmadığını anlamak için.
LAT_RANGE = (40.7, 41.7)
LON_RANGE = (27.9, 29.95)


def load_catalog() -> dict[str, dict[str, Any]]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {pkg["name"]: pkg for pkg in payload["result"]["results"]}


def pick_resource(package: dict[str, Any], want: str) -> dict[str, Any] | None:
    for resource in package.get("resources", []):
        if (resource.get("format") or "").upper() == want:
            return resource
    return None


def fill_rates(records: list[dict[str, Any]]) -> list[tuple[str, int, str]]:
    """Alan adı → kaç kayıtta dolu + bir örnek değer."""
    keys: list[str] = []
    for record in records:
        for key in record:
            if key not in keys:
                keys.append(key)
    rows: list[tuple[str, int, str]] = []
    for key in keys:
        filled = [record[key] for record in records if record.get(key) not in (None, "", [], {})]
        sample = str(filled[0])[:40] if filled else ""
        rows.append((key, len(filled), sample))
    return rows


def describe_geojson(payload: dict[str, Any]) -> dict[str, Any]:
    features = payload.get("features") or []
    geometry_types = Counter(
        (feature.get("geometry") or {}).get("type", "yok") for feature in features
    )
    properties = [feature.get("properties") or {} for feature in features]

    # Koordinat kontrolü: noktada ilk çift, çizgide ilk düğüm.
    inside = outside = unknown = 0
    for feature in features:
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates")
        while isinstance(coords, list) and coords and isinstance(coords[0], list):
            coords = coords[0]
        if not (isinstance(coords, list) and len(coords) >= 2):
            unknown += 1
            continue
        lon, lat = float(coords[0]), float(coords[1])
        if LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]:
            inside += 1
        else:
            outside += 1
    return {
        "features": len(features),
        "geometry_types": dict(geometry_types),
        "coords_inside_istanbul": inside,
        "coords_outside": outside,
        "coords_unreadable": unknown,
        "fields": [
            {"name": name, "filled": filled, "of": len(properties), "sample": sample}
            for name, filled, sample in fill_rates(properties)
        ],
    }


def describe_csv(text: str) -> dict[str, Any]:
    # Ayraç tahmin edilmiyor, ölçülüyor (karar 0007: iki besleme iki ağız).
    head = text[:4000]
    delimiter = ";" if head.count(";") > head.count(",") else ","
    rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    return {
        "delimiter": delimiter,
        "rows": len(rows),
        "fields": [
            {"name": name, "filled": filled, "of": len(rows), "sample": sample}
            for name, filled, sample in fill_rates(rows)
        ],
    }


def main() -> int:
    enable_unicode_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", action="store_true", help="raporu data/ altına yaz")
    args = parser.parse_args()

    catalog = load_catalog()
    report: dict[str, Any] = {}

    with httpx.Client(timeout=300.0, follow_redirects=True) as client:
        for slug, (dataset_no, package_name, want) in SOURCES.items():
            package = catalog.get(package_name)
            print(f"\n=== {slug} (veri seti {dataset_no}) ===")
            if package is None:
                print("  KATALOGDA YOK")
                report[slug] = {"status": "katalogda yok"}
                continue
            resource = pick_resource(package, want)
            if resource is None:
                formats = sorted(
                    {(res.get("format") or "?").upper() for res in package.get("resources", [])}
                )
                print(f"  {want} kaynağı yok; paket şu biçimleri taşıyor: {formats}")
                report[slug] = {"status": f"{want} yok", "formats": formats}
                continue

            url = resource.get("url") or ""
            data_as_of = (package.get("metadata_modified") or "")[:10]
            print(f"  portal tarihi : {data_as_of}")
            print(f"  adres         : {url[:100]}")
            try:
                response = client.get(url)
                response.raise_for_status()
            except Exception as error:
                print(f"  İNDİRİLEMEDİ: {type(error).__name__}: {error}")
                report[slug] = {"status": "indirilemedi", "error": str(error)[:200]}
                continue

            print(f"  boyut         : {len(response.content) / 1024:.0f} KB")
            try:
                if want == "GEOJSON":
                    described = describe_geojson(response.json())
                    print(f"  kayıt         : {described['features']}")
                    print(f"  geometri      : {described['geometry_types']}")
                    print(
                        f"  koordinat     : İstanbul içinde {described['coords_inside_istanbul']}, "
                        f"dışında {described['coords_outside']}, "
                        f"okunamayan {described['coords_unreadable']}"
                    )
                else:
                    described = describe_csv(response.text)
                    print(f"  ayraç         : {described['delimiter']!r}")
                    print(f"  satır         : {described['rows']}")
            except Exception as error:
                print(f"  ÇÖZÜMLENEMEDİ: {type(error).__name__}: {error}")
                report[slug] = {"status": "çözümlenemedi", "error": str(error)[:200]}
                continue

            print("  alanlar:")
            for field in described["fields"]:
                rate = field["filled"] / field["of"] if field["of"] else 0.0
                print(
                    f"    {field['name'][:28]:28} {field['filled']:>6}/{field['of']:<6} "
                    f"({rate:>4.0%})  {field['sample']}"
                )
            described["status"] = "ok"
            described["data_as_of"] = data_as_of
            described["url"] = url
            described["dataset_no"] = dataset_no
            report[slug] = described

    if args.save:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nrapor: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
