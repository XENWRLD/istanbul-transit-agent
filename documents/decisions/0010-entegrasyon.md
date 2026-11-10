# 0010 — Entegrasyon: iki sunucu, kaynaksız cevap koruması, senaryo ölçümü

**Tarih:** 10 Kasım 2026
**Durum:** uygulandı
**İlgili:** [0006](0006-mcp-ve-agent.md) (ilk agent, bağlam penceresi), [0008](0008-yer-ve-hat-araclari.md) (araç yüzeyi), [0009](0009-rag-hatti.md) (RAG)

## Bağlam

Faz 4'ün işi araçları tek bir agent'ın arkasında birleştirmek ve davranışı
senaryo setiyle ölçmek. Plan "üç sunucu" diyordu; `city_server` Faz 2
ertelendiği için **iki** sunucu bağlandı: `transit` (6 araç) ve `knowledge`
(1 araç). Grafikteki kayıtta `city` satırı yorum olarak hazır bekliyor.

İlk uçtan uca denemede dört ayrı kusur çıktı. Üçü bizim kodumuzda, biri
modelde; bu notun çoğu onları ayırmakla ilgili.

## Karar 1 — MCP oturumları agent çalıştığı sürece açık

`MultiServerMCPClient.get_tools()` her araç çağrısında yeni bir oturum
açıyor, yani sunucu sürecini her çağrıda yeniden başlatıyor.
`knowledge_server` için bunun bedeli BGE-M3'ü her aramada yeniden
yüklemek olurdu. Bunun yerine her sunucu için bir oturum `AsyncExitStack`
ile açılıp araç listeleri birleştiriliyor: süreç bir kez kalkıyor, model
bir kez yükleniyor.

## Karar 2 — Ağır model sunucu açılışında yüklenir, araç gövdesinde değil

İlk entegrasyon denemesi `McpError: Connection closed` ile düştü. Hata
agent'a özgü değildi; ham MCP istemcisiyle de tekrarlandı. Ölçüm:

| Çağrı | Süre | Sonuç |
|---|---|---|
| `search_knowledge` (süreç içinde, doğrudan) | **15.8 s** | ilk çağrı; ikincisi 0.3 s |
| boş sorgu (modeli yüklemeyen yol), MCP üzerinden | **0.0 s** | cevap döndü |
| gerçek sorgu, MCP üzerinden | **180 s+** | hiç çıktı yok, bağlantı kapandı |
| gerçek sorgu, model açılışta yüklendikten sonra | **3.2 s** | cevap döndü |

Boş sorgunun 0.0 saniyede dönmesi döngünün sağlam olduğunu gösteriyor;
asılan şey yüklemenin **kendisi**. FastMCP eşzamanlı (sync) araçları iş
parçacığı havuzunda çalıştırıyor ve BGE-M3'ü orada yüklemek kilitleniyor —
sunucunun stderr'ine tek satır bile düşmüyor.

Çözüm: `knowledge_server.warm_up()` modeli `mcp.run()`'dan **önce**, ana
iş parçacığında yüklüyor. Yan faydası ilk sorgunun 16 saniyelik yükleme
bedelini ödememesi.

Yanlış hipotezler (ölçülüp elendi): stdout'un ilerleme çubuklarıyla
kirlenmesi (stdout'a 0 karakter yazılıyor, çubuklar stderr'e gidiyor) ve
Qdrant yerel mod kilidi (kilit çakışması 0.1 saniyede `RuntimeError`
veriyor, asılmıyor).

## Karar 3 — Araç çağırmadan verilen cevap kaynaksızdır

Entegrasyondan sonra ilk gerçek soru "Metroya bisikletle binebilir miyim?"
oldu ve yerel model (`qwen3.5:4b`) **hiç araç çağırmadan** cevap verdi:
bisiklet dolabı, izin belgesi, "100-150 metre" — hepsi uydurma. Doğru cevap
SSS'de yazılıydı (katlanabilir bisiklet her saat; standart bisiklet yoğun
saatler 07-09 ve 17-20 dışında).

Üç aşamalı düzeltme, her aşama ölçüldü:

1. **Prompt'ta `search_knowledge` hiç anılmıyordu.** "Uydurma" kuralı da
   yalnızca saat/süre/sıklık içindi. Kural genelleştirildi ve araç
   yönlendirme tablosuna metin araması eklendi.
2. **"Kaynak göster" kuralı tek başına zarar verdi.** Model yine araç
   çağırmadı ama bu kez cevabın altına *"Kaynak: Metro İstanbul SSS'ine
   göre ve İETT GTFS verisine göre"* yazdı — yani uydurmayı inandırıcı
   hale getirdi. Kural "kaynağı araç çıktısından **kopyala**; araç
   çağırmadıysan kaynak cümlesi yazma" biçimine çevrildi.
3. **Koruma prompt'tan grafiğe taşındı.** `answer()` araç çağrılmayan turu
   bir kez geri çeviriyor (`NUDGE`) ve ikinci turda da çağrılmazsa sonucu
   `unverified=True` işaretliyor. CLI bunu stderr'e uyarı olarak basıyor,
   değerlendirme betiği hata sayıyor.

Uyarı metni **soruyu tekrarlıyor**: ilk sürümde uyarı tek başına
gönderildi ve yerel model soruyu kaybedip "hangi soruyu soruyorsunuz?"
diye cevap verdi.

