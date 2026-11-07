# 0004 — Raylı sistem süre modeli ve aktarma süresi

- **Tarih:** 8 Kasım 2026
- **Durum:** Kabul edildi
- **Aşama:** Faz 1
- **Kanıt:** `scripts/analyze_rail_speeds.py`, `scripts/fit_travel_model.py`,
  `scripts/validate_travel_estimates.py`, `scripts/measure_transfer_wait.py`,
  `scripts/inspect_route_factor.py`
- **İlgili:** [0001](0001-veri-kaynaklari.md) (K2), [0002](0002-gtfs-servis-gunleri.md),
  [0003](0003-istasyon-eslestirme.md)

## Bağlam

`get_rail_travel_time`'ın canlı kaynağı (`GetStationBetweenTime`) sunucu taraflı
bozuk (karar 0001, K2), bu yüzden süreler 2020 tarihli GTFS beslemesinden
geliyor. Besleme bugünkü ağın %78'ini kapsıyor; kalan 49 istasyon için
(2020 sonrası uzatmalar, F4 ve T5 hatları) süre **tahmin** edilmek zorunda.

Bu not üç şeyi belgeliyor: mesafe nereden gelir, süre nasıl hesaplanır,
aktarma kaç saniyedir.

## Karar 1 — Mesafe: önce veri seti 312, sonra koordinat

Veri seti 312 (`Raylı Sistemlere Ait Hat Uzunluk Bilgileri`) ardışık
istasyonlar arası **ray uzunluğunu metre cinsinden** veriyor ve GTFS'te hiç
olmayan M10, M12, M4U, T5 hatlarını da içeriyor. Eksik 61 istasyonun 38'i bu
tabloda bulunuyor.

Tablo kullanılabilir hale getirilmesi gereken üç kusur taşıyor:

| Kusur | Adet | Ele alınışı |
|---|---|---|
| Excel `#N/A` değeri | 3 | dışlandı |
| Ayırıcı olarak tire değil **en-dash + kırılmaz boşluk** (M7 satırları) | 11 | ayırıcı kümesi genişletildi, satırlar kurtarıldı |
| İmkansız uzunluk | 2 | dışlandı: M3 `Başak Konutları-Siteler` 254 m (kat 0.22), M4 `Maltepe-Küçükyalı` 10.994 m (kat 5.29) |

Ayrıca M9'da 929.542 m ve 854.850 m değerleri var. Bin kat kaydırma hatası
gibi duruyor (929,5 m ve 854,9 m makul olurdu) ama bunu varsaymak **veri
uydurmak** olacağı için ikisi de dışlandı ve dışlanma gerekçesiyle kaydedildi.

### Eşleştirme hat kapsamlı olmak zorunda

312'nin adlarını GTFS'e bağlamak iki çakışma yüzünden küresel yapılamaz:

- **Nitelenmiş adlar:** GTFS `Ataköy - Şirinevler`, `Bakırköy - İncirli`,
  `Davutpaşa - YTÜ` yazarken 312 `Atakoy`, `Bakırkoy`, `Davutpasa` diyor.
  Token alt kümesi eşleşmesi çözüyor, ama yalnızca tek bir hattın içinde;
  küresel yapılsa ayrı istasyonları birleştirir.
- **Tekrarlanan adlar:** `Yenimahalle` hem M3'te hem M7'de, `Çarşı` hem
  tramvayda hem minibüs hattında var. Hat kapsamı olmadan eşleştirme ilk
  gördüğünü seçer ve **sessizce yanlış mesafeyi** bildirir.

Hat kapsamlı eşleştirmeden sonra: **148 eşleşmenin tamamı GTFS'te komşu
çiftler.** Yani eşleştirme doğru çalışıyor.

### Güzergâh şişirme katsayısı yok

Başlangıç tasarımı "ray düz çizgiden uzundur, bir katsayı uygula" diyordu.
Ölçüm bunu çürüttü. 148 eşleşmenin ray/kiriş oranı **0.934'te ortalanmış
düzgün bir çan**, 0.6-1.4 aralığında:

```
  < 0.40    1 █
0.40-0.60   1 █
0.60-0.80  10 ████████████
0.80-0.90  40 ██████████████████████████████████████████████
0.90-0.95  32 █████████████████████████████████████
0.95-1.05  40 ██████████████████████████████████████████████
1.05-1.20  15 █████████████████
1.20-1.60   8 █████████
  > 1.60    1 █
```

Oranın 1'in altında olması fiziksel olarak imkansız — gerçek ray kirişten
kısa olamaz. Merkezin 1'in altında çıkması, iki kaynağın bir istasyonun
**nerede olduğu** konusunda ~%7 ayrışmasını gösteriyor (GTFS noktayı girişe,
312 peronlar arasına koyuyor), kimsenin yanlış olduğunu değil.

