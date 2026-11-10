# 0007 — İETT GTFS beslemesi: ayrı bir ağız, üç onarım, saklanmayan tablo

**Tarih:** 10 Kasım 2026
**Durum:** uygulandı
**İlgili:** [0002](0002-gtfs-servis-gunleri.md) (servis günleri), [0003](0003-istasyon-eslestirme.md) (istasyon eşleştirme)

## Bağlam

Faz 1'in raylı yarısı 2020 tarihli "Public Transport" beslemesi (veri seti
308) üzerine kuruldu ve `store/gtfs.py` o beslemeye göre yazıldı: cp1254,
virgül ayırıcı, dolmuş `calendar` penceresi. Otobüs araçları için gereken
İETT beslemesi (veri seti 75, Nisan 2026) hiç indirilmemişti.

İndirildi ve kod yazmadan önce ölçüldü (`scripts/inspect_iett_feed.py`,
`scripts/inspect_iett_times.py`). Beslemenin **beş** özelliği de okuyucunun
varsayımlarına aykırı çıktı.

## Ölçüm

| Konu | Public Transport (308) | İETT (75) |
|---|---|---|
| Kodlama | cp1254 | **UTF-8 + BOM** |
| Ayırıcı | `,` | **`;`** — ama `agency.csv` ve `stop_times.txt` `,` |
| Metin | düzgün | **çifte kodlanmış** (`routes.csv`'de 11 867 değer) |
| Koordinat | düzgün | **parçalanmış** (`410.191.700.005.564`) |
| `calendar` penceresi | dolmuş (≤2024-12-31) | **geçerli** (2026-03-16 → 2026-12-31) |
| `stop_times` | düz CSV | **zip** içinde, 150 MB, 6 155 692 satır |

Besleme büyüklüğü: 15 390 durak, 9279 hat varyantı (1097 hat kodu), 135 625
sefer.

## Karar 1 — Ağız beslemeye bağlanır, tahmin edilmez

`store/gtfs.py` artık `FeedDialect` taşıyor: kodlama, ayırıcı (dosya bazında
geçersiz kılınabilir), zip içindeki dosya adı, hangi onarımların gerektiği,
tarih penceresinin güvenilir olup olmadığı, kaynak adı ve `data_as_of`.

`dialect_for()` bilinmeyen bir besleme için **hata veriyor**, varsayılana
düşmüyor: ağzı tahmin etmek, okuyucunun sessizce mojibake ya da Gine
Körfezi'nde bir koordinat döndürmesinin yolu.

Bunun bir yan sonucu: **karar 0002 yalnızca 308 beslemesi için geçerli.**
İETT beslemesinde tarih penceresi okunabilir, bu yüzden `bus_calendar`
tablosu `window_usable = true` taşıyor. `calendar_dates.csv` hâlâ yok, yani
resmî tatiller her iki beslemede de bilinmiyor.

## Karar 2 — Metin onarımı karakter bazında

`KADIKÖY` beslemede `KADIKÃ–Y` olarak duruyor: UTF-8 baytları tek baytlık bir
kod sayfası sanılıp yeniden kodlanmış. Tek kod sayfasıyla geri çevirmek
yetmedi, çünkü bozulma **karışık**:

| Deneme | Onarılan | Kalan bozuk |
|---|---|---|
| `latin1` | 4 219 | 7 648 |
| `cp1252` | 9 038 | 2 829 |
| **karakter bazında (cp1252, olmazsa latin1)** | **11 867** | **0** |

Sebep aynı dizede görülebiliyor: `KADIKÃ–Y`in `–`si (U+2013) cp1252'de 0x96
ama latin1'de hiç yok; `ESATPAÅ\x9eA`nın `\x9e`si (U+009E) latin1'de 0x9E ama
cp1252'de hiç yok. Bu yüzden `store/text_repair.py` her karakter için önce
cp1252, olmazsa latin1 deniyor; hiçbirine sığmayan karakter varsa (gerçek
`İ`, `ş`, `ğ`) dize zaten bozuk değildir ve olduğu gibi döner.

Ölçüm sırasında tespitin kendisi de düzeltildi: `Â` ilk sürümde çifte kodlama
izi sayılıyordu, oysa "güzergâh" gerçek Türkçe. Onarılmış 2829 dize bu yüzden
"hâlâ bozuk" raporlanıyordu. Düzgün hiçbir değer onarımdan zarar görmedi
(ölçüldü: 0).

## Karar 3 — Koordinat onarımı sınır kutusuyla doğrulandı

`stops.csv` enlem/boylamı `410.191.700.005.564` biçiminde veriyor: basamaklar
doğru, ondalık ayırıcı kaybolmuş, yerine binlik nokta serpilmiş. Varsayım
"ondalık ilk iki basamaktan sonra gelir"; doğrulaması İstanbul sınır kutusu:

- 15 390 duraktan **15 386**'sı onarıldıktan sonra kutuya düştü,
- kutu dışına düşen **0**,
- onarılamayan **4** satırda kolonlar kaymış (enlem alanında
  `direction: GİDİŞ` yazıyor); bu satırlar atılıyor ve sayısı
  `ingest_meta.note` içinde duruyor.

Düzgün yazılmış bir koordinat olduğu gibi okunuyor, yani besleme bir gün
düzelirse `parse_grouped_coordinate` yine doğru çalışır.

## Karar 4 — Ham `stop_times` saklanmıyor

6 155 692 satırın yalnızca **%4.4**'ünde saat var ve o saatler sefer başına
tam **iki**: `stop_sequence=1`'den kalkış ve son duraktaki varış. Ara
duraklarda saat alanı boş (ölçüm: her 135 625 seferin tam 2 saatli satırı
var, `timepoint=1` sayısı sefer sayısına eşit).

Dolayısıyla ham tabloyu saklamak, türevlerinin vermediği hiçbir şeyi
vermiyor. Yerine iki tablo yazılıyor:

- `bus_trips` (135 625 satır) — sefer başına ilk kalkış, son varış, süre,
  durak sayısı, ilk/son durak,
- `bus_route_stops` (129 238 satır) — hat + yön başına **en eksiksiz**
  seferin sıralı durak listesi, yanında o yöndeki sefer sayısı ve kaç ayrı
  durak deseni görüldüğü (`variant_count`).

**Sonuç olarak otobüsün ara durak saatleri bu beslemeden gelemez.** O bilgi
İETT'nin canlı `GetPlanlananSeferSaati_json` servisinde; `get_next_departures`
otobüs tarafı Faz 2'de oraya bağlanacak. Besleme bugün şunları veriyor: ilk
duraktan ilk/son kalkış, sefer sayısı, sefer süresi, güzergâhın durak sırası.

## Karar 5 — Nokta katmanları tek aranabilir tabloda

`find_nearby_stops` ve `geocode_place` beş kaynağa bakmak zorunda kalmasın
diye `transit_places` kuruluyor: mod, ad, üç arama anahtarı (karar 0006'daki
`normalize`/`collapsed`/`token_key`), koordinat, kaynak, `data_as_of`,
`is_stale`.

| Mod | Satır | Kaynak |
|---|---|---|
| otobüs | 15 386 | İETT GTFS (75) |
| minibüs | 7 908 | Minibüs durakları (13) |
| raylı (metro/tramvay/füniküler/teleferik/tren) | 254 | `rail_stations` tablosu |
| vapur | 98 | Deniz ulaşım istasyonları (15) |
| dolmuş | 44 | Taksi-dolmuş durakları (9) |
| **toplam** | **23 690** | |

Raylı istasyonlar yeniden çözümlenmiyor, `ingest_rail`in yazdığı tablodan
alınıyor — istasyon kimliği zaten orada çözülmüş bir problem (karar 0003).
Katman adresleri elle yazılmıyor: `scripts/download_stop_geojson.py` depodaki
portal kataloğundan çözüyor ve `data_as_of`u portalın `metadata_modified`
alanından alıyor.

### İlk denemede yapılan hata

Raylı sorgusu `rail_stations.station` kolonunu okuyordu; doğrusu `name`.
Sorgu `except duckdb.Error` içinde olduğu için hata yutuldu ve tablo
**sessizce** hiç raylı istasyon içermedi (`rayli 0`). Artık tablonun varlığı
`information_schema`dan ayrıca kontrol ediliyor, sorgu hatası ise
yutulmuyor. Ders karar 0006/7b'nin aynısı: eksik veriyi hata gibi değil
"veri yok" gibi göstermek, sessizce yanlış cevaba davetiye.

## Maliyet

Tam ingest **8 dakika** sürüyor; zamanın neredeyse tamamı 6.15 M satırı
Python'dan akıtmakta geçiyor. Nokta tablosunu düzeltmek için bunu
tekrarlamak gerekmediği için `--only-places` bayrağı var. Akıtma bir darboğaz
olursa DuckDB'nin `read_csv`'si zip'i açtıktan sonra kullanılabilir; şimdilik
tek seferlik bir maliyet.

## Yeniden gözden geçirme koşulları

- Portal `stops.csv` koordinatlarını düzeltirse `parse_grouped_coordinate`
  yine çalışır ama ölçüm (`inspect_iett_feed.py`) yeniden koşulmalı.
- Besleme metni düzgün kodlamaya geçerse `repairs_text` bayrağı kapatılır;
  onarım iz taşımayan dizeye dokunmadığı için zararsız ama gereksizdir.
- Besleme ara duraklara saat eklerse karar 4 düşer ve `bus_stop_times`
  tablosu kurulmalı.
- `calendar.csv` penceresi 2026-12-31'de doluyor; o tarihten sonra besleme
  yenilenmezse `window_usable` yeniden ölçülmeli.
