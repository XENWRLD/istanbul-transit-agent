# 0018 — Gürültü bandı ölçüldü: dar çıktı, ve bu ilk okumamı çürüttü

**Tarih:** 12 Kasım 2026
**Durum:** band ölçüldü; ilk yorum düzeltildi, nedensellik deneyi **sırada**
**İlgili:** [0014](0014-degerlendirmeyi-hizlandirma.md) ("davranış tekrarlanıyor, oynayan tek şey süre"), [0015](0015-olu-mod-degerleri.md) (`rail-04` dört koşuda üç davranış; nudge +3), [0017](0017-mod-merkez-secimi.md) (bu bulguyu ortaya çıkaran koşu)

## Bağlam

Karar 0017 araç çıktısını ve bir docstring'i değiştirdiği için 54 senaryo
yeniden koşuldu. Sonuç 47/54, taban 50/54 — net −3. Soru şuydu: düzeltme mi
gerileme yaptı?

## Ölçüm: hayır, ama cevap daha rahatsız edici

Önce/sonra önbellek dosyaları satır satır karşılaştırıldı (LLM'e gitmeden,
iki ayrı araç yüzeyi özeti altındaki 54+54 kayıt). **12 senaryo araç çağrı
dizisini değiştirdi — %22.**

Değişikliğin dokunduğu yol tek: `find_nearby_stops` *modlu* çağrıldığında
merkez seçimi. Değişen 12 senaryonun hiçbiri o yoldan geçmiyor —
`near-01` modsuz çağırıyor, `chain-01` modlu çağırıp aynı sonucu alıyor.
Değişenler arasında bilgi tabanı (`know-06`, `know-08`), raylı sistem
(`rail-03`, `rail-04`, `rail-06`), reddetme (`refuse-03`, `refuse-05`,
`refuse-08`) ve canlı veri (`live-05`) senaryoları var.

İki açıklama mümkün ve **tek koşuyla ayrıştırılamıyor:**

1. **Koşular arası kararsızlık.** Sıcaklık 0.0 ama tohum yok ve `llama.cpp`
   kısmi GPU boşaltmayla bit düzeyinde yinelenebilir değil. Karar 0015
   `rail-04`'ün dört koşuda üç farklı davranış verdiğini ölçmüştü; bu koşu
   onun beşinci gözlemi (bu kez doğru aracı çağırıp **geçti**).
2. **Docstring'in kendisi.** `find_nearby_stops`'a dört satır eklemek
   bütün araç yüzeyini, yani her senaryonun prompt'unu kaydırıyor. Proje
   kuralı tam bu yüzden "açıklamayı değiştirmek davranış değişikliğidir"
   diyor — ama kuralın kastettiği şey ilgili aracın seçilmesiydi, bütün
   senaryoların kayması değil.

Gecikme de aynı ölçüde oynuyor: `refuse-08` önce 63.8 s, sonra **686.5 s**;
aynı girdi, aynı kod.

## Niçin bu bulgu önceki kararları da etkiliyor

Karar 0014 *"davranış tekrarlanıyor, oynayan tek şey süre"* demişti. 0015
bunu `rail-04` için çürüttü. Bu ölçüm, çürütmenin tek senaryoya özgü
olmadığını gösteriyor: **oran farkı ±3 mertebesindeyse tek koşu onu
çözemez.**

Bu, geçmişte verilmiş iki kararın gerekçesini zayıflatıyor:

- **Üç prompt değişikliğinin "net −2"si** (0015): 47 → 45. İki birim fark,
  gürültü bandının içinde. Geri alma kararının *başka* bir gerekçesi vardı
  ve o duruyor — `lines-03` doğru bir "veri yok" cevabından uydurmaya
  geçmişti, bu niteliksel bir kusur ve sayıya bakmıyor. Yani karar doğru
  kalıyor, ama "net −2" rakamı tek başına onu taşımıyordu.
- **Nudge'ın "net +3"ü** (0015): 47 → 50. Aynı mertebede. Mekanizma
  anlatısı ikna ediciydi (üç senaryo sıfır araçtan araç çağırmaya geçti),
  ama bu koşuda o üçlüden `rail-06` **sıfır araca geri döndü**. Nudge'ın
  kazancı yanlış demiyorum — ölçülmemiş diyorum.

Her iki not da sonucu tek koşudan okumuş. Yöntem hatası bu turda bende de
tekrarlanabilirdi: 47/54'ü görüp "düzeltme gerileme yaptı" diye yazmak.

## Karar

1. **Tek koşudan ±3 mertebesinde sonuç çıkarılmaz.** Bir değişikliğin
   etkisi, değişiklikten bağımsız olarak ölçülmüş bir gürültü bandının
   dışındaysa rapor edilir.
2. **Gürültü bandı ölçülmeli.** Kod sabitken aynı senaryo altkümesi N kez
   koşulur ve kaç satırın döndüğü sayılır. Bu, bugüne kadar hiç
   yapılmadı — 0014 ve 0015 bandı varsayarak konuştu.
3. **Niteliksel kanıt sayıdan güçlüdür.** `lines-03`'ün uydurmaya geçmesi
   ya da `know-08`'in aynı aracı altı kez çağırması, oran değişiminden
   bağımsız olarak bilgi taşıyor. Karar notları bundan sonra hangi
   senaryonun **neyi** yaptığını yazar, yalnızca oranı değil.
