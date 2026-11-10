"""Metin kaynaklarını indeksle: yükle → parçala → gömme → Qdrant.

    uv run python -m istanbul_agent.rag.ingest
    uv run python -m istanbul_agent.rag.ingest --dry-run      # gömme yapmaz
    uv run python -m istanbul_agent.rag.ingest --limit 50     # ilk 50 parça

Ölçüm raporu `data/rag_ingest.json` içine yazılıyor: parça dağılımı, atılan
gürültü, gerçek token sayıları, gömme süresi. Karar notu bu dosyadan
besleniyor.

Qdrant yerel modda dizini tek sürece kilitliyor: bu komut çalışırken
`knowledge_server` açık olmamalı.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from istanbul_agent.config import PROJECT_ROOT
from istanbul_agent.console import enable_unicode_output
from istanbul_agent.rag import chunking
from istanbul_agent.rag.chunking import Chunk, chunk_documents, chunk_stats
from istanbul_agent.rag.documents import load_documents, summarise

REPORT_PATH = PROJECT_ROOT / "data" / "rag_ingest.json"


def token_lengths(chunks: list[Chunk], sample: int = 120) -> dict[str, Any]:
    """Gerçek token sayılarını modelin tokenizer'ı ile ölç.

    Chunking karakterle çalışıyor; bu ölçüm karakter hedefinin PLAN.md'deki
    300-600 token aralığına gerçekten karşılık gelip gelmediğini gösteriyor.
    Tokenizer yoksa (rag ekstrası kurulu değil) ölçüm atlanıyor.
    """
    try:
        from transformers import AutoTokenizer

        from istanbul_agent.rag.embedding import EMBEDDING_MODEL

        tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL)
    except Exception as problem:
        return {"available": False, "error": f"{type(problem).__name__}: {str(problem)[:120]}"}

    step = max(1, len(chunks) // sample)
    picked = chunks[::step][:sample]
    counts = [len(tokenizer.encode(chunk.text, add_special_tokens=False)) for chunk in picked]
    chars = [chunk.chars for chunk in picked]
    return {
        "available": True,
        "sampled": len(counts),
        "tokens_median": int(statistics.median(counts)),
        "tokens_mean": int(statistics.fmean(counts)),
        "tokens_max": max(counts),
        "chars_per_token": round(sum(chars) / sum(counts), 2) if sum(counts) else None,
        "over_600_tokens": sum(1 for count in counts if count > 600),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RAG indeksini kur")
    parser.add_argument("--dry-run", action="store_true", help="gömme ve yazma yapma")
    parser.add_argument("--limit", type=int, default=None, help="yalnızca ilk N parça")
    parser.add_argument("--no-catalog", action="store_true", help="veri seti açıklamalarını atla")
    parser.add_argument("--qdrant-path", type=Path, default=None)
    parser.add_argument("--no-tokens", action="store_true", help="token ölçümünü atla")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    print("=== belgeler yükleniyor ===")
    documents = load_documents(include_catalog=not args.no_catalog)
    for row in summarise(documents):
        also = f"  (ayrıca: {', '.join(row['also_from'])})" if row["also_from"] else ""
        print(
            f"  {row['slug']:24} {row['sections']:>5} bölüm  {row['chars']:>8} karakter  "
            f"{row['data_as_of']}{also}"
        )

    print("\n=== parçalanıyor ===")
    chunks = chunk_documents(documents)
    dropped = list(chunking.LAST_DROPPED)
    if args.limit:
        chunks = chunks[: args.limit]
    stats = chunk_stats(chunks)
    print(
        f"  {stats['chunks']} parça; karakter ortanca {stats['chars_median']}, "
        f"ortalama {stats['chars_mean']}, en uzun {stats['chars_max']}"
    )
    print(
        f"  tür dağılımı {stats['by_kind']}; bölünemeyecek kadar uzun "
        f"{stats['oversized_unsplit']}; gürültü olarak atılan {len(dropped)}"
    )

    report: dict[str, Any] = {
        "documents": summarise(documents),
        "chunks": stats,
        "dropped": len(dropped),
        "dropped_samples": [sample for _, sample in dropped[:10]],
    }

    if not args.no_tokens:
        print("\n=== token ölçümü ===")
        tokens = token_lengths(chunks)
        report["tokens"] = tokens
        if tokens.get("available"):
            print(
                f"  {tokens['sampled']} parça örneklendi: token ortanca "
                f"{tokens['tokens_median']}, ortalama {tokens['tokens_mean']}, "
                f"en uzun {tokens['tokens_max']}, 600 token üstü "
                f"{tokens['over_600_tokens']}; karakter/token {tokens['chars_per_token']}"
            )
        else:
            print(f"  atlandı: {tokens.get('error')}")

    if args.dry_run:
        report["mode"] = "dry-run"
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nkuru çalıştırma; rapor: {REPORT_PATH}")
        return 0

    from istanbul_agent.rag.embedding import embed_passages, get_device
    from istanbul_agent.rag.index import build_index, index_stats

    print(f"\n=== gömme ({get_device()}) ===")
    embedded = embed_passages([chunk.text for chunk in chunks])
    per_chunk = embedded.seconds / len(chunks) if chunks else 0.0
    print(f"  {len(embedded)} parça, {embedded.seconds:.1f} s ({per_chunk * 1000:.0f} ms/parça)")
    sparse_terms = [len(item) for item in embedded.sparse]
    print(
        f"  seyrek terim sayısı: ortanca "
        f"{int(statistics.median(sparse_terms)) if sparse_terms else 0}, "
        f"en çok {max(sparse_terms, default=0)}"
    )

    print("\n=== indeks yazılıyor ===")
    written = build_index(chunks, embedded.dense, embedded.sparse, path=args.qdrant_path)
    info = index_stats(args.qdrant_path)
    print(f"  {written} nokta yazıldı; indeks {info['points']} nokta")
    for slug, count in info["by_source"].items():
        print(f"    {slug:24} {count}")

    report.update(
        {
            "mode": "index",
            "device": get_device(),
            "embedding_seconds": round(embedded.seconds, 2),
            "ms_per_chunk": round(per_chunk * 1000, 1),
            "sparse_terms_median": int(statistics.median(sparse_terms)) if sparse_terms else 0,
            "index": info,
            "total_seconds": round(time.perf_counter() - started, 1),
        }
    )
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nBitti: {time.perf_counter() - started:.1f} s; rapor: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    enable_unicode_output()
    sys.exit(main())
