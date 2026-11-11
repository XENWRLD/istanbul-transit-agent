# 0014 — Değerlendirmeyi hızlandırma: sabit maliyeti kaldırmak, paralelliği reddetmek

**Tarih:** 11 Kasım 2026
**Durum:** uygulandı
**İlgili:** [0006](0006-mcp-ve-agent.md) (bağlam bütçesi), [0010](0010-entegrasyon.md) (gömme modeli açılışta yüklenir), [0011](0011-degerlendirme-ve-paketleme.md) (değerlendirme), [0013](0013-canli-veri-dogrulama.md) (canlı veri düzeltmeleri)

## Bağlam

Karar 0013'teki düzeltmeler araç çıktılarını değiştirdi, yani 54 senaryonun
yeniden koşulması gerekiyordu. Yerel modelde tam koşu bir saate yakın. Soru
şuydu: **doğruluğu düşürmeden** daha hızlı koşabilir miyiz?

## Ölçüm — süre nereye gidiyor

Önce yığın açılışı ölçüldü:

| Açılan MCP sunucuları | Süre |
|---|---|
| transit + knowledge + city | **15.0 s** |
| knowledge çıkarılınca | 2.6 s |
| yalnız transit | 1.6 s |

Fark BGE-M3. `knowledge_server` gömme modelini **açılışta** yüklemek zorunda
(karar 0010: araç gövdesinde yüklemek FastMCP'nin iş parçacığı havuzunda
sunucuyu kilitliyor). `answer()` ise her soru için kendi yığınını açıp
kapatıyordu ve zamanlayıcıyı açılıştan **önce** başlatıyordu — yani eski
`seconds` değerleri bu 15 saniyeyi içeriyordu ve 54 senaryoda ~13.5 dakika
sadece gömme modelini yeniden yüklemeye gidiyordu. Senaryoların çoğu
`search_knowledge` çağırmıyor bile.

## Karar 1 — `answer()` ikiye ayrıldı

`ask(agent, question, ...)` **açık** bir agent'la çalışıyor; `answer()` onu
saran, kendi yığınını açan tek-soru yolu (CLI, FastAPI, Gradio bunu
kullanıyor, davranışları değişmedi). Değerlendirme betiği yığını koşu
başına bir kez açıyor.

Modele giden girdi byte byte aynı: aynı sistem prompt'u, aynı araç şemaları,
aynı soru, her senaryo için sıfırdan mesaj geçmişi. Paylaşılan tek şey MCP
oturumları ve LLM istemcisi. Doğrulama için aynı senaryolar iki kez koşuldu:

| Senaryo | 1. koşu | 2. koşu |
|---|---|---|
| rail-03 | ✓ 541.7 s, 7 çağrı | ✓ 544.6 s, **aynı 7 çağrı** |
| rail-04 | ✗ 54.8 s, 2 çağrı | ✗ 47.2 s, **aynı 2 çağrı** |
| rail-05 | ✓ 12.9 s | ✓ 12.8 s |

Davranış tekrarlanıyor, oynayan tek şey süre. Sıcaklık 0.0 ama tohum yok;
`llama.cpp` kısmi GPU boşaltmayla bit düzeyinde yinelenebilir değil, bu
yüzden süre dalgalanıyor — araç seçimi dalgalanmıyor.

## Karar 2 — Paralellik eklendi ama **kullanılmıyor**

`--concurrency N` seçeneği var, varsayılanı 1 ve bu makinede 1 kalıyor.
Gerekçe kazayla ölçüldü: ilk 6 senaryoluk koşu dönerken ben aynı makinede
671 testlik takımı ve GPU/bellek sondalarını çalıştırdım. Sonuç:

| Senaryo | Yanında başka iş varken | Makine sessizken |
|---|---|---|
| rail-02 | ✗ 144.9 s, `ReadTimeout` | ✓ **14.4 s** |

Yani kaynak çekişmesi yanlış cevap üretmiyor, **sahte başarısızlık**
üretiyor: tek bir LLM isteği 120 saniyelik `LLM_REQUEST_TIMEOUT`'u aşıyor ve
senaryo hatayla düşüyor. `--concurrency 2` tam bu çekişmeyi kasten yaratmak
olurdu. 4 GB VRAM'de her paralel şerit kendi KV önbelleğini de istiyor.

