"""Download the rail reference spreadsheets into data/rail_reference/.

`get_rail_travel_time` needs to know where a passenger can change lines.
The portal publishes that as XLSX rather than through an API:

- 313 Raylı Sistemlere Ait Aktarma Bilgileri -- the general transfer list
- 487 Raylı Sistemlere Ait İstasyon Bilgileri -- station reference
- 312 Hat Uzunluk Bilgileri -- line lengths, used to sanity-check speeds
- 530 / 532 / 409 -- per-corridor connection points for lines opened after
  the general list was last refreshed

    uv run python scripts/download_rail_reference.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

from istanbul_agent.config import PROJECT_ROOT

OUT_DIR = PROJECT_ROOT / "data" / "rail_reference"
BASE = "https://data.ibb.gov.tr/dataset"

# slug -> (dataset no, portal path)
FILES: dict[str, tuple[int, str]] = {
    "aktarma_bilgileri": (
        313,
        "366f381b-479f-44e2-9c1e-b48f1e096e44/resource"
        "/370212aa-fcc4-458b-97c6-d8b71c8a335f/download"
        "/rayl-sistemlere-ait-aktarma-bilgileri.xlsx",
    ),
    "istasyon_bilgileri": (
        487,
        "74a7f698-557f-40ff-876a-c28654dff944/resource"
        "/9baff1fa-54f3-480c-91ae-986e582d42c7/download"
        "/rayl-sistemlere-ait-istasyon-bilgileri.xlsx",
    ),
    "hat_uzunluklari": (
        312,
        "3a184554-636b-4e76-b7bb-278181588ee3/resource"
        "/12204e2f-6f29-4282-ac48-7fd798f4cb65/download"
        "/rayl-sistemlere-ait-hat-uzunluk-bilgileri.xlsx",
    ),
    "baglanti_kirazli_halkali": (
        530,
        "be440665-ef75-4295-bede-e788e4becc9c/resource"
        "/770e9ed8-5bce-4152-82bd-f19e1e97ac09/download"
        "/2021-yl-rayl-sistemler-kirazl-halkal-istasyonu-dier-hatlarla-balant-noktalar.xlsx",
    ),
    "baglanti_ikitelli_atakoy": (
        532,
        "e38c9d83-b11f-4e85-8bb8-59b9a313e8d8/resource"
        "/e3a027f6-9ed8-4280-b8fc-4114b325b5fa/download"
        "/2021-yl-rayl-sistemler-ikitelli-atakoy-hatt-dier-hatlarla-balant-noktalar.xlsx",
    ),
    "baglanti_mahmutbey_esenyurt": (
        409,
        "f1e73428-ce21-4e63-9a69-a712ef80f110/resource"
        "/1947d88a-753b-4d6a-80ae-1840a0bd1043/download"
        "/2021-yl-rayl-sistemler-mahmutbey-esenyurt-istasyonu-dier-hatlarla-balant-noktalar.xlsx",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    exit_code = 0

    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        for slug, (dataset_no, path) in FILES.items():
            target = OUT_DIR / f"{slug}.xlsx"
            if target.exists() and not args.force:
                print(f"  önbellekten  {slug:30} {target.stat().st_size / 1024:7.1f} KB")
                entries.append({"slug": slug, "dataset_no": dataset_no, "status": "önbellekten"})
                continue
            url = f"{BASE}/{path}"
            try:
                response = client.get(url)
                response.raise_for_status()
                target.write_bytes(response.content)
            except Exception as exc:
                print(f"  HATA         {slug:30} {type(exc).__name__}: {str(exc)[:90]}")
                entries.append(
                    {
                        "slug": slug,
                        "dataset_no": dataset_no,
                        "status": "hata",
                        "error": str(exc)[:200],
                    }
                )
                exit_code = 1
                continue
            print(f"  indirildi    {slug:30} {len(response.content) / 1024:7.1f} KB")
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
