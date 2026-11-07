# 0001 — Veri kaynaklarının doğrulanması ve araç tablosunun revizyonu

- **Tarih:** 6 Kasım 2026
- **Durum:** Kabul edildi
- **Aşama:** Faz 0
- **Kanıt:** `tests/fixtures/_probe_results.json`, `tests/fixtures/iett/_call_results.json`,
  `tests/fixtures/iett_wsdl/_summary.json`; betikler `scripts/probe_*.py`

## Bağlam

Proje kuralı: "İBB API'sinin davranışını tahmin etme; önce gerçek yanıtı incele, sonra
kodu yaz." Faz 0'ın görevi, `datasets/data-inventory.md` içindeki A ve A2 grubundaki
**her** uç noktaya gerçek istek atıp durumunu belgelemek, ve `documents/PLAN.md`
içindeki araç tablosunu bu bulgulara göre revize etmek.

Envanter portal kataloğundan türetilmişti; hiçbir uç nokta o aşamada canlı test
edilmemişti. Son güncelleme tarihlerinin bir kısmı 2022-2023 olduğu için uç
noktaların bir bölümünün kalkmış veya değişmiş olması bekleniyordu.

## Yöntem

39 veri seti çağrıldı: 32 REST uç noktası ve 7 İETT SOAP servisi.

- **REST** (`scripts/probe_endpoints.py`): iki geçişli. Birinci geçiş parametresiz
  liste uç noktalarını çağırır; ikinci geçiş, parametre isteyen uç noktalar için
  gerçek id'leri birinci geçişin yanıtlarından toplar (ör. `ParkDetay?id=` için
  `Park` listesinden gelen `parkID`). Böylece "id uydurduk, o yüzden hata verdi"
  ihtimali ortadan kalkar.
- **SOAP** (`scripts/probe_soap.py`, `scripts/probe_soap_calls.py`): WSDL'den metot
  envanteri çıkarılır, sonra metotlar gerçekten çağrılır.
- Her yanıt için ölçülenler: HTTP durumu, yanıt süresi, `Content-Type`, kimlik
  doğrulama talebi, şema, kayıt sayısı ve **yanıttaki en yeni zaman damgası**.
  Son ölçüt, servisin hâlâ güncel veri ile beslendiğini mi yoksa eski bir
  anlık görüntüyü mü döndürdüğünü gösterir.
- Örnek yanıtlar `tests/fixtures/` altına, kaynak URL / HTTP metodu / istek gövdesi /
  çekilme zamanı bilgisiyle (`_fixture_meta`) kaydedildi.

Ölçüm zamanı: 6 Kasım 2026, 17:20–17:23 (TRT).

## Bulgular

```
REST uç nokta   25/32 çalışıyor
İETT WSDL        7/7  okundu  (28 metot)
SOAP çağrısı     9/9  veri döndürdü
```

**Hiçbir uç nokta kimlik doğrulama istemedi.** 401/403 yok, abonelik anahtarı
istenmedi. İETT WSDL'lerinde `AuthHeader` bildirilmiş olmasına rağmen başlık
göndermeden 9 metodun hepsi çalıştı (aşağıda).

### Çalışmayan 7 uç nokta

| No | Uç nokta | Durum | Teşhis |
|---|---|---|---|
| 552 | `GetTimeTable` | ❌ sunucu hatası | HTTP 200 + `Success: false`, `IndexOutOfRange`. **18 hat × tüm yönler = 43/43 deneme aynı hatayı verdi.** |
| 513 | `GetStationBetweenTime` | ❌ sunucu hatası | HTTP 200 + `Success: false`, `NullReference`. Aynı şekilde 43/43. |
| 501 | `teas/api/open_data` | ❌ kalkmış | nginx 404. 6 yol varyantı denendi, hepsi 404. |
| 519 | `GetFailuresTypes` | ❌ sunucu hatası | `Could not find endpoint with name 'BasicHttpBinding_SI_ArizaDuyuru_Out'` — karşı tarafta yapılandırma hatası. |
| 507 | `GetDirections` | ❌ sunucu hatası | HTTP 500, boş gövde. |
| 170 | `GetStationStatus` (İsbike) | ❌ kullanılamaz | 400; zorunlu `guid` parametresinin alınabileceği tek kaynak (169) boş liste dönüyor. |
| 384 | City Map API | ⚠️ yanlış sınıflandırma | JSON uç noktası değil, developer portal sayfası. `Accept: */*` ile HTML dönüyor. |

