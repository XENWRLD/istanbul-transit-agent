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

## Dış doğrulama

Held-out test, tahminin **beslemeye** ne kadar uyduğunu ölçüyor. Beslemenin
**gerçeğe** ne kadar uyduğu ayrı bir soru ve ancak dış kaynakla ölçülebilir.
Kullanıcı 8 Kasım 2026'da altı yolculuk için bağımsız süre getirdi;
`datasets/external_travel_times.csv` bunları kaynak URL'si ve tarihiyle
tutuyor, `tests/test_external_travel_times.py` her çalıştırmada sapmayı
raporluyor.

| Hat | Yolculuk | Bizim | Dış | Fark | Not |
|---|---|---|---|---|---|
| M2 | Yenikapı → Hacıosman | 29.1 dk | 32 dk | **-%9.1** | |
| M4 | Kadıköy → Tavşantepe | 38.2 dk | 38.5 dk | -%0.9 | |
| T1 | Kabataş → Bağcılar | 67.8 dk | 63 dk | **+%7.6** | |
| F1 | Taksim → Kabataş | 2.0 dk | 2.5 dk | -%20.0 | 30 s; yuvarlama |
| M4 | Kadıköy → Sabiha Gökçen | 50.0 dk | 52 dk | -%3.8 | bizimki tahmin |
| M5 | Üsküdar → Çekmeköy | 26.7 dk | 34 dk | -%21.6 | **kaynak doğrulanmadı**, kalibrasyonda kullanılmıyor |

### Sapmaların nedeni — üç hipotez, biri doğru

**1. Durakta bekleme hesaba girmiyor mu?** Hayır, giriyor. `stop_times`
satırlarının **%95.7'si 15 s bekleme kaydediyor** (kalan %4.3 son duraklar,
0 s). Bizim `DWELL_S = 15` değeri beslemenin kendi figürü.

**2. Ortancaya yoğun saat / gece seferleri karışıyor mu?** Hayır, karışması
fark etmiyor. Saat dilimine göre ayrıştırıldığında segment süreleri **her
dilimde özdeş**:

| Hat | gece | sabah zirve | gün ortası | akşam zirve | akşam |
|---|---|---|---|---|---|
| M2 | 104 s | 104 s | 104 s | 104 s | 104 s |
| M4 | 107 s | 107 s | 107 s | 107 s | 107 s |
| T1 | 118 s | 119 s | 119 s | 119 s | 117 s |

Besleme tek bir idealize tarife taşıyor; zirve/zirve-dışı varyantı yok.
Dolayısıyla T1'in +5 dk'sı sefer seçiminden gelemez.

**3. Boru hattımız beslemeyi doğru okuyor mu?** Evet, birebir. Beslemenin
kendi uçtan uca sefer süreleri ile bizim toplamımız aynı:

| Hat | Beslemenin sefer süresi | Bizim sonucumuz |
|---|---|---|
| M2 | 29.1 dk | 29.1 dk |
| M4 | 38.4 dk | 38.2 dk |
| M5 | 26.6 dk | 26.7 dk |
| T1 | 67.8 dk | 67.8 dk |

**Sonuç:** Sapmalar bir hesaplama hatası değil; **2020 tarihli beslemenin
bugünkü işletmeden ayrışması**. Ve ayrışmanın yönü hatta göre değişiyor: M2
3 dakika iyimser, T1 5 dakika kötümser. Bu yüzden küresel bir düzeltme
(örneğin bekleme süresini artırmak) M2'yi düzeltip T1'i bozardı.

### Kalibrasyon önerisi — onay bekliyor, uygulanmadı

Ayrışma hat bazında ve farklı yönlerde olduğu için düzeltme de hat bazında
olmalı: güvenilir bir dış kaynağı olan hatta segment süreleri `dış / bizim`
oranıyla ölçeklenir, sonuç `method: gtfs_calibrated` döner.

