"""Durak ve iskele nokta katmanlarını `data/geojson/` altına indir.

`find_nearby_stops` ve `geocode_place` dört nokta katmanına dayanıyor; GTFS
yalnızca otobüs duraklarını içeriyor, iskele/minibüs/dolmuş duraklarının tek
kaynağı bu GeoJSON'lar:

| No  | Katman                       | slug              |
|-----|------------------------------|-------------------|
| 128 | İETT otobüs durakları        | `bus_stops`       |
| 15  | Deniz ulaşım istasyonları    | `ferry_piers`     |
| 13  | Minibüs durakları            | `minibus_stops`   |
| 9   | Taksi-dolmuş durakları       | `dolmus_stops`    |

İndirme adresleri **elle yazılmıyor**: depodaki portal kataloğundan
(`datasets/ibb_dataset.json`) paket adına göre çözülüyor. Böylece hem
kaynak adresi hem de verinin portal tarihi (`metadata_modified`) tek yerden
geliyor; o tarih `data_as_of` olarak manifest'e yazılıyor ve ingest oradan
okuyor.

    uv run python scripts/download_stop_geojson.py [--force]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import httpx

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output

OUT_DIR = PROJECT_ROOT / "data" / "geojson"
CATALOG_PATH = PROJECT_ROOT / "datasets" / "ibb_dataset.json"

# slug -> (veri seti no, katalogdaki paket adı)
LAYERS: dict[str, tuple[int, str]] = {
    "bus_stops": (128, "iett-otobus-duraklari-verisi"),
    "ferry_piers": (15, "deniz-ulasim-istasyonlari-vektor-verisi"),
    "minibus_stops": (13, "minibus-duraklari-verisi"),
    "dolmus_stops": (9, "taksi-dolmus-duraklari-verisi"),
}


def load_catalog() -> dict[str, dict[str, Any]]:
    """Portal kataloğunu paket adına göre indeksle."""
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {pkg["name"]: pkg for pkg in payload["result"]["results"]}


def geojson_resource(package: dict[str, Any]) -> dict[str, Any] | None:
    """Pakettin GeoJSON kaynağını bul."""
    for resource in package.get("resources", []):
        if (resource.get("format") or "").upper() == "GEOJSON":
            return resource
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="önbelleği yok say")
    parser.add_argument("--layer", action="append", choices=sorted(LAYERS))
    args = parser.parse_args()

    catalog = load_catalog()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    slugs = args.layer or sorted(LAYERS)
    entries: list[dict[str, Any]] = []
    exit_code = 0

    with httpx.Client(timeout=300.0, follow_redirects=True) as client:
        for slug in slugs:
            dataset_no, package_name = LAYERS[slug]
            package = catalog.get(package_name)
            if package is None:
                print(f"  KATALOGDA YOK {slug:16} paket adı: {package_name}")
                entries.append({"slug": slug, "status": "katalogda yok"})
                exit_code = 1
                continue
            resource = geojson_resource(package)
            if resource is None:
                print(f"  GEOJSON YOK   {slug:16} {package_name}")
                entries.append({"slug": slug, "status": "geojson kaynağı yok"})
                exit_code = 1
                continue

            data_as_of = (package.get("metadata_modified") or "")[:10]
            target = OUT_DIR / f"{slug}.geojson"
            entry: dict[str, Any] = {
                "slug": slug,
                "dataset_no": dataset_no,
                "package": package_name,
                "title": package.get("title"),
                "data_as_of": data_as_of,
                "url": resource.get("url"),
            }

            if target.exists() and not args.force:
                entry["status"] = "önbellekten"
                entry["bytes"] = target.stat().st_size
                print(
                    f"  önbellekten   {slug:16} {target.stat().st_size / 1024:9.1f} KB  "
                    f"data_as_of={data_as_of}"
                )
                entries.append(entry)
                continue

            try:
                response = client.get(str(resource.get("url")))
                response.raise_for_status()
                target.write_bytes(response.content)
            except Exception as exc:
                print(f"  HATA          {slug:16} {type(exc).__name__}: {str(exc)[:80]}")
                entry.update({"status": "hata", "error": str(exc)[:200]})
                entries.append(entry)
                exit_code = 1
                continue

            entry.update({"status": "indirildi", "bytes": len(response.content)})
            print(
                f"  indirildi     {slug:16} {len(response.content) / 1024:9.1f} KB  "
                f"data_as_of={data_as_of}"
            )
            entries.append(entry)

    manifest_path = OUT_DIR / "_stops_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {"downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "layers": entries},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nHedef: {OUT_DIR}\nManifest: {manifest_path}")
    return exit_code


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