### Beş yapısal bulgu

**1. HTTP 405 "kapalı" değil, "yanlış metot" demek.** Dört Metro uç noktası GET'e 405
döndü. POST ile çağrıldıklarında 200 verdiler ve hata mesajları eksik alanı kendileri
söyledi (`"EquipmentGroupName boş geldi"`). Bu ipucuyla iki uç nokta kurtarıldı:

| Uç nokta | Doğru çağrı |
|---|---|
| 515 `GetFaultyEquipmentDetails` | `POST {"EquipmentGroupName": "Yürüyen Merdiven"}` — değerler 518'in `GroupName` alanından |
| 505 `GetDirectionsByLineIdAndStationId` | `POST {"LineId": 4, "StationId": 188}` — ikisi aynı hatta ait olmalı |

**2. Metro API başarısızlıkta da HTTP 200 dönüyor.** Gövdede `Success: false` ve bir
.NET istisnası bulunur. İlk taramada 519 yanlışlıkla "çalışıyor" sayıldı; kontrol
`Success` alanına bakacak şekilde sıkılaştırılınca gerçek durumu ortaya çıktı.
→ **Faz 2 kuralı: Metro REST istemcisi sağlık kontrolünü HTTP durumuna değil `Success`
alanına dayandırmalı.**

**3. Metro sefer saatleri ve istasyon arası süreler sunucu taraflı bozuk.** Bu ikisi
araç tablosunda `get_next_departures` ve `get_rail_travel_time`'ın kaynağıydı.
Parametre sorunu olmadığını kanıtlamak için 18 hattın tamamı, her yön kombinasyonu
ve gerçek istasyon id'leriyle denendi (`scripts/probe_timetable.py`): 43/43 deneme
aynı istisnayı verdi. Kaynak değişikliği gerekiyor → aşağıdaki araç tablosu.

