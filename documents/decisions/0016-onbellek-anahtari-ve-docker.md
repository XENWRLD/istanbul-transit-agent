# 0016 — Önbellek anahtarı araç yüzeyini kapsıyor; Docker yolu doğrulandı

**Tarih:** 12 Kasım 2026
**Durum:** uygulandı
**İlgili:** [0014](0014-degerlendirmeyi-hizlandirma.md) (boşluğu açan karar), [0015](0015-olu-mod-degerleri.md) (anahtara uyarı metnini ekleyen karar), [0011](0011-degerlendirme-ve-paketleme.md) (paketleme)

## Bağlam

İki iş de **modele giden girdiye dokunmuyor**: prompt, uyarı metni ve araç
açıklamaları olduğu gibi duruyor. Son ölçüm (50/54, karar 0015) projenin
bugüne kadarki en iyi sonucu; onu bozma riski olan hiçbir değişiklik bu
turda yapılmadı. Ele alınan şey ölçümün **kendi güvenilirliği** ve
doğrulanmamış bir kurulum iddiası.

## 1. Önbellek anahtarı: araç yüzeyi artık anahtarda

### Boşluk

Karar 0014 anahtarı `sağlayıcı-model-promptözeti-senaryo` olarak kurmuş ve
"araç şemaları anahtarın dışında" diye açık bırakmıştı. Karar 0015 uyarı
metnini ekledi, ama aynı boşluğu yine açık bıraktı. Sonuç: CLAUDE.md
*"araç açıklamasını değiştirmek davranış değişikliğidir, değerlendirmeyi
yeniden çalıştır"* diyor, ama betik bunu kendisi uygulayamıyordu — açıklama
değişince önbellek geçerli görünüyor ve ölçüm **sessizce eski cevapları
yeni ölçüm diye raporluyordu**. Kuralın uygulanması insanın `--refresh`
yazmayı hatırlamasına bağlıydı.

Bu soyut bir risk değil: karar 0015'in kendisi üç aracın açıklamasını
değiştirdi (`geocode_place`, `find_nearby_stops`, `get_lines_at_stop` mod
listeleri) ve 54 senaryonun yeniden koşulması gerektiği elle fark edildi.

### Karar

`graph.tool_surface_digest(tools)`: **modelin gördüğü yüzeyin** özeti —
her aracın adı + açıklaması + girdi şeması, modele verildiği **sırayla**.
`istanbul_agent()` artık dördüncü öğe olarak araç listesini veriyor
(`(agent, provider, model, tools)`), çünkü yüzey yalnızca MCP oturumları
açıkken bilinebilir. `cache_key` bu özeti de alıyor.

Üç tasarım ayrıntısı, gerekçesiyle:

- **Sıralama yapılmıyor.** Araç sırası da modele giden girdinin parçası;
  sıralamak gerçek bir yeniden sıralamayı görünmez yapardı.
- **Sunucu altkümesi farklı özet verir.** `servers=("transit",)` ile açılan
  bir yığın 8 araç görür, 11 değil; kendi önbelleğini kullanması doğru.
- **Şema iki biçimde de işleniyor:** MCP araçları `args_schema`'yı sözlük
  olarak veriyor, LangChain aracı Pydantic modeli verebiliyor.

Ölçülen değer: 11 araç, özet `1d626e3dea52`.

### Kapanmayan boşluk — açıkça yazıyorum

Özet araç **gövdesini** kapsamıyor. Açıklama ve şema aynı kalıp dönen
değer değişirse (bir SQL sorgusu düzeltilir, bir TTL değişir) anahtar
değişmez. Bunu kapatmanın yolu sunucu kaynaklarını hashlemek olurdu, ama o
zaman biçimlendirme değişikliği bile 57 dakikalık koşuyu geçersiz kılardı.
Bu yüzden kural kalıyor: **araç gövdesi değişince ölçüm `--refresh` ile
koşulur.** Fark şu ki artık yüzey değişikliği için insana güvenmek
gerekmiyor; kalan tek elle iş gövde değişikliği.

### Hata sınıfını kapatan testler

0015'in yöntemi: tek tek değerler değil **ilişki** sınanıyor.

- aynı yüzey → aynı özet; açıklama / şema / sıra / araç sayısı değişince → farklı özet
- Pydantic şeması da özete giriyor (iki farklı model iki farklı özet)
- `cache_key`: yüzey değişince anahtar değişiyor; yüzey verilmezse 0015'teki anahtar
- senaryo, sağlayıcı ve model hâlâ anahtarı ayırıyor (dört ayrı anahtar)

### Mevcut önbellek taşındı