| Hat | Oran | Şimdi | Kalibreli | Dış | Kullanılır mı |
|---|---|---|---|---|---|
| M2 | 1.100 | 29.1 dk | 32.0 dk | 32.0 dk | ✅ |
| M4 | 1.009 | 38.1 dk | 38.5 dk | 38.5 dk | ✅ |
| T1 | 0.929 | 67.8 dk | 63.0 dk | 63.0 dk | ✅ |
| M5 | 1.276 | — | — | 34.0 dk | ❌ dış kaynak doğrulanmadı |
| F1 | 1.250 | — | — | 2.5 dk | ❌ 1 durak, 2 dk — yuvarlamaya uyar |
| M4 (S.Gökçen) | 1.040 | — | — | 52.0 dk | ❌ bizimki tahmin; GTFS segmentini kalibre etmez |

Bir oranın kullanılabilmesi için gereken koşullar:

- dış kaynak doğrulanmış olmalı,
- bizim sonucumuz `gtfs` olmalı (tahmini kalibre etmek döngüsel olurdu),
- yolculuk **en az 5 durak ve 10 dakika** olmalı — F1'in 2 dakikalık
  yolculuğundaki 30 saniyelik fark üzerinden tüm hattı %25 ölçeklemek, dış
  kaynağın yuvarlamasına uydurma olurdu,
- oran 0.67-1.50 arasında olmalı.

Dış kaynağı olmayan hatlar dokunulmaz (oran 1.0). Üç hat kalibre edilebilir
durumda: **M2, M4, T1**.

**Durum: öneri.** Kalibrasyon uygulanmadı; `scripts/propose_calibration.py`
oranları ve etkisini hesaplıyor, karar kullanıcıya ait.

### Otomatik raporlama

`tests/test_external_travel_times.py` her dış kaynak satırı için sapmayı
yazdırır ve ±%10'u aşanları uyarı olarak listeler. **Testi kırmaz:** ölçülen
şey veriyle gerçeklik arasındaki fark, bir hata değil. Doğrulanmamış satırlar
uyarı sayımından çıkarılır. `data/transit.duckdb` yoksa test atlanır.

## Ek bulgular (8 Kasım 2026)

### Marmaray ağa eklendi

`Kadıköy → Taksim` cevaplanamıyordu. Sebep: M4 ile M2 arasında **tek bir raylı
aktarma yok**; gerçek rota Marmaray'dan geçiyor ve Marmaray aynı beslemede
**TCDD ajansı** altında duruyor, ingest'in Metro İstanbul filtresi onu
düşürüyordu.

Marmaray eklendi: 43 istasyon, 42/42 segment süreli, `data_as_of 2019-10-16`
(metro verisinden bir yıl eski, bu yüzden Marmaray'a dokunan bir yolculuk bu
tarihi döner). Metro ağıyla 12 ortak istasyonu var: Yenikapı (M1A/M1B/M2),
Üsküdar (M5), Sirkeci (T1), Bostancı (M4/M8), Göztepe, Kartal, Küçükyalı,
Maltepe, Pendik (M4), Zeytinburnu (M1A/T1), Yenimahalle (M3/M7/T4).

**Tek aktarma yetmiyordu.** Rota M4 → Marmaray → M2, yani iki aktarma.
Tek-aktarma mantığı yerine `(hat, istasyon)` düğümleri üzerinde Dijkstra
kuruldu: kenarlar ya komşu istasyona gidiş (süre) ya da aktarma
(`TRANSFER_PENALTY_S`). Aynı hattaki ardışık atlamalar tek bacağa
birleştiriliyor ve her bacağın süresi `same_line_leg` ile yeniden
hesaplanıyor, böylece raporlanan sayı doğrudan sorgununkiyle aynı oluyor.

Sonuç: **Kadıköy → Taksim 48 dk** (M4 Kadıköy-Göztepe 7 dk, Marmaray
Göztepe-Yenikapı 19 dk, M2 Yenikapı-Taksim 7 dk, 2 aktarma 12 dk).

### Marmaray eklenirken çıkan çakışma

Segment anahtarı yalnızca istasyon adı çiftiydi. Marmaray da M4 gibi
`Bostancı → Küçükyalı` çalıştırıyor — ayrı hatlar, aynı adlar — ve biri
diğerinin süresini eziyordu; M4'ün uçtan uca süresi 38.2 dk'dan 37.0 dk'ya
düşmüştü. Anahtar `(hat, istasyon_a, istasyon_b)` oldu.

### M5'in eksik koordinatları tamamlandı

