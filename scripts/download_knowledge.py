"""RAG metin kaynaklarını `data/knowledge/` altına indir.

Envanterdeki C grubu (`datasets/data-inventory.md`) altı veri setinden
oluşuyor; biri API, biri XLSX, dördü PDF:

| No  | Kaynak                                   | slug                        |
|-----|------------------------------------------|-----------------------------|
| 509 | Metro İstanbul SSS (API, JSON)           | `metro_faq`                 |
| —   | Metro İstanbul güncel bilet fiyatı (API) | `metro_ticket_prices`       |
| 541 | Toplu taşıma bilet fiyatları 2017-2022   | `ticket_prices_2017_2022`   |
| 445 | 34 Dakika Ulaşım İndeksi (PDF, TR)       | `index_transport_tr`        |
| 442 | 34 Dakika Yürünebilirlik İndeksi (PDF)   | `index_walkability_tr`      |
| 435 | Ulaşım Ana Planı hanehalkı anketi 2006   | `uap_survey_2006`           |
| 435 | Ulaşım Ana Planı hanehalkı anketi 2012   | `uap_survey_2012`           |
| 496 | Ulaşım Ana Planı hanehalkı araştırması   | `uap_report`                |

Bilet fiyatı iki kaynaktan geliyor ve ikisi de gerekli: XLSX 2017-2022
arasını tarihsel olarak veriyor, portal notunun işaret ettiği canlı servis
ise **bugünkü** fiyatı. RAG'de fiyat sorusu sorulduğunda güncel olanı
göstermek, eski tabloyu güncel sanmaktan önemli.

Adresler elle yazılmıyor: depodaki portal kataloğundan (`ibb_dataset.json`)
paket adı + kaynak adı eşleşmesiyle çözülüyor, `data_as_of` da portalın
`metadata_modified` alanından geliyor.

    uv run python scripts/download_knowledge.py [--force] [--source metro_faq]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import httpx

from istanbul_agent.config import PROJECT_ROOT, get_settings
from istanbul_agent.console import enable_unicode_output

OUT_DIR = PROJECT_ROOT / "data" / "knowledge"
CATALOG_PATH = PROJECT_ROOT / "datasets" / "ibb_dataset.json"
MANIFEST_PATH = OUT_DIR / "_manifest.json"

# slug -> (veri seti no, paket adı, kaynak adında aranacak parça, uzantı)
CATALOG_SOURCES: dict[str, tuple[int, str, str, str]] = {
    "ticket_prices_2017_2022": (
        541,
        "toplu-tasima-bilet-fiyatlari",
        "Toplu Taşıma Bilet Fiyatları",
        "xlsx",
    ),
    "index_transport_tr": (
        445,
        "34-dakika-istanbul-ulasim-indeksi",
        "İndeks Bilgileri TR",
        "pdf",
    ),
    "index_walkability_tr": (
        442,
        "34-dakika-istanbul-yurunebilirlik-indeksi",
        "İndeks Bilgileri TR",
        "pdf",
    ),
    "uap_survey_2006": (
        435,
        "ulasim-ana-plani-hanehalki-anketleri",
        "2006 Yılı Ulaşım Ana Planı Hanehalkı Anketi",
        "pdf",
    ),
    "uap_survey_2012": (
        435,
        "ulasim-ana-plani-hanehalki-anketleri",
        "2012 Yılı Ulaşım Ana Planı Hanehalkı Anketi",
        "pdf",
    ),
    "uap_report": (
        496,
        "istanbul-ulasim-ana-plani-hanehalki-arastirmasi",
        "İstanbul Ulaşım Ana Planı Hanehalkı Araştırma",
        "pdf",
    ),
}

# slug -> (veri seti no, Metro API yolu, açıklama)
API_SOURCES: dict[str, tuple[int, str, str]] = {
    "metro_faq": (
        509,
        "/MetroIstanbul/api/MetroMobile/V2/FrequentlyAskedQuestions",
        "Metro İstanbul sıkça sorulan sorular",
    ),
    "metro_ticket_prices": (
        509,
        # Dil eki zorunlu: eksiz hali 404 veriyor (Faz 0'da doğrulanan adres
        # `/GetTicketPrice/TR`).
        "/MetroIstanbul/api/MetroMobile/V2/GetTicketPrice/TR",
        "Metro İstanbul güncel bilet fiyatları",
    ),
}


def load_catalog() -> dict[str, dict[str, Any]]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {pkg["name"]: pkg for pkg in payload["result"]["results"]}


def pick_resource(package: dict[str, Any], name_part: str, fmt: str) -> dict[str, Any] | None:
    """Paket içinde adı `name_part` içeren ve biçimi `fmt` olan kaynak."""
    wanted = name_part.casefold()
    for resource in package.get("resources", []):
        if (resource.get("format") or "").casefold() != fmt.casefold():
            continue
        if wanted in (resource.get("name") or "").casefold():
            return resource
    return None


def fetch_catalog_sources(
    client: httpx.Client, catalog: dict[str, dict[str, Any]], slugs: list[str], force: bool
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for slug in slugs:
        if slug not in CATALOG_SOURCES:
            continue
        dataset_no, package_name, name_part, extension = CATALOG_SOURCES[slug]
        package = catalog.get(package_name)
        entry: dict[str, Any] = {"slug": slug, "dataset_no": dataset_no, "kind": extension}
        if package is None:
            print(f"  KATALOGDA YOK {slug:26} {package_name}")
            entries.append({**entry, "status": "katalogda yok"})
            continue
        resource = pick_resource(package, name_part, extension)
        if resource is None:
            print(f"  KAYNAK YOK    {slug:26} {name_part!r} / {extension}")
            entries.append({**entry, "status": "kaynak yok"})
            continue

        entry.update(
            {
                "title": resource.get("name") or package.get("title"),
                "package": package_name,
                "data_as_of": (package.get("metadata_modified") or "")[:10],
                "url": resource.get("url"),
            }
        )
        target = OUT_DIR / f"{slug}.{extension}"
        if target.exists() and not force:
            print(f"  önbellekten   {slug:26} {target.stat().st_size / 1024:9.1f} KB")
            entries.append({**entry, "status": "önbellekten", "bytes": target.stat().st_size})
            continue
        try:
            response = client.get(str(resource.get("url")))
            response.raise_for_status()
            target.write_bytes(response.content)
        except Exception as problem:
            print(f"  HATA          {slug:26} {type(problem).__name__}: {str(problem)[:70]}")
            entries.append({**entry, "status": "hata", "error": str(problem)[:200]})
            continue
        print(
            f"  indirildi     {slug:26} {len(response.content) / 1024:9.1f} KB  "
            f"data_as_of={entry['data_as_of']}"
        )
        entries.append({**entry, "status": "indirildi", "bytes": len(response.content)})
    return entries


def previous_entries() -> dict[str, dict[str, Any]]:
    """Önceki manifest kayıtları, slug'a göre.

    Önbellekten gelen canlı kaynağın `data_as_of`u buradan alınır: veri ilk
    çekildiği gündür ve dosya yeniden indirilmediyse o tarih değişmez.
    """
    if not MANIFEST_PATH.exists():
        return {}
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {entry["slug"]: entry for entry in payload.get("sources", []) if entry.get("slug")}


def fetch_api_sources(client: httpx.Client, slugs: list[str], force: bool) -> list[dict[str, Any]]:
    """Metro API'sinden gelen metinleri indir.

    Metro API başarısızlıkta da HTTP 200 dönüyor; sağlık kontrolü gövdedeki
    `Success` alanına bakar, durum koduna değil (CLAUDE.md kuralı).
    """
    base = get_settings().ibb_api_base_url.rstrip("/")
    entries: list[dict[str, Any]] = []
    for slug in slugs:
        if slug not in API_SOURCES:
            continue
        dataset_no, path, description = API_SOURCES[slug]
        target = OUT_DIR / f"{slug}.json"
        entry: dict[str, Any] = {
            "slug": slug,
            "dataset_no": dataset_no,
            "kind": "json",
            "title": description,
            "url": base + path,
        }
        if target.exists() and not force:
            known = previous_entries().get(slug, {})
            entry["data_as_of"] = str(known.get("data_as_of") or "bilinmiyor")
            print(
                f"  önbellekten   {slug:26} {target.stat().st_size / 1024:9.1f} KB  "
                f"data_as_of={entry['data_as_of']}"
            )
            entries.append({**entry, "status": "önbellekten", "bytes": target.stat().st_size})
            continue
        try:
            response = client.get(base + path)
            response.raise_for_status()
            payload = response.json()
        except Exception as problem:
            print(f"  HATA          {slug:26} {type(problem).__name__}: {str(problem)[:70]}")
            entries.append({**entry, "status": "hata", "error": str(problem)[:200]})
            continue
        if isinstance(payload, dict) and payload.get("Success") is False:
            message = str(payload.get("Message") or payload.get("Error") or "")[:150]
            print(f"  BAŞARISIZ     {slug:26} Success=false: {message}")
            entries.append({**entry, "status": "api başarısız", "error": message})
            continue
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # Canlı servisin verisi çekildiği gündür.
        entry["data_as_of"] = time.strftime("%Y-%m-%d")
        print(
            f"  indirildi     {slug:26} {target.stat().st_size / 1024:9.1f} KB  "
            f"data_as_of={entry['data_as_of']}"
        )
        entries.append({**entry, "status": "indirildi", "bytes": target.stat().st_size})
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="önbelleği yok say")
    parser.add_argument(
        "--source",
        action="append",
        choices=sorted(set(CATALOG_SOURCES) | set(API_SOURCES)),
        default=None,
    )
    args = parser.parse_args()

    slugs = args.source or sorted(set(CATALOG_SOURCES) | set(API_SOURCES))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog()

    print("=== RAG metin kaynakları ===")
    with httpx.Client(timeout=300.0, follow_redirects=True) as client:
        entries = fetch_api_sources(client, slugs, args.force)
        entries += fetch_catalog_sources(client, catalog, slugs, args.force)

    # Manifest **birleştirilir**, üzerine yazılmaz: `--source` ile tek bir
    # kaynağı yenilemek, diğer kaynakların kaydını silmemeli. İlk sürüm
    # eziyordu ve iki kaynağı yeniden indirmek ötekilerin hepsini
    # manifest'ten düşürdü — yükleyici de onları görmez oldu.
    merged: dict[str, dict[str, Any]] = {}
    if MANIFEST_PATH.exists():
        previous = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        for entry in previous.get("sources", []):
            if entry.get("slug"):
                merged[entry["slug"]] = entry
    for entry in entries:
        merged[entry["slug"]] = entry
    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "sources": [merged[slug] for slug in sorted(merged)],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    failed = [entry for entry in entries if entry.get("status") not in {"indirildi", "önbellekten"}]
    print(f"\nHedef: {OUT_DIR}\nManifest: {MANIFEST_PATH}")
    if failed:
        print(f"Alınamayan kaynak: {len(failed)} — {[entry['slug'] for entry in failed]}")
    return 1 if failed else 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