4. **Araç gövdesi düzeltmeleri oranla savunulmaz.** Karar 0017'nin
   gerekçesi veri katmanındaki önce/sonra tablosudur (boş liste → üç
   iskele), senaryo oranı değil. Oran onu ne doğrular ne çürütür.

## Ölçülmeyen

Gürültü bandının kendisi. Tasarım: 10 senaryo × 3 tekrar, kod sabit,
`--refresh` ile; her tekrarda araç çağrı dizisi kaydedilir ve kaç
senaryonun dizisi tekrarlar arasında değiştiği sayılır. Maliyet ~35-40
dakika sessiz makine. Bu yapılana kadar `near-04`, `chain-05` ve kalan
prompt deneyleri **bekliyor**: hepsi ±3 mertebesinde karar istiyor ve o
mertebe şu an ölçülemiyor.

---

## Band ölçüldü ve yukarıdaki ilk yorumum yanlıştı

Bu notun ilk hâli "%22 kararsız" diyordu. O sayı, 0017'nin **iki farklı
araç yüzeyi** altındaki iki koşusunu karşılaştırmaktan geliyordu — yani
"docstring değişikliğinin etkisi" ile "koşular arası gürültü" hiç
ayrışmamıştı. Kendi kuralımı (tek koşudan sonuç çıkarma) başka bir biçimde
çiğnemiş oldum: tek **karşılaştırmadan** nedensellik okudum.

`eval/measure_variance.py` ile band iki düzeyde ölçüldü.

### Süreç içi tekrar (9 senaryo × 3 tekrar, tek yığın)

| Ölçüt | Sonuç |
|---|---|
| imzası sabit | 8/9 |
| geçme durumu oynayan | 0 |
| tekrar başına geçen | 2, 2, 2 |
| **oran bandı** | **±0** |

Gecikmeler bile tekrar ediyor: `near-04` üç kez 25.7 s, `rail-03`
86.8 / 87.0 / 86.9 s. Tek oynayan `know-08` (aynı aracı 1 ya da 6 kez
çağırıyor, üçünde de düşüyor).

Bu ölçümün kendi kusuru da ortaya çıktı: aynı süreçte, açık oturumlar ve
ısınmış modelle yapılan tekrarlar aranan varyansı örneklemiyor. Betik
bu yüzden ikiye ayrıldı (`--repeats` süreç içi, `--aggregate` süreçler
arası).

### Süreçler arası (4 ayrı süreç, 9 ortak senaryo)

| Ölçüt | Sonuç |
|---|---|
| imzası sabit | 8/9 |
| geçme durumu oynayan | 0 |
| tur başına geçen | 2, 2, 2, 2 |
| **oran bandı** | **±0** |

Yani band dar. Dokuz senaryonun sekizi dört ayrı süreçte aynı araçları
aynı sırayla çağırıyor ve geçme durumu hiç oynamıyor.

## Bunun sonucu: 47/54 muhtemelen gerçek bir gerileme

Band ±0 ise, 0017'deki 12 senaryoluk davranış değişimi gürültüyle
açıklanamaz. Geriye tek aday kalıyor: **`find_nearby_stops`
docstring'ine eklenen dört satır.** Araç yüzeyi her senaryonun prompt'una
giriyor, yani o aracı hiç çağırmayan senaryoların seçimi de kayıyor.

Bir kenar bulgu bandın sıfır olmadığını da gösteriyor: `rail-03`
0017'nin koşusunda **sıfır araç** çağırmıştı, aynı yüzeyle koşulan dört
turda ise dördünde de `get_line_route` çağırdı. O satır yinelenmiyor.
Yani band ±0 "hiç oynama yok" demek değil, "ölçülen dokuz senaryoda oran
oynamıyor" demek.

## Düzeltilen kurallar

İlk hâlindeki dört kuraldan ikisi değişiyor:

1. ~~Tek koşudan ±3 mertebesinde sonuç çıkarılmaz.~~ → **Band ölçülür, sonra
   karşılaştırılır.** Bu altküme için band ±0 olduğundan 3 senaryoluk fark
   anlamlıdır. Band, araç yüzeyi ya da sağlayıcı değişince yeniden
   ölçülür.
2. **Bir karşılaştırmanın iki ucu aynı araç yüzeyinde olmalı.** 0017'nin
   öncesi ve sonrası farklı yüzeylerdeydi; o karşılaştırma değişikliğin
   *kodunu* değil *açıklamasını* da içeriyordu ve ikisi ayrışmadı.
3. Niteliksel kanıt sayıdan güçlüdür — **duruyor** (`lines-03`'ün uydurmaya
   geçmesi orana bakmaz).
4. Araç gövdesi düzeltmeleri oranla savunulmaz — **duruyor** (0017'nin
   gerekçesi veri katmanındaki önce/sonra tablosu).

## Sırada: docstring'i tek değişken olarak ölçmek

Dört satır geri alınır (kod düzeltmesi kalır), etkilenen dört senaryo
(`rail-03`, `rail-06`, `know-06`, `know-08`) artı iki kontrol
(`route-02`, `live-02`) koşulur. Yüzey özeti eski değere (`1d626e3dea52`)
döneceği için taban satırları önbellekte hazır duruyor; karşılaştırma
aynı yüzey üzerinde yapılabilir. Maliyet ~10 dakika, 57 değil.
