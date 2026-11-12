# İstanbul Ulaşım Agent'ı

İBB Açık Veri Portalı'ndaki ulaşım verilerini **MCP araçları** olarak sunan
ve bunları bir **LangGraph** agent'ı ile birleştiren portföy projesi.
"Kadıköy'den Taksim'e metroyla kaç dakika?" gibi soruları birden fazla aracı
çağırarak, **kaynak göstererek** ve **verinin tazeliğini söyleyerek**
cevaplıyor.

Projenin asıl meselesi doğru cevap vermek değil, **cevabın nereden geldiğini
ve ne kadar güvenilir olduğunu söylemek.** Veri kaynaklarının bir kısmı 2020
tarihli, bir kısmı elle doğrulanmış, bir kısmı tahmin — araçlar bunu alan
alan işaretliyor, agent da cevaba taşıyor. Araç çağrılmadan verilen bir
cevap **kaynaksız** olarak işaretlenir.

```
Kullanıcı sorusu
      │
LangGraph ReAct agent  (LLM: Gemini 3.5 Flash | yerel qwen3.5:4b)
      │   ├─ araç çağrılmadıysa bir kez uyarır, yine çağrılmazsa
      │   │  cevabı "kaynaksız" işaretler
      │
      ├── MCP: transit_server (stdio) ─── 8 araç
      │      ├── DuckDB: GTFS + GeoJSON (otobüs, raylı sistem, durak/iskele)
      │      └── canlı: İETT SOAP (duyuru, sefer saati), Metro REST
      │               (duyuru, servis durumu, asansör/merdiven arızası)
      │
      ├── MCP: knowledge_server (stdio) ── 1 araç
      │      └── Qdrant (yerel mod) + BGE-M3: SSS, bilet fiyatı, raporlar,
      │          565 veri seti açıklaması
      │
      └── MCP: city_server (stdio) ────── 2 araç
             └── İSPARK (otopark doluluğu), trafik indeksi
```

## Hızlı kurulum