**Sonuç:** Bir metro durağı ölçeğinde kiriş mesafesi, ray uzunluğuyla gürültü
sınırları içinde aynı şeydir. Katsayı uygulanmıyor. Sınırlar (0.6-2.0)
yalnızca gerçek veri hatalarını yakalamak için, ve dağılım tam iki tane
gösteriyor.

## Karar 2 — Süre: tek ortalama hız değil, `sabit + mesafe / seyir hızı`

İlk model `mesafe / ortanca hız` idi. Doğrulama **sistematik -%11 yanlılık**
gösterdi: tahmin sürekli fazla hızlı.

Sebep fiziksel: tren bir istasyondan hızlanarak ayrılır, diğerine frenleyerek
girer. Bir durağın süresi uzunluğuyla **doğru orantılı değil**; ortalama hıza
bölmek bu iki manevranın bedelini hiç saymıyor.

Model `süre = sabit + mesafe / seyir_hızı` olarak en küçük kareler ile
uyduruldu; `sabit` hızlanma ve frenlemeyi soğuruyor.

| Model | Ort. \|hata\| | Max | Yanlılık |
|---|---|---|---|
| A — mesafe / ortanca hız | 13.7 s | 67 s | +0.31 s |
| B — sabit + mesafe/seyir (**mod** bazında) | 12.4 s | 63 s | +0.08 s |
| **C — sabit + mesafe/seyir (hat bazında, moda düşer)** | **11.0 s** | **52 s** | **-0.21 s** |

Seçilen: **C**.

### Uydurulan katsayılar

Mod bazında (`MODE_MODEL`):

| Mod | Sabit | Seyir hızı | n |
|---|---|---|---|
| metro | 33.9 s | 16.45 m/s (59 km/h) | 121 |
| tramvay | 27.5 s | 5.79 m/s (21 km/h) | 51 |
| funikuler | 0 s | 4.26 m/s | 3 — uydurmaya yetmedi, ortanca hız |
| teleferik | 0 s | 2.16 m/s | 2 — aynı |

Hat bazında kabul edilenler (`LINE_MODEL`): M1B, M2, M4, M5, M7, M8, T1, T4.

### İki hat bilinçli olarak reddedildi

En küçük kareler, mesafeleri az değişen bir düzine nokta üzerinde mutlu mutlu
saçma katsayı üretiyor:

- **M1A:** seyir hızı **46.3 m/s (166 km/h)** — bir metro için imkansız.
- **M3:** sabit **-4.2 s** — negatif, anlamsız.

