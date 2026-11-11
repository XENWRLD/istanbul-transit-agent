# Proje Planı

## Amaç

İstanbul toplu taşıma ve ulaşımıyla ilgili soruları; İBB'nin canlı verilerini, yerel olarak sorgulanan tarife/konum verilerini (GTFS + GeoJSON) ve statik metin bilgilerini (RAG) birleştirerek cevaplayan, kaynak gösteren ve ölçülebilir kalitede çalışan bir agent geliştirmek. Proje bir portföy çalışmasıdır: her önemli karar gerekçelendirilmeli, her iddia ölçümle desteklenmelidir.

Veri kaynaklarının tam listesi ve işaretleri: `datasets/data-inventory.md` (A, A2, B ve C grupları kapsam içinde; D grubu kapsam dışı).

## Mimari

```
Kullanıcı sorusu
      │
LangGraph agent  (LLM: Gemini Flash | yerel qwen3.5:4b)
      │
      ├── MCP: transit_server      → toplu taşıma (statik + canlı)
      │      ├── Yerel veri deposu (DuckDB + spatial): GTFS, durak/hat/istasyon/iskele GeoJSON'ları
      │      └── Canlı: İETT SOAP servisleri, Metro İstanbul REST servisleri
      │
      ├── MCP: city_server         → otopark, trafik, yol çalışmaları, hava kalitesi, mikromobilite, şarj
      │
      └── MCP: knowledge_server    → search_knowledge (RAG, Qdrant yerel mod)
```

Üç katmanlı iş bölümü:
- **Canlı ve sık değişen** veri (servis durumu, duyurular, otopark doluluğu, trafik, AQI) → API çağrısı, kısa süreli önbellek
- **Yapısal ve periyodik** veri (duraklar, hatlar, tarifeler, konumlar) → yerel veri deposunda sorgu
- **Metin** bilgi (SSS, bilet fiyatları, kurallar, metodoloji, veri seti açıklamaları) → RAG

### Veri tazeliği kuralı

Her araç çıktısında `data_as_of` (verinin tarihi) ve `source` alanı bulunur. Eski kaynaklar açıkça işaretlenir:
- İETT GTFS: güncel (Nisan 2026)
- "Public Transport GTFS" (minibüs, dolmuş, vapur, Marmaray, metro): 2018-2020 tarihli ve portal notuna göre güncellenmeyecek. Bu kaynaktan gelen saatler için `is_stale: true` döner ve agent cevabında bunu belirtir.
- **Metro sefer saatleri ve istasyonlar arası süreler canlı API'de bozuk** (`GetTimeTable`, `GetStationBetweenTime`: 18 hat × tüm yönler = 43/43 deneme sunucu içi istisna). Metro saatleri GTFS'ten gelir ve `is_stale: true` taşır.
- **GTFS `calendar.csv` penceresi dolmuş** (tüm `end_date` değerleri 2024-12-31 veya öncesi). Servis günleri yalnızca hafta günü desenine göre seçilir, tarih penceresi okunmaz; gerekçe `documents/decisions/0002-gtfs-servis-gunleri.md`. `calendar_dates.csv` olmadığı için resmî tatiller bilinmiyor, araç bunu belirtir.
- **T3 hattı GTFS'ten çıkarıldı:** 10 durağının 10'unda da ad, o koordinattaki gerçek istasyonla uyuşmuyor (adlar konumlara ters sırada atanmış). Ad eşleştirmesi sessizce yanlış istasyonu döndüreceği için hat tamamen kullanılmaz; gerekçe `documents/decisions/0003-istasyon-eslestirme.md`.
- İsbike servisleri portalda kapalı; erişilemezse araç bunu açıkça söyler, tahmin üretmez.

### Araç tasarımı (13 araç + 1 askıda)

Uç noktalar alan bazlı araçların arkasında birleştirilir; agent ham API'leri görmez. Aşağıdaki tablo **Faz 0'da 39 uç noktanın tamamı gerçekten çağrıldıktan sonra** revize edilmiş halidir; gerekçeler `documents/decisions/0001-veri-kaynaklari.md` içinde.