Gereken: Python 3.11+, [uv](https://docs.astral.sh/uv/), ~4 GB disk
(gömme modeli + veri). Yerel LLM isteğe bağlı
([Ollama](https://ollama.com), `qwen3.5:4b`).

```bash
# 1. Bağımlılıklar
uv sync --extra store --extra rag --extra agent --extra app

# 2. Ayarlar
cp .env.example .env
# Gemini kullanacaksan GEMINI_API_KEY'i doldur (ücretsiz katman: günde 20 istek).
# Yalnızca yerel model kullanacaksan LLM_PROVIDER=ollama yaz ve şunu çalıştır:
#   ollama pull qwen3.5:4b

# 3. Veriyi indir (~35 MB GTFS + ~40 MB metin kaynakları)
uv run python scripts/download_gtfs.py --feed iett
uv run python scripts/download_stop_geojson.py
uv run python scripts/download_rail_reference.py
uv run python scripts/download_rail_geojson.py
uv run python scripts/download_knowledge.py

# 4. Yerel depoyu kur (DuckDB)
uv run python -m istanbul_agent.store.ingest_rail
uv run python -m istanbul_agent.store.ingest_timetable
uv run python -m istanbul_agent.store.ingest          # ~6 dk (6.1 M satır akıtılıyor)

# 5. Metin indeksini kur (Qdrant + BGE-M3; model ilk çağrıda ~2.3 GB indirilir)
uv run python -m istanbul_agent.rag.ingest            # ~6 dk (CPU)

# 6. Sor
uv run python -m istanbul_agent.cli "Kadıköy'den Taksim'e metroyla kaç dakika?"
```

Demo arayüzü ve HTTP servisi:

```bash
uv run python -m istanbul_agent.app                   # Gradio, http://127.0.0.1:7860
uv run uvicorn istanbul_agent.api:app --reload        # FastAPI, /docs ve /health
```

`GET /health` verinin kurulu olup olmadığını söyler — ayakta olup altında
veri olmayan bir servis "sağlıklı" görünmez:

```json
{"ok": true, "duckdb": true, "qdrant": true, "provider": "gemini", "notes": []}
```

> **Qdrant yerel modu dizini tek sürece kilitler.** `rag.ingest` çalışırken
> arayüz/servis açık olmamalı, çünkü ikisi de `knowledge_server`'ı alt süreç
> olarak başlatıyor.

### Docker

İmaj veriyi içermez: `data/` birim olarak bağlanır, gömme modeli de ayrı
bir hacme iner. Adımların tamamı `Dockerfile` başındaki yorumda.

**Derlendi ve çalıştırıldı** (12 Kasım 2026, karar 0016): soğuk build
1145 s, `docker image inspect` 6.78 GB (containerd deposunun disk ayak izi
20.4 GB, ayrıca build cache 20.4 GB — `docker builder prune` ile geri
alınır). Veri bağlanmadan konteyner ayağa kalkıyor ve `/health` iki eksiği
çalıştırılacak komutla söylüyor; `data/` bağlandığında `ok:true`.
Konteyner içinden gerçek bir soru (`/ask`, ollama): doğru aracı çağırdı,
13 dakikalık rotayı aktarmalarıyla verdi ve tazelik uyarısını cevaba
taşıdı.

**Windows'ta üç tuzak, hepsi ölçümle çıktı:**

1. `-v "$PWD/data:/app/data"` **Git Bash'te sessizce boş dizin bağlar**
   (yol dönüşümü). PowerShell'den tam yol kullan:
   ```powershell
   docker run -d -p 8000:8000 --env-file .env `
     -e LLM_PROVIDER=ollama -e OLLAMA_BASE_URL=http://host.docker.internal:11434 `
     -v "C:\Users\<kullanici>\...\IBB PROJECT\data:/app/data" `
     -v huggingface:/root/.cache/huggingface istanbul-agent
   ```
2. `.env`'i olduğu gibi vermek LLM adresini bozar: `OLLAMA_BASE_URL`
   içindeki `127.0.0.1` konteynerin kendisidir, `host.docker.internal` ile
   ezilmeli (yukarıdaki `-e`).
3. **Ollama'nın portunu kontrol et.** `OLLAMA_HOST` ayarlıysa varsayılan
   11434 olmayabilir (bu makinede 11435).

İlk `/ask` isteği BGE-M3'ü indirdiği için uzun sürer (ölçülen 505 s'nin
378 s'si indirme); `huggingface` hacmi bağlandığı sürece bir daha inmez.

**İmaj kodu içerir, bağlanan birim yalnızca veriyi.** Kod değiştiğinde imajı
yeniden derlemek gerekiyor; `data/` bağlı olduğu için veri yeniden
kurulmuyor. Derlemeden önce `docker builder prune` koşturmak iyi olur:
imaj + build cache bu makinede 40 GB'ı aşıp bir kez diski doldurdu ve
daemon'u kilitledi (karar 0016/3). Docker'ı yeniden başlatmak gerekirse
sıra **`wsl --shutdown` → Docker Desktop**; süreçleri öldürmek veri diskini
kilitli bırakıp `device or resource busy` hatası üretiyor.
İmajın ağırlığı neredeyse tamamen tek bağımlılık katmanından geliyor:
linux `torch` tekerlekleri varsayılan olarak CUDA paketlerini çekiyor,
oysa konteynerde GPU kullanılmıyor. CPU-only torch birkaç GB düşürürdü ama
`uv.lock`'u yeniden çözmek gerekiyor (karar 0016).

## Araçlar (12)

| Araç | Ne yapar | Kaynak |
|---|---|---|
| `geocode_place` | ad → konum (durak, istasyon, iskele) | `transit_places` (23 690 nokta) |
| `find_nearby_stops` | bir yerin çevresindeki duraklar | aynı tablo, haversine |
| `get_lines_at_stop` | duraktan geçen hatlar | İETT GTFS + raylı sistem tabloları |
| `get_line_route` | hattın sıralı durakları, ilk/son kalkış | `bus_route_stops`, `bus_trips` |
| `get_rail_travel_time` | iki istasyon arası süre, aktarmalı güzergâh | GTFS `stop_times` + 312/313 referans tabloları |
| `get_next_departures` | metro/Marmaray ilk/son sefer, sefer aralığı | elle doğrulanmış saatler + GTFS + canlı duyurular |
| `search_knowledge` | kural, ücret, tanım, veri seti açıklaması | Qdrant + BGE-M3 (853 parça) |
| `get_service_alerts` | canlı sefer duyuruları, iptaller | İETT SOAP + Metro İstanbul REST |
| `get_station_accessibility` | asansör/merdiven sayısı ve **anlık arızalar** | Metro İstanbul REST |
| `find_parking` | otopark ve **anlık boş kapasite** | İSPARK REST |
| `get_traffic_status` | trafik indeksi ve eğilimi | İBB trafik indeksi |
| `find_mobility_options` | bisiklet/scooter parkı, şarj istasyonu, bisiklet yolu | 16 (1300 nokta), 275+276 (2933 istasyon, **2025-05, eski**), 1 (477 yol kaydı; 212'si henüz yapılmamış) |

Her araç çıktısı `source` ve `data_as_of` taşır. Her aracın açıklamasında
**ne zaman kullanılmaması gerektiği** yazılı; araç seçimi bu metne dayandığı
için açıklamayı değiştirmek davranış değişikliğidir ve değerlendirme
yeniden koşulur.

## Yerel depo

| Tablo | Satır | İçerik |
|---|---|---|
| `bus_trips` | 135 625 | sefer başına ilk kalkış, son varış, süre |
| `bus_route_stops` | 129 238 | hat + yön başına sıralı durak listesi |
| `bus_stops` | 15 386 | İETT durakları, koordinatları onarılmış |
| `bus_routes` | 9 274 | hat varyantları (1 095 hat kodu) |
| `transit_places` | 23 690 | beş modda aranabilir nokta tablosu |
| `rail_stations` / `rail_segments` | 312 / 285 | raylı sistem istasyonları ve ardışık süreler |
| `rail_headways` / `rail_service_windows` | 2 074 / 1 668 | sefer aralıkları ve servis pencereleri |
| `rail_interchanges` / `rail_transfers` | 158 / 96 | koordinattan kurulan aktarmalar |

Metin indeksi: **853 parça**, 8 kaynak — Metro SSS (23 TR kayıt), güncel
bilet fiyatı, 2017-2022 fiyat tarihçesi, 34 Dakika indeks belgesi, Ulaşım
Ana Planı raporu (168 sayfa) ve iki anket formu, portaldaki **565 veri seti
açıklaması**.

## Ölçümler

### Metin getirme (62 soru, LLM kullanılmadan)

| Kip | recall@1 | recall@3 | recall@5 | MRR | gecikme |
|---|---|---|---|---|---|
| **dense** | **0.968** | **1.000** | **1.000** | **0.978** | **0.27 s** |
| dense + reranker | 0.968 | 0.984 | 0.984 | 0.976 | 7.75 s |
| hybrid (RRF) | 0.952 | 0.984 | 1.000 | 0.968 | 0.30 s |
| hybrid + reranker | 0.968 | 0.984 | 0.984 | 0.973 | 7.83 s |
| sparse | 0.919 | 0.952 | 0.968 | 0.940 | 0.30 s |

Reranker bu korpusta **kazanç sağlamıyor**: recall@1'i düzeltiyor ama
recall@5'i düşürüyor ve gecikmeyi 26 katına çıkarıyor. CPU'da varsayılan
kapalı, CUDA varsa açık (karar 0009).

### Agent senaryoları (54 senaryo)

| Ölçüt | yerel `qwen3.5:4b` |
|---|---|
| kapsam | 54 senaryo, hepsi taze |
| geçen senaryo | **50/54 (%92.6)** |
| araç doğruluğu | %94.4 |
| sıra doğruluğu | %50.0 (2 senaryoda) |
| ifade doğruluğu | %92.3 |
| kaynaksız cevap | 1 |
| uyarılan tur | 9 |
| hata | 0 |
| gecikme (ortanca) | 37.2 s |

Kalan dört başarısızlık: `chain-05` ve `route-05` (doğru aracı çağırıp
çıktıyı cevap yerine **veri seti gibi tanıtıyor**), `near-04` (doğru ve
kaynaklı cevap, ama senaryonun şart koştuğu araçtan değil), `rail-04`
(sıfır araç, `unverified`). Ayrıntılar: `documents/decisions/0015-olu-mod-degerleri.md`.

Sağlayıcı karşılaştırması **ortak ve ölçülebilen** senaryolar üzerinden
yapılır (`eval/compare_providers.py`). Gemini ücretsiz katmanı günde 20
istek kabul ettiği için altı ortak senaryonun üçü `GoogleRateLimitError`
ile düştü; betik hata taşıyan satırı karşılaştırmadan **çıkarır** — kota
sınırını model başarısızlığı gibi göstermek yanıltma olurdu.

| Ölçüt | ollama | gemini |
|---|---|---|
| kapsam | 3 ortak senaryo | 3 ortak senaryo (3'ü kota hatasıyla dışarıda) |
| geçen senaryo | 3/3 | 3/3 |
| adım (toplam araç çağrısı) | 3 | 3 |
| gecikme (ortanca) | 44.5 s | **6.1 s** |

Bu kapsamda iki model aynı araçları seçiyor; ayrışan tek şey hız. Daha
geniş bir kıyas için Gemini kotasının yenilenmesi gerekiyor.

## Önce / sonra

Bu projedeki üç düzeltme, ölçümle önce-sonra olarak kayıtlı.

### 1. Kaynaksız cevap koruması

"Metroya bisikletle binebilir miyim?" sorusu, yerel modelle:

| | Cevap |
|---|---|
| **önce** | Araç çağrılmadı. *"Bisiklet dolabı… izin belgesi… yaklaşık 100-150 metre"* — hepsi uydurma. Prompt'a "kaynak göster" eklendiğinde altına *"Kaynak: Metro İstanbul SSS'ine göre"* yazdı; yani uydurma inandırıcı hale geldi. |
| **sonra** | `search_knowledge` çağrılıyor: *"Katlanabilir bisikletler işletme saatleri boyunca; standart bisikletler hafta içi 07.00-09.00 ve 17.00-20.00 dışında"* — SSS kaynaklı. Araç hâlâ çağrılmazsa cevap `unverified` işaretlenir ve CLI/API/arayüz uyarı gösterir. |

Senaryo setinde ölçülen etkisi: model 8 turda araç çağırmadan cevap verdi,
**5'i uyarıdan sonra aracı çağırdı**. Koruma olmasa kaynaksız cevap sayısı
3 değil 8 olurdu.

### 2. Bağlam penceresi araç yüzeyine bağlı

Araç sayısı 2'den 7'ye çıkınca yerel model Faz 1'de geçen senaryolarda bile
araç çağırmayı bıraktı. Ölçüm: sistem prompt'u 6 515 + yedi aracın şemaları
12 933 karakter = **~5 900 token**.

| `OLLAMA_NUM_CTX` | Araç sonuçlarına kalan | `rail-01` + `know-01` |
|---|---|---|
| **8192 (önce)** | ~2 300 token — 66 duraklı bir güzergâh tek başına aşıyor | **0/2** |
| **16384 (sonra)** | ~10 500 token | **2/2** |

### 3. Yokluk da bir araç çıktısına dayanmak zorunda

Üç senaryo **hiç araç çağırmadan** "bende yok" diyordu. Sebep uyarı
metninin son cümlesiydi: *"araçlardan hiçbiri bu soruyu cevaplayamıyorsa
'bu bilgi elimdeki kaynaklarda yok' de."* Model, aracı çağırmak yerine
**araç açıklamasını okuyup** o dalı seçiyordu.

| | `refuse-08` — "Şu an Taksim'e arabayla mı metroyla mı?" |
|---|---|
| **önce** | 0 araç çağrısı, `unverified`. *"Elindeki araçlardan sadece İstanbul genelini kapsayan bir trafik indeksi var, belirli bir güzergâhın…"* — doğru bir muhakeme, ama kaynaksız. |
| **sonra** | `get_traffic_status` çağrılıyor: *"Şu an İstanbul genelinde trafik akıcı durumda (indeks 2). Ancak bu veri sadece şehir genelidir."* |

| Ölçüt | önce | sonra |
|---|---|---|
| geçen senaryo | 47/54 (%87.0) | **50/54 (%92.6)** |
| araç doğruluğu | %88.9 | **%94.4** |
| kaynaksız cevap | 3 | **1** |

Düzelen üç senaryo (`route-04`, `refuse-08`, `rail-06`) tabanda sıfır araç
çağırıyordu; yeni düşen yok. **Ölçümün kendi dersi:** bu değişiklik ilk
turda iki prompt eklemesiyle **birlikte** yapıldı ve paket 45/54 verdi —
`refuse-08` hiç düzelmedi, iki senaryo düştü, biri uydurmaya başladı.
Değişikliği yalnız ölçmek kazancı ortaya çıkardı (karar 0015).

## Veri tazeliği

| Kaynak | Tarih | İşaret |
|---|---|---|
| İETT GTFS (otobüs) | 2026-04-21 | güncel |
| Durak/iskele nokta katmanları | 2026-03 … 2026-11 | güncel |
| Metro istasyon konumları (canlı API) | 2026-11-08 | güncel; 41 istasyonun konumu 2020 beslemesinden, `is_stale` |
| Elle doğrulanmış metro sefer saatleri | 2026-11 | `hours_is_stale: false` |
| Metro sefer aralıkları | 2020-07-09 | `headway_is_stale: true` |
| Raylı sistem süreleri (GTFS 308) | 2020-07-09 | `is_stale: true` |
| Güncel bilet fiyatı (canlı API) | çekildiği gün | güncel |
| Bilet fiyatı tarihçesi | 2022-06-10 | `is_stale: true` (soru geçmişi sormuyorsa güncel kaynak öne alınır) |

## Canlı verinin doğruluğu

Canlı kaynaklar gerçekten çağrılarak ölçüldü (`scripts/probe_live_freshness.py`,
11 Kasım 2026): hepsi ayakta, gecikme 2-3 dakika, metro arıza sayıları
çağrılar arasında değişiyor. Ölçüm dört **sessiz yanlış** ortaya çıkardı —
araç hata vermiyor, testleri geçiyor, ama doğru olmayan bir şey söylüyor —
ve dördü de düzeltildi (karar 0013):

| Kusur | Nasıl görünüyordu | Şimdi |
|---|---|---|
| İSPARK `empty: 0` | "%100 dolu" | `occupancy_known: false`, yüzde üretilmiyor |
| Otobüsün ilk seferi | 500T için "00:00" | `ilk=05:40`, `gece=00:00-00:20` |
| İETT duyurusunun yaşı | sessiz; alanda tarih yok | `recorded_day_known`, `from_earlier_day` |
| Metro servis durumu | "anlık" (26 gün önce güncellenmiş) | `age_days` + eskiye not |
| Duyuru kırpması | servis durumu listeden düşüyordu | önceliğe göre, kayıp nota yazılı |

Ortak kök hepsinde aynı: *bir değerin yokluğunu bir değer sanmak.* Fixture
tabanlı birim testler bunların hiçbirini yakalayamaz, çünkü dördü de kodun
kendi varsayımıyla tutarlıydı — biri testle **doğrulanmıştı** bile.

## Ölçüm

54 senaryo, yerel model (`qwen3.5:4b`), hepsi taze — 11 Kasım 2026:

| Ölçü | Değer |
|---|---|
| Geçen senaryo | **47/54 (%87.0)** |
| Araç doğruluğu | %88.9 |
| İfade doğruluğu | %92.3 |
| Kaynaksız cevap | 3 |
| Hata | 0 |
| Gecikme ortancası | 36.0 s |
| Tam koşu | 57.2 dk (seri) |

Düşen yedi senaryonun hepsi **araç seçimi**: model özel araç yerine `geocode_place` çağırıyor ya da hiç çağırmıyor. Karar 0013'ün araç alanları şema yüzeyini %9.6 büyüttü ve bu seçimi kaydırdı — iki senaryo düzeldi, üç tanesi düştü (net −1). Ayrıntılar: karar 0014.

## Bilinen sınırlamalar

- **Hava kalitesi verisi yok.** Uç nokta cevap veriyor, 28 istasyonu listeliyor,
  tarih parametresini kabul ediyor — ama ölçüm değerlerinin **tamamı boş**
  (12/12 istasyon, 721 kayıt, 0 dolu). Bu yüzden `get_air_quality` **yazılmadı**
  (karar 0012); istemci kodu duruyor, veri gelirse araç bir sarmalayıcı kadar
  uzakta.
- **Otobüsün ara durak saatleri yok**: hem GTFS beslemesi hem canlı servis yalnızca
  ilk duraktan kalkışı veriyor (kararlar 0007, 0012).
- **İSPARK otoparkların beşte birinde doluluk okunamıyor.** 239 açık otoparkın
  51'i 0 boş yer bildiriyor ve bu "dolu" değil "bildirmiyor" demek olabilir;
  kaynak ikisini ayırt etmiyor (karar 0013). Araç bu otoparklarda doluluk
  yüzdesi üretmiyor, bilinmediğini söylüyor.
- **İETT duyurularının hangi güne ait olduğu bilinemiyor**: kaynakta kayıt saati
  var, tarih yok. Saati şu andan ileride olan duyuru önceki bir güne aittir ama
  kaç gün öncesi olduğu belirsiz.
- **Canlı araç konumu askıya alındı:** uç nokta çalışıyor ama konum tek
  başına bir soruyu cevaplamıyor (durağa varış tahmini gerekir, o model
  projede yok).
- **Nokta-nokta rota planı yok** (hangi otobüse binip nerede inileceği);
  Faz 6'da `r5py`/OpenTripPlanner ile ele alınabilir.
- **Vapur/minibüs/dolmuş hat bilgisi yok**, yalnızca durak konumları var.
- **T3 tramvay hattının GTFS verisi reddedildi**: 10 durağının adları
  koordinatlara ters sırada atanmış (karar 0003). Konumlar canlı API'den
  alınıyor.
- **GTFS `calendar.csv`:** 308 beslemesinin tarih penceresi dolmuş, servis
  günleri yalnızca hafta günü desenine göre seçiliyor; `calendar_dates.csv`
  olmadığı için resmî tatiller bilinmiyor.
- **RAGAS (cevap sadakati) ölçülmedi:** LLM gerektiriyor ve ücretsiz kota
  günde 20 istek. Getirme metrikleri LLM'siz ölçüldü.
- **CUDA kurulmadı:** bütün ölçümler CPU'da. Kod cihazdan bağımsız; GPU'da
  gömme ve reranker ölçümleri yeniden koşulmalı.
- Metro İstanbul'un `GetTimeTable` (552) ve `GetStationBetweenTime` (513)
  uç noktaları **sunucu taraflı bozuk** (43/43 deneme istisna); bu veriler
  GTFS'ten geliyor.
- **Docker yalnızca bu makinede derlendi** (karar 0016): imaj kuruluyor,
  `/health`, `/ask` ve Gradio arayüzü (cevap + araç tablosu + harita)
  konteyner içinden doğrulandı, ama "yeni bir makinede çalışır" iddiası tek
  makinelik ölçüme dayanıyor. İngest konteyner içinde koşulmadı. İmaj GPU
  kullanmadığı hâlde CUDA'lı torch taşıyor ve bu ağırlık bir kez diski
  doldurup daemon'u kilitledi (karar 0016/3).
- **Yerel model araç çıktısını kullanıcıdan gelmiş gibi anlatıyor**
  (*"Verdiğiniz JSON verisi, İstanbul İETT'nin… GTFS formatında hazırlanmış
  olup"*) ve soruyu cevaplamayı bırakıp veri setini tanıtıyor. Kalan dört
  başarısızlığın **ikisi** bu (`chain-05`, `route-05`); ikisinde de kaynak
  aynı, `get_line_route(500T)` çıktısı. Bunu hedefleyen prompt kuralı
  yazıldı ama iki başka değişiklikle **paket halinde** ölçülünce net −2
  verdi ve geri alındı; tek başına ölçülmesi sırada bekliyor (karar 0015).
- **Gemini ile tam karşılaştırma yapılamadı:** ücretsiz katman günde 20
  istek kabul ediyor ve altı ortak senaryonun üçü `GoogleRateLimitError`
  ile düştü. Karşılaştırma betiği hata taşıyan satırı dışarıda bırakıyor,
  yani kıyas üç senaryo üzerinden.
- **`rail-04` senaryosu yinelenmiyor:** aynı girdiyle dört koşuda üç farklı
  davranış (doğru araç → geçti; iki `geocode_place`, istasyon adı
  "Sultanbeyler" yazıldı → düştü; sıfır araç → düştü). Sıcaklık 0.0 ama
  tohum yok; `llama.cpp` kısmi GPU boşaltmayla bit düzeyinde yinelenebilir
  değil. Tek senaryoluk bir koşudan "düzeldi" sonucu çıkarılamaz.
- **Değerlendirme önbelleği araç şemalarını kapsamıyor.** Anahtar
  `sağlayıcı-model-(prompt+uyarı)özeti-senaryo`; araç açıklaması ya da
  çıktısı değişince önbellek kendini geçersiz kılmaz, ölçümü `--refresh`
  ile koşmak gerekir (kararlar 0014, 0015).
- **`--concurrency` 1'de duruyor.** Ölçüldü: eval'in yanında başka iş
  çalıştırmak tek bir LLM isteğini 120 saniyelik zaman aşımının üstüne
  çıkarıp bir senaryoyu **sahte** biçimde başarısız gösteriyor
  (`rail-02`: çekişmeyle 144.9 s hata, sessiz makinede 14.4 s geçti).
  Değerlendirme koşarken aynı makinede başka iş çalıştırılmaz (karar 0014).

## Komutlar

```bash
uv run pytest                                  # 639 test
uv run ruff check . && uv run ruff format .

uv run python -m istanbul_agent.cli "soru" [--provider ollama] [--quiet]
uv run python eval/run_rag_eval.py             # getirme metrikleri (LLM'siz)
uv run python eval/run_agent_eval.py           # 54 senaryo (önbellekli)
uv run python eval/run_agent_eval.py --provider gemini --delay 18 --id rail-01
uv run python eval/compare_providers.py        # iki sağlayıcı, ortak senaryolar

uv run python -m istanbul_agent.servers.transit_server     # MCP (stdio)
uv run python -m istanbul_agent.servers.knowledge_server
uv run python -m istanbul_agent.servers.city_server
```

Araçlar MCP üzerinden sunulduğu için Claude Desktop gibi bir istemciye de
doğrudan bağlanabilirler; agent'ın gördüğü araç ile dışarıya sunulan araç
birebir aynıdır.

## Belgeler

- `documents/PLAN.md` — yol haritası, fazlar ve "bitti" tanımları
- `documents/decisions/` — mimari karar notları (bağlam, seçenekler, karar,
  ölçüm). Projedeki her önemli seçim burada gerekçeli:
  veri kaynakları (0001), GTFS servis günleri (0002), istasyon eşleştirme
  (0003), raylı süre modeli (0004), sefer saatleri (0005), MCP + agent
  (0006), İETT beslemesi (0007), yer ve hat araçları (0008), RAG hattı
  (0009), entegrasyon (0010), değerlendirme ve paketleme (0011), canlı veri araçları (0012)
- `datasets/data-inventory.md` — 39 uç noktanın işaretli envanteri
- `eval/results/` — ölçüm çıktıları (JSON)

## Teknoloji

Python 3.11+ · `uv` · MCP Python SDK (FastMCP) · LangGraph +
`langchain-mcp-adapters` · DuckDB · Qdrant (yerel mod) · BGE-M3 +
BGE-reranker-v2-m3 · `httpx` / `zeep` · FastAPI · Gradio · pytest · ruff
