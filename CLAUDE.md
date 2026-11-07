# İstanbul Ulaşım Agent'ı

İBB Açık Veri Portalı'ndaki canlı verileri (İETT, Metro İstanbul, İSPARK, trafik, hava kalitesi), yerel olarak sorgulanan tarife ve konum verilerini (GTFS + GeoJSON) ve statik metin bilgilerini (RAG) MCP araçları olarak sunan ve bunları LangGraph tabanlı bir agent ile birleştiren portföy projesi. Hedef: "Kadıköy'den Beşiktaş'a şu an en mantıklı ulaşım nedir?" gibi soruları birden fazla aracı çağırarak, kaynak göstererek cevaplamak.

Yol haritası, aşamalar ve "bitti" tanımları: `documents/PLAN.md`. Veri kaynakları ve uç noktalar: `datasets/data-inventory.md` ve `datasets/ibb_datasets.csv`. Her yeni aşamaya başlamadan önce oku.

## Mimari (özet)

- `src/istanbul_agent/servers/transit_server.py` → MCP: toplu taşıma araçları (yerel GTFS/GeoJSON + canlı İETT/Metro)
- `src/istanbul_agent/servers/city_server.py` → MCP: otopark, trafik, yol çalışmaları, hava kalitesi, mikromobilite
- `src/istanbul_agent/servers/knowledge_server.py` → MCP: `search_knowledge` (RAG)
- `src/istanbul_agent/clients/` → İETT SOAP istemcisi (`zeep`), REST istemcisi (`httpx`), ortak önbellek/yeniden deneme
- `src/istanbul_agent/store/` → GTFS ve GeoJSON ingest, DuckDB sorguları
- `src/istanbul_agent/rag/` → ingest, chunking, embedding, hibrit arama, reranking
- `src/istanbul_agent/agent/` → LangGraph grafı ve sistem prompt'u
- `src/istanbul_agent/llm.py` → LLM sağlayıcı fabrikası (gemini | ollama)
- `eval/` → RAG ve agent değerlendirme setleri, betikler, sonuçlar
- `documents/` → `PLAN.md` (yol haritası) ve `decisions/` (mimari karar notları)
- `datasets/` → `data-inventory.md` (işaretli envanter), `ibb_datasets.csv` (tam liste), `ibb_dataset.json` (portalın ham kataloğu; açıklamalar `notes` alanında, RAG kaynağı)
- `scripts/` → tek seferlik keşif ve duman testi betikleri (ürün kodu değil)

## Teknoloji yığını

- Python 3.11+, paket yönetimi `uv`
- MCP: resmi `mcp` Python SDK (FastMCP)
- Agent: LangGraph + `langchain-mcp-adapters`
- LLM: Gemini API ücretsiz katman (varsayılan), Ollama (yerel karşılaştırma, `qwen3.5:4b`)
- Embedding: `BAAI/bge-m3`, reranker: `BAAI/bge-reranker-v2-m3` (yerel, CUDA varsa GPU)
- Vektör DB: Qdrant **yerel mod** (`QdrantClient(path="data/qdrant")`), geliştirmede Docker yok
- Yapısal veri: DuckDB + spatial eklentisi (`data/transit.duckdb`)
- API istemcileri: `zeep` (İETT SOAP), `httpx` (REST)
- API/arayüz: FastAPI, demo için Gradio
- Test: pytest, lint/format: ruff
- Değerlendirme: RAGAS + kendi agent senaryo betiğimiz

## Komutlar

```bash
uv sync                                   # bağımlılıkları kur
uv run pytest                             # testler
uv run ruff check . && uv run ruff format .
uv run python scripts/smoke_llm.py        # her iki LLM sağlayıcısına gerçek istek
uv run python scripts/download_gtfs.py    # GTFS beslemelerini data/gtfs altına indir
uv run python -m istanbul_agent.store.ingest          # GTFS + GeoJSON → DuckDB
uv run python -m istanbul_agent.rag.ingest            # dokümanları indeksle
uv run python -m istanbul_agent.servers.transit_server
uv run python -m istanbul_agent.servers.city_server
uv run python -m istanbul_agent.servers.knowledge_server
uv run python -m istanbul_agent.app                   # demo arayüzü
uv run python eval/run_rag_eval.py
uv run python eval/run_agent_eval.py
```