| Araç | Sunucu | Kaynaklar |
|---|---|---|
| `geocode_place` | transit | Durak/istasyon/iskele adları (yerel GTFS + GeoJSON). Şehir Haritası API kapsamdan çıktı: 384 bir developer portal sayfası, JSON uç noktası değil. Geocoding yöntemi Faz 1'de ölçülecek. |
| `find_nearby_stops` | transit | GTFS stops + raylı sistem istasyonları + iskeleler + dolmuş/minibüs durakları |
| `get_lines_at_stop` | transit | GTFS routes/trips, İETT `GetDurak_json` |
| `get_line_route` | transit | GTFS, İETT `DurakDetay_GYY` + `HatServisi_GYY`, Metro `GetStations` / `GetStationById` |
| `get_next_departures` | transit | **Otobüs:** İETT `GetPlanlananSeferSaati_json`. **Metro:** GTFS — kaynak hat bazında seçilir (`stop_times` 12 hat, `frequencies` 7 hat; `datasets/metro_schedule_source.csv`), `is_stale: true`. Elle hazırlanan `datasets/metro_service_hours.yaml` varsa GTFS'in önüne geçer. Metro `GetTimeTable` (552) sunucu taraflı bozuk, kullanılmıyor. |
| `get_rail_travel_time` | transit | GTFS `stop_times` ardışık istasyon süreleri + aktarma süresi sabiti (313, 409, 530, 532 aktarma veri setleri). GTFS'te olmayan istasyonlar için koordinat/ortalama hız tahmini, `estimated: true`. Metro `GetStationBetweenTime` (513) sunucu taraflı bozuk, kullanılmıyor. |
| `get_service_alerts` | transit | İETT `GetDuyurular_json` (291 duyuru), Metro `GetAnnouncements`, Metro `GetServiceStatuses` |
| `get_station_accessibility` | transit | Metro `GetFaultyEquipments`, `GetFaultyEquipmentDetails` (POST `EquipmentGroupName`), `GetStations.DetailInfo` (asansör/yürüyen merdiven sayısı) |
| `find_parking` | city | İSPARK `Park` (240 otopark) + `ParkDetay`, otopark kapasite verileri |
| `get_traffic_status` | city | Trafik indeksi (548). UYM trafik duyuruları (18) henüz kaynak değil, ileride değerlendirilecek. |
| `get_air_quality` | city | `GetAQIStations` + `GetAQIByStationId` |
| `find_mobility_options` | city | Bisiklet yolları (1), bisiklet/mikromobilite park alanları (16), elektrikli araç şarj istasyonları (275, 276). İsbike çıkarıldı (servis kapalı). **Düşük öncelikli:** Faz 2'nin sonuna bırakılır. |
| `search_knowledge` | knowledge | RAG |

**Askıya alınanlar (10 Kasım 2026):**
- **`get_vehicle_positions`** — İETT `GetHatOtoKonum_json(HatKodu)` Faz 0'da doğrulandı (hat bazlı canlı konum, 46 ms) ve çalışıyor; araç yine de **yazılmayacak**. Gerekçe: canlı konum tek başına bir soruyu cevaplamıyor — "otobüs nerede" sorusu ancak durağa varış tahminiyle işe yarıyor, o da bu projede olmayan bir tahmin modeli demek. Kaynak çalıştığı için istendiğinde Faz 2'den sonra eklenebilir; envanterde uç nokta "çalışıyor" işaretli kalıyor.

Faz 0'da kapsamdan çıkanlar:
- **`get_road_works`** — tek kaynağı olan 501 (`teas/api/open_data`) kalkmış (nginx 404, 6 yol varyantı denendi). Yol çalışmaları bu projenin önceliği değil; envanterde "ertelendi".
- **İsbike** (169, 170) — servis kapalı: 169 HTTP 200 veriyor ama `dataList` boş, 170 yalnızca o boş listeden alınabilecek bir `guid` istiyor.

Tarihsel istatistik veri setleri (saatlik trafik, yolcu sayıları) araç değil; agent'a bağlam olarak gerekmedikçe kullanılmaz, istenirse Faz 6'da analiz aracı olarak eklenir.

## Aşamalar

