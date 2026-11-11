# 0012 — Canlı veri araçları: SOAP kararı, dört yeni araç, bir ölü kaynak

**Tarih:** 11 Kasım 2026
**Durum:** uygulandı
**İlgili:** [0001](0001-veri-kaynaklari.md) (uç nokta envanteri), [0005](0005-sefer-saatleri.md) (aralık kuralı), [0007](0007-iett-beslemesi.md) (ara durak saati yok), [0010](0010-entegrasyon.md) (entegrasyon)

## Bağlam

Faz 2 Faz 3-5'ten sonraya ertelenmişti; bu turda canlı araç konumu **hariç**
bütün maddeleri yapıldı. Araç konumu kullanıcı kararıyla askıda (bkz.
`documents/PLAN.md` araç tasarımı bölümü) ve bu notun konusu değil.

Yapılanlar: ortak istemci katmanı, İETT SOAP istemcisi, dört yeni araç
(`get_service_alerts`, `get_station_accessibility`, `find_parking`,
`get_traffic_status`), `get_next_departures`'a otobüs desteği ve
`city_server`'ın agent'a bağlanması. Araç sayısı 7 → **11**.

## Karar 1 — İETT SOAP: `zeep` değil, elle kurulan zarf

PLAN.md'nin açık bıraktığı karar. Ölçüm (`scripts/probe_soap_client.py`):

| Servis | `zeep` | elle zarf |
|---|---|---|
| `duyurular` (351) | ✓ 0.90 s, 192 kayıt | ✓ **0.45 s**, 192 kayıt |
| `planlanan-sefer-saati` (350) | ✗ `No definition '…GetPlanlananSeferSaati_XMLAuthHeader' in 'messages'` | ✓ **0.17 s**, 267 kayıt |

İki servisten biri `zeep` ile **hiç** açılmıyor (WSDL `AuthHeader`
bildirmiş ama eşleşen `<message>` tanımı yok), açılan serviste de `zeep`
iki kat yavaş — her istemci kurulumunda WSDL'i indirip ayrıştırıyor.

**Karar:** tek kod yolu, elle kurulan SOAP 1.1 zarfı. Tek bir protokol için
iki yol tutmak, ikinci yolun yalnızca bir serviste çalıştığı bir durumda
bakım yükünden başka bir şey getirmez. `zeep` bağımlılık listesinde
kalıyor çünkü karşılaştırma betiği onu kullanıyor; ürün kodu kullanmıyor.

Servis JSON'ı **XML gövdesinin içinde metin olarak** döndürüyor. Ayrıştırma
önce SOAP `Fault` arıyor: servis hatayı 200 ile de döndürebiliyor ve
gövdeyi JSON sanıp ayrıştırmaya çalışmak hatayı gizler.

## Karar 2 — Ortak katmanda üç kural

`clients/base.py`:

1. **Hata yutulmaz.** Erişilemeyen kaynak `LiveDataError` fırlatıyor,
   araç bunu kullanıcıya dönük bir cümleye çeviriyor. "Otopark yok" ile
   "otopark verisine ulaşamadım" aynı şey değil; araçlar bunu
   `error: "canli_veri_yok"` ile ayırıyor.
2. **Yeniden deneme yalnızca geçici hatada.** Zaman aşımı, bağlantı hatası
   ve 5xx üstel geri çekilmeyle yeniden denenir; 4xx denenmez — istek
   yanlışsa tekrarı da yanlıştır.
3. **TTL önbellek kaynak bazında.** Otopark doluluğu 60 s, trafik 300 s,
   otobüs tarifesi 6 saat, istasyon listesi 24 saat. Tek bir TTL ikisine de
   yanlış gelir. Ölçüm: 500T sefer saatleri ilk çağrıda 0.28 s, önbellekten
   **0.003 s**.

Önbellek süreç içi; MCP sunucusu uzun yaşayan bir süreç olduğu için
yeterli, çok süreçli ölçekte paylaşılan bir önbellek gerekir.

## Karar 3 — Hava kalitesi aracı **yok**: kaynakta veri yok

`GetAQIStations` 28 istasyonu konumlarıyla veriyor (WKT: `POINT (lon lat)`).
`GetAQIByStationId` de cevap veriyor. Ama:

| Deneme | Sonuç |
|---|---|
| parametresiz | HTTP 200, **721 kayıt**, dolu **0** |
| `StartDate`/`EndDate` (`dd.mm.yyyy HH:MM:SS`) | HTTP 200, 25 kayıt, dolu **0** |
| ISO tarih biçimi | HTTP 200 ama gövde JSON değil (hata metni) |
| 12 istasyon tek tek | **12/12** okuma yok |

