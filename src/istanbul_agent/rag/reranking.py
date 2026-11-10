"""BGE reranker ile adayları yeniden sırala.

Arama iki aşamalı: Qdrant hibrit arama hızlı ama kaba bir sıra veriyor,
reranker (cross-encoder) sorgu ile parçayı **birlikte** okuyup daha iyi bir
sıra üretiyor. Bedeli hız: cross-encoder her aday için bir model geçişi
demek, yoğun vektör aramasıysa tek bir nokta çarpımı.

Bu makinede torch CPU sürümü kurulu, bu yüzden reranker'ın gecikmesi
ölçülüp **varsayılan aday sayısı** ona göre seçildi; ölçüm
`eval/run_rag_eval.py` içinde reranker'lı ve reranker'sız olarak yapılıyor
(PLAN.md Faz 3 "bitti" tanımı bunu istiyor).

Reranker yüklenemezse (model indirilmemiş, bellek yetmemiş) arama
**çalışmaya devam eder** ve sonuç hibrit sırayla döner; bunu çağırana
`reranked=False` ile bildiriyoruz. Sessizce kötü sıra döndürmek, kaynak
gösteren bir sistemde kabul edilemez.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

__all__ = [
    "CPU_CANDIDATES",
    "DEFAULT_CANDIDATES",
    "RERANKER_MODEL",
    "RerankOutcome",
    "get_reranker",
    "rerank",
    "reranker_default",
]

RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

# Ölçüm (bu makine, torch CPU sürümü): 12 aday için **11-28 saniye**. Bir
# agent aracı için kabul edilemez, bu yüzden reranker CPU'da **varsayılan
# olarak kapalı**; CUDA varsa açık. Kalite farkı `eval/run_rag_eval.py` ile
# ölçülüyor, böylece kapalı olmanın bedeli sayıyla biliniyor.
DEFAULT_CANDIDATES = 12
# CPU'da yine de açılırsa aday sayısı düşürülür: gecikme aday sayısıyla
# doğrusal artıyor.
CPU_CANDIDATES = 6
MAX_LENGTH = 1024


def reranker_default() -> bool:
    """Reranker bu cihazda varsayılan olarak açık mı?"""
    from istanbul_agent.rag.embedding import get_device

    return get_device() == "cuda"


@dataclass
class RerankOutcome:
    """Yeniden sıralamanın sonucu ve neden yapılamadığı."""

    scores: list[float]
    reranked: bool
    seconds: float
    error: str | None = None


@lru_cache(maxsize=1)
def get_reranker() -> Any:
    """Reranker'ı yükle ve süreç içinde sakla."""
    from FlagEmbedding import FlagReranker

    from istanbul_agent.rag.embedding import get_device

    device = get_device()
    return FlagReranker(RERANKER_MODEL, use_fp16=device == "cuda", devices=device)


def rerank(query: str, passages: list[str]) -> RerankOutcome:
    """Sorgu ile her pasajı birlikte puanla.

    Puanlar modelden geldiği gibi döner (yüksek = daha alakalı); mutlak
    değerleri kıyaslanabilir değil, yalnızca aynı sorgu içindeki sıralama
    anlamlı.
    """
    if not passages:
        return RerankOutcome(scores=[], reranked=False, seconds=0.0)
    started = time.perf_counter()
    try:
        model = get_reranker()
        raw = model.compute_score([[query, passage] for passage in passages], normalize=False)
    except Exception as problem:
        return RerankOutcome(
            scores=[],
            reranked=False,
            seconds=time.perf_counter() - started,
            error=f"{type(problem).__name__}: {str(problem)[:150]}",
        )
    scores = [float(raw)] if isinstance(raw, (int, float)) else [float(value) for value in raw]
    return RerankOutcome(scores=scores, reranked=True, seconds=time.perf_counter() - started)