Anahtar biçimi değiştiği için 60 geçerli dosya (54 ollama = 50/54 koşusu,
6 gemini) geçersiz olacaktı. Taşımanın meşru olduğunu **git ile**
doğruladım: son ölçümden (`aec79b4`) beri `src/istanbul_agent/servers/`,
`agent/prompt.py` ve `agent/graph.py` hiç değişmedi, yani o cevaplar
bugünkü araç yüzeyiyle üretildi. Dosyalar yeni ada kopyalandı; eski adlar
geri dönüş için bırakıldı.

Taşımadan sonra dört senaryoluk kontrol: dördü de önbellekten geldi,
kayıttaki oran **50/54** olarak sabit kaldı. Yani değişiklik ölçümü
korudu, değiştirmedi — amaç tam olarak bu.

## 2. Docker yolu: derlendi, çalıştı, ölçüldü

### Bağlam

Karar 0011 Dockerfile'ı yazdı ama **derleyemedi** (bu makinede Docker
daemon kapalıydı) ve README'ye "Docker yolu doğrulanmadı" diye geçti.
Doğrulanmamış bir kurulum adımı README'de iddia olarak durmamalı: yeni bir
makinede projeyi çalıştıracak kişinin izleyeceği yol o.

### Önce eksik olan: `.dockerignore`

Dosya **yoktu**. Dockerfile'ın kendi yorumu "imaj veriyi içermez" diyor,
ama build context öyle demiyordu: `.venv` (torch dahil birkaç GB), `data/`
(124 MB), `.git` ve `eval/.cache` daemon'a gönderiliyordu. `.env` de
context'e giriyordu — imaja kopyalanmıyordu, ama gizli anahtarı build
context'ine koymak gereksiz bir risk. Dosya yazıldı: yalnızca Dockerfile'ın
kopyaladığı dört dizin ve üç dosya kalıyor.

### Ölçüm

| Ne | Sonuç |
|---|---|
| `docker build` (soğuk, cache yok) | **1145 s** (19 dk) |
| `docker image inspect .Size` | **6.78 GB** |
| `docker images` / `system df` disk kullanımı | **20.4 GB** (ayrıca 20.4 GB build cache, tamamı geri kazanılabilir) |
| veri bağlamadan `/health` | `ok:false`, iki eksiği **çalıştırılacak komutla** söylüyor |
| `data/` bağlı `/health` | `ok:true, duckdb:true, qdrant:true` |
| HEALTHCHECK | konteyner içinden koşuyor, konteyner `healthy` |
| `/ask` (ollama, konteyner içinden) | **çalıştı**: 504.9 s duvar saati, agent 126.8 s |

İki boyut rakamı aynı şeyi saymıyor: `inspect` katman toplamını veriyor,
`docker images` containerd deposunun disk ayak izini (blob + açılmış
snapshot). İkisi de ölçüldüğü gibi yazıldı; hangisinin "imaj boyutu"
olduğunu tek sayıya indirmek yanıltıcı olurdu.

`/ask` cevabı (`get_rail_travel_time` çağrıldı, `unverified: false`):
Üsküdar→Ayrılıkçeşme Marmaray ile 5 dk, aktarma 6 dk, M4 ile Kadıköy 2 dk,
toplam 13 dk — ve *"Süreler 2019 verisinden, bugün farklı olabilir."* Yani
`is_stale` bayrağı konteyner içinden de cevaba taşınıyor. Duvar saatinin
378 saniyesi BGE-M3'ün Hugging Face'ten inmesi; model `huggingface` adlı
Docker hacmine indiği için bir sonraki çalıştırmada inmiyor.

### Üç kurulum kusuru, hepsi ölçümle çıktı

1. **Dockerfile'ın `-v "$PWD/data:/app/data"` örneği Windows'ta sessizce
   boş dizin bağlıyor.** Git Bash yol dönüşümü argümanı bozuyor; konteyner
   ayağa kalkıyor, `/health` "veri yok" diyor ve sebep görünmüyor. Doğru
   biçim PowerShell'den tam Windows yolu. README'ye iki biçim de yazıldı.
   Bu, projenin "boş sonuç hatayı gizlemesin" kuralının kurulum tarafındaki
   karşılığı: `/health` doğru cevap veriyordu, yanlış olan mount'tı.
2. **`.env`'i konteynere olduğu gibi vermek LLM adresini bozuyor.**
   `OLLAMA_BASE_URL=http://127.0.0.1:11435` konteyner içinde konteynerin
   kendisini gösterir. `host.docker.internal` ile ezilmesi gerekiyor —
   Dockerfile başlığı bunu söylüyordu, README söylemiyordu.
3. **Ollama bu makinede 11434 değil 11435'te.** `OLLAMA_HOST` öyle kurulu.
   Dockerfile'ın örnek adresi varsayılan portu yazıyordu; README artık
   portun kontrol edilmesi gerektiğini söylüyor.

