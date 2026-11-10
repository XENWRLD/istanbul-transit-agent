"""RAG kaynaklarını ortak bir belge biçimine yükle.

Altı kaynak, dört ayrı biçim (`scripts/inspect_knowledge.py` ile ölçüldü):

| Kaynak | Biçim | Ölçülen özellik |
|---|---|---|
| Metro SSS (509) | JSON | 45 soru-cevap, 37'si HTML varlık kodu içeriyor |
| Güncel bilet fiyatı | JSON | 3 kart tipi, canlı — RAG'deki tek güncel fiyat |
| Bilet fiyatı 2017-2022 (541) | XLSX | 25 satır, tek tablo |
| 34 Dakika indeks bilgileri (445, 442) | PDF | 5 sayfa; **iki veri seti aynı dosyayı** veriyor |
| UAP hanehalkı araştırması (496) | PDF | 168 sayfa, 244 bin karakter, 2 sayfa metinsiz |
| UAP anket formları 2006/2012 (435) | PDF | 2006'nın metni glif adı olarak çıkıyor |

Üç şey burada çözülüyor, üçü de ölçümle doğrulandı:

1. **Aynı dosya iki kez indekslenmiyor.** 445 ve 442 veri setlerinin PDF'i
   bayt bayt aynı (sha256 eşit). Dedup içerik özetine göre yapılıyor; ikinci
   kaynak, birinci belgenin `also_from` alanına yazılıyor.
2. **2006 anketinin metni kurtarılıyor.** Fontun ToUnicode tablosu yok,
   bu yüzden pypdf glif adlarını basıyor: `/MT221/MT83/MT84` …
   Sayılar **cp1254 bayt değerleri**: 221 → `İ`, 83 → `S`, 84 → `T`.
   Çözülünce düzgün Türkçe çıkıyor ("İSTANBUL O/D ARAŞTIRMASI HANEHALKI
   SORUKAĞIDI"). `fontTools` kurmak bunu düzeltmiyor — denendi.
3. **SSS cevapları HTML'den arındırılıyor** (`&ccedil;` → `ç`, `<p>` atılır).

Yükleyiciler metni **bölmüyor**; bölme `rag/chunking.py` işi. Burada çıkan
şey başlık yolu ve tür (düz metin / tablo / soru-cevap) taşıyan bölümler.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from istanbul_agent.config import PROJECT_ROOT

__all__ = [
    "Document",
    "Section",
    "SourceMeta",
    "decode_glyph_names",
    "load_documents",
    "strip_html",
]

KNOWLEDGE_DIR = PROJECT_ROOT / "data" / "knowledge"
MANIFEST_PATH = KNOWLEDGE_DIR / "_manifest.json"
CATALOG_PATH = PROJECT_ROOT / "datasets" / "ibb_dataset.json"

# `/MT221` gibi glif adları: sayı cp1254 bayt değeri.
GLYPH_NAME = re.compile(r"/(?:MT|G|C|uni)(\d+)")
# Çözülmüş metnin Türkçe metne benzeyip benzemediğini ölçmek için.
TURKISH_LETTERS = set("abcçdefgğhıijklmnoöprsştuüvyzABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ")

HTML_TAG = re.compile(r"<[^>]+>")
MULTI_SPACE = re.compile(r"[ \t]{2,}")
MULTI_BLANK = re.compile(r"\n{3,}")

# Satırda bu kadar ardışık boşluk varsa kolon ayırıcı sayılır: tablo işareti.
TABLE_GAP = re.compile(r"(\s{3,}\S+){2,}")

# Numaralı başlık ("3.2 Yöntem") veya anket maddesi ("A1. İLÇE").
HEADING_NUMBERED = re.compile(r"^\s*(\d+(\.\d+)*|[A-ZİŞĞÜÖÇ]\d+)\.?\s+\S")


@dataclass(frozen=True)
class SourceMeta:
    """Bir kaynağın kimliği ve tazeliği."""

    slug: str
    title: str
    dataset_no: int | None
    data_as_of: str
    url: str | None
    kind: str

    @property
    def citation(self) -> str:
        """Cevapta gösterilecek kaynak adı."""
        if self.dataset_no:
            return f"{self.title} (veri seti {self.dataset_no}, {self.data_as_of})"
        return f"{self.title} ({self.data_as_of})"


@dataclass
class Section:
    """Bir belgenin tek bir parçası; henüz chunk değil.

    `kind` bölmenin nasıl yapılacağını belirliyor: `table` bölünmez
    (PLAN.md kuralı), `qa` zaten atomik, `prose` boyuta göre bölünür.
    """

    heading_path: tuple[str, ...]
    text: str
    kind: str = "prose"
    page: int | None = None

    @property
    def heading(self) -> str:
        return " > ".join(self.heading_path)


@dataclass
class Document:
    """Tek bir kaynak belgesi."""

    meta: SourceMeta
    sections: list[Section] = field(default_factory=list)
    also_from: list[SourceMeta] = field(default_factory=list)
    content_hash: str = ""

    @property
    def chars(self) -> int:
        return sum(len(section.text) for section in self.sections)


# ----------------------------------------------------------------- temizlik


def decode_glyph_names(text: str) -> tuple[str, bool]:
    """Glif adı olarak çıkan metni cp1254 üzerinden çöz.

    `(metin, çözüldü_mü)` döner. Çözüm **yalnızca** sonuç Türkçe metne
    benziyorsa kabul edilir: oran kontrolü olmadan bu dönüşüm, glif adları
    gerçekten font içi indeks olan bir PDF'de anlamsız harf çorbası
    üretirdi.
    """
    if "/MT" not in text and "/uni" not in text:
        return text, False

    def replace(match: re.Match[str]) -> str:
        code = int(match.group(1))
        if not 0 <= code <= 255:
            return " "
        try:
            return bytes([code]).decode("cp1254")
        except (ValueError, UnicodeDecodeError):
            return " "

    decoded = GLYPH_NAME.sub(replace, text)
    letters = [char for char in decoded if char.isalpha()]
    if not letters:
        return text, False
    turkish_share = sum(1 for char in letters if char in TURKISH_LETTERS) / len(letters)
    if turkish_share < 0.9:
        return text, False
    return decoded, True


def strip_html(raw: str) -> str:
    """HTML varlık kodlarını ve etiketlerini metne çevir."""
    text = html.unescape(raw or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
    text = HTML_TAG.sub("", text)
    text = html.unescape(text)
    return tidy(text)


def tidy(text: str) -> str:
    """Fazla boşlukları topla, satır sonlarını koru."""
    text = text.replace("\r\n", "\n").replace("\xa0", " ")
    text = MULTI_SPACE.sub("  ", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return MULTI_BLANK.sub("\n\n", text).strip()


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


# ----------------------------------------------------------------- yükleyiciler


def manifest_sources() -> dict[str, SourceMeta]:
    """İndirme manifest'inden kaynak kimlikleri."""
    if not MANIFEST_PATH.exists():
        return {}
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    sources: dict[str, SourceMeta] = {}
    for entry in payload.get("sources", []):
        slug = entry.get("slug")
        if not slug or entry.get("status") not in {"indirildi", "önbellekten"}:
            continue
        sources[slug] = SourceMeta(
            slug=slug,
            title=str(entry.get("title") or slug),
            dataset_no=entry.get("dataset_no"),
            data_as_of=str(entry.get("data_as_of") or "bilinmiyor"),
            url=entry.get("url"),
            kind=str(entry.get("kind") or "bilinmiyor"),
        )
    return sources


