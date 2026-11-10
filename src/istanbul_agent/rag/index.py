"""Qdrant yerel indeksi: hibrit arama (yoğun + seyrek).

Qdrant **yerel modda** çalışıyor (`QdrantClient(path=...)`), Docker yok
(CLAUDE.md geliştirme ortamı kuralı). Bunun bir bedeli var ve bilerek
kabul edildi: yerel mod dizini **tek sürece** kilitliyor. Yani ingest
çalışırken MCP sunucusu açık olmamalı; sunucu dizini okumak için açtığında
ingest bekler. Üretimde sunucu moduna geçirmek `client` kurulumunu
değiştirmekten ibaret.

Koleksiyonda iki vektör var, ikisi de aynı modelden (`rag/embedding.py`):

- `dense` — 1024 boyut, kosinüs. Anlam benzerliği.
- `lexical` — seyrek. Terim eşleşmesi: "M4", "502", "İstanbulkart" gibi
  kelimeleri yoğun vektör bulanıklaştırıyor.

Birleştirme Qdrant'ın **RRF**'si ile yapılıyor (Reciprocal Rank Fusion):
iki listeyi puanlarına değil **sıralarına** göre birleştirir. Puan
birleştirme (DBSF) denenebilir ama yoğun kosinüs ile seyrek nokta çarpımı
aynı ölçekte olmadığı için sıra tabanlı birleştirme varsayılan; seçim
`eval/run_rag_eval.py` ile ölçülüp karar notuna yazılıyor.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.rag.chunking import Chunk
from istanbul_agent.rag.embedding import DENSE_DIM

__all__ = [
    "COLLECTION",
    "QDRANT_PATH",
    "Hit",
    "build_index",
    "index_stats",
    "open_client",
    "reset_storage",
    "search_index",
]

QDRANT_PATH = PROJECT_ROOT / "data" / "qdrant"
COLLECTION = "knowledge"

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "lexical"

# Chunk kimliği metinsel ("metro_faq:3:0") ama Qdrant nokta kimliği UUID ya da
# tamsayı olmak zorunda; kimlik metinden türetiliyor ki yeniden indeksleme
# aynı noktayı güncellesin, kopya oluşturmasın.
ID_NAMESPACE = uuid.UUID("3f2b2a16-9c1d-4a61-8f4e-5f1e3c7d9a20")


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(ID_NAMESPACE, chunk_id))


@dataclass
class Hit:
    """Aramadan dönen tek bir parça."""

    chunk_id: str
    score: float
    text: str
    heading: str
    source_slug: str
    source_title: str
    citation: str
    data_as_of: str
    kind: str
    page: int | None
    url: str | None
    also_from: tuple[str, ...] = ()
    rerank_score: float | None = None

    @property
    def body(self) -> str:
        """Başlık satırı olmadan metin."""
        return self.text.split("\n\n", 1)[-1]


def open_client(path: Path | None = None) -> Any:
    """Yerel Qdrant istemcisi.

    Dizin tek sürece kilitli olduğu için istemci kullanıldıktan sonra
    kapatılmalı; çağıranlar `with closing(...)` kullanıyor.
    """
    from qdrant_client import QdrantClient

    target = path or QDRANT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(target))


def reset_storage(path: Path | None = None) -> bool:
    """Yerel Qdrant dizinini sil; silindiyse `True` döner.

    Neden tablo silmek değil de dizin: yerel modda `delete_collection` +
    aynı adla `create_collection`, **eski noktaları geri getiriyor**.
    Ölçüldü — 3 nokta yazıp koleksiyonu silip yeniden oluşturup 1 nokta
    yazınca sonuç 4 oluyor, istemciyi kapatıp açmak da düzeltmiyor. Bu
    yüzden yeniden indeksleme dizini sıfırdan kuruyor.

    Yanlış dizini silmemek için iki koşul: yol bizim beklediğimiz ad
    olmalı ve içinde Qdrant'ın yerel dosyaları bulunmalı.
    """
    import shutil

    target = path or QDRANT_PATH
    if not target.exists():
        return False
    looks_like_qdrant = (target / "meta.json").exists() or (target / "collection").exists()
    if target.name != QDRANT_PATH.name and not looks_like_qdrant:
        raise ValueError(f"Qdrant dizini gibi görünmüyor, silinmedi: {target}")
    shutil.rmtree(target)
    return True


def _ensure_collection(client: Any, *, recreate: bool) -> None:
    from qdrant_client import models

    exists = client.collection_exists(COLLECTION)
    if exists and not recreate:
        return
    if exists:
        # Buraya `recreate=True` ile ve dizin silinmemişken düşülürse eski
        # noktalar geri gelir (bkz. `reset_storage`). Çağrı sırası
        # `build_index` içinde garanti ediliyor.
        client.delete_collection(COLLECTION)
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={
            DENSE_VECTOR: models.VectorParams(size=DENSE_DIM, distance=models.Distance.COSINE)
        },
        sparse_vectors_config={SPARSE_VECTOR: models.SparseVectorParams()},
    )


def build_index(
    chunks: Sequence[Chunk],
    dense: Sequence[Sequence[float]],
    sparse: Sequence[dict[int, float]],
    *,
    path: Path | None = None,
    recreate: bool = True,
    batch: int = 64,
) -> int:
    """Parçaları indekse yaz; yazılan nokta sayısını döndür."""
    from contextlib import closing

    from qdrant_client import models

    if not (len(chunks) == len(dense) == len(sparse)):
        raise ValueError(
            f"uzunluklar eşleşmiyor: {len(chunks)} parça, {len(dense)} yoğun, {len(sparse)} seyrek"
        )

    written = 0
    if recreate:
        reset_storage(path)
    with closing(open_client(path)) as client:
        _ensure_collection(client, recreate=recreate)
        for start in range(0, len(chunks), batch):
            window = range(start, min(start + batch, len(chunks)))
            points = [
                models.PointStruct(
                    id=point_id(chunks[index].chunk_id),
                    vector={
                        DENSE_VECTOR: list(dense[index]),
                        SPARSE_VECTOR: models.SparseVector(
                            indices=list(sparse[index].keys()),
                            values=list(sparse[index].values()),
                        ),
                    },
                    payload={
                        "chunk_id": chunks[index].chunk_id,
                        "text": chunks[index].text,
                        "heading": chunks[index].heading,
                        "source_slug": chunks[index].source_slug,
                        "source_title": chunks[index].source_title,
                        "citation": chunks[index].citation,
                        "data_as_of": chunks[index].data_as_of,
                        "kind": chunks[index].kind,
                        "page": chunks[index].page,
                        "url": chunks[index].url,
                        "also_from": list(chunks[index].also_from),
                    },
                )
                for index in window
            ]
            client.upsert(collection_name=COLLECTION, points=points)
            written += len(points)
    return written


def _hit_from_point(point: Any) -> Hit:
    payload = point.payload or {}
    return Hit(
        chunk_id=str(payload.get("chunk_id") or point.id),
        score=float(point.score) if point.score is not None else 0.0,
        text=str(payload.get("text") or ""),
        heading=str(payload.get("heading") or ""),
        source_slug=str(payload.get("source_slug") or ""),
        source_title=str(payload.get("source_title") or ""),
        citation=str(payload.get("citation") or ""),
        data_as_of=str(payload.get("data_as_of") or "bilinmiyor"),
        kind=str(payload.get("kind") or ""),
        page=payload.get("page"),
        url=payload.get("url"),
        also_from=tuple(payload.get("also_from") or ()),
    )


def search_index(
    dense_query: Sequence[float],
    sparse_query: dict[int, float],
    *,
    limit: int = 10,
    prefetch: int = 40,
    mode: str = "hybrid",
    sources: Iterable[str] | None = None,
    path: Path | None = None,
    client: Any | None = None,
) -> list[Hit]:
    """İndekste ara.

    `mode`: `hybrid` (RRF ile yoğun + seyrek), `dense` (yalnızca anlam),
    `sparse` (yalnızca terim). Üç kipin karşılaştırılabilir olması
    değerlendirmenin gereği — hibritin işe yaradığını göstermenin yolu
    yalnız kiplerle ölçmek.
    """
    from contextlib import closing, nullcontext

    from qdrant_client import models

    query_filter = None
    if sources:
        query_filter = models.Filter(
            must=[
                models.FieldCondition(key="source_slug", match=models.MatchAny(any=list(sources)))
            ]
        )

    sparse_vector = models.SparseVector(
        indices=list(sparse_query.keys()), values=list(sparse_query.values())
    )

    context = nullcontext(client) if client is not None else closing(open_client(path))
    with context as connection:
        if mode == "dense":
            response = connection.query_points(
                collection_name=COLLECTION,
                query=list(dense_query),
                using=DENSE_VECTOR,
                limit=limit,
                query_filter=query_filter,
                with_payload=True,
            )
        elif mode == "sparse":
            response = connection.query_points(
                collection_name=COLLECTION,
                query=sparse_vector,
                using=SPARSE_VECTOR,
                limit=limit,
                query_filter=query_filter,
                with_payload=True,
            )
        elif mode == "hybrid":
            response = connection.query_points(
                collection_name=COLLECTION,
                prefetch=[
                    models.Prefetch(
                        query=list(dense_query),
                        using=DENSE_VECTOR,
                        limit=prefetch,
                        filter=query_filter,
                    ),
                    models.Prefetch(
                        query=sparse_vector,
                        using=SPARSE_VECTOR,
                        limit=prefetch,
                        filter=query_filter,
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit,
                with_payload=True,
            )
        else:
            raise ValueError(f"bilinmeyen arama kipi: {mode!r} (hybrid | dense | sparse)")

    return [_hit_from_point(point) for point in response.points]


def index_stats(path: Path | None = None) -> dict[str, Any]:
    """İndeksin durumu: nokta sayısı ve kaynak dağılımı."""
    from collections import Counter
    from contextlib import closing

    with closing(open_client(path)) as client:
        if not client.collection_exists(COLLECTION):
            return {"exists": False, "points": 0, "by_source": {}}
        info = client.get_collection(COLLECTION)
        points, _ = client.scroll(
            collection_name=COLLECTION, limit=10_000, with_payload=["source_slug"]
        )
        by_source = Counter(str((point.payload or {}).get("source_slug")) for point in points)
    return {
        "exists": True,
        "points": info.points_count,
        "by_source": dict(sorted(by_source.items())),
    }
