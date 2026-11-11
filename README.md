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
      ├── MCP: transit_server (stdio) ─── 6 araç
      │      ├── DuckDB: GTFS + GeoJSON (otobüs, raylı sistem, durak/iskele)
      │      └── Metro İstanbul REST (duyurular)
      │
      ├── MCP: knowledge_server (stdio) ── 1 araç
      │      └── Qdrant (yerel mod) + BGE-M3: SSS, bilet fiyatı, raporlar,
      │          565 veri seti açıklaması
      │
      └── MCP: city_server ──────────────── ⏸ Faz 2 ertelendi
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

Docker ile çalıştırma adımları `Dockerfile` başındaki yorumda; imaj veriyi
içermez, `data/` birim olarak bağlanır.

> **Dockerfile doğrulanmadı.** Bu makinede Docker CLI kurulu ama daemon
> çalışmıyor, bu yüzden imaj derlenip denenemedi (karar 0011). Doğrulamak
> için: `docker build -t istanbul-agent .` ve ardından
> `curl localhost:8000/health`.

## Araçlar (7)

| Araç | Ne yapar | Kaynak |
|---|---|---|
| `geocode_place` | ad → konum (durak, istasyon, iskele) | `transit_places` (23 690 nokta) |
| `find_nearby_stops` | bir yerin çevresindeki duraklar | aynı tablo, haversine |
| `get_lines_at_stop` | duraktan geçen hatlar | İETT GTFS + raylı sistem tabloları |
| `get_line_route` | hattın sıralı durakları, ilk/son kalkış | `bus_route_stops`, `bus_trips` |
| `get_rail_travel_time` | iki istasyon arası süre, aktarmalı güzergâh | GTFS `stop_times` + 312/313 referans tabloları |
| `get_next_departures` | metro/Marmaray ilk/son sefer, sefer aralığı | elle doğrulanmış saatler + GTFS + canlı duyurular |
| `search_knowledge` | kural, ücret, tanım, veri seti açıklaması | Qdrant + BGE-M3 (853 parça) |

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

### Agent senaryoları (49 senaryo)

| Ölçüt | yerel `qwen3.5:4b` | `gemini-3.5-flash` |
|---|---|---|
| kapsam | 49 senaryo | 6 senaryo (kota: günde 20 istek) |
| geçen senaryo | 41/49 (%83.7) | 6/6 |
| araç doğruluğu | %87.8 | %100 |
| kaynaksız cevap | 3 | 0 |
| gecikme (ortanca) | 44 s | 25.9 s |

Aynı 6 senaryoda yan yana (`eval/compare_providers.py`):

| Ölçüt | ollama | gemini |
|---|---|---|
| geçen senaryo | 3/6 | **6/6** |
| adım (toplam araç çağrısı) | 17 | **10** |
| gecikme (ortanca) | 53.1 s | **25.9 s** |

Yerel modelin kaçırdığı üç senaryo: iki araç gerektiren soru (8 çağrı yapıp
adım sınırına çarptı), iskele hat sorusunda yanlış araç, vapur tarifesi
sorusunda yokluğu söylememe.

## Önce / sonra

Bu projedeki iki düzeltme, ölçümle önce-sonra olarak kayıtlı.

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

## Bilinen sınırlamalar

- **Faz 2 (canlı veri araçları) ertelendi.** Otopark, trafik, hava kalitesi,
  istasyon erişilebilirliği ve otobüsün ara durak saatleri **yok**. Agent bu
  sorularda bilginin yokluğunu söylüyor; senaryo setinde 8 senaryo tam bunu
  ölçüyor.
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
- **Dockerfile derlenip denenmedi** (bu makinede Docker daemon kapalı).
- Yerel model bazı cevaplarda araç çıktısını kullanıcıdan gelmiş gibi
  anlatıyor (*"verdiğiniz JSON verisi…"*). Cevap yanlış değil ama kullanıcıya
  dönük değil; prompt düzeltmesi 49 senaryonun yeniden ölçülmesini
  gerektirdiği için Faz 2 turuna bırakıldı (karar 0011).

## Komutlar

```bash
uv run pytest                                  # 538 test
uv run ruff check . && uv run ruff format .

uv run python -m istanbul_agent.cli "soru" [--provider ollama] [--quiet]
uv run python eval/run_rag_eval.py             # getirme metrikleri (LLM'siz)
uv run python eval/run_agent_eval.py           # 49 senaryo (önbellekli)
uv run python eval/run_agent_eval.py --provider gemini --delay 18 --id rail-01
uv run python eval/compare_providers.py        # iki sağlayıcı, ortak senaryolar

uv run python -m istanbul_agent.servers.transit_server     # MCP (stdio)
uv run python -m istanbul_agent.servers.knowledge_server
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
  (0009), entegrasyon (0010), değerlendirme ve paketleme (0011)
- `datasets/data-inventory.md` — 39 uç noktanın işaretli envanteri
- `eval/results/` — ölçüm çıktıları (JSON)

## Teknoloji

Python 3.11+ · `uv` · MCP Python SDK (FastMCP) · LangGraph +
`langchain-mcp-adapters` · DuckDB · Qdrant (yerel mod) · BGE-M3 +
BGE-reranker-v2-m3 · `httpx` / `zeep` · FastAPI · Gradio · pytest · ruff
