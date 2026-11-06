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
- İETT GTFS: güncel (Mart 2026)
- "Public Transport GTFS" (minibüs, dolmuş, vapur, Marmaray, eski metro): 2018-2020 tarihli ve portal notuna göre güncellenmeyecek. Bu kaynaktan gelen saatler için `is_stale: true` döner ve agent cevabında bunu belirtir.
- İsbike servisleri portalda kapalı; erişilemezse araç bunu açıkça söyler, tahmin üretmez.

### Araç tasarımı (hedef ~15 araç)

Uç noktalar alan bazlı araçların arkasında birleştirilir; agent ham API'leri görmez. Başlangıç önerisi (Faz 0'da gerçek yanıtlar görüldükten sonra revize edilir):

| Araç | Sunucu | Kaynaklar |
|---|---|---|
| `geocode_place` | transit | Durak/istasyon/iskele adları (yerel), gerekirse Şehir Haritası API |
| `find_nearby_stops` | transit | GTFS stops + raylı sistem istasyonları + iskeleler + dolmuş/minibüs durakları |
| `get_lines_at_stop` | transit | GTFS routes/trips, İETT hat-durak servisi |
| `get_line_route` | transit | GTFS + İETT hat-durak-güzergâh, Metro hat/istasyon listeleri |
| `get_next_departures` | transit | GTFS stop_times, İETT planlanan sefer saati, Metro sefer saatleri |
| `get_rail_travel_time` | transit | Metro istasyonlar arası süreler |
| `get_vehicle_positions` | transit | İETT konum metotları (**önce varlığı doğrulanacak**) |
| `get_service_alerts` | transit | İETT duyuruları, Metro duyuruları ve servis durumu |
| `get_station_accessibility` | transit | Metro arızalı ekipman, asansör/yürüyen merdiven verileri |
| `find_parking` | city | İSPARK liste + detay, otopark kapasite verileri |
| `get_traffic_status` | city | Trafik indeksi, UYM trafik duyuruları |
| `get_road_works` | city | Yol bakım çalışmaları |
| `get_air_quality` | city | Hava kalitesi istasyonları + ölçümleri |
| `find_mobility_options` | city | Bisiklet yolları/park alanları, İsbike, şarj istasyonları |
| `search_knowledge` | knowledge | RAG |

Tarihsel istatistik veri setleri (saatlik trafik, yolcu sayıları) araç değil; agent'a bağlam olarak gerekmedikçe kullanılmaz, istenirse Faz 6'da analiz aracı olarak eklenir.

## Aşamalar

### Faz 0 — Keşif ve iskelet
- Proje iskeleti: `uv`, klasör yapısı, ruff, pytest, `.env.example`, `llm.py` sağlayıcı fabrikası.
- `datasets/data-inventory.md` içindeki A ve A2 grubundaki **her** uç noktaya gerçek istek at: çalışıyor mu, kimlik doğrulama gerekiyor mu, yanıt formatı ne? Örnek yanıtları `tests/fixtures/` altına kaydet.
- İETT Web Servis Kullanım Dokümanı V1.5'teki metot listesini çıkar; özellikle araç konumu veren metot var mı doğrula.
- Bulguları `documents/decisions/0001-veri-kaynaklari.md` içine yaz; çalışmayan uç noktaları envanterde işaretle ve araç tablosunu revize et.

**Bitti tanımı:** Tüm A/A2 uç noktalarının durumu (çalışıyor / kapalı / değişmiş) belgelenmiş; `llm.py` ile Gemini ve Ollama'ya istek atılabiliyor; araç tablosu son haline getirilmiş.

### Faz 1 — Yerel veri deposu ve statik toplu taşıma araçları
- `ingest` betikleri: GTFS (İETT + genel), B grubundaki GeoJSON'lar → DuckDB (`data/transit.duckdb`), spatial eklentisi ile.
- Her tabloya kaynak ve `data_as_of` bilgisi.
- Araçlar: `geocode_place`, `find_nearby_stops`, `get_lines_at_stop`, `get_line_route`, `get_next_departures` (yalnızca GTFS kısmı).
- Basit ReAct agent ile ilk uçtan uca deneme.

