"""Bölümleri aranabilir parçalara (chunk) ayır.

Üç tür bölüm var ve üçü ayrı ele alınıyor (`rag/documents.py`):

- **`qa`** — Metro SSS soru-cevapları ve veri seti açıklamaları. Zaten
  atomik; **bölünmez ve birleştirilmez**. Bir soruyu ikiye kesmek cevabı
  yarım bırakır, iki soruyu birleştirmek aramayı bulanıklaştırır.
- **`table`** — bilet fiyatı tabloları ve PDF içindeki kolonlu bloklar.
  **Bölünmez** (PLAN.md kuralı): "2017: 2.6 ₺" satırı başlığından
  ayrıldığında hangi bilet olduğu kaybolur. Hedef boyutu aşan tablo
  olduğu gibi kalır ve `oversized: true` işaretlenir.
- **`prose`** — PDF düz metni. Aynı başlık altındaki bölümler hedef boyuta
  kadar birleştirilir, aşınca cümle sınırından bölünür ve parçalar
  arasında örtüşme bırakılır.

Her chunk metninin başına **başlık yolu** yazılır ("UAP Araştırması >
3.2 Yöntem"). Gömme (embedding) o satırı da görür; başlıksız bir paragraf
"oran %12 arttı" dediğinde neyin arttığı kaybolurdu.

Boyutlar karakterle ölçülüyor, token'la değil: tokenizer'ı chunking'in
zorunlu bağımlılığı yapmamak için. Hedef aralık PLAN.md'deki 300-600
token'a karşılık geliyor — gerçek token sayıları `scripts/show_chunks.py`
ile ölçülüp karar notuna yazılıyor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from istanbul_agent.rag.documents import Document, Section

__all__ = [
    "MAX_CHARS",
    "OVERLAP_CHARS",
    "TARGET_CHARS",
    "Chunk",
    "chunk_documents",
    "chunk_section",
    "is_informative",
]

# XLM-R tokenizer'ı Türkçede ortalama ~3.3 karakter/token üretiyor; 1600
# karakter ≈ 480 token, 2000 ≈ 600. Ölçüm: `scripts/show_chunks.py`.
TARGET_CHARS = 1600
MAX_CHARS = 2000
OVERLAP_CHARS = 200
# Bundan kısa parça tek başına indekslenmez; komşusuyla birleştirilir.
MIN_CHARS = 120

SENTENCE_END = re.compile(r"(?<=[.!?:;])\s+|\n")

# Gürültü eşiği **türe göre** değişiyor, çünkü gürültünün kaynağı tek:
# PDF anket formlarındaki boş alanlar ("SAAT ____ : ____") ve etiketsiz kod
# ızgaraları ("KULLANIN 01 1 2 1 2"). Soru-cevap ve tablo türleri yapısal
# kaynaklardan (SSS servisi, fiyat tablosu) geliyor ve orada böyle bir
# gürültü yok; onlara düz metin eşiğini uygulamak **gerçek içeriği**
# atıyordu: "Yıl | Bilet | Fiyat / 2017 | Tam | 2.60" tablosunda 16 harf
# var, kısa bir SSS cevabında ("Evet.") daha az.
#
# İki yanlış denemeden sonra bu hale geldi:
# 1. "harfler, boşluk dışı karakterlerin %40'ı olmalı" — raporun gerçek
#    istatistik tablolarını (nüfus yoğunluğu, kira bedelleri) atıyordu;
#    sayı yoğunluğu gürültü işareti değil.
# 2. tek eşik (25 harf) — küçük fiyat tablosunu ve kısa SSS cevabını
#    atıyordu.
MIN_LETTERS = 25
MIN_LETTERS_TABLE = 12
# Alt çizgi oranı: form alanı ("____  :  ____") buradan yakalanıyor.
MAX_UNDERSCORE_SHARE = 0.2

# İçindekiler satırları ("4.2. Eğitim ......... 37"): nokta dizisi taşır ve
# kısadır. Metni değil sayfa numarasını taşıdıkları için indekslenmezler.
DOT_LEADER = re.compile(r"\.{4,}")
DOT_LEADER_MAX_CHARS = 400

# Son `chunk_documents` çağrısında atılan parçalar; ölçüm betikleri raporlar.
LAST_DROPPED: list[tuple[str, str]] = []


@dataclass
class Chunk:
    """İndekslenen en küçük birim."""

    chunk_id: str
    text: str
    heading: str
    source_slug: str
    source_title: str
    citation: str
    data_as_of: str
    kind: str
    page: int | None = None
    oversized: bool = False
    url: str | None = None
    also_from: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def chars(self) -> int:
        return len(self.text)


def is_informative(text: str, kind: str = "prose") -> bool:
    """Parça aranmaya değer metin mi?

    Başlık yolu sayıma girmez: başlık her parçada var ve tek başına bir
    parçayı bilgi taşıyor hale getirmez.

    Eşik türe göre: `qa` yapısal kaynaktan geldiği için her zaman kabul
    edilir, `table` daha düşük harf eşiği kullanır (fiyat tablosunda az
    kelime çok sayı olur), `prose` en katı eşiği kullanır çünkü gürültünün
    tamamı oradan geliyor.
    """
    body = text.split("\n\n", 1)[-1].strip()
    if not body:
        return False
    if kind == "qa":
        return True

    letters = [char for char in body if char.isalpha()]
    dense = [char for char in body if not char.isspace()]
    underscore_share = body.count("_") / len(dense) if dense else 1.0
    if underscore_share > MAX_UNDERSCORE_SHARE:
        return False
    if kind == "table":
        return len(letters) >= MIN_LETTERS_TABLE
    if len(letters) < MIN_LETTERS:
        return False
    return not (len(body) < DOT_LEADER_MAX_CHARS and DOT_LEADER.search(body))


def _with_heading(heading: str, text: str) -> str:
    """Başlık yolunu metnin başına ekle."""
    return f"{heading}\n\n{text}" if heading else text


def _split_prose(text: str) -> list[str]:
    """Uzun düz metni cümle sınırından, örtüşmeli olarak böl."""
    if len(text) <= MAX_CHARS:
        return [text]
    pieces = [piece for piece in SENTENCE_END.split(text) if piece and piece.strip()]
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}".strip() if current else piece
        if len(candidate) <= TARGET_CHARS:
            current = candidate
            continue
        if current:
            chunks.append(current)
            # Örtüşme: önceki parçanın sonundan bir miktar metni taşı, böylece
            # sınırda kalan cümle iki parçada da aranabilir olsun.
            tail = current[-OVERLAP_CHARS:]
            cut = tail.find(" ")
            current = (tail[cut + 1 :] if cut >= 0 else tail) + " " + piece
        else:
            current = piece
        # Tek bir "cümle" hedefin iki katından uzunsa (tablo artığı, listeler)
        # sert kesme yapılır; sonsuz büyüyen parça olmaz.
        while len(current) > MAX_CHARS:
            chunks.append(current[:TARGET_CHARS])
            current = current[TARGET_CHARS - OVERLAP_CHARS :]
    if current.strip():
        chunks.append(current.strip())
    return chunks


def chunk_section(section: Section) -> list[tuple[str, bool]]:
    """Bir bölümü parçalara ayır; `(metin, hedefi_aşıyor)` listesi döner."""
    text = section.text.strip()
    if not text:
        return []
    if section.kind in {"qa", "table"}:
        # Bölünmez türler: hedefi aşsa da bütün kalır, ama işaretlenir.
        return [(text, len(text) > MAX_CHARS)]
    return [(piece, False) for piece in _split_prose(text)]


def chunk_documents(documents: list[Document]) -> list[Chunk]:
    """Belgeleri chunk'lara çevir.

    Aynı başlık altındaki kısa düz metin bölümleri birleştirilir: PDF'ten
    satır satır gelen metin, tek başına anlamsız 50 karakterlik parçalar
    üretiyor.
    """
    chunks: list[Chunk] = []
    dropped: list[tuple[str, str]] = []
    for document in documents:
        meta = document.meta
        merged: list[Section] = []
        for section in document.sections:
            if (
                merged
                and section.kind == "prose"
                and merged[-1].kind == "prose"
                and merged[-1].heading_path == section.heading_path
                and len(merged[-1].text) < TARGET_CHARS
            ):
                merged[-1] = Section(
                    heading_path=section.heading_path,
                    text=f"{merged[-1].text}\n{section.text}",
                    kind="prose",
                    page=merged[-1].page,
                )
                continue
            merged.append(section)

        # Hâlâ çok kısa kalan düz metin parçalarını komşusuna ekle: başlık
        # değişse de içerik aynı sayfadan geliyor.
        compacted: list[Section] = []
        for section in merged:
            if (
                compacted
                and section.kind == "prose"
                and compacted[-1].kind == "prose"
                and len(section.text) < MIN_CHARS
                and len(compacted[-1].text) + len(section.text) < MAX_CHARS
            ):
                compacted[-1] = Section(
                    heading_path=compacted[-1].heading_path,
                    text=f"{compacted[-1].text}\n{section.text}",
                    kind="prose",
                    page=compacted[-1].page,
                )
                continue
            compacted.append(section)

        for index, section in enumerate(compacted):
            for part, (text, oversized) in enumerate(chunk_section(section)):
                body = _with_heading(section.heading, text)
                if not is_informative(body, section.kind):
                    dropped.append((meta.slug, body.replace("\n", " ")[:80]))
                    continue
                chunks.append(
                    Chunk(
                        chunk_id=f"{meta.slug}:{index}:{part}",
                        text=body,
                        heading=section.heading,
                        source_slug=meta.slug,
                        source_title=meta.title,
                        citation=meta.citation,
                        data_as_of=meta.data_as_of,
                        kind=section.kind,
                        page=section.page,
                        oversized=oversized,
                        url=meta.url,
                        also_from=tuple(other.slug for other in document.also_from),
                    )
                )
    LAST_DROPPED.clear()
    LAST_DROPPED.extend(dropped)
    return chunks


def chunk_stats(chunks: list[Chunk]) -> dict[str, Any]:
    """Parça dağılımının ölçülebilir özeti."""
    import statistics

    sizes = [chunk.chars for chunk in chunks]
    by_kind: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for chunk in chunks:
        by_kind[chunk.kind] = by_kind.get(chunk.kind, 0) + 1
        by_source[chunk.source_slug] = by_source.get(chunk.source_slug, 0) + 1
    return {
        "chunks": len(chunks),
        "chars_total": sum(sizes),
        "chars_median": int(statistics.median(sizes)) if sizes else 0,
        "chars_mean": int(statistics.fmean(sizes)) if sizes else 0,
        "chars_min": min(sizes, default=0),
        "chars_max": max(sizes, default=0),
        "over_max": sum(1 for size in sizes if size > MAX_CHARS),
        "under_min": sum(1 for size in sizes if size < MIN_CHARS),
        "oversized_unsplit": sum(1 for chunk in chunks if chunk.oversized),
        "by_kind": by_kind,
        "by_source": by_source,
    }
