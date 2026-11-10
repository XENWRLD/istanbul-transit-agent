"""RAG değerlendirmesi: hangi arama kipi, reranker'lı mı reranker'sız mı?

`eval/rag_questions.jsonl` içindeki her soru için beklenen **kaynak** ve
beklenen **anahtar kelimeler** yazılı. Ölçülen üç şey:

- **recall@k** — beklenen kaynaklardan biri ilk k sonuçta var mı,
- **MRR** — ilk doğru sonucun sırasının tersi (1.0 = birinci sırada),
- **anahtar kelime isabeti** — getirilen metin beklenen kelimeyi içeriyor mu.
  Kaynak doğru olup içerik yanlış olabilir; bu ikisini ayırmak gerekiyor.

LLM kullanılmıyor: bu betik **getirme** kalitesini ölçüyor. Cevap
doğruluğu/sadakati (RAGAS) agent tarafı ile birlikte Faz 4-5'te ölçülecek,
çünkü ücretsiz LLM kotası günde 20 istek (karar 0006) ve her kip için 51
soru sormak o kotayı anında tüketir.

    uv run python eval/run_rag_eval.py
    uv run python eval/run_rag_eval.py --config hybrid --config dense
    uv run python eval/run_rag_eval.py --no-rerank     # reranker'lı kipleri atla

Sonuç `eval/results/rag_eval.json` ve ekrana tablo olarak yazılır.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from typing import Any

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output

QUESTIONS_PATH = PROJECT_ROOT / "eval" / "rag_questions.jsonl"
RESULTS_DIR = PROJECT_ROOT / "eval" / "results"
RESULTS_PATH = RESULTS_DIR / "rag_eval.json"

TOP_K = (1, 3, 5)
LIMIT = 5

MODES = ("hybrid", "dense", "sparse")


@dataclass
class Question:
    id: str
    question: str
    expected_sources: list[str]
    keywords: list[str]
    note: str = ""


def load_questions() -> list[Question]:
    questions: list[Question] = []
    for line in QUESTIONS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        questions.append(
            Question(
                id=row["id"],
                question=row["question"],
                expected_sources=list(row.get("expected_sources") or []),
                keywords=list(row.get("keywords") or []),
                note=row.get("note", ""),
            )
        )
    return questions


def evaluate(
    questions: list[Question], *, mode: str, use_reranker: bool, client: Any
) -> dict[str, Any]:
    """Tek bir yapılandırmayı ölç."""
    from istanbul_agent.rag.search import search_knowledge

    ranks: list[int | None] = []
    keyword_hits: list[bool] = []
    latencies: list[float] = []
    misses: list[dict[str, Any]] = []

    for question in questions:
        outcome = search_knowledge(
            question.question,
            limit=LIMIT,
            mode=mode,
            use_reranker=use_reranker,
            client=client,
        )
        latencies.append(outcome.seconds)

        rank: int | None = None
        for position, hit in enumerate(outcome.hits, start=1):
            if hit.source_slug in question.expected_sources:
                rank = position
                break
        ranks.append(rank)

        text = " ".join(hit.text.casefold() for hit in outcome.hits)
        hit_keyword = (
            any(keyword.casefold() in text for keyword in question.keywords)
            if question.keywords
            else True
        )
        keyword_hits.append(hit_keyword)

        if rank is None or not hit_keyword:
            misses.append(
                {
                    "id": question.id,
                    "question": question.question,
                    "expected": question.expected_sources,
                    "got": [hit.source_slug for hit in outcome.hits],
                    "rank": rank,
                    "keyword_hit": hit_keyword,
                }
            )

    found = [rank for rank in ranks if rank is not None]
    return {
        "mode": mode,
        "reranker": use_reranker,
        "questions": len(questions),
        **{
            f"recall@{k}": round(
                sum(1 for rank in ranks if rank is not None and rank <= k) / len(questions), 3
            )
            for k in TOP_K
        },
        "mrr": round(sum(1 / rank for rank in found) / len(questions), 3),
        "keyword_hit_rate": round(sum(keyword_hits) / len(questions), 3),
        "latency_median_s": round(statistics.median(latencies), 2),
        "latency_max_s": round(max(latencies), 2),
        "misses": misses,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", action="append", choices=MODES, default=None)
    parser.add_argument("--no-rerank", action="store_true", help="reranker'lı kipleri atla")
    parser.add_argument("--only-rerank", action="store_true", help="yalnızca reranker'lı kipler")
    args = parser.parse_args(argv)

    from contextlib import closing

    from istanbul_agent.rag.embedding import get_device
    from istanbul_agent.rag.index import COLLECTION, open_client

    questions = load_questions()
    modes = args.config or list(MODES)
    rerank_options: list[bool] = []
    if not args.only_rerank:
        rerank_options.append(False)
    if not args.no_rerank:
        rerank_options.append(True)

    print(f"=== RAG değerlendirmesi: {len(questions)} soru, cihaz {get_device()} ===")
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []

    with closing(open_client()) as client:
        if not client.collection_exists(COLLECTION):
            print("indeks yok: uv run python -m istanbul_agent.rag.ingest")
            return 1
        for use_reranker in rerank_options:
            for mode in modes:
                label = f"{mode}{'+rerank' if use_reranker else ''}"
                print(f"\n--- {label} ---", flush=True)
                row = evaluate(questions, mode=mode, use_reranker=use_reranker, client=client)
                rows.append(row)
                print(
                    f"  recall@1 {row['recall@1']:.3f}  recall@3 {row['recall@3']:.3f}  "
                    f"recall@5 {row['recall@5']:.3f}  MRR {row['mrr']:.3f}  "
                    f"anahtar kelime {row['keyword_hit_rate']:.3f}  "
                    f"gecikme ortanca {row['latency_median_s']:.2f} s"
                )

    print("\n=== özet ===")
    print(
        f"  {'yapılandırma':18} {'recall@1':>9} {'recall@3':>9} {'recall@5':>9} "
        f"{'MRR':>6} {'kelime':>7} {'gecikme':>8}"
    )
    for row in rows:
        label = f"{row['mode']}{'+rerank' if row['reranker'] else ''}"
        print(
            f"  {label:18} {row['recall@1']:>9.3f} {row['recall@3']:>9.3f} "
            f"{row['recall@5']:>9.3f} {row['mrr']:>6.3f} "
            f"{row['keyword_hit_rate']:>7.3f} {row['latency_median_s']:>7.2f}s"
        )

    best = max(rows, key=lambda row: (row["recall@3"], row["mrr"]))
    print(
        f"\n  en iyi recall@3: {best['mode']}{'+rerank' if best['reranker'] else ''} "
        f"({best['recall@3']:.3f})"
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(
            {
                "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "device": get_device(),
                "questions": len(questions),
                "limit": LIMIT,
                "configs": rows,
                "total_seconds": round(time.perf_counter() - started, 1),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"  sonuç: {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