**4. `zeep` İETT WSDL'lerinin yarısını reddediyor, ama servisler çalışıyor.** Dört servis
(`350`, `349`, `172`, `171`) her metoda SOAP `AuthHeader` bildiriyor ama eşleşen
`<message>` tanımını WSDL'e koymamış. `zeep` bunu tolere etmiyor:
`IndexError: No definition '{http://tempuri.org/}...AuthHeader' in 'messages' found`.
WSDL XML'i doğrudan ayrıştırılarak 28 metodun tamamı çıkarıldı ve **AuthHeader
göndermeden** 9 çağrının hepsi veri döndürdü — başlık bildirilmiş ama zorunlu
tutulmuyor.
→ **Faz 2 kuralı: bu dört servis için `zeep` doğrudan kullanılamaz.** Ya elle kurulan
SOAP zarfı (Faz 0'da bu yöntem çalıştı) ya da WSDL'i yamalayan bir ara katman gerekir.
CLAUDE.md'deki "İETT SOAP istemcisi (`zeep`)" ifadesi bu nedenle kayıtsız şartsız
geçerli değil; karar Faz 2'de ölçülecek.

**5. İsbike gerçekten kapalı, ama 200 dönerek.** Portal notu doğru. `GetAllStationStatus`
HTTP 200 veriyor fakat `dataList` boş; `GetStationStatus` ise boş listeden
alınamayacak bir `guid` istiyor. İkisi de kullanılamaz.

### Araç konumu: doğrulandı ✅

Araç tablosundaki `get_vehicle_positions` "önce varlığı doğrulanacak" diye
işaretliydi. `sefer-gerceklesme` servisinde iki metot bulundu ve ikisi de gerçekten
çağrıldı:

| Metot | Kayıt | Alanlar | Not |
|---|---|---|---|
| `GetFiloAracKonum_json()` | **6940 araç** | `Operator, Garaj, KapiNo, Saat, Boylam, Enlem, Hiz, Plaka` | Tüm filo. 1.1 MB, 1.4 s. |
| `GetHatOtoKonum_json(HatKodu)` | hat başına 0-52 | `kapino, boylam, enlem, hatkodu, guzergahkodu, hatad, yon, son_konum_zamani, yakinDurakKodu` | Hat bazlı. 46 ms. |

Veri canlı: `son_konum_zamani` damgaları istek anıyla aynı dakikada (`2026-11-06 17:20:58`),
6940 aracın 2975'i hareket halinde, 13 işletmeci.

Hat `34` için yalnızca 2 araç dönmesi şüphe yarattığı için 10 hat çapraz kontrol edildi
(`scripts/probe_hat_konum.py`): `34AS` 52, `500T` 31, `76D` 26, `15F` 13 araç. Metot
sağlıklı; `34` kısa bir hat varyantı. `E-10`, `145M`, `14` kodları 0 döndürdü (geçersiz
kod veya o an sefer yok).

**Karar:** `get_vehicle_positions` **hat bazlı `GetHatOtoKonum_json`'a dayanır.**
Gerekçe: hat, yön ve en yakın durak kodunu zaten taşıdığı için agent'ın sorusuna
doğrudan cevap verir; 1.1 MB'lık tüm filo yanıtı ise bir LLM bağlamına sığmaz ve
filtrelenmesi istemci tarafına yük bindirir. `GetFiloAracKonum_json` **agent aracı
olarak sunulmaz**; ileride istatistik/analiz gerekirse ayrıca değerlendirilir.

### İETT SOAP metot envanteri (28 metot)

Tam liste imzalarıyla `tests/fixtures/iett_wsdl/*.methods.txt` içinde. Projede
kullanılacak olanlar:

| Servis | Metot | Ne verir | Doğrulandı |
|---|---|---|---|
| `duyurular` | `GetDuyurular_json()` | 291 hat duyurusu (`HATKODU, HAT, TIP, GUNCELLEME_SAATI, MESAJ`) | ✅ |
| `sefer-gerceklesme` | `GetHatOtoKonum_json(HatKodu)` | canlı araç konumu | ✅ |
| `planlanan-sefer-saati` | `GetPlanlananSeferSaati_json(HatKodu)` | hat 34 için 267 sefer saati | ✅ |
| `hat-durak-guzergah` | `GetHat_json(HatKodu)` | hat adı, uzunluk, sefer süresi | ✅ |
| `hat-durak-guzergah` | `GetDurak_json(DurakKodu)` | durak bilgisi | WSDL'de var |
| `ibb` | `DurakDetay_GYY(hat_kodu)` | 53 durak, sıralı, koordinatlı | ✅ |
| `ibb` | `HatServisi_GYY(hat_kodu)` | hat durumu, bölge | ✅ |

Kapsam dışı kalanlar: `GetGaraj_json` (86 garaj — yolcu bilgisi değil),
`GetBozukSatih_json` (bozuk yol bildirimleri), `GetKazaLokasyon_*` (kaza konumları),
`GetAkarYakitToplamLitre_*` (yakıt tüketimi), `ibb360` metotları (arşiv yolculuk
istatistikleri — D grubu niteliğinde).

## Kararlar

### K1 — `get_next_departures`: metro tarafı GTFS'e taşınıyor

552 bozuk olduğu için metro sefer saatleri canlı API'den alınamıyor. Otobüs tarafı
İETT `GetPlanlananSeferSaati_json` ile çalışmaya devam eder (doğrulandı: 267 kayıt).
Metro için "Public Transport GTFS" (veri seti 308) kullanılır; metro verisi
**09.07.2020 tarihli ve güncellenmeyecek**, dolayısıyla bu kaynaktan gelen sonuçlar
`is_stale: true` ile işaretlenir. Elle hazırlanan güncel bir
`datasets/metro_service_hours.yaml` varsa GTFS'in önüne geçer.

### K2 — `get_rail_travel_time`: GTFS `stop_times` üzerinden hesaplanır

513 bozuk. Süreler GTFS'ten türetilir; GTFS'te bulunmayan istasyonlar için koordinat
mesafesi ve hat tipinin GTFS'ten hesaplanan ortalama hızıyla tahmin üretilir ve
`estimated: true` döner. Ayrıntılı tasarım ve aktarma süresi sabiti ayrı bir karar
notuna yazılacak (bkz. Faz 1 görevi).

### K3 — `get_road_works`: ertelendi, araç tablosundan çıkarıldı

Tek kaynağı olan 501 kalkmış. Yol bakım çalışmaları bu projenin önceliği değil;
araç tablosundan çıkarılır, envanterde "ertelendi" olarak işaretlenir.

"UYM Trafik Duyuruları" CSV'si (veri seti 18, B grubu, son güncelleme 2026-11-01 —
yani güncel) yol çalışmaları aracının yerine **geçmez**; ancak ileride
`get_traffic_status` aracının kaynaklarına eklenmesi değerlendirilebilir. Bu bir
niyet beyanı, karar değil.

