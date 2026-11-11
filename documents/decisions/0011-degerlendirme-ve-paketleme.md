# 0011 — Model karşılaştırması, paketleme ve "yeni makinede çalışır mı"

**Tarih:** 11 Kasım 2026
**Durum:** uygulandı (bir kısmı doğrulanmadı, aşağıda)
**İlgili:** [0006](0006-mcp-ve-agent.md) (kota), [0009](0009-rag-hatti.md) (RAG ölçümü), [0010](0010-entegrasyon.md) (senaryo seti)

## Bağlam

Faz 5'in işi üç şey: iki LLM sağlayıcısını ölçerek karşılaştırmak, projeyi
paketlemek (HTTP servisi, demo arayüzü, Docker) ve README'yi "yeni bir
makinede izleyip çalıştırabilecek" hale getirmek.

## Karar 1 — Karşılaştırma kesişim üzerinden yapılır, ortalama üzerinden değil

Gemini ücretsiz katmanı **günde 20 istek** kabul ediyor (karar 0006) ve her
senaryo 2-4 istek harcıyor. 49 senaryoyu Gemini ile koşmak mümkün değil.

Yanlış yol şu olurdu: yerel modelin 49 senaryodaki oranını Gemini'nin 6
senaryodaki oranıyla aynı tabloda karşılaştırmak. Bunlar iki farklı set;
oranları yan yana koymak kıyas değil, yanıltma olur.

`eval/compare_providers.py` bu yüzden **ortak senaryolar** üzerinden
çalışıyor: iki sağlayıcının kayıtlı satırlarını kesiştiriyor ve yalnızca o
kesişimi kıyaslıyor. Alt küme de rastgele seçilmedi — yerel modelde **3
geçen + 3 başarısız** senaryo alındı, çünkü yalnızca geçenleri kıyaslamak
farkı göstermez.

| Ortak 6 senaryo | `qwen3.5:4b` | `gemini-3.5-flash` |
|---|---|---|
| geçen senaryo | 3/6 | **6/6** |
| araç doğruluğu | 4/6 | **6/6** |
| adım (toplam araç çağrısı) | 17 | **10** |
| gecikme (ortanca) | 53.1 s | **25.9 s** |
| gecikme (toplam) | 550 s | **152 s** |

Gemini, yerel modelin kaçırdığı üç senaryonun **üçünü de** geçti:
`chain-02` (iki araç gerektiren soru — yerel model 8 çağrı yapıp adım
sınırına çarptı, Gemini 2 çağrıyla bitirdi), `lines-03` (iskele hat
sorusunda yerel model yanlış aracı seçti), `refuse-05` (yerel model vapur
tarifesinin yokluğunu söylemedi).

Dikkat çeken ikinci sonuç: **ağ üzerinden çağrılan model, yerelden iki kat
hızlı.** 4 GB'lık bir modeli CPU'da koşturmanın bedeli, ağ gecikmesinden
büyük.

Planda "yerel model 15 araçta zorlanırsa yönlendirici (router) deneyi yap"
yazıyordu. Bu deney **yapılmadı**: araç sayısı 7 ve yerel modelin kaybı
araç *sayısından* değil, ölçülen üç şeyden geliyor — bağlam penceresi
(karar 0010/4, düzeltildi), araç çıktısını yorumlama ("verdiğiniz JSON
verisi…") ve çok adımlı soruda döngüye girme. Yönlendirici bunların
hiçbirini çözmez; araç sayısı 10'u aşarsa (Faz 2) yeniden değerlendirilmeli.

## Karar 2 — `unverified` bayrağı her arayüzde görünür

Kaynaksız cevap koruması Faz 4'te grafiğe kondu (karar 0010/3). Faz 5'te
üç arayüzün hepsine taşındı:

| Arayüz | Nasıl gösteriliyor |
|---|---|
| CLI | stderr'e uyarı satırı, cevaptan **önce** |
| FastAPI | `AskResponse.unverified` alanı (şema açıklamasında "istemci bunu kullanıcıya göstermeli") |
| Gradio | ekranın üstünde "⚠ Bu cevap kaynaksız" bloğu |

Bayrağı taşımayan bir arayüz, korumayı etkisiz hale getirirdi.

`GET /health` de aynı ilkeden: ayakta olan ama verisi kurulmamış bir servis
"sağlıklı" dönmüyor; `duckdb` ve `qdrant` ayrı ayrı raporlanıyor ve
eksikse `notes` içinde çalıştırılacak komut yazıyor.

## Karar 3 — Araçlar depo yokluğunu söyler, istisna atmaz

README'nin kurulum adımlarını sırayla izlemeyen biri (örneğin 4. adımı
atlayıp doğrudan soru soran) şunu görüyordu:

