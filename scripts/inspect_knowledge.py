"""RAG kaynaklarının metni gerçekte nasıl çıkıyor?

Chunking kararı (boyut, örtüşme, başlık bazlı mı) ancak metnin biçimi
bilinirse verilebilir. Burada ölçülenler:

**PDF'ler** — sayfa sayısı, çıkarılan karakter, **metni hiç çıkmayan sayfa**
(taranmış görüntü işareti), sayfa başına ortalama uzunluk, başlık gibi
görünen satırlar (numaralı başlık, BÜYÜK HARF satır), tablo gibi görünen
satırlar (çok sayıda sayı veya ardışık boşluk).

**JSON** — Metro SSS'de kaç soru var, cevaplar ne kadar uzun, HTML etiketi
içeriyor mu.

**XLSX** — kaç sayfa, kaç satır, başlık satırı nerede.

**Veri seti açıklamaları** — portal kataloğundaki `notes` alanlarının
sayısı ve uzunluk dağılımı.

Çıktı `data/knowledge_probe.json`; tek seferlik keşif betiği.

    uv run python scripts/inspect_knowledge.py
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output

KNOWLEDGE_DIR = PROJECT_ROOT / "data" / "knowledge"
CATALOG_PATH = PROJECT_ROOT / "datasets" / "ibb_dataset.json"
OUT_PATH = PROJECT_ROOT / "data" / "knowledge_probe.json"

# Numaralı başlık ("3.2 Yöntem") ya da tamamı büyük harf kısa satır.
HEADING_NUMBERED = re.compile(r"^\s*\d+(\.\d+)*\.?\s+\S")
TABLE_ISH = re.compile(r"(\s{3,}\S+){2,}")


def looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not 3 <= len(stripped) <= 90:
        return False
    if HEADING_NUMBERED.match(stripped):
        return True
    letters = [char for char in stripped if char.isalpha()]
    if not letters:
        return False
    upper_share = sum(1 for char in letters if char.isupper()) / len(letters)
    return upper_share > 0.85 and not stripped.endswith(".")


def probe_pdf(path: Path) -> dict[str, Any]:
    reader = PdfReader(str(path))
    pages = len(reader.pages)
    lengths: list[int] = []
    empty_pages = 0
    headings: list[str] = []
    table_lines = 0
    total_lines = 0
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception as problem:
            return {
                "pages": pages,
                "error": f"{type(problem).__name__}: {str(problem)[:120]}",
            }
        lengths.append(len(text))
        if len(text.strip()) < 20:
            empty_pages += 1
        for line in text.splitlines():
            total_lines += 1
            if TABLE_ISH.search(line):
                table_lines += 1
            if looks_like_heading(line) and len(headings) < 40:
                headings.append(line.strip())
    return {
        "pages": pages,
        "size_mb": round(path.stat().st_size / 1e6, 2),
        "chars_total": sum(lengths),
        "chars_per_page_median": int(statistics.median(lengths)) if lengths else 0,
        "pages_without_text": empty_pages,
        "lines_total": total_lines,
        "table_like_lines": table_lines,
        "heading_samples": headings[:20],
    }


def probe_metro_faq(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = payload.get("Data") if isinstance(payload, dict) else payload
    items = data if isinstance(data, list) else []
    questions: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        questions.append(item)
    keys = sorted({key for item in questions for key in item})
    answer_key = next(
        (key for key in keys if "answer" in key.lower() or "cevap" in key.lower()), None
    )
    question_key = next(
        (key for key in keys if "question" in key.lower() or "soru" in key.lower()), None
    )
    answers = [str(item.get(answer_key) or "") for item in questions] if answer_key else []
    return {
        "items": len(questions),
        "keys": keys,
        "question_key": question_key,
        "answer_key": answer_key,
        "answer_chars_median": int(statistics.median([len(a) for a in answers])) if answers else 0,
        "answer_chars_max": max((len(a) for a in answers), default=0),
        "answers_with_html": sum(1 for a in answers if "<" in a and ">" in a),
        "sample": questions[0] if questions else None,
    }


def probe_ticket_prices(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    data = payload.get("Data") if isinstance(payload, dict) else payload
    groups = data if isinstance(data, list) else []
    return {
        "groups": len(groups),
        "sample": groups[0] if groups else None,
        "group_names": [str(group.get("Type")) for group in groups if isinstance(group, dict)],
    }


def probe_xlsx(path: Path) -> dict[str, Any]:
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheets: list[dict[str, Any]] = []
    for sheet in book.worksheets:
        rows = list(sheet.iter_rows(max_row=8, values_only=True))
        sheets.append(
            {
                "title": sheet.title,
                "rows": sheet.max_row,
                "columns": sheet.max_column,
                "first_rows": [
                    [str(cell) if cell is not None else "" for cell in row] for row in rows
                ],
            }
        )
    book.close()
    return {"sheets": sheets}


def probe_dataset_notes() -> dict[str, Any]:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    packages = payload["result"]["results"]
    notes = [(pkg.get("title") or "", (pkg.get("notes") or "").strip()) for pkg in packages]
    with_notes = [note for _, note in notes if note]
    return {
        "packages": len(packages),
        "with_notes": len(with_notes),
        "note_chars_median": int(statistics.median([len(n) for n in with_notes]))
        if with_notes
        else 0,
        "note_chars_max": max((len(n) for n in with_notes), default=0),
        "short_notes_under_200": sum(1 for n in with_notes if len(n) < 200),
    }


def main() -> int:
    if not KNOWLEDGE_DIR.exists():
        print(f"kaynak yok: {KNOWLEDGE_DIR} — scripts/download_knowledge.py")
        return 1

    report: dict[str, Any] = {}

    print("=== PDF'ler ===")
    for path in sorted(KNOWLEDGE_DIR.glob("*.pdf")):
        info = probe_pdf(path)
        report[path.stem] = {"kind": "pdf", **info}
        if "error" in info:
            print(f"  {path.stem:24} {info['pages']:>4} sayfa  HATA {info['error']}")
            continue
        print(
            f"  {path.stem:24} {info['pages']:>4} sayfa  {info['size_mb']:>6.1f} MB  "
            f"{info['chars_total']:>8} karakter  ortanca/sayfa {info['chars_per_page_median']:>5}  "
            f"metinsiz sayfa {info['pages_without_text']:>3}  "
            f"tablo gibi satır {info['table_like_lines']:>5}/{info['lines_total']}"
        )
        for heading in info["heading_samples"][:5]:
            print(f"       başlık?: {heading[:88]}")

    faq_path = KNOWLEDGE_DIR / "metro_faq.json"
    if faq_path.exists():
        info = probe_metro_faq(faq_path)
        report["metro_faq"] = {"kind": "json", **info}
        print("\n=== Metro SSS ===")
        print(
            f"  {info['items']} soru, alanlar {info['keys']}, soru={info['question_key']}, "
            f"cevap={info['answer_key']}"
        )
        print(
            f"  cevap uzunluğu ortanca {info['answer_chars_median']}, en uzun "
            f"{info['answer_chars_max']}, HTML içeren {info['answers_with_html']}"
        )
        if info["sample"]:
            print(f"  örnek: {json.dumps(info['sample'], ensure_ascii=False)[:300]}")

    price_path = KNOWLEDGE_DIR / "metro_ticket_prices.json"
    if price_path.exists():
        info = probe_ticket_prices(price_path)
        report["metro_ticket_prices"] = {"kind": "json", **info}
        print("\n=== Güncel bilet fiyatları ===")
        print(f"  {info['groups']} grup: {info['group_names']}")
        print(f"  örnek: {json.dumps(info['sample'], ensure_ascii=False)[:300]}")

    xlsx_path = KNOWLEDGE_DIR / "ticket_prices_2017_2022.xlsx"
    if xlsx_path.exists():
        info = probe_xlsx(xlsx_path)
        report["ticket_prices_2017_2022"] = {"kind": "xlsx", **info}
        print("\n=== Bilet fiyatları 2017-2022 (XLSX) ===")
        for sheet in info["sheets"]:
            print(f"  sayfa {sheet['title']!r}: {sheet['rows']} satır x {sheet['columns']} kolon")
            for row in sheet["first_rows"][:4]:
                print(f"    {row}")

    notes = probe_dataset_notes()
    report["dataset_notes"] = {"kind": "catalog", **notes}
    print("\n=== Veri seti açıklamaları ===")
    print(
        f"  {notes['packages']} paket, {notes['with_notes']}'inde açıklama var; "
        f"uzunluk ortanca {notes['note_chars_median']}, en uzun {notes['note_chars_max']}, "
        f"200 karakterin altında {notes['short_notes_under_200']}"
    )

    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nrapor: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
