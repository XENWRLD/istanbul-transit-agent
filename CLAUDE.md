# İstanbul Ulaşım Agent'ı

İBB Açık Veri Portalı'ndaki canlı verileri (İETT, Metro İstanbul, İSPARK, trafik, hava kalitesi), yerel olarak sorgulanan tarife ve konum verilerini (GTFS + GeoJSON) ve statik metin bilgilerini (RAG) MCP araçları olarak sunan ve bunları LangGraph tabanlı bir agent ile birleştiren portföy projesi. Hedef: "Kadıköy'den Beşiktaş'a şu an en mantıklı ulaşım nedir?" gibi soruları birden fazla aracı çağırarak, kaynak göstererek cevaplamak.

Yol haritası, aşamalar ve "bitti" tanımları: `documents/PLAN.md`. Veri kaynakları ve uç noktalar: `datasets/data-inventory.md` ve `datasets/ibb_datasets.csv`. Her yeni aşamaya başlamadan önce oku.

## Mimari (özet)

- `src/istanbul_agent/servers/transit_server.py` → MCP: toplu taşıma araçları (yerel GTFS/GeoJSON + canlı İETT/Metro)
- `src/istanbul_agent/servers/city_server.py` → MCP: otopark, trafik, yol çalışmaları, hava kalitesi, mikromobilite
- `src/istanbul_agent/servers/knowledge_server.py` → MCP: `search_knowledge` (RAG); gömme modelini **açılışta** yükler (`warm_up`)
- `src/istanbul_agent/clients/` → İETT SOAP istemcisi (`zeep`), REST istemcisi (`httpx`), ortak önbellek/yeniden deneme
- `src/istanbul_agent/store/` → GTFS ve GeoJSON ingest, DuckDB sorguları (`ingest` otobüs + nokta katmanları, `ingest_rail` raylı sistem, `ingest_timetable` sefer saatleri)
- `src/istanbul_agent/rag/` → ingest, chunking, embedding, hibrit arama, reranking
- `src/istanbul_agent/agent/` → LangGraph grafı (transit + knowledge sunucularına kalıcı MCP oturumları) ve sistem prompt'u
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
uv run python scripts/download_rail_reference.py      # 312/313 raylı sistem tabloları
uv run python scripts/download_rail_geojson.py       # raylı sistem istasyon noktaları (6)
uv run python -m istanbul_agent.store.ingest_rail     # raylı sistem tabloları → DuckDB
uv run python -m istanbul_agent.store.ingest_timetable # sefer saatleri tabloları → DuckDB
uv run python scripts/show_travel_examples.py         # get_rail_travel_time örnekleri
uv run python scripts/show_departure_examples.py      # get_next_departures örnekleri
uv run python scripts/compare_service_hours.py        # GTFS vs elle doğrulanmış saatler
uv run python scripts/download_stop_geojson.py        # durak/iskele nokta katmanları (128, 15, 13, 9)
uv run python scripts/inspect_iett_feed.py --full     # İETT beslemesinin ağzını ölç
uv run python scripts/inspect_iett_times.py           # tarife nerede + metin onarımı ölçümü
uv run python -m istanbul_agent.store.ingest          # İETT GTFS + nokta katmanları → DuckDB (~8 dk)
uv run python -m istanbul_agent.store.ingest --only-places  # yalnızca transit_places
uv run python scripts/download_knowledge.py            # RAG metin kaynakları (509, 541, 445, 442, 435, 496)
uv run python scripts/inspect_knowledge.py             # kaynakların metni gerçekte nasıl çıkıyor
uv run python -m istanbul_agent.rag.ingest             # dokümanları indeksle (~6 dk, CPU)
uv run python -m istanbul_agent.rag.ingest --dry-run   # gömme yapmadan parça ölçümü
uv run python -m istanbul_agent.servers.transit_server   # MCP sunucusu (stdio)
uv run python -m istanbul_agent.cli "soru"                # agent, komut satırı
uv run python scripts/run_agent_questions.py          # 5 senaryo x 2 sağlayıcı
uv run python -m istanbul_agent.servers.city_server
uv run python -m istanbul_agent.servers.knowledge_server
uv run python -m istanbul_agent.app                   # demo arayüzü
uv run python eval/run_rag_eval.py
uv run python eval/run_agent_eval.py                  # 48 senaryo (ollama, önbellekli)
uv run python eval/run_agent_eval.py --provider gemini --limit 5   # kota: günde 20 istek
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
- Ücretsiz API kotası sınırlı (Gemini: dakikada 5, **günde 20** istek — ölçüldü, karar 0006): değerlendirme betikleri LLM yanıtlarını `eval/.cache/` altında önbelleğe alır, 429 hatalarında üstel geri çekilme (backoff) uygular. Kota dolarsa yerel model (`--provider ollama`) ile devam edilir.
- İBB API'sinin davranışını tahmin etme; önce gerçek yanıtı incele, sonra kodu yaz.
- Metro İstanbul REST API'si başarısızlıkta da HTTP 200 döner; gövdedeki `Success` alanına bak, durum koduna değil.
- **İki GTFS beslemesi iki ayrı ağızdır; ağzı tahmin etme, `store/gtfs.py` içindeki `dialect_for()` ile al** (karar 0007). 308 beslemesi (metro/vapur/minibüs): cp1254, virgül, `calendar.csv` penceresi dolmuş — servis günleri yalnızca hafta günü desenine göre seçilir (karar 0002). 75 beslemesi (İETT otobüs): UTF-8+BOM, noktalı virgül (ama `agency.csv` ve `stop_times.txt` virgüllü), metin çifte kodlanmış (`store/text_repair.py` onarır), `stops.csv` koordinatları parçalanmış (`geo.parse_grouped_coordinate` onarır), `calendar.csv` penceresi **geçerli**, `stop_times` zip içinde.
- **İETT beslemesi ara duraklara saat vermez:** sefer başına yalnızca iki saat var (ilk duraktan kalkış, son durağa varış). Ham `stop_times` saklanmıyor; `bus_trips` ve `bus_route_stops` türetiliyor. Ara durak saatleri yalnızca canlı İETT servisinden gelebilir (karar 0007).
- Marmaray aynı GTFS beslemesinde **TCDD** ajansı altındadır, Metro İstanbul'da değil; ajans filtresi onu düşürür. Metro ağı Boğaz'ın iki yakasında ancak Marmaray üzerinden bağlanır.
- Aktarmalar koordinattan kurulur, ad eşleşmesinden değil: yarıçap 400 m, 313'ün iki ucunu da adlandırdığı çiftlerde 1000 m. Aktarma cezası = 210 s bekleme + yürüme (≤400 m için 150 s, üstünde mesafe / 1.2 m/s) (karar 0004).
- Segment anahtarları hattı içermek zorunda: Marmaray ve M4 aynı adlı istasyonlar arasında (`Bostancı→Küçükyalı`) ayrı hatlar işletiyor.
- Sefer saatleri: `datasets/metro_service_hours.yaml` içindeki hatlarda (11 metro kodu + Marmaray) ilk/son sefer YALNIZCA o dosyadan okunur, GTFS saatleri kullanılmaz; gün tipleri pzt_pers/cuma/cumartesi/pazar; ara istasyon saati uç istasyonun sefer süresi kadar kaydırılmışıdır. Saat ve aralık ayrı bayrak taşır: `hours_is_stale` / `headway_is_stale` (aralık Marmaray dışında hâlâ GTFS'ten). Dosyada olmayan hatlarda kaynak hat bazında seçilir (`stop_times` ölçülür, `frequencies` beyan edilir), 04:00 öncesi kalkışlar gece servisidir ve 4 seferden az veriden tarife üretilmez (kararlar 0005/8).
- Agent ham API'leri görmez: uç noktalar `documents/PLAN.md` araç tablosundaki alan bazlı araçların arkasında birleştirilir. Yeni araç eklemeden önce mevcut bir araca parametre eklemenin yeterli olup olmadığını düşün; toplam araç sayısını ~15'te tut.
- Her araç çıktısı `source` ve `data_as_of` alanı taşır; 2018-2020 tarihli "Public Transport GTFS" verisinden gelen sonuçlar `is_stale: true` ile işaretlenir.
- API kapalıysa veya hata verirse araç bunu açıkça döner; asla tahmini veri üretmez.
- **Bir aracın boş sonucu parametre hatasını gizlemesin** (kararlar 0006/7a, 0008/3): geçersiz bir mod, bilinmeyen bir hat ya da yanlış yazılmış bir ad "sonuç yok" değil, geçerli değerleri veya adayları sayan bir hata döndürür. Araç eşit puanlı adaylar arasından keyfî seçim yapmaz; hepsini döndürür ya da seçimi kullanıcıya bırakır.
- Durak/istasyon araması **küme** döndürür: aynı adı taşıyan ve 300 m içindeki noktalar tek yer, uzaktakiler ayrı yer. Durak kimliği ad + konumdur, ad tek başına değil (karar 0008).
- **Qdrant yerel modunda koleksiyonu silip yeniden oluşturmak eski noktaları geri getiriyor** (ölçüldü, karar 0009): yeniden indeksleme `reset_storage` ile dizini siler. Yerel mod dizini tek sürece kilitler — `knowledge_server` açıkken ingest çalışmaz.
- RAG parçalarında gürültü eşiği **tür bazlıdır** (karar 0009): `qa` her zaman kabul, `table` ≥12 harf, `prose` ≥25 harf. Tek eşik kullanmak küçük fiyat tablolarını ve kısa SSS cevaplarını atıyordu; harf **oranı** kullanmak raporun istatistik tablolarını atıyordu.
- Tazelik kaynağa göre belirlenir, tarihe göre değil: 2023 tarihli bir araştırma raporu eski değildir, eskiyen şey fiyat gibi değişen bilgidir. Güncel kaynak ilk 3 sonuçta eskinin önüne alınır — **soru geçmişi sormuyorsa** (karar 0009).
- **MCP stdio sunucusunda ağır modeli araç gövdesinde yükleme, açılışta yükle** (karar 0010): BGE-M3'ü FastMCP'nin iş parçacığı havuzunda yüklemek sunucuyu kilitliyor — boş sorgu 0.0 s'de dönerken gerçek sorgu 180 s'de çıktı vermeden asılıyor ve istemci `Connection closed` alıyor. Aynı çağrı süreç içinde 15.8 s. `warm_up()` ana iş parçacığında yükler, sonrası 3.2 s.
- Agent MCP oturumlarını **açık tutar**; `MultiServerMCPClient.get_tools()` her araç çağrısında yeni oturum açtığı için gömme modeli her aramada yeniden yüklenirdi.
- **Araç çağırmadan verilen cevap kaynaksızdır.** `answer()` araç çağrılmayan turu bir kez geri çevirir (soruyu tekrarlayan bir uyarıyla) ve ikinci turda da çağrılmazsa sonucu `unverified=True` işaretler; CLI bunu uyarı olarak basar. Prompt'a "kaynak göster" yazmak tek başına yetmedi — model kaynağı da uydurdu (karar 0010).
- Türkçe metin karşılaştırmasında `str.casefold()` kullanma: `"YAKLAŞIK".casefold()` noktalı `i` üretip `yaklaşık` ile eşleşmiyor. `store.station_names.fold_case()` kullan.
- `OLLAMA_NUM_CTX` araç yüzeyine bağlıdır: yedi araçta prompt + şemalar ~5900 token, 8192'lik pencere taşıyor ve model hiç araç çağırmıyor. Varsayılan 16384 (kararlar 0006, 0010); araç eklenince yeniden ölç.
- Mimari bir karar verdiğinde (chunk boyutu, model seçimi, araç tasarımı) `documents/decisions/` altına kısa bir not ekle: bağlam, seçenekler, karar, gerekçe.
- Yeni bir araç veya RAG değişikliği eklerken ilgili test ve değerlendirme senaryosunu da ekle.
- Küçük, anlamlı commit'ler at; commit mesajları İngilizce.