Canlı Metro API'si M5'in son üç istasyonu için `Latitude: null` döndürüyor
(`GetStationById` ve `GetStations` aynı şeyi söylüyor; asansör/merdiven
sayıları da 0 — kayıtlar yarım girilmiş görünüyor).

Veri seti 6 (`Raylı Sistem İstasyon Noktaları`, haftalık güncellenen GeoJSON)
üçünü de taşıyor ve `PROJE_ASAMA` alanıyla mevcut/inşaat halinde ayrımı
yapıyor:

| İstasyon | Koordinat |
|---|---|
| Veysel Karani | 40.96898, 29.23993 |
| Hasanpaşa | 40.96867, 29.25492 |
| Sultanbeyli | 40.96607, 29.27249 |

Ingest artık eksik koordinatları bu katmandan tamamlıyor ve `coord_source`
alanında hangi kaynaktan geldiğini kaydediyor. `Üsküdar → Sultanbeyli`
cevaplanamıyordu, şimdi **43 dk (tahmin)**.

### Tahmin sonuçlarına hata payı eklendi

`estimated` dönen her sonuç artık `error_margin` taşıyor: held-out
tablosundan, durak sayısına göre.

| Durak | Ortalama | Maksimum |
|---|---|---|
| 11+ | ±%1.4 | ±%5.3 |
| 7-10 | ±%2.2 | ±%8.1 |
| 4-6 | ±%3.3 | ±%14.2 |
| 1-3 | ±%10.9 | ±%29.1 |

Agent bunu "yaklaşık 50 dk (±2 dk)" şeklinde kullanabilir. Pay hiçbir zaman
0 dakikaya yuvarlanmaz — yuvarlanırsa yanlış bir kesinlik ima ederdi.

Ayrıca `warnings` alanı eklendi; bilinen sapmalı hatlar için uyarı taşır.
Şimdilik tek kayıt M6 (%29 düşük).

## Karar 4 — Aktarma fiziksel bir şeydir, ad eşleşmesi değil

> **DÜZELTME (8 Kasım 2026).** Marmaray eklendiğinde aktarmalar ad eşleşmesiyle
> kuruluyordu. Bu yanlıştı ve `Kadıköy → Taksim` rotasını bozdu: yolcu
> "Göztepe"de M4'ten Marmaray'a geçiriliyordu, oysa M4'ün Göztepe'si E-5
> üzerinde, Marmaray'ın Göztepe'si sahil hattında ve **1770 m** arada var.

### Ölçüm: 82 aktarma iddiasının 26'sı yanlıştı

Ad eşleştirmesinin ürettiği en uç örnekler:

| Hat A | Hat B | İstasyon | Mesafe |
|---|---|---|---|
| M5 | Marmaray2 | Altınşehir | **32.956 m** |
| M9 | T3 | Bahariye | **20.677 m** |
| Marmaray | T4 | Yenimahalle | **13.218 m** |
| M7 | Marmaray | Yenimahalle | 11.385 m |
| M5 | T3 | Çarşı | 7.798 m |
| M4 | Marmaray | Kartal | 2.622 m |
| M1A | Marmaray | Zeytinburnu | 2.203 m |
| M4 | Marmaray | Maltepe / Göztepe / Bostancı / Küçükyalı / Pendik | 1.061-1.784 m |

Aynı hata **gerçek aktarmayı kaçırıyordu**: M4'ün `Ayrılık Çeşmesi` kaydı ile
Marmaray'ın `Ayrılıkçeşme` kaydı **7 metre** arayla duruyor, ama Türkçe iyelik
eki yüzünden anahtarlar ayrışıyor (`ayrilikcesmesi` vs `ayrilikcesme`).

### Karar

**Ad hiç kullanılmıyor.** Bir aktarma, farklı hatlara ait iki istasyonun
yürüme mesafesinde olmasıdır — fiziksel bir olgu, bu yüzden fiziksel olarak
ölçülüyor:

| Kural | Değer |
|---|---|
| Aktarma yarıçapı | **≤ 400 m** |
| Kaynak | `rail_interchanges` tablosu, ingest'te koordinatlardan üretilir |
| Ad benzerliği | **gerekmiyor ve kontrol edilmiyor** |