def load_pdf(path: Path, meta: SourceMeta) -> Document:
    """PDF'i başlıklara göre bölümlere ayır.

    Başlık sezgisi ölçüme dayanıyor: numaralı satır ya da neredeyse tamamı
    büyük harf olan kısa satır. Tablo gibi görünen satır blokları ayrı bir
    bölüm olarak işaretleniyor ki chunking onları bölmesin.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    document = Document(meta=meta)
    heading_path: tuple[str, ...] = (meta.title,)
    buffer: list[str] = []
    table_buffer: list[str] = []
    repaired_pages = 0

    def flush(page: int | None, kind: str) -> None:
        nonlocal buffer, table_buffer
        lines = table_buffer if kind == "table" else buffer
        text = tidy("\n".join(lines))
        if text:
            document.sections.append(
                Section(heading_path=heading_path, text=text, kind=kind, page=page)
            )
        if kind == "table":
            table_buffer = []
        else:
            buffer = []

    for number, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception:
            continue
        text, repaired = decode_glyph_names(raw)
        repaired_pages += 1 if repaired else 0
        for line in text.splitlines():
            if TABLE_GAP.search(line):
                if buffer:
                    flush(number, "prose")
                table_buffer.append(line)
                continue
            if table_buffer:
                flush(number, "table")
            if looks_like_heading(line):
                flush(number, "prose")
                heading_path = (meta.title, line.strip())
                continue
            buffer.append(line)
        flush(number, "prose")
        if table_buffer:
            flush(number, "table")

    if repaired_pages:
        document.sections.insert(
            0,
            Section(
                heading_path=(meta.title, "Not"),
                text=(
                    f"Bu belgenin {repaired_pages} sayfasındaki metin, PDF'in font "
                    "tablosu eksik olduğu için glif adlarından çözülerek okundu."
                ),
                kind="prose",
            ),
        )
    return document


def load_metro_faq(path: Path, meta: SourceMeta, *, language: str = "TR") -> Document:
    """Her soru-cevap tek bir bölüm; bölünmez.

    Servis aynı soruları **iki dilde** veriyor (23 TR + 22 EN, ölçüldü) ve
    İngilizce kopyalar aramada Türkçe karşılığının yanına yerleşip sonuç
    yeri harcıyordu: "bisikletle binebilir miyim" sorusunda ilk üç sonucun
    ikincisi aynı cevabın İngilizcesiydi. Kullanıcıya dönük metin Türkçe
    olduğu için yalnızca TR kayıtları indeksleniyor.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("Data") if isinstance(payload, dict) else payload
    document = Document(meta=meta)
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if language and str(item.get("Language") or "").upper() != language.upper():
            continue
        question = strip_html(str(item.get("Question") or "")).strip()
        answer = strip_html(str(item.get("Answer") or "")).strip()
        if not question or not answer:
            continue
        document.sections.append(
            Section(
                heading_path=(meta.title, question),
                text=f"Soru: {question}\nCevap: {answer}",
                kind="qa",
            )
        )
    return document