## Karar 4 — Bağlam penceresi araç yüzeyine bağlı: 8192 → 16384

Prompt büyüyüp araç sayısı 2'den 7'ye çıktıktan sonra yerel model Faz 1'de
geçen senaryolarda bile araç çağırmamaya başladı. Ölçüm sebebi gösterdi:

| Parça | Boyut |
|---|---|
| sistem prompt'u | 6 515 karakter |
| 7 aracın şeması + açıklaması | 12 933 karakter |
| **toplam** | **19 448 karakter ≈ 5 900 token** |

8192'lik pencerede araç sonuçlarına ~2 300 token kalıyordu; 66 duraklı bir
güzergâh tek başına bunu aşıyor. Bozulma karar 0006'da ölçülenin aynısı:
model hızlı ama araçsız ve yanlış cevap veriyor.

`OLLAMA_NUM_CTX` varsayılanı **16384** oldu. Aynı iki senaryo (`rail-01`,
`know-01`) 8192'de 0/2, 16384'te **2/2** geçiyor. Karar 0006'nın
"araç sayısı artarsa yeniden ölç" koşulu tam olarak buydu.

## Karar 5 — Hangi kusur bizim, hangisi modelin

MCP tarafı düzeltildikten sonra aynı soru iki sağlayıcıyla soruldu:

| Sağlayıcı | Araç | Sonuç |
|---|---|---|
| `gemini-3.5-flash` | `search_knowledge` | ✓ SSS'teki doğru cevap, 25.8 s |
| `qwen3.5:4b` (num_ctx 8192) | yok | ✗ uydurma |
| `qwen3.5:4b` (num_ctx 16384) | `search_knowledge` | ✓ doğru |

Yani bağlama doğruydu; yerel modelin sorunu bağlam penceresiydi. Bu ayrım
olmadan "model yetersiz" demek, kendi hatamızı modele yüklemek olurdu.

## Karar 6 — Türkçe karşılaştırmada `casefold()` kullanılmaz

Değerlendirme betiği cevapta beklenen ifadeyi arıyor ve ilk sürümü
`str.casefold()` kullanıyordu. `"YAKLAŞIK".casefold()` **noktalı** `i`
üretiyor, aranan kelime ise `yaklaşık` (noktasız `ı`) — eşleşme kaçıyor ve
ölçüm doğru cevabı yanlış sayıyor. Bir birim testi bunu yakaladı.

`store/station_names.fold_case()` eklendi: Türkçe harfleri katlıyor ama
`normalize()` gibi tesis eklerini atmıyor. Karşılaştırma yapan her yer
bunu kullanır.

## Senaryo seti

`eval/agent_scenarios.jsonl` — 48 senaryo, yedi aracın tamamını kapsıyor:

| Grup | Senaryo | Ne ölçülüyor |
|---|---|---|
| `rail-*` | 6 | süre, aktarma, tahmin hata payı, olmayan istasyon |
| `dep-*` | 6 | ilk/son sefer, aralık, tazelik bayrağı, hat dışı istasyon |
| `place-*` | 4 | ad → konum, olmayan yer |
| `near-*` | 4 | çevre araması, mod filtresi |
| `lines-*` | 3 | duraktan hatlar, hat verisi olmayan mod |
| `route-*` | 5 | güzergâh, ilk kalkış, olmayan hat, ara durak saati yokluğu |
| `know-*` | 8 | SSS kuralları, güncel/tarihsel fiyat, veri seti açıklamaları |
| `chain-*` | 5 | iki ve üç adımlı zincirler, sıra kontrolü |
| `refuse-*` | 8 | Faz 2'de olmayan araçlar: uydurmak yerine yokluğu söylemek |

Her senaryoda beklenen araçlar, gerekiyorsa **sıra**, ve cevapta geçmesi
gereken ifadeler yazılı. Son kısım önemli: doğru aracı çağırıp tazelik
uyarısını yutmak da hatadır.

Ölçülen oranlar: araç doğruluğu, sıra doğruluğu, ifade doğruluğu ve
**kaynaksız cevap sayısı**. Kota (Gemini: günde 20 istek) nedeniyle
varsayılan sağlayıcı yerel model; cevaplar `eval/.cache/` altında soru +
sağlayıcı + model + **prompt özetine** göre önbelleğe alınıyor. Prompt
değiştiğinde anahtar değişiyor, çünkü prompt değişikliği davranış
değişikliğidir (CLAUDE.md kuralı).

<!-- SONUÇ TABLOSU -->

## Yeniden gözden geçirme koşulları

- `city_server` eklenince (Faz 2) araç sayısı 7'den 10'un üzerine çıkar;
  bağlam penceresi ve araç seçimi yeniden ölçülmeli, tek ReAct döngüsünün
  yettiği yeniden sorgulanmalı (karar 0006'nın koşulu).
- Yerel model değişirse `num_ctx` ve düşünme ayarı yeniden ölçülmeli.
- RAGAS (cevap sadakati) hâlâ çalıştırılmadı; Gemini kotası günde 20 istek
  olduğu sürece 48 senaryo × kip ölçümü yapılamaz. Faz 5'te yerel modelle
  ya da kota artırımıyla ele alınacak.