### Faz 0 — Keşif ve iskelet ✅ bitti (7 Kasım 2026)
- Proje iskeleti: `uv`, klasör yapısı, ruff, pytest, `.env.example`, `llm.py` sağlayıcı fabrikası.
- `datasets/data-inventory.md` içindeki A ve A2 grubundaki **her** uç noktaya gerçek istek at: çalışıyor mu, kimlik doğrulama gerekiyor mu, yanıt formatı ne? Örnek yanıtları `tests/fixtures/` altına kaydet.
- İETT Web Servis Kullanım Dokümanı V1.5'teki metot listesini çıkar; özellikle araç konumu veren metot var mı doğrula.
- Bulguları `documents/decisions/0001-veri-kaynaklari.md` içine yaz; çalışmayan uç noktaları envanterde işaretle ve araç tablosunu revize et.

**Bitti tanımı:** Tüm A/A2 uç noktalarının durumu (çalışıyor / kapalı / değişmiş) belgelenmiş; `llm.py` ile Gemini ve Ollama'ya istek atılabiliyor; araç tablosu son haline getirilmiş.

**Sonuç:** 39 veri seti çağrıldı — REST 25/32 çalışıyor, İETT SOAP 7/7 WSDL okundu (28 metot), 9/9 doğrulanan çağrı. Hiçbiri kimlik doğrulama istemedi. `llm.py` ile Gemini (`gemini-3.5-flash`) ve Ollama (`qwen3.5:4b`) doğrulandı. Araç sayısı 15 → 14.

### Faz 1 — Yerel veri deposu ve statik toplu taşıma araçları
- `ingest` betikleri (bitti): GTFS (İETT + genel), B grubundaki nokta katmanları → DuckDB (`data/transit.duckdb`). **İki besleme iki ayrı ağız:** 308 cp1254/virgül, 75 UTF-8+BOM/noktalı virgül + çifte kodlanmış metin + parçalanmış koordinat + zip'li `stop_times`; ağız `dialect_for()` içinde bildirilir (karar 0007). İETT beslemesi ara duraklara saat vermediği için ham `stop_times` saklanmaz, `bus_trips` + `bus_route_stops` türetilir. Nokta tablosu `transit_places`: otobüs 15 386, minibüs 7 908, raylı 254, vapur 98, dolmuş 44.
- Her tabloya kaynak ve `data_as_of` bilgisi.
- Araçlar (bitti): `geocode_place`, `find_nearby_stops`, `get_lines_at_stop`, `get_line_route` — tasarım ve ölçümler karar 0008. Yer araması kümeler (aynı adlı ve 300 m içindeki noktalar tek yer), mod adı eşanlamlı kabul eder, hat sorguları hat koduna göre toplanır. `get_next_departures` (GTFS kısmı) Faz 1'de bitmişti.
- **Metro kapsam analizi** (bitti): 2020 GTFS beslemesinin bugünkü ağa oranı hat hat ölçüldü — 8 hat tam, 8 kısmen, 2 yok (F4, T5). 248 istasyonun 187'si eşleşti, 49'u gerçekten eksik, 11'i (T3) güvenilmez. İstasyon adı normalize kuralı `store/station_names.py`, eşleme `datasets/station_name_map.csv`, hat bütünlük skorları `datasets/metro_line_integrity.csv`.
- **`get_rail_travel_time`**: GTFS `stop_times` üzerinden aynı hat; hat değişiminde aktarma veri setlerinden aktarma noktası + sabit aktarma süresi; GTFS'te olmayan istasyonlarda koordinat mesafesi / hat tipinin ortalama hızı ile tahmin (`estimated: true`). Tahmin yöntemi GTFS'te bulunan hatlarda doğrulanır: ortalama ve maksimum hata raporlanır.
- **`get_next_departures` (metro)** (bitti): ilk/son sefer, saat dilimine göre aralık, hafta içi/sonu farkı. Kaynak hat bazında seçilir (`datasets/metro_schedule_source.csv`); `datasets/metro_service_hours.yaml` varsa GTFS'in önüne geçer, yoksa `is_stale: true` döner. Metro duyurularında ilgili hat için tarife değişikliği varsa cevaba eklenir. Aralık aynı saat içindeki boşlukların ortancasından ölçülür; gece servisi ayrı segment; ilk/son sefer istasyon bazında da verilir; 4 seferden az veriden tarife üretilmez (Marmaray). Karar 0005.
- Her iki araç için fixture tabanlı birim testler ve en az 10 elle doğrulanmış örnek sorgu.
- Basit ReAct agent ile ilk uçtan uca deneme (bitti): `transit_server` MCP sunucusu iki aracı stdio üzerinden sunuyor, LangGraph ReAct agent'ı `langchain-mcp-adapters` ile bağlanıyor, arayüz `uv run python -m istanbul_agent.cli "soru"`. İstasyon adı eşleştirmesi dört adım + bulunamazsa en yakın 3 aday. Karar 0006.