**Bitti tanımı:** "Kadıköy iskelesine en yakın otobüs durakları ve oradan geçen hatlar" gibi 5 sorunun en az 4'ü doğru araçlarla doğru cevaplanıyor; ingest tek komutla baştan çalıştırılabiliyor.

### Faz 2 — Canlı veri araçları
- İki API istemcisi: İETT için SOAP (`zeep`), Metro ve diğerleri için REST (`httpx`). Ortak katman: zaman aşımı, yeniden deneme, kaynak bazlı TTL önbellek.
- `transit_server` canlı araçları: `get_next_departures` (canlı kısım), `get_rail_travel_time`, `get_service_alerts`, `get_station_accessibility`, doğrulandıysa `get_vehicle_positions`.
- `city_server` araçlarının tamamı.

**Bitti tanımı:** Her canlı araç fixture tabanlı birim testlerinden geçiyor; API kapalıyken araç anlamlı bir hata mesajı dönüyor; agent 10 canlı veri sorusunun en az 8'inde doğru aracı seçiyor.

### Faz 3 — RAG hattı ve `search_knowledge`
- Kaynaklar: C grubu (Metro SSS, bilet fiyatları, 34 Dakika metodolojileri, Ulaşım Ana Planı raporları), API kullanım dokümanları (PDF), tüm veri seti açıklamaları (`ibb_datasets.csv` + JSON'daki `notes`).
- Başlık bazlı chunking (tablolar bölünmez), BGE-M3, Qdrant yerel mod, hibrit arama + BGE reranker.
- `eval/rag_questions.jsonl`: en az 50 soru, beklenen kaynak ve referans cevap.

**Bitti tanımı:** RAG değerlendirmesi çalışıyor, metrikler `eval/results/` altında; reranker'lı ve reranker'sız karşılaştırma yapılmış.

### Faz 4 — Entegrasyon
- Üç MCP sunucusu aynı agent'a bağlı.
- Sistem prompt'u kuralları: kaynak göster; canlı veri statik veriyle çelişirse canlıyı esas al; `is_stale` verilerde bunu söyle; rota hesaplaması yapamadığında bunu açıkça belirt ve alternatif olarak ilgili durak/hat/saat bilgisini ver.
- Çok adımlı senaryolar: "Tekerlekli sandalyeyle Kadıköy'den Beşiktaş'a", "Şu an Taksim'e arabayla mı metroyla mı", "Üsküdar'dan sıradaki vapur".

**Bitti tanımı:** `eval/agent_scenarios.jsonl` içindeki en az 40 senaryo otomatik çalışıyor; beklenen araçlar ve sıra kontrol ediliyor.

### Faz 5 — Değerlendirme, paketleme, demo
- Gemini Flash vs yerel `qwen3.5:4b`: araç seçimi doğruluğu, cevap kalitesi, adım sayısı, gecikme. Yerel model 15 araçta zorlanırsa araç sayısını azaltan bir "yönlendirici" deneyi yap ve sonucu raporla.
- Gradio demo (harita üzerinde durak/otopark gösterimi artı puan), FastAPI uç noktası, Dockerfile.
- README: mimari diyagram, kurulum, metrik tabloları, veri tazeliği ve bilinen sınırlamalar.

**Bitti tanımı:** Yeni bir makinede README'yi izleyen biri projeyi çalıştırabiliyor; README'de en az iki "önce/sonra" tablosu var.

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
- Araç sayısı ve gruplaması (15 araç mı, daha az mı)
- Geocoding yöntemi (yalnızca yerel durak adları mı, Şehir Haritası API mi)
- Önbellek TTL değerleri (kaynak bazında)
- Chunk boyutu ve örtüşme (300-600 token aralığında başla)
- Sparse arama yöntemi (BGE-M3 sparse mi, BM25 mi)
- Reranker aday sayısı ve döndürülen sonuç sayısı
- Tek ReAct döngüsü mü, planlayıcı düğüm mü
- 4 GB VRAM'e sığan en iyi yerel model (`qwen3.5:4b`, `gemma4:e4b` vb.)

## Kapsam dışı (şimdilik)

- `data-inventory.md` D grubu ve işaretsiz veri setleri
- Kullanıcı hesapları, kalıcı sohbet geçmişi
- Bulut deploy
- Model fine-tuning
