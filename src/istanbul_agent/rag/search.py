"""`search_knowledge` aracının arama hattı.

Akış: sorguyu gömme → Qdrant'ta hibrit arama (yoğun + seyrek, RRF) →
reranker ile yeniden sırala → kaynak ve tarih bilgisiyle döndür.

Üç şey bu hattın işi, üçü de projenin "kaynak göster, tazeliği söyle"
kuralından geliyor:

1. **Her sonuç kaynağını taşır** (`citation`, `data_as_of`, varsa sayfa).
2. **Eski kaynak işaretlenir.** Bilet fiyatı iki kaynakta var: canlı Metro
   servisi (bugün) ve 2017-2022 XLSX'i. İkincisi `is_stale: true` döner,
   çünkü "bilet ne kadar" sorusunun cevabı 2022 fiyatı değil.
3. **Boş sonuç boş döner.** Alakasız parçayı "en iyi eşleşme" diye vermek,
   modele uydurma davetiyesi; eşik altındaki sonuçlar atılıyor ve araç
   bulunamadığını söylüyor.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from istanbul_agent.rag.index import Hit, search_index
from istanbul_agent.rag.reranking import (
    CPU_CANDIDATES,
    DEFAULT_CANDIDATES,
    rerank,
    reranker_default,
)

__all__ = [
    "STALE_BEFORE",
    "SearchOutcome",
    "asks_about_past",
    "is_stale_source",
    "search_knowledge",
]

# Bu tarihten eski kaynaklar eski sayılır. 2024 sınırı: 34 Dakika indeks
# belgeleri (2024-02) ve UAP raporu (2023) **tarihsel belge** olarak doğru
# ve eskimiş sayılmazlar; eskiyen şey **fiyat ve tarife** gibi değişen
# bilgidir. Bu yüzden tazelik tarihe değil kaynağa göre belirleniyor.
STALE_BEFORE = "2024-01-01"

# Değişen bilgi taşıyan kaynaklar: tarihleri geçmişte kaldıysa eski sayılır.
VOLATILE_SOURCES = {"ticket_prices_2017_2022"}

# Alakasızlığı kesmek için reranker eşiği. BGE reranker'ın ham puanı
# logit; 0'ın altı "alakalı değil" tarafı. Eşik `eval/run_rag_eval.py` ile
# ölçülüp karar notuna yazılıyor.
MIN_RERANK_SCORE = -2.0

# İlk bu kadar sonuç içinde güncel kaynak, eski kaynağın önüne alınır.
# Ölçümde görülen sorun: "tam bilet kaç lira" sorusunda **2022** fiyat
# tablosu birinci, güncel fiyat ikinci geliyordu. İkisi de doğru kaynak ama
# sıralama kullanıcıyı eski fiyata götürüyor.
#
# Pencere **sıra** üzerinden tanımlı, puan üzerinden değil: hibrit arama RRF
# puanı (pozitif) ile reranker logiti (negatif olabilir) aynı ölçekte değil,
# bu yüzden "puanı yakınsa" kuralı iki kipte farklı davranırdı.
FRESHNESS_WINDOW = 3

# Soru geçmişi soruyorsa güncelliği öne almak **yanlış** olur: "2019'da bilet
# ne kadardı" sorusunun cevabı tarihsel tablodur. İlk sürüm bu soruda da
# güncel fiyatı öne alıyordu; sıra kuralı artık soruya bakıyor.
PAST_YEAR = re.compile(r"(?<!\d)(19\d{2}|20[0-2]\d)(?!\d)")
PAST_WORDS = (
    "tarihçe",
    "tarihsel",
    "geçmiş",
    "eskiden",
    "yıllara göre",
    "hangi yıl",
    "o zaman",
    "önceki yıl",
    "zam",
)


@dataclass
class SearchOutcome:
    """`search_knowledge` sonucu."""

    query: str
    hits: list[Hit] = field(default_factory=list)
    reranked: bool = False
    mode: str = "hybrid"
    seconds: float = 0.0
    rerank_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.hits)


def is_stale_source(slug: str, data_as_of: str) -> bool:
    """Bu kaynaktan gelen bilgi eski mi?

    Tarih tek başına yetmiyor: 2023 tarihli bir araştırma raporu eskimiş
    değil, o yılın araştırmasıdır. Eskiyen şey fiyat gibi değişen bilgi.
    """
    if slug in VOLATILE_SOURCES:
        return data_as_of < STALE_BEFORE
    return False


def asks_about_past(query: str) -> bool:
    """Soru geçmiş bir dönemi mi soruyor?

    Bugünün yılından küçük bir yıl geçiyorsa ya da tarihçe soran bir
    kalıp varsa evet. Bu durumda eski kaynak doğru kaynaktır.
    """
    from datetime import date

    text = (query or "").casefold()
    if any(word in text for word in PAST_WORDS):
        return True
    this_year = date.today().year
    return any(int(match) < this_year for match in PAST_YEAR.findall(text))


def _prefer_fresh(hits: list[Hit], query: str) -> list[Hit]:
    """İlk `FRESHNESS_WINDOW` sonuç içinde güncel kaynakları öne al.

    Kararlı sıralama: güncel/eski dışında hiçbir şeyi değiştirmez, yani
    aramanın kendi sırası korunur. Soru geçmişi soruyorsa hiç karışmaz.
    """
    if asks_about_past(query):
        return hits
    window = hits[:FRESHNESS_WINDOW]
    if not any(is_stale_source(hit.source_slug, hit.data_as_of) for hit in window):
        return hits
    reordered = sorted(window, key=lambda hit: is_stale_source(hit.source_slug, hit.data_as_of))
    return reordered + hits[FRESHNESS_WINDOW:]


def search_knowledge(
    query: str,
    *,
    limit: int = 5,
    mode: str = "hybrid",
    use_reranker: bool | None = None,
    candidates: int | None = None,
    sources: list[str] | None = None,
    path: Path | None = None,
    client: Any | None = None,
) -> SearchOutcome:
    """Metin kaynaklarında ara.

    `candidates` reranker'a kaç aday gideceğini belirler; `limit` kaç sonuç
    döneceğini. Reranker kapalıysa hibrit sıra doğrudan kullanılır.
    """
    from istanbul_agent.rag.embedding import embed_query, get_device

    # Reranker CPU'da 12 aday için 11-28 saniye sürüyor; varsayılanı cihaz
    # belirliyor ve kapalıysa bu cevaba not olarak yazılıyor.
    on_gpu = get_device() == "cuda"
    if use_reranker is None:
        use_reranker = reranker_default()
    if candidates is None:
        candidates = DEFAULT_CANDIDATES if on_gpu else CPU_CANDIDATES

    wanted = (query or "").strip()
    outcome = SearchOutcome(query=wanted, mode=mode)
    if not wanted:
        outcome.notes.append("Boş sorgu ile arama yapılamaz.")
        return outcome

    started = time.perf_counter()
    dense_query, sparse_query = embed_query(wanted)
    take = max(limit, candidates if use_reranker else limit)
    hits = search_index(
        dense_query,
        sparse_query,
        limit=take,
        mode=mode,
        sources=sources,
        path=path,
        client=client,
    )
    if not hits:
        outcome.seconds = time.perf_counter() - started
        outcome.notes.append("Metin kaynaklarında bu soruyla eşleşen bir bölüm bulunamadı.")
        return outcome

    if use_reranker:
        result = rerank(wanted, [hit.text for hit in hits])
        outcome.rerank_seconds = result.seconds
        if result.reranked and len(result.scores) == len(hits):
            for hit, score in zip(hits, result.scores, strict=True):
                hit.rerank_score = score
            hits = [hit for hit in hits if (hit.rerank_score or 0.0) >= MIN_RERANK_SCORE]
            hits.sort(key=lambda hit: hit.rerank_score or 0.0, reverse=True)
            outcome.reranked = True
        else:
            outcome.notes.append(
                "Yeniden sıralama yapılamadı, sonuçlar hibrit arama sırasıyla veriliyor"
                + (f" ({result.error})" if result.error else "")
                + "."
            )

    if not use_reranker:
        outcome.notes.append(
            "Yeniden sıralama kapalı (bu makinede CPU'da 10 saniyeden uzun sürüyor); "
            "sonuçlar hibrit arama sırasıyla."
        )

    hits = _prefer_fresh(hits, wanted)
    outcome.hits = hits[:limit]
    outcome.seconds = time.perf_counter() - started
    if not outcome.hits:
        outcome.notes.append(
            "Bulunan bölümlerin hiçbiri soruyla yeterince alakalı değil; "
            "uydurma yapmak yerine boş dönüldü."
        )
    return outcome