**Bitti tanımı:** "Kadıköy iskelesine en yakın otobüs durakları ve oradan geçen hatlar" gibi 5 sorunun en az 4'ü doğru araçlarla doğru cevaplanıyor; ingest tek komutla baştan çalıştırılabiliyor; `get_rail_travel_time` tahmin hatası ölçülmüş ve raporlanmış; her iki metro aracı eski veriyi `is_stale` ile işaretliyor.

**Sonuç (10 Kasım 2026): bitti.** Kabul sorularının **5/5**'inde doğru araç seçildi (yerel model `qwen3.5:4b`, ölçüm karar 0008). Araç sayısı 6; `uv run python -m istanbul_agent.store.ingest` beslemeyi baştan kuruyor (~8 dk); 419 birim testi geçiyor. Faz 1'de açığa çıkan üç araç kusuru düzeltildi: geçersiz mod boş sonuç döndürüyordu, ön ek birkaç adaya uyduğunda reddediyordu, benzerlik eşitliğini keyfî bozuyordu.

### Faz 2 — Canlı veri araçları ✅ bitti (11 Kasım 2026), canlı kaynakta doğrulandı

**Doğrulama (karar 0013):** araçlar yazıldıktan sonra her canlı kaynak gerçekten
çağrıldı (`scripts/probe_live_freshness.py`). Hepsi ayakta, gecikme 2-3 dakika.
Ama dört **sessiz yanlış** çıktı ve düzeltildi: İSPARK'ın `empty: 0` değeri
"%100 dolu" diye sunuluyordu (oysa "bildirmiyor" olabilir, 51/239 otopark),
otobüs tarafında 04:00 öncesi kalkışlar günün ilk seferi sayılıyordu (500T:
"00:00" yerine 05:40), İETT duyurularının tarihsiz olduğu söylenmiyordu, metro
servis durumunun 26 günlük yaşı gizliydi ve duyuru kırpması servis durumunu
listeden düşürüyordu. Test sayısı 639 → 662.

**Faz 3-5'ten sonra yapıldı** (kullanıcı kararı; RAG canlı veriye bağlı değildi). Canlı araç konumu askıda kaldı. Sonuç — araç sayısı 7 → **11**:

| Araç | Kaynak | Durum |
|---|---|---|
| `get_service_alerts` | İETT `GetDuyurular_json` + Metro duyuru/servis durumu | ✅ |
| `get_station_accessibility` | Metro `GetStations.DetailInfo` + `GetFaultyEquipmentDetails` | ✅ |
| `get_next_departures` (otobüs) | İETT `GetPlanlananSeferSaati_json` | ✅ |
| `find_parking` | İSPARK `Park` | ✅ |
| `get_traffic_status` | Trafik indeksi (548) | ✅ |
| `get_air_quality` | `GetAQIByStationId` | ❌ **yazılmadı: kaynakta veri yok** — 721 kaydın, 12 istasyonun 12'sinde `AQI: null` (karar 0012/3) |
| `get_vehicle_positions` | İETT `GetHatOtoKonum_json` | ⏸ askıda (plan kararı) |
| `find_mobility_options` | bisiklet/şarj katmanları | ⏸ yapılmadı (düşük öncelik) |
- İki API istemcisi (bitti): İETT için **elle kurulan SOAP zarfı** (`zeep` değil — ölçüm: iki servisten birini hiç açamıyor, açtığında iki kat yavaş; karar 0012/1), Metro ve şehir kaynakları için REST. Ortak katman `clients/base.py`: zaman aşımı, geçici hatada üstel geri çekilmeli yeniden deneme (4xx denenmez), kaynak bazlı TTL önbellek (otopark 60 s, tarife 6 saat).
  - **Risk:** `zeep` dört İETT WSDL'ini açamıyor (350, 349, 172, 171) — `AuthHeader` bildirilmiş ama eşleşen `<message>` tanımı yok. Faz 0'da elle kurulan SOAP zarfı çalıştı. İstemci yöntemi ölçümle seçilecek; karar `documents/decisions/` altına yazılacak.
  - **Metro REST istemcisi sağlık kontrolünü HTTP durumuna değil `Success` alanına dayandırır:** API başarısızlıkta da HTTP 200 + `Success: false` dönüyor.
