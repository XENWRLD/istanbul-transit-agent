"""BGE-M3 ile gömme: yoğun (dense) ve seyrek (sparse) vektörler.

Model tek geçişte ikisini de üretiyor, bu yüzden hibrit arama için ikinci
bir model gerekmiyor:

- **yoğun** 1024 boyutlu vektör — anlam benzerliği,
- **seyrek** token → ağırlık sözlüğü — terim eşleşmesi. "M4", "İstanbulkart",
  "502" gibi kelimeleri yoğun vektör bulanıklaştırıyor; seyrek taraf onları
  birebir yakalıyor.

Cihaz otomatik seçiliyor: CUDA varsa GPU, yoksa CPU. Bu makinede kurulu
torch **CPU sürümü** (`torch 2.15.0+cpu`), bu yüzden ölçümler CPU'dan;
CUDA tekerlekleri kurulursa aynı kod GPU'da çalışır
(`scripts/probe_embedding.py` ile yeniden ölçülmeli).

Model ilk çağrıda indiriliyor (~2.3 GB) ve süreç içinde tutuluyor: her
sorguda yeniden yüklemek 30 saniyeden fazla sürüyor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

__all__ = [
    "DENSE_DIM",
    "EMBEDDING_MODEL",
    "EmbeddingResult",
    "embed_passages",
    "embed_query",
    "get_device",
    "get_model",
]

EMBEDDING_MODEL = "BAAI/bge-m3"
DENSE_DIM = 1024

# CPU'da büyük yığın RAM'i şişiriyor, GPU'da 4 GB VRAM sınırı var; ikisinde de
# küçük yığın güvenli taraf. Ölçüm: `scripts/probe_embedding.py`.
BATCH_SIZE_CPU = 4
BATCH_SIZE_GPU = 8

# BGE-M3'ün penceresi 8192 token; parçalarımız 2000 karakterin altında, yani
# bu sınır yalnızca beklenmedik uzun bir parça için var.
MAX_LENGTH = 1024


@dataclass
class EmbeddingResult:
    """Bir metin kümesinin yoğun ve seyrek gömmeleri."""

    dense: list[list[float]]
    sparse: list[dict[int, float]]
    seconds: float

    def __len__(self) -> int:
        return len(self.dense)


def get_device() -> str:
    """Kullanılacak cihaz: CUDA varsa `cuda`, yoksa `cpu`."""
    try:
        import torch
    except ImportError:  # pragma: no cover — rag ekstrası kurulu değil
        return "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


@lru_cache(maxsize=1)
def get_model() -> Any:
    """BGE-M3'ü yükle ve süreç içinde sakla."""
    from FlagEmbedding import BGEM3FlagModel

    device = get_device()
    # fp16 yalnızca GPU'da anlamlı; CPU'da yavaşlatıyor.
    return BGEM3FlagModel(EMBEDDING_MODEL, use_fp16=device == "cuda", devices=device)


def batch_size() -> int:
    return BATCH_SIZE_GPU if get_device() == "cuda" else BATCH_SIZE_CPU


def _to_sparse_dict(raw: Any) -> dict[int, float]:
    """FlagEmbedding'in seyrek çıktısını `{token_id: ağırlık}` sözlüğüne çevir."""
    if raw is None:
        return {}
    items = raw.items() if hasattr(raw, "items") else enumerate(raw)
    sparse: dict[int, float] = {}
    for key, value in items:
        try:
            token_id = int(key)
            weight = float(value)
        except (TypeError, ValueError):
            continue
        if weight > 0:
            sparse[token_id] = weight
    return sparse


def _encode(texts: list[str]) -> EmbeddingResult:
    model = get_model()
    started = time.perf_counter()
    output = model.encode(
        texts,
        batch_size=batch_size(),
        max_length=MAX_LENGTH,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    dense = [list(map(float, vector)) for vector in output["dense_vecs"]]
    sparse = [_to_sparse_dict(item) for item in output.get("lexical_weights", [])]
    if not sparse:
        sparse = [{} for _ in dense]
    return EmbeddingResult(dense=dense, sparse=sparse, seconds=time.perf_counter() - started)


def embed_passages(texts: list[str]) -> EmbeddingResult:
    """İndekslenecek metinleri gömme.

    BGE-M3 sorgu ve pasaj için ayrı ön ek istemiyor (E5 ailesinin aksine),
    bu yüzden metin olduğu gibi gönderiliyor.
    """
    if not texts:
        return EmbeddingResult(dense=[], sparse=[], seconds=0.0)
    return _encode(texts)


def embed_query(text: str) -> tuple[list[float], dict[int, float]]:
    """Tek bir sorguyu gömme; `(yoğun, seyrek)` döner."""
    result = _encode([text])
    return result.dense[0], result.sparse[0]