```
IOException: IO Error: Cannot open database ".../data/transit.duckdb"
in read-only mode: database does not exist
```

Bu, MCP üzerinden bir yığın izi olarak dönüyor ve ne yapılacağını
söylemiyor. Altı aracın hepsi artık depo kurulu değilse tipli bir hata
döndürüyor:

```json
{"ok": false, "error": "depo_yok",
 "message": "Yerel veri deposu kurulmamış (transit.duckdb yok). Kurulum: ..."}
```

Bu, CLAUDE.md'deki "araç hatayı açıkça döner, asla sessizce tahmin
üretmez" kuralının kurulum hatasına uzatılması.

## Karar 4 — Harita, araç çıktısından okunur

Demo arayüzündeki harita, araç sonuçlarındaki `lat`/`lon` çiftlerini
okuyup OpenStreetMap üzerine basıyor. İki kısıt bilerek konuldu:

- Araç sonuçları MCP üzerinden **JSON metni** olarak dönüyor; yapı yeniden
  ayrıştırılmıyor, yalnızca koordinat çiftleri ve varsa bir önceki `name`
  alanı okunuyor. Ayrıştırılamayan çıktı haritayı boş bırakıyor, hata
  vermiyor.
- Koordinat İstanbul sınır kutusunun dışındaysa haritaya konmuyor; araç
  çıktısındaki başka bir sayı çiftinin haritaya düşmesini bu engelliyor.

Harita planın "artı puan" kalemiydi; asıl gösterilen şey **çağrılan
araçlar tablosu** — agent'ın doğru aracı seçip seçmediği cevaba bakarak
anlaşılmıyor (karar 0006).

## Doğrulanmayan: Docker imajı

`Dockerfile` yazıldı (uv ile kilit dosyasından kurulum, `data/` birim
olarak bağlanıyor, imaj veriyi ve modeli içermiyor, `HEALTHCHECK`
`/health`e bakıyor) ama **bu makinede derlenemedi**: Docker CLI kurulu
(29.6.2) ancak daemon çalışmıyor (`failed to connect to the docker API at
npipe:////./pipe/dockerDesktopLinuxEngine`). `docker build --check` bile
daemon istiyor.

Yani Dockerfile **doğrulanmamış** durumda ve README'de de böyle işaretli.
Doğrulamak için Docker Desktop açıkken:

```bash
docker build -t istanbul-agent .
docker run --rm -p 8000:8000 --env-file .env -v "$PWD/data:/app/data" istanbul-agent
curl localhost:8000/health
```

Beklenen ilk engel: `torch` ve gömme modeli imajı büyütüyor (rag ekstrası
CPU torch ile ~2.5 GB); gerekirse `--extra rag` imajdan çıkarılıp metin
araması ayrı bir servise taşınabilir.

## Yerel modelin kalan davranış kusuru (düzeltilmedi)

Yerel model bazı cevaplarda araç çıktısını kullanıcıdan gelmiş gibi
yorumluyor: *"Verdiğiniz JSON verisi, İstanbul İETT'nin 4. Levent ile Şifa
Sondurak arasında çalışan otobüs güzergahlarını içermektedir…"*. Cevap
yanlış değil ama kullanıcıya dönük bir cevap da değil. HTTP üzerinden tam
yığın denemesinde de görüldü (`/ask`, 148 s, doğru araç).

Prompt'a "araç çıktısını kullanıcıya anlatma, cevabı yaz" maddesi
eklenebilir; eklenmedi çünkü prompt değişikliği davranış değişikliğidir ve
49 senaryonun yeniden koşulmasını gerektiriyor (CLAUDE.md kuralı). Faz 2
araçları eklenirken prompt zaten yeniden ölçülecek; madde o turda
eklenmeli.

## Yeniden gözden geçirme koşulları

- Gemini kotası artarsa karşılaştırma 49 senaryonun tamamında koşulmalı;
  bugünkü sonuç 6 senaryo üzerinden.
- RAGAS hâlâ ölçülmedi (LLM gerektiriyor, kota 20/gün). Yerel modelle
  ölçmek mümkün ama yargıç olarak yerel modelin kalitesi ayrı bir soru.
- Docker imajı derlenip `curl localhost:8000/health` ile doğrulanmalı.
- `city_server` eklenince README'nin araç tablosu, mimari diyagramı ve
  sınırlamalar bölümü güncellenmeli.