Yani uç nokta çalışıyor, tarih parametresini bile doğru biçimde kabul
ediyor, ama `AQI` ve `Concentration` alanlarının **tamamı null**.

**Karar: `get_air_quality` yazılmadı.** Faz 0'da İsbike için verilen
kararın aynısı (169 HTTP 200 veriyor ama `dataList` boş → kapsam dışı).
Veri olmayan bir kaynağın üstüne araç koymak, agent'a "bilmiyorum" demenin
daha pahalı bir yolunu eklemek olurdu. İstemci kodu (`clients/city.py`)
duruyor ve veri gelmeye başlarsa araç bir sarmalayıcı kadar uzakta;
`parse_air_quality` hiç dolu kayıt yoksa `None` döndürüyor, sıfır
uydurmuyor.

Bu, kullanıcının istediği kapsamdan **bir maddenin düşmesi** demek ve
sebebi ölçüm. Prompt'ta hava kalitesi "elinde yok" listesinde kaldı.

## Karar 4 — Otobüs sefer saatleri: aralık kuralı metrodan devralındı

`get_next_departures` artık raylı sistemde bulunamayan hattı İETT'nin
`GetPlanlananSeferSaati_json`'ında arıyor. Gün tipi kodları ölçüldü:
`SGUNTIPI` = `I` / `C` / `P` → hafta içi / cumartesi / pazar (500T'de üç
tip de 234 kayıt).

Özet (yön + gün tipi başına ilk/son kalkış, sefer sayısı, aralık) iki kuralı
karar 0005'ten devralıyor:

- aralık **ortanca** boşluk, ortalama değil — gece boşlukları ortalamayı
  bozuyor,
- **4 seferden az** kayıttan aralık üretilmiyor.

Bilinmeyen bir gün tipi kodu atılmıyor, olduğu gibi taşınıyor: sessizce
düşürmek hattın o gün hiç seferi yokmuş gibi görünmesine yol açardı.

**Saatler ilk duraktan kalkışı gösteriyor.** Ara durak saati bu kaynakta da
yok; karar 0007'nin bulgusu değişmedi ve araç bunu her cevapta not olarak
söylüyor.

## Karar 5 — Duyuru kaynağı hat koduna göre seçilir

İlk sürümde `get_service_alerts(line="500T")` sorgusu Metro İstanbul'un ağ
geneli duyurusunu da döndürdü ("tüm hatlarımızda kış tarifesi"), çünkü o
duyuru hiçbir hat kodu taşımıyor ve "her hat" sayılıyor.

Artık mod verilmediğinde hat kodundan çıkarılıyor: `M2`, `T1`, `F4`, `TF2`,
`Marmaray` raylı; geri kalanı otobüs. Ölçüm: 500T → yalnızca İETT, M7 →
yalnızca Metro kaynakları.

Kaynaklardan biri erişilemezse araç **yine cevap veriyor** ve
`sources_failed` ile hangisinin eksik olduğunu söylüyor; hepsi
erişilemezse `canli_veri_yok` dönüyor.

## Karar 6 — Erişilebilirlik: donanım + anlık arıza birlikte

`get_station_accessibility` iki kaynağı birleştiriyor:
`GetStations.DetailInfo` (istasyon başına asansör/yürüyen merdiven sayısı,
248 istasyon) ve `GetFaultyEquipmentDetails` (POST, `EquipmentGroupName`
ister; istasyon adıyla arıza listesi).