### İmaj boyutu: sebebi ölçüldü, düzeltme ayrı bir karara bırakıldı

Katman dökümü tek suçluyu gösteriyor: bağımlılık katmanı. İkinci
`uv sync` yalnızca 1.99 MB, yani katman tekrarı **yok**. Ağırlık
`sentence-transformers` → `torch` zincirinden geliyor ve linux tekerlekleri
varsayılan olarak CUDA paketlerini çekiyor (`nvidia-cublas`, `cudnn`,
`nccl`, `cufft`, `cusolver`, `triton`…) — **konteynerde GPU kullanılmıyor**,
gömme modeli CPU'da koşuyor.

CPU-only torch imajı birkaç GB düşürürdü, ama bunun için
`[tool.uv.sources]` ile ayrı bir indeks tanımlamak ve `uv.lock`'u yeniden
çözmek gerekiyor; kilit dosyası geliştirme ortamıyla paylaşıldığı için bu,
bu makinedeki CUDA'lı kurulumu da etkileyebilir. Bu turun kuralı
"cevapları değiştirmeyen değişiklik" olduğu için **yapılmadı**; ölçülmüş
bir öneri olarak duruyor.

### Ne doğrulanmadı

- **Gradio arayüzü konteynerden açılmadı.** `/ask` çalıştığı için aynı
  yığını kullanan arayüzün çalışması bekleniyor, ama beklenti ölçüm değil.
- **Konteyner içinden ingest koşulmadı.** Dockerfile başlığındaki veri
  kurulum komutu denenmedi; depo bu makinede hazır olduğu için bağlandı.
- **İmaj yalnızca bu makinede derlendi.** "Yeni bir makinede çalışır"
  iddiası hâlâ tek makinelik bir ölçüme dayanıyor.

## 3. İmajın boyutu teorik bir rahatsızlık değil: diski doldurup daemon'u kilitledi

Yukarıdaki 2. bölüm imajın 6.78 GB katman toplamı + 20.4 GB disk ayak izi +
20.4 GB build cache taşıdığını ölçmüş ve CPU-only torch'u "ayrı bir karara
bırakıldı" diye geçmişti. Aynı gün o boyut **kurulumu durdurdu**.

Olay sırası, hepsi ölçülü:

1. Yeni araç (karar 0019) eklendikten sonra imaj yeniden derlendi.
2. Derleme yarı yolda **kilitlendi**: 45 dakika "sürüyor" göründü, oysa
   `docker` istemcileri 0 saniye CPU'da bekliyordu ve buildx 4 saniyede
   durmuştu. Sebep disk: imaj + cache 40 GB'ı aşmış durumdaydı.
3. Daemon da kilitlendi — `docker version` bile 25 saniyede dönmedi.
4. Süreçleri zorla kapatmak **ikinci bir arıza üretti**: `wsl --shutdown`
   yapılmadığı için `docker-desktop` dağıtımı çalışır kalıp veri diskini
   tuttu ve Docker Desktop yeniden açılırken
   `preparing block device /dev/sdd: device or resource busy` hatasıyla
   düştü.
5. Kurtarma: `wsl --shutdown` (dağıtım `Stopped` olur, disk serbest kalır)
   → Docker Desktop yeniden başlatılır. Daemon 29.9.3 olarak geri geldi,
   imaj ve birimler yerinde, kayıp yok.

Üç şey kayda geçiyor:

- **Docker'ı yeniden başlatmanın doğru sırası** süreç öldürmek değil:
  `wsl --shutdown`, sonra Docker Desktop. Süreç öldürmek diski kilitli
  bırakıyor.
- **`docker builder prune` kurulum adımlarının parçası.** Ölçüm: prune
  yalnızca **kullanılmayan** cache'i siliyor, imajın kendi katmanı
  kullanımda olduğu için kalıyor (bu turda 79 MB geri geldi, çünkü 20 GB
  zaten daemon yeniden kurulurken temizlenmişti).
- **CPU-only torch artık "iyi olurdu" değil, ölçülmüş bir ihtiyaç.** İmajın
  ağırlığının neredeyse tamamı konteynerde hiç kullanılmayan CUDA
  paketlerinden geliyor ve bu ağırlık bu makinede bir kez kurulumu
  durdurdu. Yine de `uv.lock`'un yeniden çözülmesini gerektirdiği için bu
  turda yapılmadı; gerekçesi güçlenmiş bir öneri olarak duruyor.

Bir yöntem notu: "45 dakika süren derleme" diye raporlamak yanlış olurdu.
Süreçlerin CPU'suna bakmak ilerlemediğini gösterdi — duvar saati ilerleme
kanıtı değil.