### K4 — `find_mobility_options`: İsbike çıkarıldı, araç düşük öncelikli

İsbike servisleri (169, 170) kullanılamaz → envanterde "ertelendi". Araç, bisiklet
yolları (1), bisiklet/mikromobilite park alanları (16) ve elektrikli araç şarj
istasyonları (275, 276) ile kalır — hepsi B grubu statik GeoJSON/CSV. Araç **düşük
öncelikli**; Faz 2'nin sonuna bırakılır.

### K5 — `get_service_alerts`: İETT duyuruları eklendi

`GetDuyurular_json` beklenenden zengin çıktı: 291 kayıt, hat kodu ve mesaj tipiyle
(`TIP: "Sefer"`). Metro duyuruları (512) ve Metro servis durumu (520) ile birlikte
bu aracın kaynağı olur.

### K6 — Kimlik doğrulama katmanı şimdilik gereksiz

39 uç noktanın hiçbiri anahtar istemedi. `.env`'de API anahtarı alanı açılmaz;
bir servis anahtar istemeye başlarsa o zaman eklenir. (`.env.example` bu durumu
yorum olarak not ediyor.)

## Sonuçlar ve riskler

- **Üç araç kaynak kaybetti** (`get_next_departures` metro tarafı, `get_rail_travel_time`,
  `get_road_works`). İkisi GTFS'e taşındı, biri ertelendi.
- **Metro istasyon ve hat verileri sağlam** (168: 248 istasyon, 508: 18 hat), üstelik
  istasyon kayıtları `DetailInfo` içinde asansör/yürüyen merdiven sayısı ve koordinat
  taşıyor — `get_station_accessibility` ve `geocode_place` için beklenenden iyi.
- **Risk:** Metro API'sinin dört uç noktası sunucu taraflı bozuk ve bunları düzeltmek
  bizim elimizde değil. Faz 2'de bu uç noktalar yeniden denenmeli; düzelirlerse
  GTFS'ten canlı veriye dönülebilir. Fixture'lar bu geçişi kolaylaştırmak için
  istek gövdelerini de kaydediyor.
- **Risk:** GTFS metro verisi 2020 tarihli. O tarihten sonra açılan hatlar (M7 uzatmaları,
  M9, M12 vb.) GTFS'te eksik olacak. Kapsam analizi Faz 1'in ilk işi.

## Revize araç tablosu

`documents/PLAN.md` içindeki tablonun yerine önerilen hali ayrı bir öneri olarak
sunulmuştur; kullanıcı onayından sonra PLAN.md'ye işlenecektir.