Canlı ölçüm: 1966 yürüyen merdivenin 56'sı, 669 asansörün 29'u arızalı;
arızalı asansörler istasyon ve tarihle listeleniyor (ör. "F1 Kabataş,
2026-11-08").

Araç her cevaba şunu ekliyor: **arıza listesi yalnızca bildirilmiş
arızaları içerir, listede olmaması çalıştığı garantisi değildir.** Asansörü
olmayan istasyon (0) ayrıca işaretleniyor — tekerlekli sandalye sorusunda
belirleyici olan bu.

## Bulunan iki kusur

### Araçlar sunucuda görünmedi

İki yeni transit aracı in-process `list_tools()` ile görünürken MCP stdio
üzerinden **görünmedi** (6 yerine 8 beklenirken 6). Sebep: araçlar dosyaya
`main()` ve `if __name__ == "__main__"` bloğundan **sonra** eklenmişti;
sunucu modül olarak çalıştığında `mcp.run()` o noktada bloke ediyor ve
sonraki kod hiç çalışmıyor. Giriş noktası dosya sonuna taşındı.

Ders: modül sonuna kod eklerken giriş noktasının nerede olduğuna bakmak
gerekiyor; in-process test bu hatayı yakalamıyor.

### Trafik eğilimi gereksizce "bilinmiyor" diyordu

`TrafficIndex.trend` iki geçmiş nokta istiyordu; tek nokta da
karşılaştırmaya yeter. Bir birim testi bunu yakaladı ve kural "hiç geçmiş
yoksa bilinmiyor" oldu.

## Ölçüm: agent üzerinden 13 senaryo

Senaryo seti 49 → **54** oldu: beş yeni senaryo (`live-01…05`) ve dört
"araç yok" senaryosu artık gerçek araç senaryosu (`refuse-01`, `-02`,
`-06`, `-07`). Araç beklenmeyen üç senaryo kaldı: hava kalitesi (veri yok),
canlı araç konumu (askıda), vapur tarifesi (kaynak yok).

Prompt değiştiği için senaryo önbelleği geçersiz oldu (anahtar prompt
özetini içeriyor). Değişen ve yeni senaryolar yerel modelle koşuldu:

| Senaryo | Araç | Sonuç |
|---|---|---|
| `live-01` 500T iptal var mı | `get_service_alerts` | ✓ (ilk denemede Ollama çöktü, tekrarda geçti) |
| `live-02` M7'de sorun var mı | `get_service_alerts` | ✓ |
| `live-03` 500T ilk kalkış | `get_line_route` | ✓ |
| `live-04` kaç asansör arızalı | `get_station_accessibility` | ✓ |
| `live-05` Kadıköy'de park yeri | `find_parking` | ✓ |
| `refuse-01` Taksim'de boş otopark | `find_parking` | ✓ |
| `refuse-02` trafik ne durumda | `get_traffic_status` | ✓ |
| `refuse-03` hava kalitesi | (araç yok) | ✓ yokluğu söyledi |
| `refuse-04` otobüs şu an nerede | (askıda) | ✓ |
| `refuse-05` sıradaki vapur | (kaynak yok) | ✓ |
| `refuse-06` asansör çalışıyor mu | `get_station_accessibility` | ✓ |
| `refuse-07` tekerlekli sandalye | `get_station_accessibility` | ✓ |
| `refuse-08` arabayla mı metroyla mı | `get_traffic_status` | ✗ araç çağırmadı, kaynaksız |

**12/13.** `refuse-08` gerçek bir başarısızlık: trafik verisi artık var,
modelin indeksi çekip "süre karşılaştırması yapamam" demesi gerekiyordu.

Bir de ortam sınırı görüldü: `live-01` ilk denemede `ResponseError: model
runner has unexpectedly stopped` ile düştü. 11 araç + 7729 karakter prompt
+ 16384 bağlam, 16 GB RAM'de Ollama'yı zorluyor. Tekrarda geçti; ölçüm
yapılırken bu tür çökmeler tekrar denenmeli ve raporlanmalı.

**Kalan 41 senaryo bu turda yeniden koşulmadı**; prompt değiştiği için
önbellek boş ve tam koşu yerel modelde ~1 saat sürüyor.

## Bağlam bütçesi (yeniden ölçüldü)

| | Faz 4 (7 araç) | Faz 2 sonrası (11 araç) |
|---|---|---|
| sistem prompt'u | 6 515 karakter | 7 729 karakter |
| araç şemaları | 12 933 karakter | 18 918 karakter |
| **toplam** | ~5 900 token | **~8 075 token** |
| `num_ctx` | 16 384 | 16 384 (değişmedi) |

Araç sonuçlarına kalan pay ~10 500 → ~8 300 token'a düştü. Şimdilik
yetiyor; araç sayısı artarsa (Faz 6) `num_ctx` ya da araç açıklamalarının
uzunluğu yeniden ölçülmeli.

## Yeniden gözden geçirme koşulları

- Hava kalitesi uç noktası veri yayımlamaya başlarsa `get_air_quality`
  bir sarmalayıcı kadar uzakta; `parse_air_quality` hazır.
- İETT WSDL'leri düzelirse `zeep` yeniden değerlendirilebilir; bugünkü
  ölçümde hız açısından da gerek yok.
- 54 senaryonun tamamı yerel modelle yeniden koşulmalı; bu turda 13'ü
  koşuldu.
- Ollama çökmesi tekrarlanırsa `num_ctx` düşürmek ya da araç
  açıklamalarını kısaltmak ölçülmeli.