- `transit_server` canlı araçları: `get_next_departures` (canlı/otobüs kısmı), `get_service_alerts`, `get_station_accessibility`. `get_vehicle_positions` askıda (bkz. araç tasarımı bölümü).
- `city_server` araçları: `find_parking`, `get_traffic_status`, `get_air_quality`. `find_mobility_options` düşük öncelikli, en sona.

**Bitti tanımı:** Her canlı araç fixture tabanlı birim testlerinden geçiyor; API kapalıyken araç anlamlı bir hata mesajı dönüyor; agent 10 canlı veri sorusunun en az 8'inde doğru aracı seçiyor.

**Sonuç: bitti.** Canlı kaynaklar ağa çıkmadan, Faz 0 fixture'larıyla test ediliyor (639 test). Kaynağa ulaşılamadığında araç `canli_veri_yok` dönüyor — boş liste değil. Agent üzerinden koşulan 13 canlı/değişen senaryonun **12'si** geçti; başarısız olan `refuse-08` ("arabayla mı metroyla mı") trafik aracını hiç çağırmadı. Ayrıntılar: `documents/decisions/0012-canli-veri-araclari.md`.

### Faz 3 — RAG hattı ve `search_knowledge` ✅ bitti (10 Kasım 2026)
- Kaynaklar (bitti): Metro SSS (509, yalnızca TR kayıtlar), güncel bilet fiyatı (canlı API), bilet fiyatı 2017-2022 (541), 34 Dakika indeks belgesi (445 ve 442 **aynı dosya**, bir kez indekslendi), UAP raporu (496), UAP anket formları 2006/2012 (435), 565 veri seti açıklaması. İETT SOAP kullanım dokümanı kapsam dışı (depoda yok).
- Başlık bazlı chunking (tablolar ve soru-cevaplar bölünmez), BGE-M3 (yoğun + seyrek tek geçişte), Qdrant yerel mod, hibrit arama (RRF) + isteğe bağlı BGE reranker. Gürültü filtresi tür bazlı; tazelik sırası soruya bakıyor (karar 0009).
- `eval/rag_questions.jsonl`: **62 soru**, beklenen kaynak ve anahtar kelimeler (terim ağırlıklı 8 soru dahil: servis adı, hat kodu, kısaltma).

**Bitti tanımı:** RAG değerlendirmesi çalışıyor, metrikler `eval/results/` altında; reranker'lı ve reranker'sız karşılaştırma yapılmış.

**Sonuç:** 8 kaynak indirildi ve ölçüldü, **853 parça** indekslendi (Qdrant yerel mod, BGE-M3 yoğun + seyrek). `eval/rag_questions.jsonl` **62 soru**; getirme metrikleri `eval/results/rag_eval.json`:

| Kip | recall@1 | recall@3 | recall@5 | MRR | gecikme |
|---|---|---|---|---|---|
| dense | 0.968 | **1.000** | **1.000** | 0.978 | 0.27 s |
| dense + rerank | 0.968 | 0.984 | 0.984 | 0.976 | 7.75 s |
| hybrid | 0.952 | 0.984 | 1.000 | 0.968 | 0.30 s |
| hybrid + rerank | 0.968 | 0.984 | 0.984 | 0.973 | 7.83 s |
| sparse | 0.919 | 0.952 | 0.968 | 0.940 | 0.30 s |