Sonuç: **76 aktarma çifti** (önce 82 iddia, 26'sı yanlış). Ad eşleştirmesinin
kaçırdığı, mesafenin yakaladığı gerçek aktarmalar:

| Hat A | İstasyon | Hat B | İstasyon | Mesafe |
|---|---|---|---|---|
| M4 | Ayrılık Çeşmesi | Marmaray | Ayrılıkçeşme | **7 m** |
| T3 | Kadıköy İDO | M4 | Kadıköy | 81 m |
| M1A | Bakırköy-İncirli | M3 | İncirli | 101 m |
| M2 | Şişhane | F2 | Beyoğlu | 101 m |
| TF2 | Eyüp | T5 | Eyüpsultan Teleferik | 108 m |
| T1 | Gülhane | Marmaray | Sirkeci | 192 m |
| M7 | Karadeniz Mahallesi | T4 | Kiptaş-Venezia | 200 m |
| M1A | Topkapı-Ulubatlı | T4 | Vatan | 209 m |
| M2 | Vezneciler-İstanbul Ü. | T1 | Laleli-İstanbul Ü. | 224 m |
| M1A | Aksaray | T1 | Yusufpaşa | 239 m |

### 400 m'nin dışında kalan tek kayıp: F4 ↔ M6

> **GÜNCELLEME.** Bu kayıp karar 6 ile kapatıldı: 313'ün adlandırdığı
> çiftlerde yarıçap 1000 m'ye çıktı, F4 ağa bağlandı.

400-1000 m bandında 113 çift var ve 313 bunların çoğunu adlandırıyor. Ama
hepsinin 400 m içinde bir alternatifi var — örneğin `M1A Aksaray ↔ T1 Aksaray`
557 m ile dışarıda kalıyor, fakat `M1A Aksaray ↔ T1 Yusufpaşa` 239 m ile
içeride; M1↔T1 bağlantısı kaybolmuyor.

**Tek istisna F4 ↔ M6:**

| Hat A | İstasyon | Hat B | İstasyon | Mesafe | 313 |
|---|---|---|---|---|---|
| F4 | Boğaziçi Ü./Hisarüstü | M6 | Boğaziçi Ü.-Hisarüstü | 463 m | ✅ |
| F4 | Aşiyan | M6 | Boğaziçi Ü.-Hisarüstü | 838 m | ✅ |

F4 iki istasyonluk bir füniküler ve tek bağlantısı M6; 400 m kuralıyla ağdan
kopuyor. Ingest bunu her çalıştırmada uyarı olarak basıyor. **Kararı
kullanıcıya bırakıyorum**: yarıçapı F4 için genişletmek mi, yoksa 313
tarafından adlandırılmış çiftler için ayrı (ör. 1000 m) bir yarıçap mı.
Şimdilik kural tek: 400 m.

### Regresyon testi

`tests/test_interchanges.py` bu hata türünü iki yönden sabitliyor:

- aynı adlı ama 400 m'den uzak iki istasyon arasında aktarma **olmamalı**
  (M4/Marmaray Göztepe vakası),
- farklı adlı ama yakın iki istasyon arasında aktarma **olmalı**
  (Ayrılık Çeşmesi vakası),
- ve depodaki hiçbir aktarma yarıçapı aşmamalı; `Kadıköy → Taksim` rotası
  Ayrılık Çeşmesi'nden geçmeli.

### Çok aktarmalı rota

`Kadıköy → Taksim` iki aktarma gerektiriyor, bu yüzden tek-aktarma mantığı
`(hat, istasyon)` düğümleri üzerinde Dijkstra ile değiştirildi. Kenarlar
komşu istasyona gidiş (süre) veya aktarma (`TRANSFER_PENALTY_S`); aynı
hattaki ardışık atlamalar tek bacağa birleştirilip süresi `same_line_leg` ile
yeniden hesaplanıyor.

**Düzeltilmiş sonuç: 35 dk** (yanlış rotayla 48 dk çıkıyordu):

| Bacak | Hat | Durak | Süre |
|---|---|---|---|
| Kadıköy → Ayrılık Çeşmesi | M4 | 1 | 2 dk |
| Ayrılıkçeşme → Yenikapı | Marmaray1 | 3 | 13 dk |
| Yenikapı → Taksim | M2 | 4 | 7 dk |
| 2 aktarma | | | 12 dk |

## Karar 5 — Kalibrasyon: dört kapı, hepsi geçilmeli

Kalibrasyon kodu yazıldı ve **kasten pasif**. Bir hattın süresi ancak dört
kapının tamamı geçilirse ölçeklenir:

| # | Kapı | Eşik | Gerekçe |
|---|---|---|---|
| 1 | Kaynak linki gerçek bir URL olmalı | — | Kimsenin doğrulayamadığı bir sayı kanıt değil. Link gelmeden hat kalibre edilmez; kod linkle birlikte kendiliğinden aktifleşir, değişiklik gerekmez. |
| 2 | Sapma | **> %5** | M4'ün %0.9'u gürültü; onunla ölçeklemek dış kaynağın yuvarlamasına uydurma olur. |
| 3 | Yolculuk uzunluğu | **≥ 5 durak ve ≥ 10 dk** | F1'in 2 dakikalık yolculuğundaki 30 saniyelik fark, tüm hattı %25 ölçeklemeye yeter. |
| 4 | Bizim sonucumuz | **`gtfs` olmalı** | Tahmini kalibre etmek, tahminin hatasını orana katıp sonra tahmine geri uygulamak olur. |
| — | Oran | 0.67-1.50 | Dışına çıkan iki sayı muhtemelen farklı yolculukları anlatıyor. |

Ayrıca `DOĞRULANMADI` işaretli satırlar baştan dışlanır.

### Şu anki durum

| Hat | Sapma | Oran | Karar |
|---|---|---|---|
| M2 | +%10.0 | 1.100 | ⏸️ kapı 1 — kaynak linki bekleniyor |
| T1 | -%7.1 | 0.929 | ⏸️ kapı 1 — kaynak linki bekleniyor |
| M4 (Tavşantepe) | +%0.9 | 1.009 | ❌ kapı 2 — gürültü |
| F1 | +%25.0 | 1.250 | ❌ kapı 3 — 1 durak, 2 dk |
| M4 (S. Gökçen) | +%4.0 | 1.040 | ❌ kapı 4 — bizim sonucumuz tahmin |
| M5 | +%27.6 | 1.276 | ❌ dış kaynak doğrulanmadı |

**Aktif kalibrasyon: 0 hat.** `datasets/external_travel_times.csv` içindeki
`source_url` alanını doldurmak M2 ve T1'i açar.

Kalibre edilmiş bir süre `method: gtfs_calibrated` döner ve çıktıda oranı,
dayandığı yolculuğu ve kaynak linkini taşır.

Durumu görmek için: `uv run python scripts/show_calibration_gates.py`

## Karar 6 — İki yarıçap: 400 m, 313'ün adlandırdığı çiftlerde 1000 m

Karar 4'te tek bir 400 m yarıçapı vardı ve tek kaybı F4 ↔ M6 idi (463 m).
Kullanıcı kararı verdi: **resmî aktarma veri setinde (313) adlandırılmış
çiftlerde yarıçap 1000 m, diğer tüm çiftlerde 400 m.** Gerekçe: 313 işletmecinin
"burada aktarma var" beyanıdır ve bizim mesafe sezgimizden daha iyi kanıttır.

### "Adlandırılmış çift" iki istasyon demek, iki hat demek değil

İlk uygulamada 313'ü **hat çifti** düzeyinde okudum ve aktarma sayısı 76'dan
189'a çıktı. Sebep: 313 `M4 Kadıköy → T3` diyor; hat çiftini serbest bırakmak
M4 Kadıköy'ü **1 km içindeki her T3 istasyonuna** bağlıyor — Bahariye (787 m),
Kilise (826 m), Moda (631 m) dahil. Yani karar 4'te düzelttiğim hatanın
mesafeyle sınırlanmış bir kopyası.

Doğrusu **istasyon çifti** düzeyinde okumak: bir çift ancak 313 **iki ucu da**
adlandırıyorsa geniş yarıçapı kullanır. 313'ün yazımı beslemeden farklı
olduğu için iki uç, *yalnızca o satırın andığı hatlar içinde* benzerlikle
eşleştiriliyor (`sanctioned_station_pairs`); ağ genelinde ad araması yapılmıyor
— 33 km'lik aktarmalar o yüzden çıkmıştı.

Sonuç: 400-1000 m bandındaki **113 çiftin 3'ü** kabul edildi.

| Hat A | İstasyon | Hat B | İstasyon | Mesafe | Yürüme |
|---|---|---|---|---|---|
| M1B | Bağcılar Meydan | T1 | Bağcılar | 408 m | 340 s |
| F4 | Boğaziçi Ü./Hisarüstü | M6 | Boğaziçi Ü.-Hisarüstü | 463 m | 386 s |
| F3 | Vadistanbul | M2A | Seyrantepe | 949 m | 791 s |

Aktarma sayısı 76 → **79 çift**; F4 artık ağa bağlı:
`Aşiyan → Levent` = 18 dk (F4 1 dk + aktarma 10 dk + M6 6 dk).

### Yürüme süresi artık mesafeden

Aktarma cezası iki parçaydı: 210 s ölçülen ortalama bekleme + 150 s varsayılan
yürüme. 150 s bir istasyon kompleksi içindeki peron geçişi için makul, ama
463 m'lik bir sokak yürüyüşü için değil. Yeni kural:

| Mesafe | Yürüme |
|---|---|
| ≤ 400 m | 150 s (peron geçişi, karar 0004 özgün değeri) |
| > 400 m | mesafe / **1.2 m/s** (yaygın yürüme hızı) |

Ceza artık çift başına hesaplanıyor (`RailNetwork.transfer_penalty`); tek bir
`TRANSFER_PENALTY_S` sabiti değil. F4 ↔ M6 aktarması 596 s, Yenikapı'daki
peron geçişi 360 s.

**Kuralın sürekli olmadığını not etmek lazım:** 400 m / 1.2 = 333 s, yani
sınırın bir metre ötesi yürümeyi 150 s'den 334 s'ye çıkarıyor. Bu kasıtlı:
400 m'yi ancak 313'ün adlandırdığı bir çift geçebiliyor ve böyle bir çift
koridor değil, sokak geçişidir. Alternatif `max(150, mesafe/1.2)` olurdu, ama
o 180-400 m arasındaki peron geçişlerini de uzatır ve ölçülmüş 150 s'lik
tabanı gerekçesiz değiştirir.

### Testler

`tests/test_interchanges.py`:

- 313 adlandırdığı 463 m'lik çift **kabul edilir**, yürümesi mesafeden gelir;
- 313 desteği olmayan 500 m'lik çift **reddedilir**;
- 313 adlandırsa bile 1500 m'lik çift **reddedilir** (313 yarıçapı genişletir,
  kaldırmaz);
