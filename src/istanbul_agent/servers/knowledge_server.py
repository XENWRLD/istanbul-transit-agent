"""MCP sunucusu: metin bilgisi araması (RAG).

Tek araç sunuyor: `search_knowledge`. Arkasında Qdrant yerel indeksi var
(854 parça, 8 kaynak) ve hibrit arama + isteğe bağlı yeniden sıralama
(`rag/search.py`).

Qdrant yerel modda dizini **tek sürece** kilitliyor: bu sunucu açıkken
`python -m istanbul_agent.rag.ingest` çalıştırılamaz. Sunucu indeksi tek
bir istemciyle açıp süreç boyunca tutuyor; her sorguda açıp kapatmak hem
yavaş hem de kilit yarışına yol açıyordu.

    uv run python -m istanbul_agent.servers.knowledge_server
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from istanbul_agent.rag.index import COLLECTION, index_stats, open_client
from istanbul_agent.rag.search import is_stale_source
from istanbul_agent.rag.search import search_knowledge as _search_knowledge

# log_level: sunucu agent'ın alt süreci; INFO satırları kullanıcının
# terminalinde cevabın içine karışıyor.
mcp = FastMCP("istanbul-knowledge", log_level="WARNING")

# Tek istemci, süreç boyunca. Yerel mod dizini kilitlediği için her
# çağrıda açıp kapatmak hem yavaş hem riskli.
_client: Any | None = None


def client() -> Any:
    global _client
    if _client is None:
        _client = open_client()
    return _client


# --------------------------------------------------------------- modeller


class KnowledgeNotFound(BaseModel):
    """Soruyla eşleşen bir bölüm bulunamadı."""

    ok: Literal[False] = False
    error: Literal["bilgi_bulunamadi", "indeks_yok"] = "bilgi_bulunamadi"
    asked: str
    message: str
    notes: list[str] = Field(default_factory=list)


class PassageOut(BaseModel):
    """Bulunan tek bir metin bölümü."""

    text: str = Field(description="Bölümün metni; başlık satırı dahil")
    heading: str = Field(description="Belge içindeki başlık yolu")
    source: str = Field(description="Kaynak adı ve veri seti numarası")
    source_slug: str
    data_as_of: str = Field(description="Kaynağın tarihi (YYYY-MM-DD)")
    is_stale: bool = Field(
        description=(
            "Bu bölüm değişen bir bilgiyi (ör. bilet fiyatı) eski bir kaynaktan "
            "veriyorsa true; cevapta mutlaka belirt"
        )
    )
    page: int | None = Field(None, description="PDF kaynaklarda sayfa numarası")
    url: str | None = None
    score: float = Field(description="Arama puanı; yalnızca aynı sorgu içinde kıyaslanabilir")
    kind: str = Field(description="qa | prose | table")


class KnowledgeOut(BaseModel):
    """`search_knowledge` cevabı."""

    ok: Literal[True] = True
    asked: str
    passages: list[PassageOut]
    reranked: bool = Field(description="Yeniden sıralama uygulandı mı")
    seconds: float
    notes: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------- araç


@mcp.tool()
def search_knowledge(
    query: str, limit: int = 4, source: str | None = None
) -> KnowledgeOut | KnowledgeNotFound:
    """İBB'nin metin kaynaklarında arama yapar: SSS, bilet fiyatları, raporlar, veri seti açıklamaları.

    İndekslenen kaynaklar:
    - **Metro İstanbul sıkça sorulan sorular** (bisiklet, evcil hayvan, kayıp
      eşya, engelli erişimi, kart işlemleri gibi kural soruları),
    - **güncel bilet fiyatları** (Anonim/Mavi/Elektronik İstanbulkart) ve
      2017-2022 fiyat tarihçesi,
    - **34 Dakika İstanbul indeks belgeleri** (yöntem açıklamaları),
    - **Ulaşım Ana Planı hanehalkı araştırması** ve anket formları,
    - **İBB Açık Veri Portalı'ndaki 565 veri setinin açıklaması** ("hangi
      veri setinde ne var" sorularının cevabı).

    Parametreler:
        query: aranacak soru ya da ifade. Kullanıcının sorusunu olduğu gibi
            vermek en iyi sonucu veriyor; anahtar kelimeye indirgemek
            gerekmiyor.
        limit: kaç bölüm dönecek. Varsayılan 4, en çok 10.
        source: aramayı tek bir kaynağa kısıtlar — `metro_faq`,
            `metro_ticket_prices`, `ticket_prices_2017_2022`,
            `index_transport_tr`, `uap_report`, `uap_survey_2006`,
            `uap_survey_2012`, `dataset_notes`. Genelde gerekmez.

    Her bölüm `source` ve `data_as_of` taşır; cevapta kaynağı belirt.
    `is_stale: true` olan bölüm değişen bir bilgiyi eski kaynaktan veriyor
    (ör. 2022 bilet fiyatı) — onu güncel diye sunma.

    NE ZAMAN KULLANMA:
    - Sefer saati, sefer aralığı veya yolculuk süresi için kullanma —
      `get_next_departures` ve `get_rail_travel_time` kullan. Buradaki
      metinler kural ve açıklama içerir, tarife içermez.
    - Durak, istasyon, iskele konumu veya "en yakın durak" için kullanma —
      `geocode_place` / `find_nearby_stops` kullan.
    - Bir hattın güzergâhı için kullanma — `get_line_route` kullan.
    - Canlı bilgi (arıza, duyuru, doluluk, trafik) için kullanma; bu indeks
      statik metinlerden kuruludur ve canlı veri içermez.
    """
    wanted = (query or "").strip()
    if not wanted:
        return KnowledgeNotFound(
            asked=query or "",
            message="Arama için bir soru ya da ifade gerekli.",
        )

    try:
        connection = client()
        if not connection.collection_exists(COLLECTION):
            return KnowledgeNotFound(
                asked=wanted,
                error="indeks_yok",
                message=(
                    "Metin indeksi kurulmamış. Kurulum: uv run python -m istanbul_agent.rag.ingest"
                ),
            )
    except Exception as problem:
        return KnowledgeNotFound(
            asked=wanted,
            error="indeks_yok",
            message=(
                f"Metin indeksi açılamadı ({type(problem).__name__}). Qdrant yerel modu "
                "dizini tek sürece kilitliyor; ingest aynı anda çalışıyor olabilir."
            ),
        )

    outcome = _search_knowledge(
        wanted,
        limit=max(1, min(int(limit), 10)),
        sources=[source] if source else None,
        client=client(),
    )
    if not outcome.found:
        return KnowledgeNotFound(
            asked=wanted,
            message=(
                "Metin kaynaklarında bu soruyla yeterince alakalı bir bölüm bulunamadı. "
                "Uydurma yapma; bilginin olmadığını söyle."
            ),
            notes=list(outcome.notes),
        )

    return KnowledgeOut(
        asked=wanted,
        passages=[
            PassageOut(
                text=hit.text,
                heading=hit.heading,
                source=hit.citation,
                source_slug=hit.source_slug,
                data_as_of=hit.data_as_of,
                is_stale=is_stale_source(hit.source_slug, hit.data_as_of),
                page=hit.page,
                url=hit.url,
                score=round(hit.rerank_score if hit.rerank_score is not None else hit.score, 4),
                kind=hit.kind,
            )
            for hit in outcome.hits
        ],
        reranked=outcome.reranked,
        seconds=round(outcome.seconds, 2),
        notes=list(outcome.notes),
    )


def warm_up() -> None:
    """Gömme modelini sunucu açılmadan, **ana iş parçacığında** yükle.

    Neden açılışta: model ilk arama sırasında yüklendiğinde sunucu
    kilitleniyordu. Ölçüm — boş sorgu (model yüklemeyen yol) 0.0 saniyede
    cevap veriyor, gerçek sorgu 180 saniyede hiçbir çıktı üretmeden
    asılıyor ve istemci "Connection closed" alıyor. Aynı çağrı süreç içinde
    doğrudan yapıldığında 15.8 saniye sürüyor. Fark, yüklemenin FastMCP'nin
    iş parçacığı havuzunda olması.

    Yan faydası: ilk sorgu 16 saniyelik model yükleme bedelini ödemiyor.
    """
    from istanbul_agent.rag.embedding import get_model

    get_model()


def main() -> None:
    warm_up()
    try:
        mcp.run()
    finally:
        with suppress(Exception):
            if _client is not None:
                _client.close()


if __name__ == "__main__":
    main()


__all__ = ["index_stats", "mcp", "search_knowledge", "warm_up"]