Bu uydurmalar kendi noktalarını iyi açıklıyor (M1A'nın hatası 8.3 s) ama
**başka bir yere genellenemez** — ki tahminin tek kullanım amacı tam olarak
budur: beslemede hiç olmayan bir segmenti kestirmek. İkisi de moda düşürüldü.

Koruma sınırları: sabit 0-90 s arasında, seyir hızı modun uydurduğu hızın
0.6-1.6 katı arasında olmalı.

### İstasyonda bekleme

Ara istasyonlarda **15 s** eklenir (varışta eklenmez). 162.871 `stop_times`
satırının ortancası; dağılım son derece dar (max 15 s).

## Karar 3 — Aktarma süresi: 360 saniye (6 dakika)

Tek bir sabit, iki parçaya ayrılarak:

| Parça | Değer | Dayanak |
|---|---|---|
| Bağlantı treni beklemesi | **210 s** | **Ölçüldü.** Rastgele gelen bir yolcu H aralıklı bir hatta ortalama H/2 bekler. Hat bazında ölçülen ortalama beklemelerin ortancası 3.5 dk. |
| Peronlar arası yürüme | **150 s** | **Varsayım.** Peron düzeyinde veri yok. |
| **Toplam** | **360 s** | |

Ölçülen bekleme süreleri (`scripts/measure_transfer_wait.py`, hafta içi
07:00-20:00, karar 0002'nin hat bazlı kaynak seçimiyle):

| Hat | Sefer aralığı | Ortalama bekleme |
|---|---|---|
| M2 | 3.0 dk | 1.5 dk |
| M4 | 4.5 dk | 2.3 dk |
| T1, T4, F1 | 5.0 dk | 2.5 dk |
| M1A, M1B | 6.0 dk | 3.0 dk |
| M3, M3A, M5, M7 | 8.0 dk | 4.0 dk |
| M6 | 9.0 dk | 4.5 dk |
| M9, TF1, TF2 | 10.0 dk | 5.0 dk |

Ortanca 3.5 dk, en kötü hat 5.0 dk.

### Gerekçe

Aktarmanın hangi hatta yapılacağını önceden bilmediğimiz için hatlar arası
**ortanca** alındı; yoğun aktarma hatlarına (M2, M4, T1) ağırlık vermek daha
iyimser bir sayı verirdi ama kullanıcıyı sistematik olarak yanıltırdı.

Yürüme payı ölçülmedi, **varsayım olduğu açıkça işaretli**. Veri seti 487
istasyon büyüklüğünü (m²) veriyor; ileride peron düzeyinde veri gelirse bu
parça ölçülebilir hale gelir. O zamana kadar 150 s, Yenikapı (M1/M2/Marmaray)
gibi uzun aktarmalarla Taksim gibi kısa olanların arasında bir orta değer.

Sabit tek bir değer olarak tutuldu (istenen tasarım). Hat çiftine göre
değişken aktarma süresi, ölçülebilir veri geldiğinde değerlendirilecek.

## Doğrulama — tahmin ne kadar yanılıyor?

Tahmin, GTFS'in cevabını **bildiği** segmentlere uygulanıp karşılaştırıldı
(held-out test). İki ölçek:

### Tek segment (177 ardışık çift)

| Kırılım | Ort. \|hata\| | Ortanca | Max | Yanlılık |
|---|---|---|---|---|
| **Tümü** | **11.4 s** | 7 s | 52 s | -0.5 s |
| metro | 12.0 s | 7 s | 52 s | -1.0 s |
| tramvay | 8.8 s | 7 s | 28 s | +0.0 s |
| funikuler | 15.7 s | 3 s | 44 s | +13.7 s |
| teleferik | 38.0 s | 38 s | 43 s | -5.0 s |
| mesafe 312'den | 10.7 s | 6 s | 52 s | -0.2 s |
| mesafe koordinattan | 14.6 s | 10 s | 44 s | -1.8 s |

### Tam yolculuk (300 rastgele istasyon çifti)

| Kırılım | Ort. \|hata\| | Ortanca | Max | Yanlılık |
|---|---|---|---|---|
| **Tümü** | **%6.6** | %3.1 | %29.1 | -%4.1 |
| metro | %7.6 | %3.3 | %29.1 | -%5.1 |
| tramvay | %2.5 | %2.4 | %9.5 | +%0.1 |
| 2-3 durak | %10.9 | %6.5 | %29.1 | -%8.2 |
| 4-6 durak | %3.3 | %3.1 | %14.2 | -%0.4 |
| 7-10 durak | %2.2 | %1.8 | %8.1 | +%0.3 |
| 11+ durak | %1.4 | %1.2 | %5.3 | +%0.1 |

Mutlak olarak: ortalama hata **1.0 dakika**, en kötü **3.7 dakika**.

### Okunuşu

- **Uzun yolculuklar daha doğru.** 11+ duraklı yolculuklarda hata %1.4; tek
  tek segment hataları birbirini götürüyor.
- **En zayıf yer kısa yolculuklar.** 2-3 duraklı yolculuklarda %10.9. Mutlak
  hata küçük (30-60 s) ama yüzde olarak büyük görünüyor.
- **312 koordinattan iyi.** Ray uzunluğu kullanıldığında hata 10.7 s,
  koordinat kirişi kullanıldığında 14.6 s. Önce 312'ye bakılması bu yüzden.
- **Teleferik ve funikuler güvenilmez** (n=2 ve n=3). Bu modlarda tahmin
  dönerken araç bunu ayrıca belirtmeli.

### Bilinen en kötü durum: M6

M6 (Levent-Hisarüstü, 4 istasyon) tutarlı olarak **%29 düşük** tahmin
ediliyor: gerçek 6.8 dk, tahmin 4.8 dk. M6 segmentleri 662 m'yi 125 s'de
alıyor (5.3 m/s) — metro ortancasının üçte biri. Hat yalnızca 3 segment
taşıdığı için hat bazlı uydurma eşiğini (8 nokta) geçemiyor ve metro moduna
düşüyor.

Bu bilinçli bir sınır: 3 noktaya uydurma yapmak M1A'da reddedilen hatanın
aynısı olurdu. Araç M6 için tahmin döndüğünde süre gerçekten daha uzun
olabilir.

## Sonuç — araç çıktısı

`get_rail_travel_time` her zaman şunları döner:

| Alan | Değer |
|---|---|
| `method` | `gtfs` \| `estimated` \| `transfer` \| `unavailable` |
| `estimated` | `true` ise süre modelden geldi |
| `is_stale` | GTFS kaynaklı her sonuçta `true` |
| `data_as_of` | `2020-07-09` |
| `source` | hangi veri setlerinin kullanıldığı |
| `transfer_penalty_s` | aktarma varsa 360 |

## Yeniden gözden geçirme koşulları

- Metro `GetStationBetweenTime` düzelirse süreler canlı API'den alınır, bu
  model yalnızca yedek olur.
- Peron düzeyinde yürüme verisi gelirse aktarma sabitinin 150 s'lik yarısı
  ölçülebilir.
- Yeni bir GTFS beslemesi gelirse `fit_travel_model.py` ve
  `validate_travel_estimates.py` yeniden çalıştırılmalı; katsayıların
  tamamı o betiklerin çıktısı.
