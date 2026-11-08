# 0005 — Sefer saatleri: `get_next_departures` (metro) nasıl cevap veriyor

**Tarih:** 8 Kasım 2026
**Durum:** uygulandı
**İlgili:** [0001](0001-veri-kaynaklari.md) (Metro `GetTimeTable` bozuk), [0002](0002-gtfs-servis-gunleri.md) (servis günleri), [0004](0004-rayli-sure-modeli.md)

## Bağlam

`get_next_departures` metro tarafında üç şeyi cevaplamalı: ilk/son sefer, şu
anki saat diliminde sefer aralığı, hafta içi/sonu farkı. Metro İstanbul'un
kendi tarife uç noktası (`GetTimeTable`, veri seti 552) 18 hattın tamamında
ve her yönde sunucu taraflı bozuk (karar 0001, 43/43 denemede `Success:
false`), bu yüzden kaynak 2020 tarihli GTFS beslemesi.

Besleme bu üç soruyu doğrudan cevaplamıyor; her biri için bir karar gerekti.

## Karar 1 — Kaynak hat bazında: `stop_times` mi, `frequencies` mi

`frequencies.csv` 10894 metro seferinin yalnızca 66'sını kapsıyor ve yoğun
hatlarda bu satırlar **gece servisi**: `M2 23:50-02:00, her 10 dk`. Yani
"sefer aralığını frequencies'tan oku" varsayımı yanlış.

| Kaynak | Hatlar | Nasıl |
|---|---|---|
| `stop_times` | F1, M1A, M1B, M2, M2A, M3, M3A, M4, M5, M6, T1, T3, T4 | açık kalkış saatlerinden **ölçülür** |
| `frequencies` | F2, F3, M7, M8, M9, TF1, TF2 | **beyan edilen** aralık okunur |

Seçim `datasets/metro_schedule_source.csv` dosyasında; `scripts/
decide_schedule_source.py` deterministik olarak üretiyor (karar 0002).
Hangi kaynağın kullanıldığı çıktıda `source` alanında görünüyor: ölçülen bir
aralık ile beyan edilen bir aralık aynı şey değil.

## Karar 2 — Aralık: aynı saat içindeki boşlukların **ortancası**

Bir saatin aralığı, o saat içinde kalkan ardışık seferler arasındaki
boşlukların ortancası. İki ayrıntı ölçümle belirlendi:

- **Ortanca, ortalama değil.** Servisin son seferleri gündüz aralığının üç
  katı boşluk bırakıyor; bir tek böyle boşluk saatin ortalamasını yolcunun
  karşılaştığı servisten koparıyor.
- **Saat sınırını geçen boşluklar sayılmıyor.** M9'da 06:20 seferinden sonra
  08:00 geliyor; o 100 dakikalık boşluğu saymak 06 saatini "her 7 dakika"
  diye gösteriyordu, oysa o saatte bulunan trenler 5 dakika arayla. Bu
  boşluk saatin aralığı değil, o saatteki servisin sonu.

## Karar 3 — Gece servisi ayrı bir segment

Besleme hafta sonu 00:20 trenini `24:20` değil `00:20` olarak yazıyor. En
erken kalkışı "ilk sefer" saymak M2'nin cumartesi ilk seferini **00:20**
gösteriyordu; oysa 00:20 cuma gecesinin servisi, cumartesi gündüz servisi
04:00'te başlıyor.

| Segment | Tanım |
|---|---|
| `gunduz` | kalkışı 04:00 ve sonrası |
| `gece` | kalkışı 04:00'ten önce — bir önceki günün gece hizmeti |

İkisi ayrı satırlarda tutuluyor ve çıktıda ayrı gösteriliyor: "ilk 04:00, son
23:55, gece servisi 00:03-03:40".

## Karar 4 — İstasyon bazında ilk/son sefer

"Son metro kaçta?" bir istasyonda sorulur ve M4'ün son treni Kadıköy'den
Tavşantepe'den **38 dakika sonra** kalkıyor. Bu yüzden pencere hem hat hem
de istasyon düzeyinde tutuluyor (`station_key` boşsa hat).

`frequencies` kaynaklı hatlarda beslemede istasyon kalkış saati yok; o
hatlarda istasyon penceresi hattın penceresine şablon seferin o istasyona
varış süresi eklenerek bulunuyor ve **`estimated: true`** ile işaretleniyor.

## Karar 5 — Elle doğrulanmış saatler GTFS'in önüne geçer

`datasets/metro_service_hours.yaml` bir hattı tanımlıyorsa cevap oradan
gelir ve `is_stale: false` olur; tanımlı değilse GTFS'e düşer ve
`is_stale: true` döner. Dosya şu an **hiçbir hat içermiyor**: kaynağı
doğrulanmamış bir saat yazmak, 2020 verisini "güncel" diye sunmak olurdu.
Her hat için `source_url` ve `retrieved_at` zorunlu. Bir hat eklendiğinde
kod değişikliği gerekmez.

