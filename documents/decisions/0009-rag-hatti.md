# 0009 — RAG hattı: kaynaklar, parçalama, hibrit arama, reranker

**Tarih:** 10 Kasım 2026
**Durum:** uygulandı
**İlgili:** [0006](0006-mcp-ve-agent.md) (araç açıklamaları), [0007](0007-iett-beslemesi.md) (kaynağı ölçerek okumak)

## Bağlam

Faz 3'ün işi metin kaynaklarını aranabilir hale getirmek: Metro SSS, bilet
fiyatları, 34 Dakika indeks belgeleri, Ulaşım Ana Planı raporu ve anket
formları, artı portaldaki 565 veri setinin açıklaması. Kod yazmadan önce
kaynaklar indirildi ve ölçüldü (`scripts/download_knowledge.py`,
`scripts/inspect_knowledge.py`).

Faz 2 (canlı veri araçları) kullanıcı kararıyla **ertelendi**, iptal
edilmedi; RAG hattı canlı veriye bağlı değil, bu yüzden sıra değişimi
teknik bir borç yaratmıyor.

## Ölçüm: kaynaklar gerçekte ne içeriyor

| Kaynak | Ölçülen |
|---|---|
| Metro SSS (509) | 45 kayıt — **23 TR + 22 EN**, 37'sinde HTML varlık kodu |
| Güncel bilet fiyatı (API) | 3 kart tipi; RAG'deki tek güncel fiyat |
| Bilet fiyatı 2017-2022 (541) | tek tablo, 25 satır |
| 34 Dakika indeks (445 **ve** 442) | 5 sayfa — **iki veri seti aynı dosya**, sha256 eşit |
| UAP raporu (496) | 168 sayfa, 244 bin karakter, 2 sayfa metinsiz |
| UAP anketleri (435) | 2006: metin **glif adı** olarak çıkıyor; 2012: okunabilir |
| Veri seti açıklamaları | 565 paketin **565**'inde açıklama, ortanca 132 karakter |

Üç bulgu kodu doğrudan belirledi.

### 1. Aynı dosya iki veri setinde

445 ve 442 paketleri bayt bayt aynı PDF'i yayımlıyor. İkisini de
indekslemek her aramada aynı parçayı iki kez döndürürdü. Dedup içerik
özetine (sha256) göre; ikinci kaynak birincinin `also_from` alanına
yazılıyor, böylece kaynak gösterirken ikisi de anılabiliyor.

### 2. 2006 anketinin metni kurtarıldı

pypdf bu PDF'te `/MT221/MT83/MT84…` basıyor: fontun ToUnicode tablosu yok,
glif adları doğrudan çıkıyor. Sayılar **cp1254 bayt değerleri** (221 → `İ`,
83 → `S`). Çözülünce düzgün Türkçe geliyor: "İSTANBUL O/D ARAŞTIRMASI
HANEHALKI SORUKAĞIDI". `fontTools` kurmak düzeltmiyor — denendi.

Dönüşüm körü körüne uygulanmıyor: sonucun harflerinin **%90'ı** Türk
alfabesinde değilse onarım reddediliyor, çünkü glif adları bazı PDF'lerde
gerçekten font içi indekstir ve orada dönüşüm harf çorbası üretir.

### 3. Metro SSS iki dilli

Servis aynı soruyu TR ve EN olarak veriyor. İngilizce kopyalar aramada
Türkçe karşılığının yanına yerleşip sonuç yeri harcıyordu: "bisikletle
binebilir miyim" sorusunda ilk üç sonucun ikincisi aynı cevabın
İngilizcesiydi. Kullanıcıya dönük metin Türkçe olduğu için yalnızca TR
kayıtları indeksleniyor (23 kayıt).

## Karar 1 — Parçalama türe göre

Üç tür var ve üçü ayrı ele alınıyor:

- **`qa`** (SSS soru-cevapları, veri seti açıklamaları) — atomik:
  bölünmez **ve** birleştirilmez. Bir soruyu kesmek cevabı yarım bırakır,
  iki soruyu birleştirmek aramayı bulanıklaştırır.
- **`table`** (fiyat tabloları, PDF'teki kolonlu bloklar) — bölünmez
  (PLAN.md kuralı). Hedefi aşan tablo bütün kalır, `oversized: true`
  işaretlenir (korpusta 1 parça).
- **`prose`** — aynı başlık altında birleştirilir, 1600 karakteri aşınca
  cümle sınırından 200 karakter örtüşmeyle bölünür.

Her parçanın başına **başlık yolu** yazılıyor ("UAP Araştırması > 3.2
Yöntem"); başlıksız bir paragraf "oran %12 arttı" dediğinde neyin arttığı
kaybolur.

**Sonuç: 853 parça**, karakter ortancası 381. Gerçek token sayıları
(BGE-M3 tokenizer'ı, 120 parça örneklendi): ortanca **105**, ortalama 178,
en uzun 739; karakter/token **3.81**.

PLAN.md'nin hedefi 300-600 token'dı ve parçaların çoğu bunun **altında**.
Sebep yapısal: 588 parçanın türü `qa` ve bunlar doğası gereği kısa (bir SSS
cevabı, bir veri seti açıklaması). 300-600 aralığı düz metin için geçerli
ve orada tutuluyor. Atomik birimi hedef boyuta ulaşsın diye şişirmek,
aramayı iyileştirmez, bozar.

## Karar 2 — Gürültü filtresi, iki yanlış denemeden sonra

Anket formları indekse girmemesi gereken satırlar taşıyor: boş alanlar
("SAAT ____ : ____"), etiketsiz kod ızgaraları ("KULLANIN 01 1 2 1 2") ve
içindekiler satırları ("4.2. Eğitim ......... 37").

| Deneme | Kural | Sonuç |
|---|---|---|
| 1 | harfler, boşluk dışı karakterlerin ≥%40'ı olmalı | **yanlış** — raporun gerçek istatistik tablolarını (nüfus yoğunluğu, kira bedelleri) attı. Sayı yoğunluğu gürültü işareti değil. |
| 2 | tek eşik: en az 25 harf | **yanlış** — küçük fiyat tablosunu (16 harf) ve kısa SSS cevabını attı |
| 3 | **türe göre eşik** | `qa` her zaman kabul, `table` ≥12 harf, `prose` ≥25 harf + içindekiler kuralı; alt çizgi oranı >%20 ise her türde gürültü |

Son hâlde **46 parça** atılıyor, hepsi anket formu artığı ya da içindekiler
satırı; istatistik tabloları korunuyor (ölçüldü).

## Karar 3 — Qdrant yerel modda dizin silinerek sıfırlanıyor

Yeniden indeksleme ilk sürümde `delete_collection` + `create_collection`
yapıyordu. Ölçüm bunun **çalışmadığını** gösterdi:

| Adım | Beklenen | Gerçekleşen |
|---|---|---|
| 3 nokta yaz | 3 | 3 |
| koleksiyonu sil, yeniden oluştur, 1 nokta yaz | 1 | **4** |
| istemciyi kapat, yeniden aç | 1 | **4** |
| sil → kapat → aç → oluştur → 1 nokta yaz | 1 | **5** |

Yani yerel modda silinen koleksiyon yeniden oluşturulduğunda eski noktalar
geri geliyor. Gerçek etkisi şuydu: SSS'yi tek dile indirdikten sonra indeks
854 nokta yerine 876 nokta içerdi ve **22 İngilizce kopya** orada kaldı.

Çözüm: `recreate=True` ile yeniden indekslemede dizin tamamen siliniyor
(`reset_storage`). İki güvenlik koşulu var — yolun adı beklenen ad olmalı ya
da içinde Qdrant'ın yerel dosyaları bulunmalı.

Yerel modun ikinci bedeli: dizin **tek sürece** kilitli. `knowledge_server`
açıkken ingest çalışmıyor; bu hem sunucu docstring'inde hem aracın hata
mesajında yazılı.

## Karar 4 — Arama kipi: ölçüm yoğun aramayı seçti

62 soruluk set (`eval/rag_questions.jsonl`), her soruda beklenen kaynak ve
anahtar kelimeler yazılı. `eval/run_rag_eval.py` LLM kullanmıyor; getirme
kalitesini ölçüyor.

| Kip | recall@1 | recall@3 | recall@5 | MRR | kelime isabeti | gecikme (ortanca) |
|---|---|---|---|---|---|---|
| **dense** | **0.968** | **1.000** | **1.000** | **0.978** | 0.968 | **0.27 s** |
| dense + rerank | 0.968 | 0.984 | 0.984 | 0.976 | 0.952 | 7.75 s |
| hybrid (RRF) | 0.952 | 0.984 | 1.000 | 0.968 | **0.984** | 0.30 s |
| hybrid + rerank | 0.968 | 0.984 | 0.984 | 0.973 | 0.968 | 7.83 s |
| sparse | 0.919 | 0.952 | 0.968 | 0.940 | 0.919 | 0.30 s |

Yoğun arama tek başına hibritten iyi. İlk soru seti anlam ağırlıklıydı, bu
yüzden karşılaştırmayı adil kılmak için **terim ağırlıklı 8 soru** eklendi
(servis adları, hat kodları, kısaltmalar: `GetTicketPrice`, `ParkDetay`,
`M7`, `GTFS`, `AQI`, `İSTMARİN`). Sonuç değişmedi.

**Karar:** varsayılan kip `hybrid` kalıyor ama bu bir ölçüm sonucu değil,
bir risk kararı: fark küçük (recall@3'te 0.984 vs 1.000, tek soru) ve
seyrek taraf terim eşleşmesini garanti ediyor — hibrit, anahtar kelime
isabetinde en iyi kip (0.984). Korpus büyüdükçe (özellikle kod ve kısaltma
yoğun içerikle) yeniden ölçülmeli; bugünkü veriyle "hibrit şart" demek
ölçümün söylemediği bir şeyi söylemek olur.

### Reranker bu korpusta kazanç sağlamıyor

Tablodaki en önemli satır reranker'lı kipler. Reranker:

- hibritin **recall@1**'ini düzeltiyor (0.952 → 0.968),
- ama her iki kipte **recall@5'i düşürüyor** (1.000 → 0.984),
- ve gecikmeyi **26 katına** çıkarıyor (0.3 s → 7.8 s).

recall@5 düşüşünün sebebi alaka eşiği (`MIN_RERANK_SCORE = -2.0`): reranker
doğru parçayı "alakasız" puanlayıp attığı bir soru var. Yani reranker
burada yalnızca pahalı değil, **net olarak zararlı**.

Bu sonuç korpusun küçük (853 parça) ve soruların çoğunda tek bir açıkça
doğru parça olmasıyla açıklanabilir; reranker, birbirine çok benzeyen çok
sayıda aday yarıştığında kazanmaya başlar. Korpus büyüdüğünde yeniden
ölçülmeli — bu yüzden kod kaldırılmadı, yalnızca varsayılan kapatıldı.

## Karar 5 — Reranker CPU'da varsayılan olarak kapalı

Bu makinede kurulu torch **CPU sürümü** (`2.15.0+cpu`; CUDA tekerlekleri
ayrı bir indirme). Ölçüm:

| Yapılandırma | Sorgu gecikmesi |
|---|---|
| hibrit arama, reranker yok | **0.3 s** (ortanca) |
| + reranker, 6 aday | **7.8 s** (ortanca; 62 soruda ölçüldü) |
| + reranker, 12 aday | 11-28 s |

Bir agent aracı için 10 saniyenin üstü kabul edilemez, bu yüzden reranker
**cihaza göre** varsayılan: CUDA varsa açık, CPU'da kapalı. Kapalı olduğu
cevaba not olarak yazılıyor — sessizce daha kötü sıra döndürmek, kaynak
gösteren bir sistemde kabul edilemez. Reranker yüklenemezse de arama
çalışmaya devam ediyor ve `reranked=False` dönüyor.

Kalite farkı aynı değerlendirme betiğiyle ölçülüyor
(`--only-rerank`), böylece kapalı olmanın bedeli sayıyla biliniyor.

## Karar 6 — Tazelik: tarih değil kaynak, ve soruya bakarak

Bilet fiyatı iki kaynakta var: canlı servis (bugün) ve 2017-2022 tablosu.
İlk sürümde "tam bilet kaç lira" sorusunda **2022 tablosu birinci**
geliyordu — ikisi de doğru kaynak ama sıralama kullanıcıyı eski fiyata
götürüyordu.

İki kural eklendi:

1. **Tazelik kaynağa göre belirleniyor, tarihe göre değil.** 2023 tarihli
   bir araştırma raporu "eski" değildir, o yılın araştırmasıdır. Eskiyen
   şey fiyat gibi değişen bilgi; `VOLATILE_SOURCES` bunu adlandırıyor.
2. **İlk 3 sonuç içinde güncel kaynak eskinin önüne alınıyor** — ama soru
   geçmişi soruyorsa alınmıyor. "2019'da bilet ne kadardı" sorusunun cevabı
   tarihsel tablodur; ilk sürüm orada da güncel fiyatı öne alıyordu.
   Geçmiş tespiti: bugünün yılından küçük bir yıl ya da tarihçe soran
   kalıp ("yıllara göre", "eskiden", "tarihçe").

Sıra kuralı **puan yerine sıra** üzerinden tanımlı: hibrit RRF puanı
pozitif, reranker logiti negatif olabiliyor; "puanı yakınsa" kuralı iki
kipte farklı davranırdı.

## Maliyet

| İş | Süre |
|---|---|
| BGE-M3 indirme | ~6 dk (2.3 GB) |
| 853 parçayı gömme (CPU) | **355 s** (~400 ms/parça) |
| indeks yazma | <5 s |
| sorgu (reranker yok) | 0.3 s |
| sorgu (reranker, 6 aday, CPU) | 10-12 s |

## Kapsam dışı bırakılanlar

- **RAGAS** (cevap doğruluğu/sadakati) bu fazda çalıştırılmadı: LLM
  gerektiriyor ve ücretsiz kota günde 20 istek (karar 0006). Her kip için
  62 soru sormak kotayı anında tüketir. Getirme metrikleri LLM'siz ölçüldü;
  cevap metrikleri Faz 4-5'te agent ile birlikte ölçülecek.
- **CUDA**: GPU tekerlekleri kurulmadı. Kod cihazdan bağımsız; CUDA
  kurulursa gömme ve reranker ölçümleri yeniden koşulmalı
  (`eval/run_rag_eval.py`).
- İETT SOAP kullanım dokümanı (PDF) kaynak olarak eklenmedi: depoda yok,
  Faz 0 metot listelerini WSDL'den çıkarmıştı.

## Yeniden gözden geçirme koşulları

- Korpus büyürse (özellikle kod/kısaltma yoğun içerikle) arama kipi
  yeniden ölçülmeli; bugünkü fark tek soru.
- CUDA kurulursa reranker varsayılanı kendiliğinden açılır; gecikme ve
  kalite yeniden ölçülmeli.
- Metro SSS servisi dil alanını değiştirirse (ör. `Language: 1`) dil
  filtresi sessizce her şeyi atar; `rag_ingest.json` içindeki parça sayısı
  bu yüzden izlenmeli.
- Qdrant sunucu moduna geçilirse `reset_storage` gereksizleşir ve tek
  süreç kilidi kalkar.
