# İstanbul Ulaşım Agent'ı

İBB Açık Veri Portalı üzerine kurulu, canlı API'leri, yerel GTFS/GeoJSON verilerini ve
RAG'i MCP araçları olarak sunup LangGraph tabanlı bir agent ile birleştiren portföy
projesi.

> Geliştirme aşamasında. Yol haritası: [`documents/PLAN.md`](documents/PLAN.md).
> Mimari diyagram, metrik tabloları ve bilinen sınırlamalar Faz 5'te buraya gelecek.

## Kurulum

```powershell
uv sync                      # sanal ortam + bağımlılıklar (Python 3.11)
Copy-Item .env.example .env  # sonra GEMINI_API_KEY alanını doldur
```

Yerel model için Ollama'nın çalışıyor olması gerekir:

```powershell
ollama pull qwen3.5:4b
```

## Komutlar

```powershell
uv run pytest                              # birim testler (gerçek API/LLM çağrısı yok)
uv run ruff check .                        # lint
uv run ruff format .                       # format
uv run python scripts/smoke_llm.py         # her iki LLM sağlayıcısına gerçek istek
```

Sonraki aşamalarda eklenecek komutlar [`CLAUDE.md`](CLAUDE.md) içindeki "Komutlar"
bölümünde listelenir.

## Veri kaynakları

İşaretli envanter [`datasets/data-inventory.md`](datasets/data-inventory.md),
tam liste `datasets/ibb_datasets.csv`. Her araç çıktısı `source` ve `data_as_of`
alanı taşır; eski veri kaynakları `is_stale` ile işaretlenir.