Seçenek yine de duruyor: daha büyük bellekli bir makinede ya da zaman aşımı
yükseltilerek anlamlı olur. Varsayılanı 1 yapmak, ölçülmemiş bir hızlanma
için ölçümün kendisini riske atmamak demek.

**Çıkarılan kural:** değerlendirme koşarken aynı makinede başka iş
çalıştırılmaz. Benim yaptığım hata ölçümü kirletti ve bir senaryoyu yanlışlıkla
başarısız gösterdi.

## Ölçülmeyen şeyi ölçülmüş gibi yazmamak

Tek bir temiz koşu: **3433 s (57.2 dakika)**, 54 senaryo, hiçbiri
önbellekten. Eski kodun temiz duvar saati **yok** — eski 59.5 dakikalık sayı
satır sürelerinin toplamıydı ve 41 satırı farklı zamanlarda koşulmuş
önbellek kayıtlarından geliyordu, yani tek koşunun duvar saati değildi.

Sağlam karşılaştırma şunlar:

| Ölçü | Önce | Sonra |
|---|---|---|
| Gecikme ortancası | 46.8 s | **36.0 s** (−%23) |
| Yığın açılışı (ölçülen sabit) | 15.0 s × 54 ≈ 13.5 dk | bir kez |

Sabit 15 saniye doğrudan ölçüldüğü için eski kodun aynı işi ~70 dakikada
bitireceği söylenebilir — ama bu bir **tahmin**, ölçüm değil.

## Sonuç — 54 senaryo, hepsi taze

| Ölçü | Değer |
|---|---|
| Geçen senaryo | **47/54 (%87.0)** |
| Araç doğruluğu | %88.9 |
| Sıra doğruluğu | %50.0 |
| İfade doğruluğu | %92.3 |
| Kaynaksız cevap | 3 |
| Uyarılan tur | 8 |
| Hata | **0** |
| Adım ortancası | 1.0 (en çok 7) |

Önceki ölçüm 48/54'tü. Değişenler:

- **Düzelen (2):** `chain-02` (eskiden `geocode_place`'i beş kez çağırıp adım
  tavanına çarpıyordu), `lines-03`
- **Yeni düşen (3):** `rail-04` (`get_rail_travel_time` yerine iki kez
  `geocode_place`), `near-04` (`find_nearby_stops` yerine `geocode_place`),
  `route-04` (hiç araç çağırmadı)
- **Hâlâ düşen (4):** `chain-05`, `rail-06`, `refuse-08`, `route-05`

Net **−1 senaryo**. Sebep hızlandırma değil, karar 0013: araç çıktılarına
alan eklemek şema yüzeyini 18918 → 20736 karaktere (+%9.6) büyüttü ve bu
prompt'u kaydırıp araç seçimini değiştirdi. Üç yeni başarısızlığın hepsi
**eksik seçim** (özel araç yerine `geocode_place`, ya da hiç araç), iki
düzelme de aynı kaymanın öbür yüzü. CLAUDE.md'nin kuralı tam bunu söylüyordu:
araç açıklamasını değiştirmek davranış değişikliğidir.

Bu −1 kabul edilmiş değil, **ölçülmüş**. Düzeltmek prompt ya da araç
açıklaması değişikliği gerektirir, o da yeni bir 57 dakikalık koşu demektir;
ayrı bir iş olarak duruyor.

## Uzun kuyruk nerede

| Senaryo | Süre | Çağrı |
|---|---|---|
| rail-03 | 533 s | 7 × `get_rail_travel_time` |
| chain-01 | 260 s | — |
| route-05 | 148 s | — |
| chain-05 | 134 s | — |

Süreyi yığın değil **tur sayısı** belirliyor: model aynı aracı tekrar tekrar
çağırıyor ve her turda bağlam büyüdüğü için tur başına maliyet ~13 s'den
~78 s'ye çıkıyor. `rail-03` tek başına toplam sürenin dokuz dakikası. Gerçek
hızlanma buradan gelir — döngüyü kesmekten, altyapıdan değil. Ama o,
doğruluğa dokunan bir değişiklik.

## Önbellek anahtarının boşluğu

Anahtar `sağlayıcı-model-promptözeti-senaryo`. `prompt.py` değişmediği için
karar 0013'ün araç çıktısı değişiklikleri önbelleği **geçersiz kılmadı** ve
`--refresh` verilmeden koşulsa düzeltmelerden önceki cevaplar yeniden
raporlanırdı. Araç şemaları anahtara girmiyor; bu boşluk biliniyor ve
kapatılmadı.
