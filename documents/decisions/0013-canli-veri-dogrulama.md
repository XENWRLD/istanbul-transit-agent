# 0013 — Canlı veriyi gerçek kaynakta doğrulama: dört sessiz yanlış

**Tarih:** 11 Kasım 2026
**Durum:** uygulandı
**İlgili:** [0005](0005-sefer-saatleri.md) (gece servisi kuralı), [0007](0007-iett-beslemesi.md) (İETT beslemesi), [0012](0012-canli-veri-araclari.md) (canlı veri araçları)

## Bağlam

Faz 2 bittiğinde canlı araçların hepsi test geçiyordu — ama birim testler
kural gereği ağa çıkmıyor, fixture okuyor. "Araç çalışıyor" ile "araç
doğru söylüyor" aynı şey değil: fixture yalnızca kodun kendi beklediğini
doğrular, kaynağın gerçekte ne gönderdiğini doğrulamaz.

Bu yüzden her canlı kaynağa gerçek istek atıp dönen veriyi **şimdiyle**
karşılaştırdım. Kaynakların hepsi ayaktaydı ve veri sahiden canlıydı:
İSPARK damgası 2 dakika, trafik indeksi 3 dakika önceydi; metro arıza
sayıları karar 0012'de yazdığım ölçümden kaymıştı (56 → 50 merdiven,
29 → 28 asansör), yani sabit bir yanıt değil.

Buna karşılık dört tane **sessiz yanlış** çıktı: araç hata vermiyor, boş
dönmüyor, testleri geçiyor — sadece doğru olmayan bir şey söylüyor. Hepsi
aynı aileden: *bir değerin yokluğunu bir değer sanmak.*

## Karar 1 — İSPARK'ta `empty: 0` "dolu" değil, "bilinmiyor"

`find_parking` 0 boş yer bildiren otoparklar için `occupancy_percent: 100`
üretiyordu. Ölçüm, bu sıfırların doluluk değil **suskunluk** olduğunu
gösteriyor (11 Kasım 2026, 12:57):

| Kesit | Sıfır bildiren |
|---|---|
| Açık + kapasiteli otopark | 51 / 239 |
| Bayrampaşa | 9 / 10 |
| Bağcılar | 7 / 11 |
| Beşiktaş | 1 / 23 |
| Kapalı otopark | 40 / 85 |
| **Yol üstü** | **0 / 47** |

İlçeye ve tipe göre kümelenen bir sıfır, doluluk dağılımı değil kaynak
dağılımıdır. 1310 kapasiteli Bağcılar Devlet Hastanesi otoparkının tam
tamına 0 boş yer bildirmesi de buna uyuyor. Detay ucu (`ParkDetay`) aynı
sıfırı veriyor ve zaman damgası taze — çünkü damga ölçüme değil **tüm
partiye** basılıyor, dolayısıyla tazelik damgası bir ölçümün varlığını
kanıtlamıyor.

Kaynak "dolu" ile "bildirmiyor" arasını ayırt etmiyor, biz de
ayırmıyoruz: `CarPark.reports_occupancy` false oluyor,
`occupancy_percent` None dönüyor, ham `empty` değeri açıkta duruyor ve
araç nota "bu otoparklar için 'dolu' deme" yazıyor. Gerçekten dolu bir
otopark da bu kovaya düşüyor — bilmediğimizi söylemek, %100 uydurmaktan
iyidir.

Bu, karar 0012/2'deki kuralın bir kat aşağısı: *boş liste "veri yok"
demekle aynı şey değildir* → **sıfır da "bilinmiyor" demek değildir.**

## Karar 2 — Gece servisi kuralı otobüste de geçerli

`get_next_departures("500T")` D yönü için **"00:00–23:45"** döndürüyordu.
Oysa o yönde 04:00 öncesi yalnızca iki kalkış var (00:00, 00:20) ve
günün gerçek ilk seferi 05:40'ta.

Sebep, raylı sistemde karar 0005'te ölçülenin aynısı: besleme cuma
gecesinin seferini `24:20` değil `00:20` diye yazıyor, dolayısıyla en
erken kalkışı olduğu gibi almak gecenin kuyruğunu günün başı sanıyor.
Kural raylı tarafta vardı (`store/timetable.py: NIGHT_END_S`,
`ServiceWindow.night_service`), İETT tarafında yoktu — aynı projede aynı
kuralın iki farklı uygulanışı.

