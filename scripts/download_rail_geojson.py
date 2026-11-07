"""Download the rail station GeoJSON (datasets 6 and 4) into data/geojson/.

Needed because the live Metro API returns `Latitude: null` for M5's last
three stations -- Veysel Karani, Hasanpaşa and Sultanbeyli -- and both of its
endpoints agree, so a journey through them cannot be estimated. The station
point layer is updated weekly and should have them.

    uv run python scripts/download_rail_geojson.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

from istanbul_agent.config import PROJECT_ROOT

OUT_DIR = PROJECT_ROOT / "data" / "geojson"

# slug -> (dataset no, url)
FILES: dict[str, tuple[int, str]] = {
    "rail_station_points": (
        6,
        "https://data.ibb.gov.tr/dataset/04ec9805-2483-46c7-914f-30c50857a846"
        "/resource/3dc8203f-3613-48a8-85e9-24fffb7821ad/download"
        "/rayli_sistem_istasyon_poi_verisi.geojson",
    ),
    "rail_station_areas": (
        4,
        "https://data.ibb.gov.tr/dataset/271c7bdf-4b0c-4642-8e50-96acba3d4756"
        "/resource/3804f1c6-10de-4fa0-9753-74e5df485d3a/download"
        "/rayli_sistem_istasyon_verisi.geojson",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    exit_code = 0

    with httpx.Client(timeout=180.0, follow_redirects=True) as client:
        for slug, (dataset_no, url) in FILES.items():
            target = OUT_DIR / f"{slug}.geojson"
            if target.exists() and not args.force:
                print(f"  önbellekten  {slug:24} {target.stat().st_size / 1024:8.1f} KB")
                entries.append({"slug": slug, "dataset_no": dataset_no, "status": "önbellekten"})
                continue
            try:
                response = client.get(url)
                response.raise_for_status()
                target.write_bytes(response.content)
            except Exception as exc:
                print(f"  HATA         {slug:24} {type(exc).__name__}: {str(exc)[:80]}")
                entries.append({"slug": slug, "status": "hata", "error": str(exc)[:200]})
                exit_code = 1
                continue
            print(f"  indirildi    {slug:24} {len(response.content) / 1024:8.1f} KB")
            entries.append(
                {
                    "slug": slug,
                    "dataset_no": dataset_no,
                    "status": "indirildi",
                    "bytes": len(response.content),
                    "url": url,
                }
            )

    (OUT_DIR / "_manifest.json").write_text(
        json.dumps(
            {"downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "files": entries},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nHedef: {OUT_DIR.relative_to(PROJECT_ROOT)}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
