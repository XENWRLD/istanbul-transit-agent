"""Download the GTFS feeds into data/gtfs/<feed>/.

Two feeds matter to this project:

- `public_transport` (dataset 308): minibus, taxi-minibus, ferries, Marmaray
  and **metro**. Portal note says the metro data will not be updated, so
  anything derived from it is served with `is_stale: true`.
- `iett` (dataset 75): the bus feed, current as of April 2026.

Files are cached on disk; pass --force to refetch. The downloader records
what it fetched in data/gtfs/<feed>/_manifest.json so later phases can report
`data_as_of` without guessing.

    uv run python scripts/download_gtfs.py
    uv run python scripts/download_gtfs.py --feed public_transport --force
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

from istanbul_agent.config import PROJECT_ROOT, get_settings

GTFS_DIR = PROJECT_ROOT / "data" / "gtfs"

PUBLIC_TRANSPORT_ID = "121a9892-7945-419a-9b89-49f6083926df"
IETT_ID = "8540e256-6df5-4719-85bc-e64e91508ede"


def resource(dataset_id: str, resource_id: str, filename: str) -> str:
    return (
        f"https://data.ibb.gov.tr/dataset/{dataset_id}/resource/{resource_id}/download/{filename}"
    )


# feed -> (dataset no, portal metadata_modified, {filename: url})
FEEDS: dict[str, tuple[int, str, dict[str, str]]] = {
    "public_transport": (
        308,
        "2025-03-12",
        {
            "agency.csv": resource(
                PUBLIC_TRANSPORT_ID, "42ae499d-ae9c-4906-ac5c-96e0c155e00b", "agency.csv"
            ),
            "calendar.csv": resource(
                PUBLIC_TRANSPORT_ID, "c84ca913-29ac-4f15-87cd-076aef3dccd6", "calendar.csv"
            ),
            "frequencies.csv": resource(
                PUBLIC_TRANSPORT_ID, "a4c86ce6-64da-41e2-9584-5d83b5fb895c", "frequencies.csv"
            ),
            "routes.csv": resource(
                PUBLIC_TRANSPORT_ID, "36b554c7-cae0-4b7e-978f-fc6a43664e88", "routes.csv"
            ),
            "shapes.csv": resource(
                PUBLIC_TRANSPORT_ID, "83317085-aa56-41b0-9447-ea579567f2cb", "shapes.csv"
            ),
            "stop_times.csv": resource(
                PUBLIC_TRANSPORT_ID, "ac646b83-3b6f-4ca2-afb4-9071ab44d9af", "stop_times.csv"
            ),
            "stops.csv": resource(
                PUBLIC_TRANSPORT_ID, "d1f7c258-bbc1-406f-9ab2-7a7c1797c673", "stops.csv"
            ),
            "trips.csv": resource(
                PUBLIC_TRANSPORT_ID, "dcee1700-e59f-4a5f-8009-f602045a4507", "trips.csv"
            ),
        },
    ),
    "iett": (
        75,
        "2026-04-21",
        {
            "agency.csv": resource(IETT_ID, "df13606d-194b-4587-b868-39ecdc5f8769", "agency.csv"),
            "calendar.csv": resource(
                IETT_ID, "6c9623b1-3858-4b37-b936-8ffa78de2a69", "calendar.csv"
            ),
            "routes.csv": resource(IETT_ID, "46dbe388-c8c2-45c4-ac72-c06953de56a2", "routes.csv"),
            "stops.csv": resource(IETT_ID, "2299bc82-983b-4bdf-8520-5cef8c555e29", "stops.csv"),
            "trips.csv": resource(IETT_ID, "7ff49bdd-b0d2-4a6e-9392-b598f77f5070", "trips.csv"),
            # The CSV of stop_times is large; the zip is the same data.
            "stop_times.zip": resource(
                IETT_ID, "80401c1c-c240-4a32-8f40-ef697100a681", "stop_times.zip"
            ),
        },
    ),
}


def fetch(client: httpx.Client, url: str, target: Path, force: bool) -> dict[str, object]:
    if target.exists() and not force:
        return {
            "file": target.name,
            "bytes": target.stat().st_size,
            "status": "önbellekten",
        }
    started = time.perf_counter()
    with client.stream("GET", url) as response:
        response.raise_for_status()
        target.write_bytes(response.read())
    return {
        "file": target.name,
        "bytes": target.stat().st_size,
        "status": "indirildi",
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "url": url,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", choices=sorted(FEEDS), action="append")
    parser.add_argument("--force", action="store_true", help="refetch even if cached")
    args = parser.parse_args()
    feeds = args.feed or sorted(FEEDS)

    settings = get_settings()
    exit_code = 0

    # These files are tens of MB, so allow much longer than an API call.
    with httpx.Client(timeout=300.0, follow_redirects=True) as client:
        for feed in feeds:
            dataset_no, portal_modified, files = FEEDS[feed]
            target_dir = GTFS_DIR / feed
            target_dir.mkdir(parents=True, exist_ok=True)
            print(f"\n=== {feed}  (veri seti {dataset_no}, portal: {portal_modified}) ===")

            entries: list[dict[str, object]] = []
            for filename, url in files.items():
                try:
                    entry = fetch(client, url, target_dir / filename, args.force)
                except Exception as exc:
                    print(f"  HATA {filename}: {type(exc).__name__}: {str(exc)[:120]}")
                    entries.append({"file": filename, "status": "hata", "error": str(exc)[:200]})
                    exit_code = 1
                    continue
                size_mb = int(entry["bytes"]) / 1_048_576
                print(f"  {entry['status']:12} {filename:20} {size_mb:8.2f} MB")
                entries.append(entry)

            (target_dir / "_manifest.json").write_text(
                json.dumps(
                    {
                        "feed": feed,
                        "dataset_no": dataset_no,
                        "portal_metadata_modified": portal_modified,
                        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "api_base": settings.ibb_api_base_url,
                        "files": entries,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    print(f"\nHedef: {GTFS_DIR.relative_to(PROJECT_ROOT)}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
