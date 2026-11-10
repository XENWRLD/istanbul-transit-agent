"""Measure every interchange the network claims, and find the false ones.

A same-name shortcut in `transfer_options` created interchanges without
checking where the two stations are. That is wrong on the Asian side: M4 runs
along the E-5 while Marmaray follows the coast, and they have six station
names in common -- Göztepe, Bostancı, Küçükyalı, Maltepe, Kartal, Pendik --
that are 1-2 km apart. Routing Kadıköy to Taksim through "Göztepe" had the
passenger teleport between two different places.

Prints the distance for every claimed interchange so the false ones are
visible, and checks whether the real M4-Marmaray interchange (Ayrılık
Çeşmesi) is found at all.

    uv run python scripts/audit_transfers.py
    uv run python scripts/audit_transfers.py --radius 400
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output
from istanbul_agent.store.geo import haversine_m
from istanbul_agent.store.rail_queries import load_network
from istanbul_agent.store.station_names import collapsed, normalize

OUT = PROJECT_ROOT / "data" / "transfer_audit.json"
DEFAULT_RADIUS_M = 400.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radius", type=float, default=DEFAULT_RADIUS_M)
    args = parser.parse_args()
    radius = args.radius

    network = load_network()
    lines = sorted({station.line for station in network.stations})

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for from_line in lines:
        for to_line in lines:
            if from_line >= to_line:
                continue
            for change_from, change_to in network.transfer_options(from_line, to_line):
                key = (
                    from_line,
                    network.key(change_from.name),
                    to_line,
                    network.key(change_to.name),
                )
                if key in seen:
                    continue
                seen.add(key)
                distance = None
                if change_from.has_position and change_to.has_position:
                    distance = haversine_m(
                        change_from.lat,
                        change_from.lon,
                        change_to.lat,
                        change_to.lon,
                    )
                rows.append(
                    {
                        "from_line": from_line,
                        "from_station": change_from.name,
                        "to_line": to_line,
                        "to_station": change_to.name,
                        "distance_m": round(distance, 1) if distance is not None else None,
                        "same_name": network.key(change_from.name) == network.key(change_to.name),
                    }
                )

    print(f"{len(rows)} aktarma iddiası\n")

    good = [r for r in rows if r["distance_m"] is not None and r["distance_m"] <= radius]
    bad = [r for r in rows if r["distance_m"] is not None and r["distance_m"] > radius]
    unknown = [r for r in rows if r["distance_m"] is None]

    print(f"=== {radius:.0f} m SINIRINI GEÇEMEYENLER ({len(bad)}) ===")
    print(f"{'A hattı':10} {'A istasyonu':26} {'B hattı':10} {'B istasyonu':26} {'mesafe':>9}  ad")
    for row in sorted(bad, key=lambda r: -r["distance_m"]):
        print(
            f"{row['from_line']:10} {row['from_station'][:25]:26} "
            f"{row['to_line']:10} {row['to_station'][:25]:26} "
            f"{row['distance_m']:>8.0f}m  {'aynı' if row['same_name'] else 'farklı'}"
        )

    print(f"\n=== Sınırı geçenler ({len(good)}) ===")
    for row in sorted(good, key=lambda r: r["distance_m"]):
        print(
            f"{row['from_line']:10} {row['from_station'][:25]:26} "
            f"{row['to_line']:10} {row['to_station'][:25]:26} "
            f"{row['distance_m']:>8.0f}m  {'aynı' if row['same_name'] else 'farklı'}"
        )

    if unknown:
        print(f"\n=== Koordinatı eksik, ölçülemeyen ({len(unknown)}) ===")
        for row in unknown:
            print(
                f"{row['from_line']:10} {row['from_station'][:25]:26} "
                f"{row['to_line']:10} {row['to_station'][:25]:26}"
            )

    # ---- is the real M4 <-> Marmaray interchange there? ----
    print("\n" + "=" * 78)
    print("Ayrılık Çeşmesi: M4 ile Marmaray'ın gerçek aktarması")
    print("=" * 78)
    for pattern in ("ayrilik", "ayrilikcesme", "ayrilikcesmesi"):
        hits = [
            station
            for station in network.stations
            if pattern in collapsed(station.name)
            or pattern in normalize(station.name).replace(" ", "")
        ]
        if hits:
            print(f"\n  '{pattern}' içeren istasyonlar:")
            for station in hits:
                print(
                    f"    {station.line:10} {station.name:26} "
                    f"anahtar={network.key(station.name):26} "
                    f"lat={station.lat} lon={station.lon}"
                )
            break

    m4 = [s for s in network.stations if s.line == "M4" and "ayrilik" in collapsed(s.name)]
    marmaray = [
        s
        for s in network.stations
        if s.line.startswith("Marmaray") and "ayrilik" in collapsed(s.name)
    ]
    if m4 and marmaray:
        distance = haversine_m(m4[0].lat, m4[0].lon, marmaray[0].lat, marmaray[0].lon)
        print(f"\n  M4 '{m4[0].name}' ↔ Marmaray '{marmaray[0].name}': {distance:.0f} m")
        print(f"  anahtarlar: {network.key(m4[0].name)!r} vs {network.key(marmaray[0].name)!r}")
        print(f"  collapsed : {collapsed(m4[0].name)!r} vs {collapsed(marmaray[0].name)!r}")
        print(
            "  → "
            + (
                "anahtarlar eşit, eşleşmeliydi"
                if network.key(m4[0].name) == network.key(marmaray[0].name)
                else "ANAHTARLAR FARKLI: ad eşleştirmesi bu çifti bulamıyor"
            )
        )
        current = network.transfer_options("M4", marmaray[0].line)
        print(f"  mevcut M4↔{marmaray[0].line} aktarmaları: {len(current)}")
        for change_from, change_to in current:
            print(f"    {change_from.name} → {change_to.name}")
    else:
        print(f"\n  M4 tarafı: {[s.name for s in m4]}")
        print(f"  Marmaray tarafı: {[s.name for s in marmaray]}")

    OUT.write_text(
        json.dumps(
            {
                "audited_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "radius_m": radius,
                "claims": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n-> {OUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