**Reranker bu korpusta kazanç sağlamıyor:** recall@1'i düzeltiyor ama recall@5'i düşürüyor ve gecikmeyi 26 katına çıkarıyor. Varsayılan cihaza göre (CUDA'da açık, CPU'da kapalı); kod korpus büyüdüğünde yeniden ölçülmek üzere duruyor. Kararlar ve ölçümler: `documents/decisions/0009-rag-hatti.md`. RAGAS (cevap sadakati) LLM kotası nedeniyle Faz 4-5'e bırakıldı.

### Faz 4 — Entegrasyon
- **İki** MCP sunucusu aynı agent'a bağlı: `transit` (6 araç) + `knowledge` (1 araç). Plan "üç sunucu" diyordu; `city_server` Faz 2 ertelendiği için henüz yok, grafikteki kayıtta satırı yorum olarak bekliyor. Oturumlar agent çalıştığı sürece açık tutulur — `get_tools()` her çağrıda yeni oturum açtığı için gömme modeli her aramada yeniden yüklenirdi (karar 0010/1).
- **Ağır model sunucu açılışında yüklenir.** BGE-M3'ü FastMCP'nin iş parçacığı havuzunda yüklemek sunucuyu kilitliyordu: boş sorgu 0.0 s, gerçek sorgu 180 s+ ve `Connection closed`. `warm_up()` sonrası 3.2 s (karar 0010/2).
- Sistem prompt'u kuralları: kaynağı **araç çıktısından kopyala** (araç çağırmadıysan kaynak yazma); canlı/güncel veri eskiyle çelişirse günceli esas al; `is_stale`/`estimated` bayraklarını cevaba taşı; elinde araç olmayan konuda (otopark, trafik, hava kalitesi, canlı konum, vapur tarifesi, erişilebilirlik, nokta-nokta rota) bilginin yokluğunu söyle ve elindekini ver.
- **Kaynaksız cevap koruması grafikte:** araç çağrılmayan tur bir kez geri çevrilir, ikinci turda da çağrılmazsa cevap `unverified` işaretlenir. Prompt'a "kaynak göster" yazmak tek başına zarar verdi — model kaynağı da uydurdu (karar 0010/3).
- Çok adımlı senaryolar ve araç yokluğu senaryoları `eval/agent_scenarios.jsonl` içinde ("Tekerlekli sandalyeyle Kadıköy'den Beşiktaş'a", "Şu an Taksim'e arabayla mı metroyla mı", "Üsküdar'dan sıradaki vapur" — üçü de Faz 2 araçları olmadığı için **yokluğu söylemesi** beklenen senaryolar).

**Bitti tanımı:** `eval/agent_scenarios.jsonl` içindeki en az 40 senaryo otomatik çalışıyor; beklenen araçlar ve sıra kontrol ediliyor.

**Sonuç (10 Kasım 2026): bitti.** **49 senaryo** otomatik çalışıyor; araç, sıra ve cevaptaki ifade kontrol ediliyor. Yerel modelle (`qwen3.5:4b`, `num_ctx=16384`) ölçüm:

| Oran | Sonuç |
|---|---|
| geçen senaryo | 41/49 (%83.7) |
| araç doğruluğu | %87.8 |
| sıra doğruluğu | %50 (2 senaryoda) |
| ifade doğruluğu | %91.3 (23 senaryoda) |
| kaynaksız cevap | 3 (uyarı olmasa 8) |
| gecikme ortanca | 44 s |

Başarısız 8 senaryo üç gruba düşüyor: araca bakmadan "bende yok" demek (3), yanlış araç ya da adım sınırına çarpmak (3), doğru aracı çağırıp yokluğu söylememek (2). Ayrıntılar ve ölçümün kendi kusuru: `documents/decisions/0010-entegrasyon.md`. Sonuçlar `eval/results/agent_eval.json`.