def load_ticket_prices(path: Path, meta: SourceMeta) -> Document:
    """Güncel bilet fiyatları: kart tipi başına bir bölüm."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    groups = payload.get("Data") if isinstance(payload, dict) else payload
    document = Document(meta=meta)
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        card = str(group.get("Type") or "").strip()
        prices = group.get("TicketPrices") or []
        lines = [
            f"- {str(price.get('Name') or '').strip()}: {str(price.get('Price') or '').strip()}"
            for price in prices
            if isinstance(price, dict)
        ]
        if not card or not lines:
            continue
        document.sections.append(
            Section(
                heading_path=(meta.title, card),
                text=(
                    f"{card} ile toplu taşıma bilet fiyatları "
                    f"({meta.data_as_of} tarihinde Metro İstanbul servisinden alındı):\n"
                    + "\n".join(lines)
                ),
                kind="table",
            )
        )
    return document


def load_price_history(path: Path, meta: SourceMeta) -> Document:
    """2017-2022 bilet fiyatları: tablo bütün halinde tek bölüm."""
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    document = Document(meta=meta)
    for sheet in book.worksheets:
        rows = [
            [("" if cell is None else str(cell)).strip() for cell in row]
            for row in sheet.iter_rows(values_only=True)
        ]
        rows = [row for row in rows if any(row)]
        if not rows:
            continue
        header, *body = rows
        lines = [" | ".join(header), "-" * 40]
        lines += [" | ".join(row) for row in body]
        document.sections.append(
            Section(
                heading_path=(meta.title, sheet.title),
                text=(
                    "Toplu taşıma bilet fiyatlarının yıllara göre değişimi "
                    "(2017-2022, bugünün fiyatı değil):\n" + "\n".join(lines)
                ),
                kind="table",
            )
        )
    book.close()
    return document


def load_dataset_notes() -> Document:
    """Portal kataloğundaki veri seti açıklamaları: her veri seti bir bölüm.

    565 paketin tamamında açıklama var, ortanca uzunluk 132 karakter. Bunlar
    zaten chunk boyutunda; bölmek yerine başlık, biçim ve güncelleme tarihi
    eklenerek tek parça tutuluyor — "hangi veri setinde ne var" sorusunun
    cevabı bu.
    """
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    packages = payload["result"]["results"]
    meta = SourceMeta(
        slug="dataset_notes",
        title="İBB Açık Veri Portalı veri seti açıklamaları",
        dataset_no=None,
        data_as_of="2026-11-06",
        url="https://data.ibb.gov.tr/",
        kind="catalog",
    )
    document = Document(meta=meta)
    for package in packages:
        title = (package.get("title") or "").strip()
        notes = (package.get("notes") or "").strip()
        if not title or not notes:
            continue
        formats = sorted(
            {
                (resource.get("format") or "").upper()
                for resource in package.get("resources", [])
                if resource.get("format")
            }
        )
        modified = (package.get("metadata_modified") or "")[:10]
        document.sections.append(
            Section(
                heading_path=(meta.title, title),
                text=(
                    f"Veri seti: {title}\n"
                    f"Biçim: {', '.join(formats) or 'bilinmiyor'}\n"
                    f"Son güncelleme: {modified or 'bilinmiyor'}\n"
                    f"Açıklama: {tidy(notes)}"
                ),
                kind="qa",
            )
        )
    return document


LOADERS = {
    "metro_faq": load_metro_faq,
    "metro_ticket_prices": load_ticket_prices,
    "ticket_prices_2017_2022": load_price_history,
}


def _content_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_documents(*, include_catalog: bool = True) -> list[Document]:
    """Bütün kaynakları yükle, aynı dosyayı iki kez indekslemeden.

    445 ve 442 veri setleri aynı PDF'i yayımlıyor (sha256 eşit). İkisini de
    indekslemek her aramada aynı parçayı iki kez döndürürdü; ikinci kaynak
    birincinin `also_from` listesine yazılıyor, böylece kaynak gösterirken
    ikisi de anılabiliyor.
    """
    documents: list[Document] = []
    by_hash: dict[str, Document] = {}

    for slug, meta in sorted(manifest_sources().items()):
        extension = "json" if meta.kind == "json" else meta.kind
        path = KNOWLEDGE_DIR / f"{slug}.{extension}"
        if not path.exists():
            continue
        digest = _content_hash(path)
        if digest in by_hash:
            by_hash[digest].also_from.append(meta)
            continue
        loader = LOADERS.get(slug)
        document = loader(path, meta) if loader else load_pdf(path, meta)
        document.content_hash = digest
        if not document.sections:
            continue
        by_hash[digest] = document
        documents.append(document)

    if include_catalog:
        documents.append(load_dataset_notes())
    return documents


def iter_sections(documents: list[Document]) -> Iterator[tuple[Document, Section]]:
    for document in documents:
        for section in document.sections:
            yield document, section


def summarise(documents: list[Document]) -> list[dict[str, Any]]:
    """Yüklenen belgelerin ölçülebilir özeti."""
    return [
        {
            "slug": document.meta.slug,
            "title": document.meta.title,
            "data_as_of": document.meta.data_as_of,
            "sections": len(document.sections),
            "chars": document.chars,
            "kinds": sorted({section.kind for section in document.sections}),
            "also_from": [other.slug for other in document.also_from],
        }
        for document in documents
    ]
