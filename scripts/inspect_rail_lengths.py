"""How much of the missing network does the inter-station length table cover?

Dataset 312 turns out to list the track length between consecutive stations
in metres, including for lines the 2020 GTFS feed never had (M10, M12). If it
covers the stations GTFS is missing, `get_rail_travel_time` can estimate from
a real track distance instead of a straight line -- a materially better
estimate, since a metro between two stations covers more ground than the
chord between them.

Checks the coverage against the 49 stations decision 0003 marked missing, and
reads the transfer table while we are here.

    uv run python scripts/inspect_rail_lengths.py
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict

import openpyxl

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output
from istanbul_agent.store.station_names import normalize

REFERENCE_DIR = PROJECT_ROOT / "data" / "rail_reference"
NAME_MAP = PROJECT_ROOT / "datasets" / "station_name_map.csv"


def rows(path_stem: str) -> list[tuple]:
    workbook = openpyxl.load_workbook(REFERENCE_DIR / f"{path_stem}.xlsx", data_only=True)
    sheet = workbook.worksheets[0]
    return [row for row in sheet.iter_rows(values_only=True) if any(row)]


def main() -> int:
    # ---------------- inter-station lengths ----------------
    length_rows = rows("hat_uzunluklari")[1:]
    by_line: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
    unparsed = 0

    for line, segment, length in ((r + (None,) * 3)[:3] for r in length_rows):
        if not line or not segment or length is None:
            unparsed += 1
            continue
        text = str(segment)
        # "Kabatas-Taksım" -- a single hyphen separates the pair, but some
        # station names contain hyphens themselves.
        parts = [p.strip() for p in text.split("-")]
        if len(parts) < 2:
            unparsed += 1
            continue
        if len(parts) == 2:
            left, right = parts
        else:
            # Ambiguous: report rather than guess which hyphen separates.
            left, right = parts[0], parts[-1]
        try:
            metres = float(str(length).replace(",", "."))
        except ValueError:
            unparsed += 1
            continue
        by_line[str(line).strip()].append((left, right, metres))

    print("=== Veri seti 312: istasyonlar arası uzunluklar ===")
    print(f"  {len(length_rows)} satır, {len(by_line)} hat, ayrıştırılamayan {unparsed}")
    print(f"\n{'Hat':8} {'segment':>8} {'toplam km':>10} {'ortalama m':>11} {'min':>7} {'max':>7}")
    for line in sorted(by_line):
        segments = by_line[line]
        lengths = [m for _, _, m in segments]
        print(
            f"{line:8} {len(segments):>8} {sum(lengths) / 1000:>10.1f} "
            f"{sum(lengths) / len(lengths):>11.0f} {min(lengths):>7.0f} {max(lengths):>7.0f}"
        )

    gtfs_lines = {
        "F1",
        "F2",
        "F3",
        "M1A",
        "M1B",
        "M2",
        "M2A",
        "M3",
        "M3A",
        "M4",
        "M5",
        "M6",
        "M7",
        "M8",
        "M9",
        "T1",
        "T3",
        "T4",
        "TF1",
        "TF2",
    }
    extra = sorted(set(by_line) - gtfs_lines)
    print(f"\n  GTFS'te olmayan hatlar: {', '.join(extra) or 'yok'}")

    # ---------------- do the lengths cover what GTFS misses? ----------------
    missing_by_line: dict[str, list[str]] = defaultdict(list)
    with NAME_MAP.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["decision"] in ("GERÇEKTEN EKSİK", "HAT GÜVENİLMEZ", "ŞÜPHELİ — KULLANMA"):
                missing_by_line[row["line"]].append(row["api_station"])

    print("\n\n=== Eksik istasyonlar 312'de var mı? ===")
    print(f"{'Hat':6} {'eksik':>6} {'312 de bulunan':>16}  bulunamayanlar")
    total_missing = total_found = 0
    for line in sorted(missing_by_line):
        missing = missing_by_line[line]
        # The length table uses line families (M1 not M1A), so search widely.
        candidates = set()
        for length_line, segments in by_line.items():
            family = length_line.rstrip("AB")
            if family == line.rstrip("AB") or length_line == line:
                for left, right, _ in segments:
                    candidates.add(normalize(left))
                    candidates.add(normalize(right))
        found = [s for s in missing if normalize(s) in candidates]
        not_found = [s for s in missing if normalize(s) not in candidates]
        total_missing += len(missing)
        total_found += len(found)
        print(
            f"{line:6} {len(missing):>6} {len(found):>16}  "
            f"{', '.join(not_found[:4])}{' …' if len(not_found) > 4 else ''}"
        )
    print(f"\n  toplam: {total_found}/{total_missing} eksik istasyon 312'de bulunuyor")

    # ---------------- transfers ----------------
    transfer_rows = rows("aktarma_bilgileri")[1:]
    print(f"\n\n=== Veri seti 313: aktarma bilgileri ({len(transfer_rows)} satır) ===")
    rail_to_rail = 0
    other = 0
    samples: list[str] = []
    for line, source, target in ((r + (None,) * 3)[:3] for r in transfer_rows):
        if not source or not target:
            continue
        target_text = str(target).strip()
        # A rail-to-rail transfer names a line code; the rest point at
        # Metrobüs, ferries or the nostalgic tram.
        is_rail = any(
            target_text.upper().startswith(prefix) for prefix in ("M", "T", "F", "MARMARAY")
        ) and not target_text.lower().startswith(("metrobüs", "metrobus"))
        if is_rail:
            rail_to_rail += 1
            if len(samples) < 12:
                samples.append(f"{line}: {str(source).strip()} → {target_text}")
        else:
            other += 1
    print(f"  raylı↔raylı: {rail_to_rail}   diğer (metrobüs/vapur/nostaljik): {other}")
    print("\n  örnekler:")
    for sample in samples:
        print(f"    {sample}")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
