"""Find the bad rows in the inter-station length table before trusting it.

The per-line summary showed M9 totalling 1802 km with a single 929 km
segment, and 14 rows that would not parse at all. A length table used for
travel-time estimates has to be filtered, not taken whole, so look at what
is actually wrong with those rows.

    uv run python scripts/inspect_rail_length_outliers.py
"""

from __future__ import annotations

import sys

import openpyxl

from istanbul_agent.config import PROJECT_ROOT

REFERENCE_DIR = PROJECT_ROOT / "data" / "rail_reference"

# No metro station pair in Istanbul is more than a few km apart; the M4
# airport spur is the longest real gap at about 11 km.
PLAUSIBLE_MAX_M = 12_000.0
PLAUSIBLE_MIN_M = 100.0


def main() -> int:
    workbook = openpyxl.load_workbook(REFERENCE_DIR / "hat_uzunluklari.xlsx", data_only=True)
    sheet = workbook.worksheets[0]
    rows = [row for row in sheet.iter_rows(values_only=True) if any(row)]
    header, data = rows[0], rows[1:]
    print(f"başlık: {header}\n{len(data)} veri satırı\n")

    unparsed: list[tuple] = []
    outliers: list[tuple[str, str, float]] = []
    ok = 0

    for raw in data:
        line, segment, length = (tuple(raw) + (None,) * 3)[:3]
        if not line or not segment or length is None:
            unparsed.append(raw)
            continue
        try:
            metres = float(str(length).replace(",", "."))
        except (TypeError, ValueError):
            unparsed.append(raw)
            continue
        parts = [p.strip() for p in str(segment).split("-")]
        if len(parts) < 2:
            unparsed.append(raw)
            continue
        if not PLAUSIBLE_MIN_M <= metres <= PLAUSIBLE_MAX_M:
            outliers.append((str(line).strip(), str(segment).strip(), metres))
            continue
        ok += 1

    print(f"=== Ayrıştırılamayan satırlar ({len(unparsed)}) ===")
    for raw in unparsed:
        print(f"  {raw}")

    print(
        f"\n=== Makul aralık dışı ({len(outliers)}) "
        f"[{PLAUSIBLE_MIN_M:.0f}-{PLAUSIBLE_MAX_M:.0f} m] ==="
    )
    for line, segment, metres in sorted(outliers, key=lambda row: -row[2]):
        print(f"  {line:6} {segment[:46]:48} {metres:>12,.1f} m")

    print("\n=== Özet ===")
    print(f"  kullanılabilir : {ok}")
    print(f"  aralık dışı    : {len(outliers)}")
    print(f"  ayrıştırılamaz : {len(unparsed)}")
    print(f"  toplam         : {len(data)}")

    # M9 in detail: its summary was dominated by one absurd value.
    print("\n=== M9 satırlarının tamamı ===")
    for raw in data:
        if raw[0] and str(raw[0]).strip() == "M9":
            print(f"  {str(raw[1])[:50]:52} {raw[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
