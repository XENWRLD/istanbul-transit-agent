"""Check what the rail ingest actually put in the store.

Specifically: stations that exist in the 2020 feed but not in today's live
network. Those are closed or renamed stops, and leaving them in the station
graph silently lengthens any journey routed through them, so they have to be
accounted for rather than quietly carried.

    uv run python scripts/inspect_store_rail.py
"""

from __future__ import annotations

import sys

from istanbul_agent.store.db import read_only_connection


def main() -> int:
    with read_only_connection() as connection:
        print("=== GTFS'te olup canlı API'de olmayan istasyonlar ===")
        rows = connection.execute(
            """
            SELECT line, name, stop_order, gtfs_stop_id
            FROM rail_stations
            WHERE in_gtfs AND NOT in_live_api
            ORDER BY line, stop_order
            """
        ).fetchall()
        print(f"{len(rows)} istasyon\n")
        for line, name, order, stop_id in rows:
            label = order if order is not None else "?"
            print(f"  {line:6} #{label:>3} {name[:36]:38} gtfs={stop_id}")

        print("\n=== Canlı API'de olup GTFS'te olmayan (beklenen: 2020 sonrası) ===")
        for line, count in connection.execute(
            """
            SELECT line, count(*) FROM rail_stations
            WHERE in_live_api AND NOT in_gtfs
            GROUP BY line ORDER BY count(*) DESC
            """
        ).fetchall():
            print(f"  {line:6} {count:>3}")

        print("\n=== Hat başına istasyon kaynağı ===")
        print(f"{'Hat':6} {'toplam':>7} {'her ikisi':>10} {'yalnız GTFS':>12} {'yalnız API':>11}")
        for line, total, both, gtfs_only, api_only in connection.execute(
            """
            SELECT line,
                   count(*) AS total,
                   sum(CASE WHEN in_gtfs AND in_live_api THEN 1 ELSE 0 END) AS both,
                   sum(CASE WHEN in_gtfs AND NOT in_live_api THEN 1 ELSE 0 END) AS gtfs_only,
                   sum(CASE WHEN in_live_api AND NOT in_gtfs THEN 1 ELSE 0 END) AS api_only
            FROM rail_stations GROUP BY line ORDER BY line
            """
        ).fetchall():
            flag = "  <-- yalnız GTFS var" if gtfs_only else ""
            print(f"{line:6} {total:>7} {both:>10} {gtfs_only:>12} {api_only:>11}{flag}")

        print("\n=== Segment süre kaynağı ===")
        for kind, count in connection.execute(
            """
            SELECT
                CASE
                    WHEN duration_s IS NOT NULL THEN 'GTFS süresi'
                    WHEN track_m IS NOT NULL THEN '312 uzunluğu (tahmin)'
                    WHEN straight_m IS NOT NULL THEN 'koordinat (tahmin)'
                    ELSE 'veri yok'
                END AS kind,
                count(*)
            FROM rail_segments GROUP BY kind ORDER BY count(*) DESC
            """
        ).fetchall():
            print(f"  {kind:26} {count:>4}")

        missing = connection.execute(
            "SELECT line, from_name, to_name FROM rail_segments "
            "WHERE duration_s IS NULL AND track_m IS NULL AND straight_m IS NULL"
        ).fetchall()
        if missing:
            print(f"\n  Hiç veri olmayan segmentler ({len(missing)}):")
            for line, left, right in missing:
                print(f"    {line:6} {left} → {right}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