Bu komutlar aşamalar ilerledikçe gerçekleşecek; bir komut değişirse bu bölümü güncelle.

## Geliştirme ortamı

- Windows 11 dizüstü: Ryzen 5 5600H, 16 GB RAM, RTX 3050 Laptop (~4 GB VRAM). RAM kısıtlı, ağır servisleri (Docker, büyük modeller) varsayılan yapma.
- Dosya yollarında her zaman `pathlib` kullan; kabuk komutlarını PowerShell'de de çalışacak şekilde yaz.
- Yerel modelde `OLLAMA_KEEP_ALIVE=24h` önerilir.

## Kurallar

- Kod, değişken ve fonksiyon adları İngilizce; MCP araç açıklamaları (docstring) ve kullanıcıya dönük metinler Türkçe.
- Her MCP aracının açıklamasında üç şey olmalı: ne yaptığı, parametrelerin anlamı ve **ne zaman KULLANILMAMASI gerektiği**. Araç seçimi bu metne dayanıyor; açıklamayı değiştirmek davranış değişikliğidir, değerlendirmeyi yeniden çalıştır.
- Tip ipuçları zorunlu. Araç girdi/çıktıları Pydantic modelleriyle tanımlanır.
- LLM sağlayıcısı yalnızca `llm.py` üzerinden oluşturulur; başka yerde doğrudan SDK çağırma. Sağlayıcı `.env` içindeki `LLM_PROVIDER` ile seçilir.
- Gizli anahtarlar yalnızca `.env` içinde; `.env.example` güncel tutulur, `.env` asla commit edilmez.
- Birim testleri gerçek LLM veya İBB API çağrısı yapmaz. Gerçek API yanıtlarından örnekleri `tests/fixtures/` altına kaydedip onları kullan.
- Ücretsiz API kotası sınırlı: değerlendirme betikleri LLM yanıtlarını `eval/.cache/` altında önbelleğe alır, 429 hatalarında üstel geri çekilme (backoff) uygular.
- İBB API'sinin davranışını tahmin etme; önce gerçek yanıtı incele, sonra kodu yaz.
- Metro İstanbul REST API'si başarısızlıkta da HTTP 200 döner; gövdedeki `Success` alanına bak, durum koduna değil.
- İBB GTFS CSV'leri **cp1254** kodlamalıdır, UTF-8 değil. `calendar.csv` penceresi dolmuştur: servis günleri yalnızca hafta günü desenine göre seçilir (karar 0002).
- Agent ham API'leri görmez: uç noktalar `documents/PLAN.md` araç tablosundaki alan bazlı araçların arkasında birleştirilir. Yeni araç eklemeden önce mevcut bir araca parametre eklemenin yeterli olup olmadığını düşün; toplam araç sayısını ~15'te tut.
- Her araç çıktısı `source` ve `data_as_of` alanı taşır; 2018-2020 tarihli "Public Transport GTFS" verisinden gelen sonuçlar `is_stale: true` ile işaretlenir.
- API kapalıysa veya hata verirse araç bunu açıkça döner; asla tahmini veri üretmez.
- Mimari bir karar verdiğinde (chunk boyutu, model seçimi, araç tasarımı) `documents/decisions/` altına kısa bir not ekle: bağlam, seçenekler, karar, gerekçe.
- Yeni bir araç veya RAG değişikliği eklerken ilgili test ve değerlendirme senaryosunu da ekle.
- Küçük, anlamlı commit'ler at; commit mesajları İngilizce.