`clients/iett.py` artık `NIGHT_END_MINUTES = 4 * 60` ile pencereyi
bölüyor: `first`/`last` gündüz servisi, `night_service` 04:00 öncesi
pencere, `night_only` yalnızca gece çalışan yönler için. Aralık
pencerenin kendi kalkışlarından ölçülüyor, gece seferi gündüz aralığını
bozmuyor. Düzeltmeden sonra 500T hafta içi: `ilk=05:40 son=23:45
gece=00:00-00:20`.

**Not:** Bu kusuru doğrulayan bir test vardı
(`test_times_past_midnight_sort_last`, `first == "00:05"` diye iddia
ediyordu). Test kusuru kodladığı için yanlışı yakalamadı; testin kendisi
değiştirildi ve nedeni yazıldı.

## Karar 3 — Her duyuru aynı yaşta değil, araç bunu söylüyor

İki ayrı sorun, aynı kök: tazelik varsayılıyordu.

**İETT duyurularında tarih yok.** Ölçülen alanlar yalnızca `HATKODU`,
`HAT`, `TIP`, `GUNCELLEME_SAATI`, `MESAJ` ve saat alanı
`"Kayit Saati: 16:07"` biçiminde — **gün yok**. Dağılım toplu yükleme
gösteriyor: 204 duyurunun 114'ü 04:05–04:08 arasında kaydedilmiş. Saat
12:57'de görülen 16:07'li bir kayıt bugün olamaz, yani önceki bir güne
ait. Araç artık `recorded_day_known: false` veriyor ve saati şu andan
ileride olanları `from_earlier_day: true` ile işaretliyor (ölçümde 40
duyurunun 9'u).

**Metro servis durumu aylarca yayında kalıyor.** İki yayın kaydı 26 ve 23
gün önce güncellenmişti ama araç bunları "anlık servis durumu" diye
veriyordu. Tarih alanda vardı, eksik olan yaşı söylemekti: `age_days`
alanı eklendi ve 7 günden eski kayıtlar için not düşülüyor. Eşik
keyfî değil pratik: kaynak kaydı kaldırmadığı için durum hâlâ yürürlükte
*olabilir*, o yüzden kayıt gizlenmiyor — yaşı söyleniyor.

## Karar 4 — Kırpma sıraya göre değil, önceliğe göre

`get_service_alerts` cevabı `alerts[:40]` ile kırpıyordu ve bu kırpma
**ekleme sırasına** göreydi: önce 219 İETT duyurusu, sonra metro
duyuruları, en sonda hat bazlı servis durumu. Sonuç: filtresiz bir
sorguda listedeki en işletmesel kayıt — "M7'de teknik arıza nedeniyle
mekik seferi yapılıyor" — 219 güzergâh duyurusunun arkasında kalıp hiç
dönmüyordu.

Sınırın kendisi şart (229 duyuruyu modele vermek bağlamı yiyor), sıra
değişti: `_alert_rank` ile servis durumu → metro duyurusu → sefer iptali
→ diğer. Kırpılan sayı nota yazılıyor; kaç duyurunun listelenmediğini
söylemeyen bir kırpma, karar 0012/2'nin yasakladığı sessiz kayıptır.

Notlar da artık kırpma **sonrası** listeden üretiliyor: önce "219
duyuruda tarih yok" diyip cevaba 40 duyuru koymak, modele listede
olmayan şeyi anlatmak olurdu.

## Sonuç

| Kusur | Nasıl görünüyordu | Şimdi |
|---|---|---|
| İSPARK sıfırı | "%100 dolu" | `occupancy_known: false`, yüzde yok |
| Otobüs ilk seferi | "00:00" | `ilk=05:40`, `gece=00:00-00:20` |
| İETT duyuru yaşı | sessiz | `recorded_day_known`, `from_earlier_day` |
| Metro durum yaşı | "anlık" | `age_days`, 7 günden eskiye not |
| Duyuru kırpması | servis durumu düşüyordu | önceliğe göre, kayıp nota yazılı |

Test sayısı 639 → **662**. Dördü de gerçek kaynakta tekrar ölçülerek
doğrulandı.

## Çıkarılan kural

Fixture tabanlı testler bu dört kusurun hiçbirini yakalayamazdı: dördü de
kodun kendi varsayımıyla tutarlıydı, kaynakla tutarsızdı. Bir canlı araç
yazıldıktan **sonra** da gerçek kaynağa bakmak gerekiyor; ölçüm betiği
`scripts/probe_live_freshness.py` olarak duruyor ve yeni canlı kaynak
eklenince çalıştırılmalı.
