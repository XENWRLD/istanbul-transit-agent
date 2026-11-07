"""Print the shape of the rail reference spreadsheets before parsing them.

The portal publishes these as XLSX with no schema documentation, so look at
the actual sheets and headers before writing a parser against them.

    uv run python scripts/inspect_rail_reference.py
"""

from __future__ import annotations

import sys

import openpyxl

from istanbul_agent.config import PROJECT_ROOT

REFERENCE_DIR = PROJECT_ROOT / "data" / "rail_reference"
PREVIEW_ROWS = 8


def main() -> int:
    paths = sorted(REFERENCE_DIR.glob("*.xlsx"))
    if not paths:
        print("Dosya yok. Önce: uv run python scripts/download_rail_reference.py")
        return 1

    for path in paths:
        workbook = openpyxl.load_workbook(path, data_only=True)
        print(f"\n{'=' * 90}")
        print(f"### {path.stem}    sayfalar: {workbook.sheetnames}")
        for sheet in workbook.worksheets:
            print(f"\n-- '{sheet.title}'  {sheet.max_row} satır × {sheet.max_column} kolon")
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index >= PREVIEW_ROWS:
                    print("   ...")
                    break
                cells = ["" if cell is None else str(cell)[:24] for cell in row]
                print("   | " + " | ".join(cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
