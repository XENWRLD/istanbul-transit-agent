"""Mikromobilite kaynaklarını `data/mobility/` altına indir.

`find_mobility_options` dört kaynağa dayanıyor; hiçbiri canlı servis değil,
hepsi portaldaki dosya:

| No  | Katman                                | slug           | Biçim   |
|-----|---------------------------------------|----------------|---------|
| 16  | Bisiklet/mikromobilite park alanları  | `bike_parking` | GeoJSON |
| 276 | Elektrikli araç şarj istasyonları     | `ev_charging`  | GeoJSON |
| 275 | Elektrikli araç şarj soketleri        | `ev_sockets`   | CSV     |
| 1   | Bisiklet yolları (çizgi)              | `bike_paths`   | GeoJSON |

Adresler elle yazılmıyor: depodaki portal kataloğundan
(`datasets/ibb_dataset.json`) paket adına göre çözülüyor, böylece verinin
portal tarihi (`metadata_modified`) de tek yerden gelip manifest'e
`data_as_of` olarak yazılıyor — ingest tazeliği oradan okuyor.

Ölçülen tarihler (karar 0019): 16 → 2026-11-04 ve 1 → 2026-11-06 taze;
275 → 2025-05-20 ve 276 → 2025-05-13 **bir buçuk yıldan eski**, bu yüzden
şarj verisi `is_stale` ile işaretlenir.

    uv run python scripts/download_mobility.py [--force] [--layer ev_charging]
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

OUT_DIR = PROJECT_ROOT / "data" / "mobility"
CATALOG_PATH = PROJECT_ROOT / "datasets" / "ibb_dataset.json"

# slug -> (veri seti no, katalogdaki paket adı, biçim, dosya uzantısı)
LAYERS: dict[str, tuple[int, str, str, str]] = {
    "bike_parking": (16, "bisiklet-ve-mikromobilite-park-alanlari", "GEOJSON", "geojson"),
    "ev_charging": (276, "elektrikli-arac-sarj-istasyonlari-verisi", "GEOJSON", "geojson"),
    "ev_sockets": (275, "elektrikli-arac-sarj-istasyonlari-soket-verileri", "CSV", "csv"),
    "bike_paths": (1, "istanbul-bisiklet-yollari-verisi", "GEOJSON", "geojson"),
}


def load_catalog() -> dict[str, dict[str, Any]]:
    """Portal kataloğunu paket adına göre indeksle."""
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {pkg["name"]: pkg for pkg in payload["result"]["results"]}


def pick_resource(package: dict[str, Any], want: str) -> dict[str, Any] | None:
    """Paketin istenen biçimdeki kaynağını bul."""
    for resource in package.get("resources", []):
        if (resource.get("format") or "").upper() == want:
            return resource
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="önbelleği yok say")
    parser.add_argument("--layer", action="append", choices=sorted(LAYERS))
    args = parser.parse_args()

    catalog = load_catalog()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    slugs = args.layer or list(LAYERS)
    entries: list[dict[str, Any]] = []
    exit_code = 0

    with httpx.Client(timeout=300.0, follow_redirects=True) as client:
        for slug in slugs:
            dataset_no, package_name, want, suffix = LAYERS[slug]
            package = catalog.get(package_name)
            if package is None:
                print(f"  KATALOGDA YOK {slug:14} paket adı: {package_name}")
                entries.append({"slug": slug, "status": "katalogda yok"})
                exit_code = 1
                continue
            resource = pick_resource(package, want)
            if resource is None:
                formats = sorted(
                    {(res.get("format") or "?").upper() for res in package.get("resources", [])}
                )
                print(f"  {want} YOK      {slug:14} paket biçimleri: {formats}")
                entries.append({"slug": slug, "status": f"{want} kaynağı yok"})
                exit_code = 1
                continue

            data_as_of = (package.get("metadata_modified") or "")[:10]
            target = OUT_DIR / f"{slug}.{suffix}"
            entry: dict[str, Any] = {
                "slug": slug,
                "dataset_no": dataset_no,
                "package": package_name,
                "title": package.get("title"),
                "format": want,
                "data_as_of": data_as_of,
                "url": resource.get("url"),
                "file": target.name,
            }

            if target.exists() and not args.force:
                entry["status"] = "önbellekten"
                entry["bytes"] = target.stat().st_size
                print(
                    f"  önbellekten   {slug:14} {target.stat().st_size / 1024:9.1f} KB  "
                    f"data_as_of={data_as_of}"
                )
                entries.append(entry)
                continue

            try:
                response = client.get(str(resource.get("url")))
                response.raise_for_status()
                target.write_bytes(response.content)
            except Exception as exc:
                print(f"  HATA          {slug:14} {type(exc).__name__}: {str(exc)[:80]}")
                entry.update({"status": "hata", "error": str(exc)[:200]})
                entries.append(entry)
                exit_code = 1
                continue

            entry.update({"status": "indirildi", "bytes": len(response.content)})
            print(
                f"  indirildi     {slug:14} {len(response.content) / 1024:9.1f} KB  "
                f"data_as_of={data_as_of}"
            )
            entries.append(entry)

    manifest_path = OUT_DIR / "_mobility_manifest.json"
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