Gün tipi bazında da düşülebilir: override'da `hafta_ici` varsa ama
`cumartesi` yoksa, cumartesi GTFS'ten cevaplanır.

## Karar 6 — Dört seferden tarife üretilmez

Beslemedeki Marmaray rotaları (TCDD ajansı) **2-4 şablon seferden** oluşuyor
ve `frequencies` satırı yok. Bu seferlerden "ilk/son sefer" okumak uydurma
olur, bu yüzden `MIN_TRIPS_FOR_WINDOW = 4` altındaki yön/gün birleşimleri
hiç pencere üretmiyor ve araç şunu diyor:

> MARMARAY için tarife verisi yok. 2020 tarihli GTFS beslemesinde bu hat
> yalnızca birkaç örnek seferle yer alıyor; ilk/son sefer ve sefer aralığı
> bu veriden türetilemez.

Marmaray'ın süre hesabı (karar 0004) etkilenmiyor; yalnızca tarifesi yok.

## Karar 7 — Tarife duyuruları cevabın önüne geçer

2020 beslemesi, Metro İstanbul'un geçen ay kış tarifesine geçtiğini
bilemez. `GetAnnouncements/TR` biliyor. Bir duyuru cevaba iki koşulla
ekleniyor:

1. **Tarifeyle ilgili olmalı.** Anahtar kelimeler: sefer, tarife, çalışma
   saat, servis saat, düzenleme, ek sefer. M5'in uzatma açılışı hattın
   haritasını değiştiriyor, aralığını değiştirmiyor — eklenmiyor.
2. **Hattı ilgilendirmeli.** Metinde hat kodu geçmeli — ya da **hiç hat kodu
   geçmemeli**: "Metrolarda Kış Tarifesine Geçiliyor" tüm ağın ilk/son tren
   saatlerini tek bir kod anmadan değiştiriyor, bu yüzden hat kodu taşımayan
   bir tarife duyurusu her hatta gösteriliyor.

Örnek: `TF2` için besleme 08:00-23:00 diyor, duyuru "kış tarifesinde
08:00-22:00" diyor. Araç ikisini birlikte veriyor ve duyurunun hesaplanan
saatlerin önüne geçtiğini söylüyor.

## Çıktı

```
M4 / Kadıköy  @ 07.11.2026 23:30 (cumartesi)
  TAVŞANTEPE   ilk 04:00  son 23:55  her 8 dk (saat 23)  ortalama bekleme ~5 dk
               gece servisi 00:03-03:40
  KADIKÖY      ilk 04:38  son 00:33  her 8 dk (saat 23)  ortalama bekleme ~5 dk
               gece servisi 00:58-04:18
  kaynak: Public Transport GTFS (veri seti 308) — GTFS stop_times
  data_as_of=2020-07-09  is_stale=true
```

Her cevapta: hat, gün tipi, yön bazında ilk/son sefer, aralık ve hangi saate
ait olduğu, `running_now`, ortalama bekleme (aralık/2), `source`,
`data_as_of`, `is_stale`, uyarılar ve duyurular. Servis dışındaysa aralık,
servisin yeniden başladığı saatin aralığıdır — sayısal olarak en yakın
saatin değil; 02:00'de soran birine gecenin son trenlerinin aralığını vermek
yanlış cevap olur.

## Kod ve testler

| Dosya | Ne |
|---|---|
| `store/timetable.py` | beslemeden pencere ve aralık üretimi (saf fonksiyonlar) |
| `store/ingest_timetable.py` | `rail_service_windows` (1668 satır), `rail_headways` (2074 satır) |
| `store/departures.py` | `get_next_departures` + YAML override |
| `clients/metro.py` | duyuru okuma, HTML temizleme, tarife filtresi |
| `tests/test_timetable.py` | 19 test, elle kurulmuş besleme üzerinde |
| `tests/test_metro_announcements.py` | 10 test, canlı yanıt fixture'ı üzerinde |
| `tests/test_departures.py` | 16 test, override önceliği + depo sorguları |
| `scripts/show_departure_examples.py` | 14 elle doğrulanabilir örnek |

## Yeniden gözden geçirme koşulları

- `GetTimeTable` düzelirse bu kararın tamamı yeniden değerlendirilmeli;
  canlı tarife GTFS'in önüne geçer.
- Yeni bir GTFS beslemesi gelirse `decide_schedule_source.py` yeniden
  çalıştırılıp `metro_schedule_source.csv` güncellenmeli.
- `metro_service_hours.yaml` doldukça `is_stale` cevapları azalır; dosya
  bütün hatları kapsadığında GTFS yolu yalnızca yedek kalır.
- Duyuru anahtar kelimeleri canlı duyurulardan seçildi; duyuru dili
  değişirse `SCHEDULE_KEYWORDS` gözden geçirilmeli.
