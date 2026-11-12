# İstanbul Ulaşım Agent'ı

İBB Açık Veri Portalı'ndaki canlı verileri (İETT, Metro İstanbul, İSPARK, trafik, hava kalitesi), yerel olarak sorgulanan tarife ve konum verilerini (GTFS + GeoJSON) ve statik metin bilgilerini (RAG) MCP araçları olarak sunan ve bunları LangGraph tabanlı bir agent ile birleştiren portföy projesi. Hedef: "Kadıköy'den Beşiktaş'a şu an en mantıklı ulaşım nedir?" gibi soruları birden fazla aracı çağırarak, kaynak göstererek cevaplamak.

Yol haritası, aşamalar ve "bitti" tanımları: `documents/PLAN.md`. Veri kaynakları ve uç noktalar: `datasets/data-inventory.md` ve `datasets/ibb_datasets.csv`. Her yeni aşamaya başlamadan önce oku.

## Mimari (özet)

- `src/istanbul_agent/servers/transit_server.py` → MCP: toplu taşıma araçları (yerel GTFS/GeoJSON + canlı İETT/Metro)
- `src/istanbul_agent/servers/city_server.py` → MCP: otopark (`find_parking`), trafik (`get_traffic_status`), mikromobilite ve şarj (`find_mobility_options`, karar 0019). Hava kalitesi aracı **yok**: kaynakta ölçüm yok (karar 0012/3)
- `src/istanbul_agent/servers/knowledge_server.py` → MCP: `search_knowledge` (RAG); gömme modelini **açılışta** yükler (`warm_up`)
- `src/istanbul_agent/clients/` → `base.py` (zaman aşımı, yeniden deneme, kaynak bazlı TTL önbellek), `iett.py` (**elle kurulan SOAP zarfı**, `zeep` değil), `metro.py`, `city.py` (İSPARK, trafik, hava kalitesi)
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
uv run python scripts/probe_live_freshness.py        # canlı kaynaklar: taze mi, tutarlı mı (ağa çıkar)
uv run python scripts/inspect_iett_times.py           # tarife nerede + metin onarımı ölçümü
uv run python -m istanbul_agent.store.ingest          # İETT GTFS + nokta katmanları → DuckDB (~8 dk)
uv run python -m istanbul_agent.store.ingest --only-places  # yalnızca transit_places
uv run python scripts/download_knowledge.py            # RAG metin kaynakları (509, 541, 445, 442, 435, 496)
uv run python scripts/inspect_knowledge.py             # kaynakların metni gerçekte nasıl çıkıyor
uv run python scripts/inspect_mobility_sources.py      # mikromobilite kaynaklarının ağzı (ağa çıkar)
uv run python scripts/download_mobility.py             # bisiklet/şarj katmanları (1, 16, 275, 276)
uv run python -m istanbul_agent.store.ingest_mobility  # mikromobilite katmanları → DuckDB (~3 dk)
uv run python -m istanbul_agent.rag.ingest             # dokümanları indeksle (~6 dk, CPU)
uv run python -m istanbul_agent.rag.ingest --dry-run   # gömme yapmadan parça ölçümü
uv run python -m istanbul_agent.servers.transit_server   # MCP sunucusu (stdio)
uv run python -m istanbul_agent.cli "soru"                # agent, komut satırı
uv run python scripts/run_agent_questions.py          # 5 senaryo x 2 sağlayıcı
uv run python -m istanbul_agent.servers.city_server
uv run python -m istanbul_agent.servers.knowledge_server
uv run python -m istanbul_agent.app                   # Gradio demo arayüzü
uv run uvicorn istanbul_agent.api:app --reload        # FastAPI: /ask, /health
uv run python eval/compare_providers.py               # iki sağlayıcı, ortak senaryolar
uv run python eval/measure_variance.py                # olcumun gurultu bandi: 9 senaryo x 3 tekrar
uv run python eval/run_rag_eval.py
uv run python eval/run_agent_eval.py --refresh        # 54 senaryo, hepsi taze (~57 dk, seri)
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
- Gece servisi kuralı **her modda** aynı: 04:00 öncesi kalkışlar günün ilk seferi değil, gecenin kuyruğudur (besleme `24:20` değil `00:20` yazıyor). Raylı tarafta `store/timetable.py: NIGHT_END_S`, otobüste `clients/iett.py: NIGHT_END_MINUTES`; ikisi de gündüz penceresini ayrı, gece penceresini `night_service` alanında verir (kararlar 0005, 0013/2).
- **İETT beslemesi ara duraklara saat vermez:** sefer başına yalnızca iki saat var (ilk duraktan kalkış, son durağa varış). Ham `stop_times` saklanmıyor; `bus_trips` ve `bus_route_stops` türetiliyor. Ara durak saatleri yalnızca canlı İETT servisinden gelebilir (karar 0007).
- Marmaray aynı GTFS beslemesinde **TCDD** ajansı altındadır, Metro İstanbul'da değil; ajans filtresi onu düşürür. Metro ağı Boğaz'ın iki yakasında ancak Marmaray üzerinden bağlanır.
- Aktarmalar koordinattan kurulur, ad eşleşmesinden değil: yarıçap 400 m, 313'ün iki ucunu da adlandırdığı çiftlerde 1000 m. Aktarma cezası = 210 s bekleme + yürüme (≤400 m için 150 s, üstünde mesafe / 1.2 m/s) (karar 0004).
- Segment anahtarları hattı içermek zorunda: Marmaray ve M4 aynı adlı istasyonlar arasında (`Bostancı→Küçükyalı`) ayrı hatlar işletiyor.
- Sefer saatleri: `datasets/metro_service_hours.yaml` içindeki hatlarda (11 metro kodu + Marmaray) ilk/son sefer YALNIZCA o dosyadan okunur, GTFS saatleri kullanılmaz; gün tipleri pzt_pers/cuma/cumartesi/pazar; ara istasyon saati uç istasyonun sefer süresi kadar kaydırılmışıdır. Saat ve aralık ayrı bayrak taşır: `hours_is_stale` / `headway_is_stale` (aralık Marmaray dışında hâlâ GTFS'ten). Dosyada olmayan hatlarda kaynak hat bazında seçilir (`stop_times` ölçülür, `frequencies` beyan edilir), 04:00 öncesi kalkışlar gece servisidir ve 4 seferden az veriden tarife üretilmez (kararlar 0005/8).
- Agent ham API'leri görmez: uç noktalar `documents/PLAN.md` araç tablosundaki alan bazlı araçların arkasında birleştirilir. Yeni araç eklemeden önce mevcut bir araca parametre eklemenin yeterli olup olmadığını düşün; toplam araç sayısını ~15'te tut.
- Her araç çıktısı `source` ve `data_as_of` alanı taşır; 2018-2020 tarihli "Public Transport GTFS" verisinden gelen sonuçlar `is_stale: true` ile işaretlenir.
- API kapalıysa veya hata verirse araç bunu açıkça döner; asla tahmini veri üretmez. Canlı araçlar bunu `error: "canli_veri_yok"` ile yapar — **boş liste döndürmek "veri yok" demekle aynı şey değildir** (karar 0012/2). Aynı kuralın alt basamağı: **sıfır da "bilinmiyor" demek değildir** (karar 0013/1) — İSPARK `empty: 0` dönen otoparkta `occupancy_known: false` ve yüzde üretilmez, çünkü kaynak "dolu" ile "bildirmiyor" arasını ayırmıyor (51/239 otopark, sıfırlar ilçeye ve tipe göre kümelenmiş).
- **Canlı aracı yazdıktan sonra gerçek kaynağa bak.** Fixture tabanlı test kodun kendi varsayımını doğrular, kaynağı doğrulamaz; karar 0013'teki dört kusurun hiçbirini yakalamadı (biri testle *doğrulanmıştı* bile). Yeni canlı kaynak eklenince `scripts/probe_live_freshness.py` çalıştırılır.
- **Tazelik damgası ölçümün varlığını kanıtlamaz:** İSPARK damgayı tüm partiye basıyor, o yüzden veri bildirmeyen bir otoparkın damgası da taze görünüyor.
- Duyuruların yaşı kaynağa göre değişir ve araç bunu söylemek zorunda: İETT duyurusunda **tarih yok**, yalnızca saat var (`recorded_day_known: false`) ve saati şu andan ileride olan duyuru önceki bir güne aittir (`from_earlier_day`). Metro servis durumu tarihli ama kaynakta aylarca kalıyor (26 gün ölçüldü); `age_days` taşınır, 7 günden eskisi not düşer (karar 0013/3).
- **Sonuç listesi kırpılacaksa sıraya göre değil önceliğe göre kırpılır** ve kaç kaydın düştüğü nota yazılır: `get_service_alerts` ekleme sırasına göre kırpınca metro servis durumu 219 İETT duyurusunun arkasında kalıp hiç dönmüyordu (karar 0013/4). Notlar kırpma **sonrası** listeden üretilir.
- İETT SOAP çağrıları `clients/iett.py` üzerinden elle kurulan zarfla yapılır; `zeep` ürün kodunda kullanılmaz (dört WSDL'i açamıyor ve açtığında iki kat yavaş, karar 0012/1). Yanıttaki JSON **XML gövdesinin içindedir** ve önce SOAP `Fault` aranır.
- Canlı kaynak önbelleği **kaynak bazlıdır** (`SOURCE_TTL`): otopark 60 s, trafik 300 s, otobüs tarifesi 6 saat. Yeni kaynak eklerken TTL'i gerekçesiyle yaz.
- MCP sunucu modülünde `main()` ve `if __name__` bloğu **dosyanın sonunda** durur; araç tanımı o bloktan sonra gelirse sunucu modül olarak çalıştığında hiç kaydolmaz (karar 0012'de ölçüldü: in-process 8 araç, stdio 6).
- **Bir aracın boş sonucu parametre hatasını gizlemesin** (kararlar 0006/7a, 0008/3): geçersiz bir mod, bilinmeyen bir hat ya da yanlış yazılmış bir ad "sonuç yok" değil, geçerli değerleri veya adayları sayan bir hata döndürür. Araç eşit puanlı adaylar arasından keyfî seçim yapmaz; hepsini döndürür ya da seçimi kullanıcıya bırakır.
- `mode` parametresi yalnızca sonucu değil **arama aşamasını** da kısıtlar: `find_nearby_stops` merkezi modsuz ararken "Bostancı" + `vapur` metro istasyonunu merkez alıyor ve iskeleler 1.7 km uzakta kaldığı için varsayılan yarıçapta boş dönüyordu (karar 0017). Mod eşleşmesi yoksa merkez başka moddan alınır ama not bunu söyler.
- Durak/istasyon araması **küme** döndürür: aynı adı taşıyan ve 300 m içindeki noktalar tek yer, uzaktakiler ayrı yer. Durak kimliği ad + konumdur, ad tek başına değil (karar 0008).
- **Qdrant yerel modunda koleksiyonu silip yeniden oluşturmak eski noktaları geri getiriyor** (ölçüldü, karar 0009): yeniden indeksleme `reset_storage` ile dizini siler. Yerel mod dizini tek sürece kilitler — `knowledge_server` açıkken ingest çalışmaz.
- RAG parçalarında gürültü eşiği **tür bazlıdır** (karar 0009): `qa` her zaman kabul, `table` ≥12 harf, `prose` ≥25 harf. Tek eşik kullanmak küçük fiyat tablolarını ve kısa SSS cevaplarını atıyordu; harf **oranı** kullanmak raporun istatistik tablolarını atıyordu.
- Tazelik kaynağa göre belirlenir, tarihe göre değil: 2023 tarihli bir araştırma raporu eski değildir, eskiyen şey fiyat gibi değişen bilgidir. Güncel kaynak ilk 3 sonuçta eskinin önüne alınır — **soru geçmişi sormuyorsa** (karar 0009).
- **MCP stdio sunucusunda ağır modeli araç gövdesinde yükleme, açılışta yükle** (karar 0010): BGE-M3'ü FastMCP'nin iş parçacığı havuzunda yüklemek sunucuyu kilitliyor — boş sorgu 0.0 s'de dönerken gerçek sorgu 180 s'de çıktı vermeden asılıyor ve istemci `Connection closed` alıyor. Aynı çağrı süreç içinde 15.8 s. `warm_up()` ana iş parçacığında yükler, sonrası 3.2 s.
- Agent MCP oturumlarını **açık tutar**; `MultiServerMCPClient.get_tools()` her araç çağrısında yeni oturum açtığı için gömme modeli her aramada yeniden yüklenirdi.
- **Araç çağırmadan verilen cevap kaynaksızdır.** `answer()` araç çağrılmayan turu bir kez geri çevirir (soruyu tekrarlayan bir uyarıyla) ve ikinci turda da çağrılmazsa sonucu `unverified=True` işaretler; CLI bunu uyarı olarak basar. Prompt'a "kaynak göster" yazmak tek başına yetmedi — model kaynağı da uydurdu (karar 0010).
- Türkçe metin karşılaştırmasında `str.casefold()` kullanma: `"YAKLAŞIK".casefold()` noktalı `i` üretip `yaklaşık` ile eşleşmiyor. `store.station_names.fold_case()` kullan.
- `OLLAMA_NUM_CTX` araç yüzeyine bağlıdır: yedi araçta prompt + şemalar ~5900 token, 8192'lik pencere taşıyor ve model hiç araç çağırmıyor. Varsayılan 16384 (kararlar 0006, 0010); araç eklenince yeniden ölç.
- **Mikromobilite kaynaklarında üç ölçülmüş kusur var ve araç üçünü de söylemek zorunda** (karar 0019): bisiklet yolu kayıtlarının 212/477'si henüz yol değil (`exists_now`, varsayılan olarak gizli), şarj soketi sayıları **alt sınırdır** (soket dosyasının 30 073/38 950 satırı istasyonsuz), şarj verisi 2025-05 tarihli (`is_stale`) ama park ve yol verisi 2026-11 (taze) — aynı araç iki tazelikte veri döndürüyor ve bunu kayıt bazında taşıyor.
- `store.station_names.normalize()` Türkçe harfleri de katlıyor ("Park Alanı" → "park alani"); sabit sözlük anahtarlarını **katlanmış** biçimde yaz. Katlanmamış anahtar yazmak ingest'te 1300 park alanının tamamını ve 265 mevcut bisiklet yolunu sessizce atmıştı (karar 0019); testler artık anahtarların normalize'dan geçmiş olmasını tutuyor.
- Mimari bir karar verdiğinde (chunk boyutu, model seçimi, araç tasarımı) `documents/decisions/` altına kısa bir not ekle: bağlam, seçenekler, karar, gerekçe.
- Yeni bir araç veya RAG değişikliği eklerken ilgili test ve değerlendirme senaryosunu da ekle.
- Araçlar yalnızca veri hatasını değil **kurulum** hatasını da açıkça döner: depo yoksa ham `IOException` değil, çalıştırılacak komutu içeren `depo_yok` hatası (karar 0011).
- `unverified` bayrağı her arayüzde görünür olmak zorunda (CLI, FastAPI, Gradio); taşımayan bir arayüz korumayı etkisiz kılar.
- **Değerlendirme koşarken aynı makinede başka iş çalıştırılmaz** (karar 0014): test takımını eval'in yanında koşturmak tek bir LLM isteğini 120 saniyelik `LLM_REQUEST_TIMEOUT`'un üstüne çıkardı ve bir senaryoyu sahte biçimde başarısız gösterdi (`rail-02`: çekişmeyle 144.9 s hata, sessiz makinede 14.4 s geçti). `--concurrency` seçeneği bu yüzden 1'de duruyor.
- Çok soru soracak çağıran yığını **bir kez** açar: `istanbul_agent()` ile aç, `ask()` ile sor. `answer()` her çağrıda yeni yığın açar ve bu 15.0 saniye (üç sunucu; `knowledge_server` BGE-M3'ü açılışta yükler) — tek soruluk arayüzler için doğru, 54 senaryo için değil (karar 0014).
- Araç şemaları önbellek anahtarına **girmiyor** (`sağlayıcı-model-promptözeti-senaryo`): araç çıktısı değişince önbellek kendini geçersiz kılmaz, ölçümü `--refresh` ile koş (karar 0014).
- **Oran farkı, ölçülmüş gürültü bandıyla karşılaştırılmadan yorumlanmaz** (karar 0018). Band `eval/measure_variance.py` ile ölçülür ve iki düzeyi var: süreç içi tekrar ile süreçler arası tur. Ölçülen değer (9 senaryo, 4 ayrı süreç, ollama): 8/9 imza sabit, oran bandı **±0** — yani bu altkümede 3 senaryoluk fark anlamlıdır, gürültü değil. Band araç yüzeyi ya da sağlayıcı değişince yeniden ölçülür.
- **Taban, karşılaştırmayla aynı pencerede koşulur** (karar 0018): pencere içi band ±0 ama pencereler arası kayma ölçüldü — `rail-06` ve `know-06` saatler önceki tabanda geçerken aynı prompt, uyarı ve yüzey altında düştü. Saatler önceki bir kayda karşı ölçüm yapma; tabanı yeniden koş.
- **Araç açıklaması ölçülmüş biçimde davranıştır** (kararlar 0017, 0018): `find_nearby_stops` docstring'ine eklenen dört satır, o aracı hiç çağırmayan iki senaryoyu bozdu (aynı pencerede 4/6 → 2/6); aynı bilgi tek cümleye indirilince bedel kalktı. Açıklamayı uzatmadan önce bedelini ölç.
- **Bir A/B karşılaştırmasının iki ucu aynı araç yüzeyinde olmalı** (karar 0018): karar 0017'de öncesi ve sonrası farklı yüzeylerdeydi, bu yüzden kod düzeltmesinin etkisi docstring'in etkisinden ayrışmadı. Araç açıklaması her senaryonun prompt'una giriyor — o aracı hiç çağırmayan senaryoların seçimi de kayıyor.
- Niteliksel kanıt orandan güçlüdür: hangi senaryo neyi uydurdu, kaç senaryonun geçtiğinden daha çok şey söyler (karar 0018).
- Sağlayıcı karşılaştırması **ortak senaryolar** üzerinden yapılır; farklı kapsamdaki oranları yan yana koymak kıyas değil yanıltmadır (karar 0011).
- Küçük, anlamlı commit'ler at; commit mesajları İngilizce.
