"""Does the rail station GeoJSON have the coordinates the API is missing?

The live Metro API returns `Latitude: null` for M5's Veysel Karani,
Hasanpaşa and Sultanbeyli. Dataset 6 is a weekly-updated station point
layer, so check whether it covers them, and how its names line up with the
API's so the join can be built.

    uv run python scripts/inspect_rail_geojson.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.store.station_names import collapsed, normalize

GEOJSON = PROJECT_ROOT / "data" / "geojson" / "rail_station_points.geojson"
MISSING = ("Veysel Karani", "Hasanpaşa", "Sultanbeyli")


def main() -> int:
    if not GEOJSON.exists():
        print("Önce: uv run python scripts/download_rail_geojson.py")
        return 1

    data = json.loads(GEOJSON.read_text(encoding="utf-8"))
    features = data.get("features", [])
    print(f"{len(features)} nokta\n")

    first = features[0]
    print("İlk kaydın özellikleri:")
    for key, value in (first.get("properties") or {}).items():
        print(f"  {key:24} {str(value)[:60]}")
    print(f"\nGeometri: {json.dumps(first.get('geometry'), ensure_ascii=False)[:140]}")

    # Which property holds the station name, and which the line?
    properties = [f.get("properties") or {} for f in features]
    print("\nAlan doluluk oranları:")
    keys: Counter[str] = Counter()
    for row in properties:
        for key, value in row.items():
            if value not in (None, ""):
                keys[key] += 1
    for key, count in keys.most_common():
        print(f"  {key:24} {count:>5}/{len(features)}")

    print("\n" + "=" * 70)
    print("Eksik koordinatlı istasyonlar bu katmanda var mı?")
    print("=" * 70)
    for target in MISSING:
        wanted, loose = normalize(target), collapsed(target)
        hits = [
            feature
            for feature in features
            if any(
                normalize(str(value)) == wanted or collapsed(str(value)) == loose
                for value in (feature.get("properties") or {}).values()
                if isinstance(value, str)
            )
        ]
        print(f"\n  {target}: {len(hits)} eşleşme")
        for hit in hits[:4]:
            print(f"    özellikler: {json.dumps(hit.get('properties'), ensure_ascii=False)[:200]}")
            geometry = hit.get("geometry") or {}
            print(f"    geometri  : {geometry.get('type')} {str(geometry.get('coordinates'))[:80]}")

    # How many M5 stations does the layer carry at all?
    print("\n" + "=" * 70)
    print("Katmandaki M5 istasyonları")
    print("=" * 70)
    for feature in features:
        row = feature.get("properties") or {}
        if any(str(value).strip().upper() in ("M5", "M-5") for value in row.values()):
            geometry = feature.get("geometry") or {}
            name = next(
                (
                    str(value)
                    for key, value in row.items()
                    if "ad" in key.lower() or "name" in key.lower() or "istasyon" in key.lower()
                ),
                "?",
            )
            print(f"  {name[:34]:36} {str(geometry.get('coordinates'))[:46]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