### Faz 5 — Değerlendirme, paketleme, demo ✅ bitti (11 Kasım 2026)
- Gemini Flash vs yerel `qwen3.5:4b` (bitti): karşılaştırma **ortak senaryolar** üzerinden yapılıyor (`eval/compare_providers.py`), çünkü Gemini kotası 49 senaryoya yetmiyor. Ortak 6 senaryoda Gemini 6/6, yerel model 3/6; adım 10 vs 17, gecikme 25.9 s vs 53.1 s. **Yönlendirici deneyi yapılmadı** — yerel modelin kaybı araç sayısından değil bağlam penceresi, araç çıktısını yorumlama ve çok adımlı soruda döngüye girmeden geliyor (karar 0011).
- Gradio demo (bitti): cevap + **çağrılan araçlar tablosu** + araç çıktısındaki koordinatların haritası. FastAPI (bitti): `/ask`, `/health`. Dockerfile yazıldı ama **derlenemedi** (bu makinede Docker daemon kapalı).
- README (bitti): mimari diyagram, kurulum adımları, ölçüm tabloları, iki önce/sonra tablosu, veri tazeliği tablosu ve bilinen sınırlamalar.
- Ek: araçlar depo kurulmamışken ham `IOException` yerine ne çalıştırılacağını söylüyor (karar 0011/3).

**Bitti tanımı:** Yeni bir makinede README'yi izleyen biri projeyi çalıştırabiliyor; README'de en az iki "önce/sonra" tablosu var.

**Sonuç:** README'deki her komutun hedefi var (betik/modül/dosya olarak doğrulandı); kurulum adımı atlanırsa araçlar ne çalıştırılacağını söylüyor; `/health` eksik veriyi raporluyor. İki önce/sonra tablosu: kaynaksız cevap koruması ve bağlam penceresi. **Docker yolu doğrulanmadı.**

### Faz 6 (opsiyonel) — Rota motoru
- GTFS üzerinden gerçek A→B toplu taşıma rotası: `r5py` veya OpenTripPlanner, `plan_trip` aracı olarak.
- Not: Dolmuş/vapur GTFS'i eski olduğu için rota sonuçlarında bu modlar uyarıyla gösterilir.

## Değerlendirme

| Alan | Ölçüm | Araç |
|---|---|---|
| RAG | Doğru bağlamı getirme, bağlama sadakat, cevap doğruluğu | RAGAS |
| Agent | Doğru araç, doğru parametre, doğru sıra, adım sayısı | `eval/run_agent_eval.py` |
| Veri | Eski veriyi uyarıyla sunma oranı | Senaryo bazlı kontrol |
| Sistem | Gecikme, LLM çağrı sayısı, API hata oranı | Betik içi loglama |

Kota notu: RAGAS ve agent değerlendirmesi ücretsiz kotayı tüketir. Tam değerlendirmeyi günde en fazla 1-2 kez çalıştır, yanıtları `eval/.cache/` altında sakla.

## Bilinçli olarak açık bırakılan kararlar

Ölçülerek verilecek; karar verildiğinde `documents/decisions/` altına yaz:
- Aktarma süresi sabiti (`get_rail_travel_time`, hat değişiminde eklenen süre)
- `datasets/metro_service_hours.yaml` kapsamı: hangi hatlar elle güncellenecek, hangi alanlar
- İETT SOAP istemcisi: `zeep` yamalanabilir mi, yoksa elle SOAP zarfı mı
- Geocoding yöntemi (yalnızca yerel durak adları mı, başka bir kaynak mı)
- Önbellek TTL değerleri (kaynak bazında)
- ~~Chunk boyutu ve örtüşme~~ → karar 0009: 1600/2000 karakter hedef, 200 karakter örtüşme; `qa` ve `table` bölünmez. Ölçülen token ortancası 105 (çoğu parça atomik soru-cevap).
- ~~Sparse arama yöntemi~~ → karar 0009: BGE-M3'ün seyrek çıktısı (ikinci model gerekmiyor). Ölçümde yoğun arama tek başına hibritten iyi çıktı; hibrit risk kararı olarak varsayılan kaldı.
- ~~Reranker aday sayısı~~ → karar 0009: GPU'da 12, CPU'da 6 aday; CPU'da 10 s'yi aştığı için varsayılan kapalı. Döndürülen sonuç 4 (araç varsayılanı).
- Tek ReAct döngüsü mü, planlayıcı düğüm mü
- 4 GB VRAM'e sığan en iyi yerel model (`qwen3.5:4b`, `gemma4:e4b` vb.)

## Kapsam dışı (şimdilik)

- `data-inventory.md` D grubu ve işaretsiz veri setleri
- Kullanıcı hesapları, kalıcı sohbet geçmişi
- Bulut deploy
- Model fine-tuning