- depodaki her satır kendi yarıçapına ve kendi yürüme süresine uyar;
- `M4 Kadıköy → T3 Rızapaşa` satırı T3 Bahariye'yi **yetkilendirmez**.

## Yeniden gözden geçirme koşulları

- Metro `GetStationBetweenTime` düzelirse süreler canlı API'den alınır, bu
  model yalnızca yedek olur.
- Peron düzeyinde yürüme verisi gelirse aktarma sabitinin 150 s'lik yarısı
  ölçülebilir.
- Yeni bir GTFS beslemesi gelirse `fit_travel_model.py` ve
  `validate_travel_estimates.py` yeniden çalıştırılmalı; katsayıların
  tamamı o betiklerin çıktısı.
- Daha fazla dış kaynak eklenirse `propose_calibration.py` yeniden
  çalıştırılmalı; kalibrasyon kararı o çıktıya dayanır.
- Marmaray için daha güncel bir besleme gelirse `MARMARAY_DATA_AS_OF`
  güncellenmeli.
- Kaynak linkleri gelince `show_calibration_gates.py` çalıştırılıp hangi
  hatların aktifleştiği doğrulanmalı.
- 313'ün yeni bir sürümü gelirse `sanctioned_station_pairs` eşiği (0.80
  benzerlik) yeniden gözden geçirilmeli; geniş yarıçapa giren çiftler o
  eşleşmeye dayanıyor.