## 4. Gradio doğrulaması kullanıcıya dönük bir kusur buldu: harita boştu

2. bölüm Gradio'yu "doğrulanmadı" diye bırakmıştı. Doğrulamanın kendisi,
`/ask`'in yakalamadığı bir hatayı ortaya çıkardı.

### Ölçüm

Konteynerden Gradio açıldı ve soru **arayüzün kendi fonksiyonundan**
sürüldü (`POST /gradio_api/call/run_question`), sayfa yüklemesiyle
yetinilmedi. Soru: *"Kadıköy çevresinde scooter park alanı var mı?"*

| Çıktı alanı | Sonuç |
|---|---|
| Cevap | doğru: mikromobilite park alanlarını sayıyor, *"canlı doluluk bilgisi yoktur"* diyor, kaynağın 2026-11 tarihli dosya olduğunu söylüyor |
| Durum satırı | `ollama (qwen3.5:4b) · 47.7 s · 1 araç çağrısı` |
| Çağrılan araçlar tablosu | `find_mobility_options` + parametreleri |
| **Harita** | **`null`** — oysa araç çıktısı her nokta için `lat`/`lon` taşıyor |

### Sebep

`graph.py` araç sonucunu `existing.result = str(message.content)` ile
saklıyordu. MCP araç sonucu **içerik bloğu listesi** olarak geliyor
(`[{'type': 'text', 'text': '...'}]`) ve `str()` onu Python repr'ına
çeviriyor: JSON'un satır sonları iki karakterlik `\n` dizisine
dönüşüyor. `app.extract_points` koordinatları `"lat": x, "lon": y`
deseniyle arıyor ve desenin `\s*` kısmı o diziye uymuyor.

Ölçüldü: düz metinde **1** eşleşme, repr biçiminde **0**.

Kusur yeni araca özgü değil: bu biçimde sonuç dönen **her araç** için
harita boş kalıyordu, yani README'nin özellik olarak saydığı harita bir
süredir çalışmıyordu.

### Düzeltme

Aynı modülde zaten duran düzleştirici kullanıldı:
`existing.result = _text(message.content)`. `_text` Gemini'nin blok
listesini okumak için yazılmıştı; araç sonucunda kullanılmamıştı.

**Modele giden girdi değişmiyor** — bu alan yalnızca bizim kaydımız. Bu
yüzden eval yeniden koşulmadı ve araç yüzeyi özeti aynı kaldı (karar
0018'in kuralı: karşılaştırmanın iki ucu aynı yüzeyde olmalı; burada
yüzey hiç değişmiyor).

### Niçin hiçbir test yakalamamıştı

Testler fonksiyonları sınıyordu, **araç çıktısının gerçek biçimini**
sınamıyordu. `extract_points` düz JSON metniyle çağrıldığında doğru
çalışıyor; hata yalnızca gerçek akışta, `_collect`'in sakladığı biçimde
ortaya çıkıyor. Yeni testler bu yüzden iki yerde duruyor:

- `_collect` blok listesini düzleştiriyor mu, repr işareti bırakıyor mu
- `extract_points` **gerçek araç çıktısı biçiminden** nokta çıkarıyor mu,
  İstanbul dışı koordinatı atıyor mu, ve repr biçiminin hiçbir şey
  bulmadığı (yani farkın kendisi) sabitlendi

Bu, karar 0013'ün kuralının arayüz tarafındaki karşılığı: *fixture tabanlı
test kodun kendi varsayımını doğrular, kaynağı doğrulamaz.* Burada
"kaynak" araç çıktısının gerçek biçimiydi.

### Düzeltmeden sonra: konteynerde ölçüldü

İmaj yeniden derlendi (945 s) ve aynı soru aynı yoldan tekrar soruldu:

| Çıktı alanı | Önce | Sonra |
|---|---|---|
| Harita | `null` | **11 nokta** (`scattermap`), adlarıyla |
| Yanıt boyutu | 1053 bayt | 9996 bayt |
| Cevap, durum satırı, araç tablosu | doğru | aynı |

Yani Gradio artık konteynerden **uçtan uca** doğrulanmış durumda: cevap,
çağrılan araçlar tablosu ve harita. Karar 0011'in "Docker yolu
doğrulanmadı" satırından buraya kadarki tek gerçek kusur bu haritaydı.

Kalan bir pürüz, kozmetik ve bilinçli bırakıldı: haritadaki ilk nokta
arama **merkezi** ve etiketi `find_mobility_options (find_mobility_options)`
oluyor. Sebep `extract_points`'in nokta adını yalnızca `"name"` alanından
okuması; üst düzey `lat`/`lon` çiftinin öncesinde `"around"` var, `"name"`
yok, o yüzden araç adına düşüyor. Veri doğru, etiket çirkin.