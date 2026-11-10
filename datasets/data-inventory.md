# İBB Açık Veri Portalı — Veri Seti Envanteri

Kaynak: `data.ibb.gov.tr/api/3/action/package_search?rows=1000`, 6 Kasım 2026 itibarıyla 565 veri seti.
Tam liste (açıklama, kategori, format, son güncelleme, işaret) aynı klasördeki `ibb_datasets.csv` dosyasında; portalın ham kataloğu `ibb_dataset.json` içinde.

> **Uç nokta testi yapıldı: 6 Kasım 2026, 17:20–17:23 (TRT).** A ve A2 grubundaki
> 39 veri setinin tamamına gerçek istek atıldı: REST 25/32 çalışıyor, İETT SOAP
> 7/7 WSDL okundu (28 metot), 9/9 doğrulanan çağrı. Hiçbiri kimlik doğrulama
> istemedi. Örnek yanıtlar `tests/fixtures/` altında, bulgular ve kararlar
> `documents/decisions/0001-veri-kaynaklari.md` içinde. Aşağıdaki Durum kolonu
> ölçüm sonuçlarından üretilir (`scripts/mark_inventory.py`).

## İşaretler

- **A** — Canlı API → MCP aracı (öncelikli): 22 veri seti
- **A2** — Canlı API → ikincil / destekleyici: 17 veri seti
- **B** — Statik veri → yerel tablo / konum aracı: 41 veri seti
- **C** — RAG kaynağı (metin): 6 veri seti
- **D** — İleride / analiz için: 65 veri seti
- (boş) — Bu proje için kapsam dışı: 414 veri seti

## A — Canlı API → MCP aracı (öncelikli)

| No | Veri seti | Ne işe yarar | Uç nokta | Son güncelleme | Durum |
| --- | --- | --- | --- | --- | --- |
| 502 | Ispark Parking List Web Service | İSPARK otopark listesi + anlık boş kapasite | `https://api.ibb.gov.tr/ispark/Park` | 2023-05-05 | ✅ çalışıyor (240 kayıt) |
| 163 | İSPARK Otopark Detay Bilgileri Web Servisi | Tek otoparkın detayı (ücret, saatler) | `https://api.ibb.gov.tr/ispark/ParkDetay?id=(PARKID)` | 2026-03-02 | ✅ çalışıyor (1 kayıt) |
| 548 | Traffic Index Web Service | Şehir geneli trafik indeksi | `https://api.ibb.gov.tr/tkmservices/api/TrafficData/v1/TrafficIndexHistory/1/5M` | 2022-04-04 | ✅ çalışıyor (288 kayıt; en yeni 2026-11-06 17:22) |
| 520 | Service Status List of Metro Istanbul Lines | Metro hatlarının anlık servis durumu | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetServiceStatuses` | 2023-04-25 | ✅ çalışıyor (3 kayıt; en yeni 2026-11-02 08:06) |
| 512 | Metro Istanbul Announcement List According to Different Language Options | Metro duyuruları (aksaklık, kapanma) | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetAnnouncements/%7BLanguage%7D` | 2023-04-25 | ✅ çalışıyor (8 kayıt; en yeni 2026-11-06 00:01) |
| 552 | Metro Istanbul Timetable Web Service | Metro sefer saatleri | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetTimeTable` | 2022-04-04 | ❌ sunucu hatası — HTTP 200 + Success=false: Index was out of range. Must be non-negative and less than the size of the collection. (Pa |
| 513 | List of Distance Times of Metro Istanbul Stations to the Start Station | İstasyonlar arası süre | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetStationBetweenTime` | 2023-04-25 | ❌ sunucu hatası — HTTP 200 + Success=false: Object reference not set to an instance of an object. |
| 168 | Metro İstanbul İstasyon Bilgi Listesi | Metro istasyonları | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetStations` | 2026-02-27 | ✅ çalışıyor (248 kayıt) |
| 514 | Metro Istanbul Station Information List by Line Id | Hatta göre istasyonlar | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetStationById/%7BLineId%7D` | 2023-04-25 | ✅ çalışıyor (2 kayıt) |
| 508 | Metro Istanbul Line Information List | Metro hatları | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetLines` | 2023-04-25 | ✅ çalışıyor (18 kayıt) |
| 515 | Metro Istanbul Detail Information List of Defective Equipment | Arızalı ekipman detayı (asansör/yürüyen merdiven → erişilebilirlik) | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetFaultyEquipmentDetails` | 2023-04-25 | ✅ çalışıyor (POST {"EquipmentGroupName": "Yürüyen Merdiven"}; 1 kayıt; en yeni 2026-11-06 00:00) |
| 518 | Metro Istanbul Faulty Equipment and Technical Unit List | Arızalı ekipman listesi | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetFaultyEquipments` | 2023-04-25 | ✅ çalışıyor (1 kayıt) |
| 527 | Metro Istanbul Ticket Price List Web Service | Metro bilet fiyatları | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetTicketPrice/TR` | 2023-03-27 | ✅ çalışıyor (3 kayıt) |
| 351 | IETT Announcements Web Service | İETT anlık hat duyuruları | `https://api.ibb.gov.tr/iett/UlasimDinamikVeri/Duyurular.asmx?wsdl` | 2024-11-06 | ✅ WSDL okundu, 2 metot — doğrulanan çağrı: GetDuyurular_json |
| 350 | IETT Planned Time Web Service | İETT planlanan sefer saatleri | `https://api.ibb.gov.tr/iett/UlasimAnaVeri/PlanlananSeferSaati.asmx?wsdl` | 2024-11-06 | ✅ WSDL okundu, 2 metot — zeep reddetti, WSDL XML ile okundu — AuthHeader bildirilmiş ama zorunlu değil — doğrulanan çağrı: GetPlanlananSeferSaati_json |
| 349 | IETT Journey Web Service | İETT anlık sefer gerçekleşme | `https://api.ibb.gov.tr/iett/FiloDurum/SeferGerceklesme.asmx?wsdl` | 2024-11-06 | ✅ WSDL okundu, 6 metot — zeep reddetti, WSDL XML ile okundu — AuthHeader bildirilmiş ama zorunlu değil — doğrulanan çağrı: GetFiloAracKonum_json, GetHatOtoKonum_json, GetBozukSatih_json |
| 172 | İETT Hat-Durak-Güzergâh Web Servisi | İETT hat-durak-güzergâh | `https://api.ibb.gov.tr/iett/UlasimAnaVeri/HatDurakGuzergah.asmx?wsdl` | 2026-02-27 | ✅ WSDL okundu, 9 metot — zeep reddetti, WSDL XML ile okundu — AuthHeader bildirilmiş ama zorunlu değil — doğrulanan çağrı: GetHat_json, GetGaraj_json |
| 171 | İETT Durak ve Hat Bilgileri Web Servisi | İETT durak ve hat bilgileri | `https://api.ibb.gov.tr/iett/ibb/ibb.asmx?wsdl` | 2026-02-27 | ✅ WSDL okundu, 3 metot — zeep reddetti, WSDL XML ile okundu — AuthHeader bildirilmiş ama zorunlu değil — doğrulanan çağrı: DurakDetay_GYY, HatServisi_GYY |
| 347 | İETT Yolculuk ve Sefer Bilgileri Web Servisi | İETT yolculuk ve sefer bilgileri | `https://api.ibb.gov.tr/iett/ibb/ibb360.asmx?wsdl` | 2024-11-15 | ✅ WSDL okundu, 4 metot |
| 407 | Hava Kalitesi İstasyon Bilgileri Web Servisi | Hava kalitesi istasyonları | `https://api.ibb.gov.tr/havakalitesi/OpenDataPortalHandler/GetAQIStations` | 2024-03-28 | ✅ çalışıyor (28 kayıt) |
| 408 | Hava Kalitesi İstasyon Ölçüm Sonuçları Web Servisi | Hava kalitesi ölçümleri (AQI) | `https://api.ibb.gov.tr/havakalitesi/OpenDataPortalHandler/GetAQIByStationId` | 2024-03-28 | ✅ çalışıyor (721 kayıt; en yeni 2026-11-06 17:23) |
| 501 | Road Maintenance Works Web Service | Günlük yol bakım çalışmaları | `https://api.ibb.gov.tr/teas/api/open_data` | 2023-05-05 | ⏸️ ertelendi — uç nokta kalkmış (nginx 404), yol çalışmaları aracı kapsamdan çıktı (K3) |
## A2 — Canlı API → ikincil / destekleyici

| No | Veri seti | Ne işe yarar | Uç nokta | Son güncelleme | Durum |
| --- | --- | --- | --- | --- | --- |
| 169 | Tüm İsbike İstasyonları Web Servisi | İsbike tüm istasyonlar (portal notu: geçici olarak kapalı) | `https://api.ibb.gov.tr/ispark-bike/GetAllStationStatus` | 2026-02-27 | ⏸️ ertelendi — HTTP 200 ama dataList boş; servis portal notuna göre kapalı (K4) |
| 170 | İsbike İstasyon Durumları Web Servisi | İsbike istasyon durumu (portal notu: geçici olarak kapalı) | `https://api.ibb.gov.tr/ispark-bike/GetStationStatus` | 2026-02-27 | ⏸️ ertelendi — 400, zorunlu guid yalnızca boş olan 169'dan alınabilir (K4) |
| 504 | Metro Istanbul Rail System Group List | Raylı sistem grupları | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetRailwayGroups` | 2023-04-25 | ✅ çalışıyor (4 kayıt) |
| 505 | Direction Information List by Metro Istanbul Line Id and Station Id | Hat+istasyona göre yön | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetDirectionsByLineIdAndStationId` | 2023-04-25 | ✅ çalışıyor (POST {"LineId": 4, "StationId": 188}; 1 kayıt) |
| 506 | Direction Information List by Metro Istanbul by Line ID | Hatta göre yön | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetDirectionById/%7BLineId%7D` | 2023-04-25 | ✅ çalışıyor (2 kayıt) |
| 507 | Metro Istanbul Line Direction Information List | Yön listesi | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetDirections` | 2023-04-25 | ❌ çalışmıyor — HTTP 500: (boş gövde) |
| 516 | Metro Istanbul Fault Types List | Arıza türleri | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetFailureTypes` | 2023-04-25 | ✅ çalışıyor (4 kayıt) |
| 517 | Metro Istanbul Technical Object Types List | Teknik nesne türleri | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetTechnicalObjectTypes` | 2023-04-25 | ✅ çalışıyor (26 kayıt) |
| 519 | Metro Istanbul Fault Types List | Arıza türleri (2) | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetFailuresTypes` | 2023-04-25 | ❌ sunucu hatası — HTTP 200 + Success=false: Could not find endpoint with name 'BasicHttpBinding_SI_ArizaDuyuru_Out'. |
| 511 | Metro Istanbul News List According to Different Language Options | Metro haberleri | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetNews/%7BLanguage%7D` | 2023-04-25 | ✅ çalışıyor (11 kayıt; en yeni 2026-10-17 00:01) |
| 510 | Metro Istanbul Event List | Metro etkinlikleri | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetActivities` | 2023-04-25 | ✅ çalışıyor (168 kayıt; en yeni 2026-02-05 00:00) |
| 549 | Metro Istanbul Network Map List Web Service | Metro ağ haritaları | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetMaps` | 2022-04-04 | ✅ çalışıyor (7 kayıt; en yeni 2026-07-29 00:00) |
| 550 | Metro Istanbul Address List Web Service According to Different Language Options | Metro adresleri | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetAddresses/{Language}` | 2022-04-04 | ✅ çalışıyor (15 kayıt) |
| 551 | Metro Istanbul Ongoing Project List Web Service | Metro devam eden projeler | `https://api.ibb.gov.tr/MetroIstanbul/api/MetroMobile/V2/GetLineProjects` | 2022-04-04 | ✅ çalışıyor (67 kayıt; en yeni 2026-11-09 00:00) |
| 382 | Signalized Junction Information Web Service | Sinyalize kavşaklar | `https://api.ibb.gov.tr/web/api/junction` | 2024-08-21 | ✅ çalışıyor (2590 kayıt) |
| 352 | IETT Vehicle Features Web Service | İETT araç özellikleri | `https://api.ibb.gov.tr/iett/AracAnaVeri/AracOzellik.asmx?wsdl` | 2024-11-06 | ✅ WSDL okundu, 2 metot |
| 384 | City Map API | Şehir Haritası API (geocoding için değerlendirilebilir) | `https://sehirharitasiapi.ibb.gov.tr/developer/` | 2024-05-20 | ℹ️ JSON uç noktası değil — developer portal sayfası; geocoding kararı Faz 1'e kaldı |
## B — Statik veri → yerel tablo / konum aracı

| No | Veri seti | Ne işe yarar | Format | Son güncelleme |
|---|---|---|---|---|
| 75 | IETT GTFS Data | İETT GTFS (otobüs tarifeleri, duraklar, güzergâhlar) | CSV,ZIP | 2026-04-21 |
| 308 | Public Transport GTFS Data | Toplu taşıma GTFS | CSV | 2025-03-12 (metro verisi 2020-07-09, güncellenmeyecek) → `get_next_departures` (metro) ve `get_rail_travel_time` kaynağı, `is_stale: true` |
| 6 | Raylı Sistem İstasyon Noktaları Verisi | Raylı sistem istasyon noktaları | GEOJSON | 2026-11-06 |
| 4 | Rail System Station Areas Data | Raylı sistem istasyon alanları | GEOJSON | 2026-11-06 |
| 7 | Raylı Sistem Hatları Vektör Verisi | Raylı sistem hatları | GEOJSON | 2026-11-04 |
| 12 | Maritime Transportation Lines Vector Data | Deniz ulaşım hatları | GEOJSON | 2026-11-04 |
| 15 | Marine Transportation Stations Vector Data | İskeleler | GEOJSON | 2026-11-04 |
| 315 | İstanbul Şehir Hatları İskeleleri | Şehir Hatları iskeleleri | KML | 2025-03-05 |
| 314 | Contracted Piers Transferred in Istanbul | Sözleşmeli iskeleler | KML | 2025-03-05 |
| 128 | İETT Otobüs Durakları Verisi | İETT durakları | GEOJSON | 2026-03-18 |
| 129 | İETT Hat Güzergahları Verisi | İETT güzergâhları | GEOJSON | 2026-03-18 |
| 11 | Minibus Lines Data | Minibüs hatları | GEOJSON | 2026-11-04 |
| 13 | Minibus Stops Data | Minibüs durakları | GEOJSON | 2026-11-04 |
| 8 | Taksi Dolmuş Hatları Verisi | Taksi-dolmuş hatları | GEOJSON | 2026-11-04 |
| 9 | Taksi Dolmuş Durakları Verisi | Taksi-dolmuş durakları | GEOJSON | 2026-11-04 |
| 10 | Taksi Durakları Verisi | Taksi durakları | GEOJSON | 2026-11-04 |
| 1 | Istanbul Bicycle Paths Data | Bisiklet yolları | GEOJSON | 2026-11-06 |
| 16 | Bicycle and Micromobility Parking Areas | Bisiklet/mikromobilite park alanları | GEOJSON | 2026-11-04 |
| 109 | Bisiklet Bakım İstasyonları | Bisiklet bakım istasyonları | XLSX | 2026-04-06 |
| 5 | Yayalaştırılmış Yol Verisi | Yayalaştırılmış yollar | GEOJSON | 2026-11-06 |
| 536 | ISPARK Parking Information | İSPARK otopark bilgileri | CSV | 2023-02-27 |
| 108 | Existing Parking Numbers and Capacities | Mevcut otopark sayı ve kapasiteleri | XLSX | 2026-04-06 |
| 120 | ISPARK Car Parks According to the System Used | Sisteme göre İSPARK otoparkları | XLSX | 2026-03-31 |
| 159 | Istanbulkart Filling Center Information | İstanbulkart dolum noktaları | CSV,XLSX | 2026-03-02 |
| 275 | Elektrikli Araç Şarj istasyonları Soket Verileri | Elektrikli araç şarj soketleri | CSV | 2025-05-20 |
| 276 | Elektrikli Araç Şarj İstasyonları Verisi | Elektrikli araç şarj istasyonları | GEOJSON | 2025-05-13 |
| 199 | Disabled Charging Points at Metro Stations | Metro istasyonlarında engelli şarj noktaları | XLSX | 2026-02-11 |
| 529 | NUMBER OF ELEVATORS AND ESCALATORS AT THE KIRAZLI-HALKALI STATION | Kirazlı-Halkalı asansör/yürüyen merdiven sayıları | XLSX | 2023-03-17 |
| 313 | Information on Transfer of Rail Systems | Raylı sistem aktarma bilgileri | XLSX | 2025-03-07 → `get_rail_travel_time` aktarma noktaları |
| 530 | RAIL SYSTEMS KIRAZLI-HALKALI STATION CONNECTIONS WITH OTHER LINES | Kirazlı-Halkalı aktarma bağlantıları | XLSX | 2023-03-17 |
| 532 | Raylı Sistemler İkitelli-Ataköy Hattı Diğer Hatlarla Bağlantı Noktaları | İkitelli-Ataköy aktarma bağlantıları | XLSX | 2023-03-15 |
| 409 | Rail Systems Mahmutbey-Esenyurt Station Connection Points with Other Lines | Mahmutbey-Esenyurt aktarma noktaları | XLSX | 2024-03-27 |
| 487 | STATION INFORMATION OF RAIL SYSTEMS | Raylı sistem istasyon bilgileri | XLSX | 2023-06-12 |
| 312 | Line Length Information of Rail Systems | Raylı sistem hat uzunlukları | XLSX | 2025-03-07 |
| 18 | Ulaşım Yönetim Merkezi Trafik Duyuruları Verisi | UYM trafik duyuruları (güncel CSV) | CSV | 2026-11-01 → ileride `get_traffic_status` için değerlendirilecek (karar 0001, K3) |
| 288 | Hourly Traffic Density Data Set | Saatlik trafik yoğunluğu (tarihsel) | CSV | 2025-04-16 |
| 249 | Istanbul Traffic Index | Trafik indeksi (tarihsel) | CSV | 2025-09-04 |
| 289 | Hourly Public Transport Data Set | Saatlik toplu taşıma (tarihsel) | CSV | 2025-04-15 |
| 221 | Raylı Sistemler İstasyon Bazlı Yolcu ve Yolculuk Sayıları | İstasyon bazlı yolcu sayıları | CSV | 2026-02-04 |
| 222 | İstanbul Deniz İskeleleri Yolcu Sayıları | İskele yolcu sayıları | CSV | 2026-02-04 |
| 268 | 1. Derece Acil Ulaşım Yolları Verisi | 1. derece acil ulaşım yolları | GEOJSON | 2025-06-05 |

## C — RAG kaynağı (metin)

| No | Veri seti | Ne işe yarar | Format | Son güncelleme |
|---|---|---|---|---|
| 509 | Metro Istanbul Frequently Asked Questions List | Metro İstanbul SSS (API ama metin → doğrudan RAG) | API | 2023-04-25 |
| 541 | Public Transportation Ticket Prices | Toplu taşıma bilet fiyatları (2022, güncelliğini kontrol et) | XLSX | 2022-06-10 |
| 445 | 34 Dakika İstanbul Ulaşım İndeksi | 34 Dakika Ulaşım İndeksi (PDF metodoloji) | GEOJSON,PDF | 2024-02-01 |
| 442 | 34 Dakika İstanbul Yürünebilirlik İndeksi | 34 Dakika Yürünebilirlik İndeksi (PDF) | GEOJSON,PDF | 2024-02-01 |
| 435 | Transportation Master Plan Household Surveys | Ulaşım Ana Planı hane anketleri (PDF) | CSV,PDF | 2024-02-14 |
| 496 | Istanbul Transportation Master Plan Household Survey | Ulaşım Ana Planı hane anketi raporu (PDF) | PDF,TXT | 2023-05-18 |

## D — İleride / analiz için

| No | Veri seti | Ne işe yarar | Format | Son güncelleme |
|---|---|---|---|---|
| 27 | UKOME Yol Parçası Bazında Ana Arterler | UKOME ana arterler | CSV | 2026-10-13 |
| 28 | UKOME Major Arteries by Pathway (Continuous) | UKOME ana arterler (sürekli) | CSV | 2026-10-13 |
| 84 | Raylı Sistemler Günlük, Aylık, Yıllık Hat Bazlı Sefer Sayıları | Raylı sistem sefer sayıları | XLSX | 2026-04-15 |
| 88 | RAIL SYSTEMS LINE BASED FLIGHT NUMBERS | Hat bazlı sefer sayıları | XLSX | 2026-04-14 |
| 114 | İBB Şehir Hatları Sefer Sayıları | Şehir Hatları sefer sayıları | XLSX | 2026-04-03 |
| 47 | Şehir Hatları A.Ş. Denizyolu Taşımacılığı | Şehir Hatları taşımacılık | XLSX | 2026-06-17 |
| 113 | Number of Vehicles, Lines and Wharfs on the Basis of Maritime Enteprises | Deniz işletmeleri araç/hat/iskele | XLSX | 2026-04-03 |
| 554 | Maximum Number of Trips Based on Marine Businesses | Deniz işletmeleri maks. sefer | XLSX | 2022-04-04 |
| 291 | Maximum Number of Trips by Highways | Karayolu maks. sefer | XLSX | 2025-04-09 |
| 292 | Number of Vehicles and Lines by Highways | Karayolu araç ve hat | XLSX | 2025-04-09 |
| 98 | Rail Systems Ümraniye-Ataşehir-Göztepe Metro Line Information | Ümraniye-Ataşehir-Göztepe metro bilgileri | XLSX | 2026-04-09 |
| 101 | Rail Systems Pendik Center-Kaynarca Center-Fevzi Çakmak Metro Line Information | Pendik-Kaynarca-Fevzi Çakmak metro bilgileri | XLSX | 2026-04-08 |
| 105 | Rail Systems Dudullu-Bostancı Metro Line Information | Dudullu-Bostancı metro bilgileri | XLSX | 2026-04-06 |
| 106 | Rail Systems Çekmeköy-Sultanbeyli Metro Line Information | Çekmeköy-Sultanbeyli metro bilgileri | XLSX | 2026-04-06 |
| 300 | Raylı Sistemler Yenidoğan-Emek Metro Hattı Bilgileri | Yenidoğan-Emek metro bilgileri | XLSX | 2025-03-24 |
| 411 | Rail Systems Kabataş-Mecidiyeköy-Mahmutbey Metro Line Information | M7 hat bilgileri | XLSX | 2024-03-27 |
| 412 | Rail Systems Eminönü-Alibeyköy Tram Line Information | T5 tramvay bilgileri | XLSX | 2024-03-27 |
| 413 | Rail Systems Kabataş-Mecidiyeköy-Mahmutbey Metro Line Parking Information | M7 otopark bilgileri | XLSX | 2024-03-27 |
| 414 | Rail Systems İkitelli-Ataköy Metro Line Data | İkitelli-Ataköy hat bilgileri | XLSX | 2024-03-27 |
| 415 | Rail Systems Rumelihisarüstü-Aşiyan Funicular Line District Station Data | Aşiyan füniküler | XLSX | 2024-03-27 |
| 417 | Raylı Sistemler Kirazlı-Halkalı Metro Hattı Bilgileri | Kirazlı-Halkalı hat bilgileri | XLSX | 2024-03-26 |
| 421 | Rail Systems Mahmutbey-Esenyurt Metro Line Information | Mahmutbey-Esenyurt hat bilgileri | XLSX | 2024-03-19 |
| 423 | Rail Systems Karnarca-Icmeler Metro Line Information | Kaynarca-İçmeler hat bilgileri | XLSX | 2024-03-13 |
| 410 | Rail Systems Mahmutbey-Esenyurt Metro Line District Station Information | Mahmutbey-Esenyurt istasyon bilgileri | XLSX | 2024-03-27 |
| 528 | RAIL SYSTEMS KIRAZLI-HALKALI METRO LINE STATION INFORMATION | Kirazlı-Halkalı istasyon bilgileri | XLSX | 2023-03-17 |
| 96 | Pendik-Kaynarca-Fevzi Çakmak Metro Line Station and Main Line Tunnel Routes | Pendik-Kaynarca güzergâh (KML) | KML | 2026-04-10 |
| 97 | Dudullu Bostancı Metro Line Station and Main Line Tunnel Routes | Dudullu-Bostancı güzergâh (KML) | KML | 2026-04-10 |
| 99 | Ümraniye-Ataşehir-Göztepe Metro Line Station and Main Line Routes | Ümraniye-Göztepe güzergâh (KMZ) | KMZ | 2026-04-09 |
| 107 | Çekmeköy-Sancaktepe-Sultanbeyli Metro Line Station and Main Line Tunnel Routes | Çekmeköy-Sultanbeyli güzergâh (KMZ) | KMZ | 2026-04-06 |
| 420 | T5-Eminönü-Alibeyköy Tram Line Route Plan and Station Information | T5 güzergâh (KMZ) | KMZ | 2024-03-19 |
| 488 | Rail Systems Line Based Trip Numbers | Hat bazlı sefer sayıları (eski) | XLSX | 2023-06-12 |
| 489 | Number and Rate of Passengers Based on Transport Mode Types | Ulaşım türüne göre yolcu oranları | XLSX | 2023-06-12 |
| 491 | Daily Maximum Journeys of the Rail System | Raylı sistem günlük maks. yolculuk | XLSX | 2023-06-08 |
| 220 | Yaş Grubuna Göre Raylı Sistemler İstasyon Bazlı Yolcu ve Yolculuk Sayıları | Yaş grubuna göre istasyon yolcuları | CSV,XLSX | 2026-02-05 |
| 223 | Toplu Ulaşımda İstanbulkart Harici Geçiş Verisi | İstanbulkart harici geçişler | CSV | 2026-01-28 |
| 157 | Electronic Ticket Pass Data Used in Public Transport | Elektronik bilet geçişleri | XLSX | 2026-03-02 |
| 158 | İstanbulkart Yıllara ve Kart Tiplerine Göre Kullanım Verisi | İstanbulkart kart tipi kullanımı | XLSX | 2026-03-02 |
| 310 | Günlük Araç Sayımı | Günlük araç sayımı | CSV | 2025-03-07 |
| 309 | Routes Shown On Varıous Message Boards In Istanbul | Mesaj panolarında gösterilen rotalar | CSV,XLSX | 2025-03-12 |
| 318 | Değişken Mesaj Panolarında Gösterilen Bilgiler | Değişken mesaj pano bilgileri | CSV | 2025-03-03 |
| 124 | Mevcut Akıllı Ulaşım Sistemleri Sayısı | Akıllı ulaşım sistemleri sayısı | XLSX | 2026-03-24 |
| 185 | Ulaşım Talep Tahmin Modeli Verisi | Ulaşım talep tahmin modeli | KMZ,XLSX | 2026-02-18 |
| 556 | İstanbul Ulaşım Modeli Zirve Saat Türel Dağılım Oranları | Zirve saat türel dağılım | CSV | 2022-04-04 |
| 290 | Traffic Density Map | Trafik yoğunluk haritası (HTML) | HTML | 2025-04-15 |
| 450 | İstanbul Bisiklet Haritası | Bisiklet haritası (HTML) | HTML | 2024-01-29 |
| 461 | Bicification Project Data | Bisikletleşme projesi | GEOJSON | 2023-11-29 |
| 353 | Ulaşım Sanat Yapıları Veri Seti | Ulaşım sanat yapıları | GEOJSON | 2024-10-23 |
| 14 | Geçitler Veri Seti | Geçitler | GEOJSON | 2026-11-04 |
| 76 | İnşaat Aşamasındaki Kavşaklar | İnşaat aşamasındaki kavşaklar | XLSX | 2026-04-20 |
| 78 | İnşaat Aşamasındaki Yol Çalışmaları | İnşaat aşamasındaki yol çalışmaları | XLSX | 2026-04-20 |
| 438 | 34 Dakika İstanbul Barınma İndeksi | 34 Dakika Barınma | GEOJSON,PDF | 2024-02-01 |
| 439 | 34 Dakika İstanbul Çalışma İndeksi | 34 Dakika Çalışma | GEOJSON,PDF | 2024-02-01 |
| 440 | 34 Dakika İstanbul İhtiyacını Karşılama İndeksi | 34 Dakika İhtiyaç | GEOJSON,PDF | 2024-02-01 |
| 441 | 34 Dakika İstanbul Yaşam Kalitesi İndeksi | 34 Dakika Yaşam Kalitesi | GEOJSON,PDF | 2024-02-01 |
| 443 | 34 Dakika İstanbul Karşılanabilirlik İndeksi | 34 Dakika Karşılanabilirlik | GEOJSON,PDF | 2024-02-01 |
| 444 | 34 Dakika İstanbul Vakit Geçirme İndeksi | 34 Dakika Vakit Geçirme | GEOJSON,PDF | 2024-02-01 |
| 446 | 34 Minutes Istanbul Health Index | 34 Dakika Sağlık | GEOJSON,PDF | 2024-02-01 |
| 447 | 34 Dakika İstanbul Öğrenme İndeksi | 34 Dakika Öğrenme | GEOJSON,PDF | 2024-02-01 |
| 448 | 34 Dakika İstanbul Kültürel Aktivite İndeksi | 34 Dakika Kültürel Aktivite | GEOJSON,PDF | 2024-02-01 |
| 449 | 34 Dakika İstanbul Çeşitlilik İndeksi | 34 Dakika Çeşitlilik | GEOJSON,PDF | 2024-02-01 |
| 116 | Yıllara Göre İSTMARİN Kapasite ve Doluluk Oranı Bilgileri | İSTMARİN kapasite/doluluk | XLSX | 2026-04-02 |
| 219 | Yıllara Göre İSPARK Otoparkları Abone Sayıları | İSPARK abone sayıları | CSV | 2026-02-05 |
| 79 | Fiyat Etiketi, Tarife, Dara ve AVM Otopark Denetimleri | AVM otopark denetimleri | XLSX | 2026-04-20 |
| 467 | VDYM İlçe Bazında Hane Yakınındaki Parklanma Tercihleri | VDYM parklanma tercihleri | XLSX | 2023-09-06 |
| 468 | VDYM İlçe Bazında Hanelerdeki Araç Sahipliği Durumu | VDYM araç sahipliği | XLSX | 2023-09-06 |

## Notlar

- İETT servisleri SOAP (`.asmx?wsdl`); Metro İstanbul servisleri REST/JSON (`api.ibb.gov.tr/MetroIstanbul/Help` dokümantasyonu var). İki farklı istemci gerekecek.
- Her API veri setinin yanında bir PDF kullanım dokümanı var; bunlar RAG'deki "veri seti açıklamaları" bölümü için de kaynak.
- Portalda deniz ulaşımı (Şehir Hatları) için canlı sefer API'si yok; vapur için GTFS/sefer sayıları ve iskele konumları kullanılmalı.

### Faz 0 uç nokta testinden çıkan notlar

- **Kimlik doğrulama yok.** 39 uç noktanın hiçbiri anahtar istemedi (401/403 görülmedi). İETT WSDL'lerinde `AuthHeader` bildirilmiş ama başlık gönderilmeden de çağrılar çalıştı.
- **Metro API başarısızlıkta da HTTP 200 dönüyor**, gövdede `Success: false` ve bir .NET istisnası bulunur. Sağlık kontrolü HTTP durumuna değil `Success` alanına bakmalı.
- **Dört Metro uç noktası GET'e 405 döner, POST ister** (552, 513, 515, 505). Zorunlu alanları kendi hata mesajlarında söylüyorlar; doğrulanan gövdeler yukarıdaki Durum kolonunda.
- **`zeep` dört İETT WSDL'ini reddediyor** (350, 349, 172, 171): `AuthHeader` bildirilmiş ama eşleşen `<message>` tanımı WSDL'de yok. Metot listeleri WSDL XML'i doğrudan ayrıştırılarak çıkarıldı; Faz 2'de bu servisler için `zeep` yerine elle kurulan SOAP zarfı gerekebilir.
- **Metro sefer saatleri (552) ve istasyon arası süreler (513) sunucu taraflı bozuk.** 18 hat × tüm yönler = 43/43 deneme aynı istisnayı verdi. Bu iki aracın kaynağı GTFS'e taşındı (karar 0001, K1-K2).
- **İsbike (169, 170) kullanılamaz:** 169 HTTP 200 veriyor ama `dataList` boş; 170 ise yalnızca o boş listeden alınabilecek bir `guid` istiyor.
- **Araç konumu doğrulandı ama aracı askıya alındı (10 Kasım 2026):** `get_vehicle_positions` planda askıda, gerekçe `documents/PLAN.md` araç tasarımı bölümünde. Uç noktanın kendisi çalışıyor: `sefer-gerceklesme.GetHatOtoKonum_json(HatKodu)` hat bazlı canlı konum (koordinat, yön, en yakın durak) döndürüyor; `GetFiloAracKonum_json()` ise 6940 araçla 1.1 MB — agent aracı olarak sunulmayacak.
- **Araç konumu metotları İETT otobüsleri içindir**; metro/tramvay/füniküler için canlı araç konumu veren bir uç nokta portalda yok.

## Tüm veri setleri

| No | Veri seti | Kategori | Format | Son güncelleme | İşaret |
|---|---|---|---|---|---|
| 1 | Istanbul Bicycle Paths Data | Mobilite | GEOJSON | 2026-11-06 | B |
| 2 | Muhtarlık Adres Bilgileri | Yaşam | GEOJSON | 2026-11-06 |  |
| 3 | Şehir Tuvaletleri Veri Seti | Yaşam | GEOJSON | 2026-11-06 |  |
| 4 | Rail System Station Areas Data | Mobilite | GEOJSON | 2026-11-06 | B |
| 5 | Yayalaştırılmış Yol Verisi | Mobilite | GEOJSON | 2026-11-06 | B |
| 6 | Raylı Sistem İstasyon Noktaları Verisi | Mobilite | GEOJSON | 2026-11-06 | B |
| 7 | Raylı Sistem Hatları Vektör Verisi | Mobilite | GEOJSON | 2026-11-04 | B |
| 8 | Taksi Dolmuş Hatları Verisi | Mobilite | GEOJSON | 2026-11-04 | B |
| 9 | Taksi Dolmuş Durakları Verisi | Mobilite | GEOJSON | 2026-11-04 | B |
| 10 | Taksi Durakları Verisi | Mobilite | GEOJSON | 2026-11-04 | B |
| 11 | Minibus Lines Data | Mobilite | GEOJSON | 2026-11-04 | B |
| 12 | Maritime Transportation Lines Vector Data | Mobilite | GEOJSON | 2026-11-04 | B |
| 13 | Minibus Stops Data | Mobilite | GEOJSON | 2026-11-04 | B |
| 14 | Geçitler Veri Seti | Mobilite; Security | GEOJSON | 2026-11-04 | D |
| 15 | Marine Transportation Stations Vector Data | Mobilite | GEOJSON | 2026-11-04 | B |
| 16 | Bicycle and Micromobility Parking Areas | Mobilite | GEOJSON | 2026-11-04 | B |
| 17 | Düşey Trafik İşaretleme Çalışmaları | Mobilite | XLSX | 2026-11-04 |  |
| 18 | Ulaşım Yönetim Merkezi Trafik Duyuruları Verisi | Mobilite | CSV | 2026-11-01 | B |
| 19 | İSTAÇ FORTUNE 500 Sıralaması |  | XLSX | 2026-10-27 |  |
| 20 | İBB Ulusal Uzun Vadeli Kredi Notu | Ekonomi; Governance | XLSX | 2026-10-23 |  |
| 21 | İBB Uluslararası Uzun Vadeli Kredi Notu | Ekonomi; Governance | XLSX | 2026-10-23 |  |
| 22 | Tescil Durumuna Göre Kültür Varlıkları Sayıları | Yaşam | XLSX | 2026-10-22 |  |
| 23 | Atık Tesisleri | Çevre | CSV | 2026-10-19 |  |
| 24 | Asya Bölgesi Plansız Elektrik Kesintileri | Enerji | CSV | 2026-10-19 |  |
| 25 | Avrupa Bölgesi Plansız Elektrik Kesintileri | Enerji | CSV | 2026-10-19 |  |
| 26 | Toplu Taşıma Araçlarına Verilen Taşımacılık Belgeleri Sayıları | Mobilite | CSV | 2026-10-15 |  |
| 27 | UKOME Yol Parçası Bazında Ana Arterler | Mobilite | CSV | 2026-10-13 | D |
| 28 | UKOME Major Arteries by Pathway (Continuous) | Mobilite | CSV | 2026-10-13 | D |
| 29 | Kültür Varlıklarını Korumaya Yönelik Tamamlanan Projelendirme Çalışmaları | Yaşam | XLSX | 2026-10-13 |  |
| 30 | Akaryakıt İstasyonları | Çevre | CSV | 2026-10-12 |  |
| 31 | İstanbul Kültür Varlıkları Envanteri | Yaşam | CSV | 2026-10-12 |  |
| 32 | Amount of Landfill Leachate Treated in Landfill Leachate Treatment Plants |  | XLSX | 2026-10-09 |  |
| 33 | Yatay Trafik İşaretleme Çalışmaları | Mobilite | XLSX | 2026-09-16 |  |
| 34 | Eski Eserlerde Koruma Uygulama ve Denetim Çalışmaları | Yaşam | XLSX | 2026-09-15 |  |
| 35 | Trafik Eğitimi Verilen Okul Sayıları | Governance; İnsan | XLSX | 2026-09-02 |  |
| 36 | Trafik Eğitimi Verilen Öğrenci Sayıları | Governance; Yaşam | XLSX | 2026-09-02 |  |
| 37 | Uygulaması Devam Eden Kentsel Açık ve Yeşil Alanlar | Çevre | XLSX | 2026-09-01 |  |
| 38 | Tüketici Şikayetleri Çözümleme Faaliyetleri | İnsan | XLSX | 2026-08-31 |  |
| 39 | Kütüphanelerde Gerçekleştirilen Etkinlik Sayısı ve Katılımcı Sayısı | Yaşam | XLSX | 2026-08-31 |  |
| 40 | Ulaşıma Yönelik Alınan Karar Türü ve Sayıları | Mobilite | XLSX | 2026-08-20 |  |
| 41 | Kurban Satış ve Kesim Noktalarında Kesilen Kurban Sayıları | Yaşam | XLSX | 2026-08-17 |  |
| 42 | Animal Products Production Quantity | Ekonomi; Çevre | XLSX | 2026-07-30 |  |
| 43 | Basın Yayın Faaliyetleri | Governance | XLSX | 2026-07-27 |  |
| 44 | Engellilere Yönelik Verilen Destekleyici Eğitim Hizmetleri | İnsan | XLSX | 2026-07-27 |  |
| 45 | Alıcı Ortamlara Göre Şebekeden Deşarj Edilen Atıksu Miktarı (Bin M3/Yıl) | Çevre | XLSX | 2026-07-17 |  |
| 46 | VetAkademi Kapsamında Eğitim Verilen Kişi Sayısı | İnsan | XLSX | 2026-06-22 |  |
| 47 | Şehir Hatları A.Ş. Denizyolu Taşımacılığı | Mobilite | XLSX | 2026-06-17 | D |
| 48 | Yangın Önleme Faaliyetleri Raporları | Security | XLSX | 2026-06-12 |  |
| 49 | İtfai Olaylar Ambulans Çıkış Nedenleri | Security | XLSX | 2026-06-12 |  |
| 50 | Müzelerimiz ve Ziyaretçi Sayıları | Yaşam | XLSX | 2026-06-11 |  |
| 51 | Cankurtaran Ekipleri Müdahale İstatistikleri | Security | XLSX | 2026-05-22 |  |
| 52 | İBB Kütüphaneleri Kitap Sayıları | Yaşam | XLSX | 2026-05-22 |  |
| 53 | İtfai Olaylar Ortalama Varış Süresi | Security | XLSX | 2026-05-20 |  |
| 54 | Sinyalizasyon Sistem Çalışmaları | Mobilite | XLSX | 2026-05-14 |  |
| 55 | İtfaiye Eğitimleri ve İlkyardım Çalışmaları | İnsan | XLSX | 2026-05-14 |  |
| 56 | İtfai İstatistikler-yangın | Security | XLSX | 2026-05-14 |  |
| 57 | İtfai Olaylar | Security | XLSX | 2026-05-07 |  |
| 58 | Genç Üniversiteli Eğitim Yardımı | İnsan | XLSX | 2026-05-06 |  |
| 59 | Yangın Olmayan Diğer İtfai Olaylar | Security | XLSX | 2026-05-04 |  |
| 60 | Tüketici Hakları Kapsamında Bilgilendirme ve Eğitim Çalışmaları | Governance; İnsan | XLSX | 2026-05-04 |  |
| 61 | Baca Temizleme ve Denetleme Faaliyetleri | Security | XLSX | 2026-05-04 |  |
| 62 | Cankurtaran Veri Seti | Security | XLSX | 2026-05-04 |  |
| 63 | Yaş Aralığına Göre İBB Çözüm Merkezi Başvuru İstatistikleri | Governance | XLSX | 2026-05-04 |  |
| 64 | Çözüm Merkezi Konumları | Governance | XLSX | 2026-04-29 |  |
| 65 | Sektörel Dağılıma Göre Çözüm Merkezi İstatistikleri | Governance | XLSX | 2026-04-29 |  |
| 66 | Medeni Hale Göre İBB Çözüm Merkezi Başvuru İstatistikleri | Governance | XLSX | 2026-04-29 |  |
| 67 | İlçelere Göre İBB Çözüm Merkezi Başvuru İstatistikleri | Governance | XLSX | 2026-04-28 |  |
| 68 | İBB Çözüm Merkezi Başvuru Sayıları | Governance | XLSX | 2026-04-28 |  |
| 69 | İstanbul İli Balıkçı Bulunmayan Semt Pazarları | Yaşam | XLSX | 2026-04-28 |  |
| 70 | Number of IMM personnel who received Basic Occupational Health and Safety Training | Governance; İnsan | CSV | 2026-04-27 |  |
| 71 | Eğitim Durumuna Göre İBB Çözüm Merkezi Başvuru İstatistikleri | Governance | XLSX | 2026-04-27 |  |
| 72 | Cinsiyete Göre Çözüm Merkezi Başvuru İstatistikleri | Governance | XLSX | 2026-04-24 |  |
| 73 | Başvuru Tipine Göre İBB Çözüm Merkezi İstatistikleri | Governance | XLSX | 2026-04-24 |  |
| 74 | Başvuru Geliş Kanalına Göre İBB Çözüm Merkezi İstatistikleri | Governance | XLSX | 2026-04-24 |  |
| 75 | IETT GTFS Data | Mobilite | CSV,ZIP | 2026-04-21 | B |
| 76 | İnşaat Aşamasındaki Kavşaklar | Mobilite | XLSX | 2026-04-20 | D |
| 77 | İlçe ve Sektör Bazında Birinci Sınıf Gayrisıhhi Müessese Sayısı | Governance; Security; Çevre | XLSX | 2026-04-20 |  |
| 78 | İnşaat Aşamasındaki Yol Çalışmaları | Mobilite; Security | XLSX | 2026-04-20 | D |
| 79 | Fiyat Etiketi, Tarife, Dara ve AVM Otopark Denetimleri | Governance | XLSX | 2026-04-20 | D |
| 80 | Devam Eden Duvar, Üstgeçit, Köprü Güçlendirme ve Heyelan Önleme Çalışmaları | Security | XLSX | 2026-04-17 |  |
| 81 | Tamamlanan Duvar, Üstgeçit, Köprü Güçlendirme ve Heyelan Önleme Çalışmaları | Security | XLSX | 2026-04-17 |  |
| 82 | İstanbul Kentsel Açık ve Yeşil Alan Verileri | Çevre | XLSX | 2026-04-15 |  |
| 83 | Park Bahçe ve Yeşil Alan Verileri | Çevre | XLSX | 2026-04-15 |  |
| 84 | Raylı Sistemler Günlük, Aylık, Yıllık Hat Bazlı Sefer Sayıları | Mobilite | XLSX | 2026-04-15 | D |
| 85 | Completed Site Information For Rail Systems | Mobilite | XLSX | 2026-04-15 |  |
| 86 | Rail Systems Wagon Mileage Information | Mobilite | XLSX | 2026-04-15 |  |
| 87 | Rail Systems Daily, Monthly, Annual Wagon Kilometer Information | Mobilite | XLSX | 2026-04-14 |  |
| 88 | RAIL SYSTEMS LINE BASED FLIGHT NUMBERS | Mobilite | XLSX | 2026-04-14 | D |
| 89 | Lojistik Faaliyetlere Yönelik Düzenlenen Belge Türü ve Sayıları | Governance; Mobilite | XLSX | 2026-04-14 |  |
| 90 | Kent Lokantaları Konumları | Governance; Yaşam | XLSX | 2026-04-13 |  |
| 91 | İstanbul İlçeleri Hayvansal Ürünler Üretim Miktarı | Ekonomi; Çevre | XLSX | 2026-04-13 |  |
| 92 | Emekli Pazar Desteği Verileri | Yaşam | XLSX | 2026-04-13 |  |
| 93 | Sosyal Tesis Konumları | Governance; Yaşam | XLSX | 2026-04-13 |  |
| 94 | Sosyal Tesis Doluluk Oranları | Governance; Yaşam | XLSX | 2026-04-13 |  |
| 95 | Kent Lokantaları Kapasite ve Doluluk Oranları | Governance; Yaşam | XLSX | 2026-04-13 |  |
| 96 | Pendik-Kaynarca-Fevzi Çakmak Metro Line Station and Main Line Tunnel Routes | Mobilite | KML | 2026-04-10 | D |
| 97 | Dudullu Bostancı Metro Line Station and Main Line Tunnel Routes | Mobilite | KML | 2026-04-10 | D |
| 98 | Rail Systems Ümraniye-Ataşehir-Göztepe Metro Line Information | Mobilite | XLSX | 2026-04-09 | D |
| 99 | Ümraniye-Ataşehir-Göztepe Metro Line Station and Main Line Routes | Mobilite | KMZ | 2026-04-09 | D |
| 100 | İBB Meclis Üye Dağılımı | Governance | XLSX | 2026-04-08 |  |
| 101 | Rail Systems Pendik Center-Kaynarca Center-Fevzi Çakmak Metro Line Information | Mobilite | XLSX | 2026-04-08 | D |
| 102 | İGDAŞ Bina Bilgileri | Governance | XLSX | 2026-04-07 |  |
| 103 | Yıllara Göre Trafiğe Kayıtlı Taşıtların Ortalama Yaşları | Mobilite | XLS | 2026-04-07 |  |
| 104 | Population Information | İnsan | XLSX | 2026-04-07 |  |
| 105 | Rail Systems Dudullu-Bostancı Metro Line Information | Mobilite | XLSX | 2026-04-06 | D |
| 106 | Rail Systems Çekmeköy-Sultanbeyli Metro Line Information | Mobilite | XLSX | 2026-04-06 | D |
| 107 | Çekmeköy-Sancaktepe-Sultanbeyli Metro Line Station and Main Line Tunnel Routes | Mobilite | KMZ | 2026-04-06 | D |
| 108 | Existing Parking Numbers and Capacities | Mobilite | XLSX | 2026-04-06 | B |
| 109 | Bisiklet Bakım İstasyonları | Mobilite | XLSX | 2026-04-06 | B |
| 110 | Yaş Durumuna Göre İlçe Bazlı Görme Engelli Vatandaş Sayısı | İnsan | XLSX | 2026-04-03 |  |
| 111 | Number of Visually Impaired Citizens by District Based on Educational Status | İnsan | XLSX | 2026-04-03 |  |
| 112 | Number of Visually Impaired Citizens by District by Gender | İnsan | XLSX | 2026-04-03 |  |
| 113 | Number of Vehicles, Lines and Wharfs on the Basis of Maritime Enteprises | Mobilite | XLSX | 2026-04-03 | D |
| 114 | İBB Şehir Hatları Sefer Sayıları | Mobilite | XLSX | 2026-04-03 | D |
| 115 | IMM Bayrampasa & Ataşehir Vegetable-Fruit Market Total Vehicle Entry Numbers | Mobilite | XLSX | 2026-04-03 |  |
| 116 | Yıllara Göre İSTMARİN Kapasite ve Doluluk Oranı Bilgileri | Mobilite | XLSX | 2026-04-02 | D |
| 117 | İSTMARİN Yıllara Göre Bağlanan Tekne Ebatları Verisi | Mobilite | XLSX | 2026-03-31 |  |
| 118 | İSTMARİN Yıllara Göre Bağlanan Teknelerin Bayrakları Verisi | Mobilite | XLSX | 2026-03-31 |  |
| 119 | Yıllara Göre İSTMARİN Dışında Olan Tekne Sayıları Verisi | Mobilite | XLSX | 2026-03-31 |  |
| 120 | ISPARK Car Parks According to the System Used | Mobilite | XLSX | 2026-03-31 | B |
| 121 | Özel Konulu İş Sağlığı ve Güvenliği Eğitimi Alan İBB Personeli Sayısı | İnsan | XLSX | 2026-03-31 |  |
| 122 | Deprem Farkındalık ve Güvenli Yaşam Eğitimi Alan Kişi Sayısı | İnsan | XLSX | 2026-03-31 |  |
| 123 | Üye Olunan Uluslararası Kuruluşlar Verisi | Governance | CSV,XLS,XLSX | 2026-03-25 |  |
| 124 | Mevcut Akıllı Ulaşım Sistemleri Sayısı | Mobilite | XLSX | 2026-03-24 | D |
| 125 | İBB Lokasyon Verileri | Governance | CSV,PDF,XLSX | 2026-03-24 |  |
| 126 | Tech Istanbul Konumları | Governance | XLSX | 2026-03-24 |  |
| 127 | İETT Elektrikli Araç Özellikleri Verisi | Mobilite | XLSX | 2026-03-19 |  |
| 128 | İETT Otobüs Durakları Verisi | Mobilite | GEOJSON | 2026-03-18 | B |
| 129 | İETT Hat Güzergahları Verisi | Mobilite | GEOJSON | 2026-03-18 | B |
| 130 | İBB Binalarında Bulunan OED (Otomatik Eksternal Defibrilatör) Cihazları | Yaşam | XLSX | 2026-03-06 |  |
| 131 | Uygulaması Tamamlanan Kentsel Açık ve Yeşil Alanlar | Çevre | XLSX | 2026-03-04 |  |
| 132 | Yıllara Göre Yeşil Alanlar İçin Harcanan Su Miktarı | Çevre | XLSX | 2026-03-04 |  |
| 133 | İBB Birimleri Su Tüketimi | Çevre | XLSX | 2026-03-04 |  |
| 134 | Ağaç ve Peyzaj AŞ Taahhüt Faaliyetleri | Çevre | XLSX | 2026-03-04 |  |
| 135 | İBB Ağaç AŞ Bahçe Market ve Fidanlık Kapasite Verileri | Yaşam | XLSX | 2026-03-04 |  |
| 136 | Herbal Mortar Productions | Çevre | XLSX | 2026-03-04 |  |
| 137 | Ağaç ve Peyzaj AŞ Laboratuvar Analiz Bilgileri | Çevre | XLSX | 2026-03-04 |  |
| 138 | Ağaç ve Peyzaj Inc Production Amounts with Cooperatives | Yaşam; Çevre | XLSX | 2026-03-04 |  |
| 139 | The Amount of Trash Extracted from the Golden Horn and Streams | Çevre | XLSX | 2026-03-03 |  |
| 140 | Gemilerden Toplanan Atık Miktarı ve Türü | Çevre | XLSX | 2026-03-03 |  |
| 141 | Gemi Kaynaklı Petrol Türevli Atıkların Susuzlaştırılmasıyla Geri Kazanılan Miktar | Çevre | XLSX | 2026-03-03 |  |
| 142 | The Amount of Solid Waste Collected from the Sea Surface, Coasts and Beaches | Çevre | XLSX | 2026-03-03 |  |
| 143 | Atık Alım Hizmeti Verilen Gemi Sayısı | Çevre | XLSX | 2026-03-03 |  |
| 144 | IMM Halls of Residence Locations | İnsan | XLSX | 2026-03-03 |  |
| 145 | Mekanik Süpürme Araç Sayısı | Mobilite; Çevre | XLSX | 2026-03-03 |  |
| 146 | İlçelere Göre Yıllık Tıbbi Atık Miktarları | Çevre | XLSX | 2026-03-03 |  |
| 147 | İlçelere Göre Yıllık Mekanik Süpürme Miktarı | Çevre | XLSX | 2026-03-03 |  |
| 148 | İlçe Bazında Yıllık Evsel Atık Miktarları | Çevre | XLSX | 2026-03-03 |  |
| 149 | Elle Süpürme Personel Sayısı | Governance; Çevre | XLSX | 2026-03-03 |  |
| 150 | Çöp Kamyonu Sayısı | Mobilite; Çevre | XLSX | 2026-03-03 |  |
| 151 | Çevre Hizmetlerine Göre Yıllık Atık Miktarı | Çevre | XLSX | 2026-03-03 |  |
| 152 | Atıktan Geri Kazanım Miktarları | Çevre | XLSX | 2026-03-03 |  |
| 153 | Yıllara Göre İstanbulkart Mobil Uygulama İndirme Sayıları | Bilgi ve İletişim Teknolojileri; Mobilite | XLSX | 2026-03-03 |  |
| 154 | Yıllara Göre İstanbulKart Ulaşım Dışı Kullanım Verileri | Ekonomi; Mobilite | XLSX | 2026-03-03 |  |
| 155 | Yıllara Göre İstanbulKart QR Kullanım Verileri | Ekonomi; Mobilite | XLSX | 2026-03-03 |  |
| 156 | IstanbulCard Digital Account Transaction Data by Year | Ekonomi; Mobilite | XLSX | 2026-03-03 |  |
| 157 | Electronic Ticket Pass Data Used in Public Transport | Mobilite | XLSX | 2026-03-02 | D |
| 158 | İstanbulkart Yıllara ve Kart Tiplerine Göre Kullanım Verisi | Mobilite | XLSX | 2026-03-02 | D |
| 159 | Istanbulkart Filling Center Information | Mobilite | CSV,XLSX | 2026-03-02 | B |
| 160 | Ecology and Wildlife Services Locations | Çevre | XLSX | 2026-03-02 |  |
| 161 | Institute Istanbul ISMEK Locations | Yaşam; İnsan | XLSX | 2026-03-02 |  |
| 162 | Solid Waste Transfer Station Location | Çevre | XLSX | 2026-03-02 |  |
| 163 | İSPARK Otopark Detay Bilgileri Web Servisi | Mobilite | API,PDF | 2026-03-02 | A |
| 164 | Bulgur Palas'ta Etkinlik Düzenleyen Kulüp ve Kuruluşlar | Yaşam | XLSX | 2026-03-02 |  |
| 165 | Solar Energy Systems Installation Completed | Enerji | XLSX | 2026-03-02 |  |
| 166 | İBB Birimleri Elektrik Tüketimi | Enerji | XLSX | 2026-03-02 |  |
| 167 | İBB Birimleri Doğalgaz Tüketimi | Enerji | XLSX | 2026-03-02 |  |
| 168 | Metro İstanbul İstasyon Bilgi Listesi | Mobilite | API | 2026-02-27 | A |
| 169 | Tüm İsbike İstasyonları Web Servisi | Mobilite | API,PDF | 2026-02-27 | A2 |
| 170 | İsbike İstasyon Durumları Web Servisi | Mobilite | API,PDF | 2026-02-27 | A2 |
| 171 | İETT Durak ve Hat Bilgileri Web Servisi | Mobilite | API,PDF | 2026-02-27 | A |
| 172 | İETT Hat-Durak-Güzergâh Web Servisi | Bilgi ve İletişim Teknolojileri; Mobilite | API,PDF | 2026-02-27 | A |
| 173 | Bulgur Palas Yıllık Etkinlik Detayları ve Katılımcı Sayıları | Yaşam | XLSX | 2026-02-26 |  |
| 174 | Bulgur Palas Aylık Etkinlik ve Katılımcı Dağılımı | Yaşam | XLSX | 2026-02-26 |  |
| 175 | Şehir Tiyatroları Oyun İstatistiği | Yaşam | XLSX | 2026-02-26 |  |
| 176 | İlçelere Göre İGDAŞ Abone Sayıları | Enerji | XLSX | 2026-02-24 |  |
| 177 | İlçelere Göre Doğalgaz Kullanım Sınıfı Bazında Kullanıcı Sayısı ve Tüketim Miktarı | Enerji | XLSX | 2026-02-24 |  |
| 178 | İGDAŞ Investment Type and Length Information | Governance | XLSX | 2026-02-24 |  |
| 179 | Darülaceze Hizmetleri | Yaşam | XLSX | 2026-02-23 |  |
| 180 | Mahallelere Göre Sosyal Yardım Alan Hane Sayısı | Yaşam | XLSX | 2026-02-23 |  |
| 181 | Yapılan Ayni ve Nakdi Yardım Tür ve Sayıları | Yaşam | XLSX | 2026-02-23 |  |
| 182 | Şehir Tiyatroları Veri Seti | Yaşam | CSV | 2026-02-23 |  |
| 183 | Psychological Support Services | Yaşam | XLSX | 2026-02-19 |  |
| 184 | İSKİ İlçe Bazlı Çağrı ve Çözüm Oranı | Governance; Çevre | XLSX | 2026-02-18 |  |
| 185 | Ulaşım Talep Tahmin Modeli Verisi | Mobilite | KMZ,XLSX | 2026-02-18 | D |
| 186 | İstanbul'da Meydana Gelen Su Kesintileri | Çevre | XLSX | 2026-02-13 |  |
| 187 | İBB Müzeleri Lokasyon Çalışma Gün ve Saatleri | Yaşam | XLSX | 2026-02-12 |  |
| 188 | IMM Libraries Location Working Days and Hours | Yaşam | XLSX | 2026-02-12 |  |
| 189 | Kütüphane ve Müzeler Müdürlüğü Okuyucu İstatistikleri | Yaşam; İnsan | XLSX | 2026-02-12 |  |
| 190 | Cemetery Address Information | Yaşam | XLSX | 2026-02-12 |  |
| 191 | Yangın Sayısı | Security | XLSX | 2026-02-12 |  |
| 192 | Tonnage data of IMM fruit and vegetable market | Yaşam | XLSX | 2026-02-12 |  |
| 193 | Perakende Akaryakıt Satış Miktarı | Enerji | XLSX | 2026-02-12 |  |
| 194 | Otomasyonlu Akaryakıt Satış Miktarı | Enerji | XLSX | 2026-02-12 |  |
| 195 | IMM Facilities Electrical Energy Production Amounts | Enerji | XLSX | 2026-02-12 |  |
| 196 | Dökme Akaryakıt Satış Miktarı | Enerji | XLSX | 2026-02-12 |  |
| 197 | İstanbul İli Balıkçı Bulunan Semt Pazarları | Yaşam | XLSX | 2026-02-12 |  |
| 198 | Kültür Merkezleri Veri Seti | Yaşam | XLSX | 2026-02-11 |  |
| 199 | Disabled Charging Points at Metro Stations | Enerji; Mobilite | XLSX | 2026-02-11 | B |
| 200 | Various Sports Organizations | Yaşam | XLSX | 2026-02-11 |  |
| 201 | Lojistik Destek Verilen Spor Organizasyonları | Yaşam | XLSX | 2026-02-11 |  |
| 202 | Location and Date Data of IMM Outdoor Exercises | Yaşam | XLSX | 2026-02-11 |  |
| 203 | Number of People Participating in Nature Camp | Yaşam | CSV | 2026-02-11 |  |
| 204 | Gerçekleştirilen Kültürel Etkinlik Tür ve Sayıları | Yaşam | XLSX | 2026-02-11 |  |
| 205 | İstanbul Halk Ekmek Büfelerinin Konumları Veri Seti | Yaşam | XLSX | 2026-02-10 |  |
| 206 | Social Support Types and Services | Yaşam | XLSX | 2026-02-10 |  |
| 207 | Evde Sağlık Hizmetleri | Yaşam | XLSX | 2026-02-10 |  |
| 208 | Vektörlerle Mücadele Hizmeti | Yaşam | XLSX | 2026-02-10 |  |
| 209 | Toplum Ruh Sağlığı Hizmetleri | Yaşam | XLSX | 2026-02-10 |  |
| 210 | Tıp Merkezlerinde Hizmet Verilen Hasta Sayısı | Yaşam | XLSX | 2026-02-10 |  |
| 211 | Bölgesel İstihdam Ofisi Lokasyon Verisi | Yaşam | XLSX | 2026-02-10 |  |
| 212 | Yıllara Göre Hayvan İstatistikleri | Yaşam | XLSX | 2026-02-10 |  |
| 213 | Number and Capacities of Animal Shelters by Years | Yaşam | XLSX | 2026-02-10 |  |
| 214 | Yıllara Göre Veteriner Denetim Hizmet İstatistikleri | Yaşam | XLSX | 2026-02-10 |  |
| 215 | İtfaiye İstasyonları Konum Bilgileri | Security; Yaşam | HTML,XLSX | 2026-02-09 |  |
| 216 | İtfaiye İstasyon Sayısı | Security | XLSX | 2026-02-09 |  |
| 217 | İtfaiye Araç Sayısı | Mobilite; Security | XLSX | 2026-02-06 |  |
| 218 | İtfaiye Personel Sayısı | Governance; İnsan | XLSX | 2026-02-06 |  |
| 219 | Yıllara Göre İSPARK Otoparkları Abone Sayıları | Mobilite | CSV | 2026-02-05 | D |
| 220 | Yaş Grubuna Göre Raylı Sistemler İstasyon Bazlı Yolcu ve Yolculuk Sayıları | Mobilite | CSV,XLSX | 2026-02-05 | D |
| 221 | Raylı Sistemler İstasyon Bazlı Yolcu ve Yolculuk Sayıları | Mobilite | CSV | 2026-02-04 | B |
| 222 | İstanbul Deniz İskeleleri Yolcu Sayıları | Mobilite | CSV | 2026-02-04 | B |
| 223 | Toplu Ulaşımda İstanbulkart Harici Geçiş Verisi | Mobilite | CSV | 2026-01-28 | D |
| 224 | Municipal Population Data Set | Governance | XLSX | 2026-01-13 |  |
| 225 | İstanbul Ağaç Örtüsü Kaybı | Çevre | CSV | 2025-12-24 |  |
| 226 | 2000 ve 2010 Yılı İstanbul Ağaç Örtüsü | Çevre | CSV | 2025-12-24 |  |
| 227 | Sergilenen Yerli ve Yabancı Tiyatro Eser Sayıları | Yaşam | XLSX | 2025-12-24 |  |
| 228 | Örtüaltı Tarım Alanları | Yaşam; Çevre | XLS | 2025-12-24 |  |
| 229 | Araç Türlerine Göre Yangın İstatistikleri | Security | XLSX | 2025-12-24 |  |
| 230 | Yapı Ruhsatına Göre Bina Sayısı | Yaşam | XLS | 2025-12-24 |  |
| 231 | Kültür ve Turizm Bakanlığına Bağlı Müze Ziyaretçi Sayısı | Yaşam | XLS | 2025-12-24 |  |
| 232 | Halk Kütüphanelerinde Kayıtlı Üye Çocuk Sayısı | Yaşam | XLS | 2025-12-24 |  |
| 233 | İstanbul İlçe Bazında Kazanılan Ağaç Örtüsü | Çevre | CSV | 2025-12-23 |  |
| 234 | Istanbul Observed Seasonal Cycle Data Set | Çevre | XLSX | 2025-12-17 |  |
| 235 | Sürücü Belgesi Olan Kişi Sayısı | Mobilite | XLS | 2025-12-17 |  |
| 236 | Yıllara Göre Ölümlü Yaralanmalı Trafik Kaza Sayısı | Mobilite | XLS | 2025-12-17 |  |
| 237 | Yıllara ve Yakıt Türüne Göre Trafiğe Kaydı Yapılan Motorlu Kara Taşıt Sayıları | Mobilite | XLS | 2025-12-15 |  |
| 238 | Şehir Tuvaletleri Kullanım İstatistikleri | Yaşam | XLSX | 2025-12-11 |  |
| 239 | Metro Hatlarının Ortalama Saatlik Enerji Tüketimi | Enerji | XLSX | 2025-11-20 |  |
| 240 | Kuş Türleri Veri Tabanı | Çevre | XLSX | 2025-11-18 |  |
| 241 | Dijital Uçurum Haritası Verileri | Governance | PDF,XLSX | 2025-10-21 |  |
| 242 | İBB Birleştirilmiş Stratejik Gürültü Haritaları | Çevre | GEOJSON | 2025-10-13 |  |
| 243 | Tiyatro Seyirci Sayısı | Yaşam; İnsan | CSV | 2025-09-30 |  |
| 244 | Yıllara Göre İstanbul'un Verdiği Göç Miktarı ve Nedenleri | Yaşam | XLSX | 2025-09-30 |  |
| 245 | Yıllara Göre İstanbul'un Aldığı Göç Miktarı ve Nedenleri | Yaşam | XLSX | 2025-09-30 |  |
| 246 | Şarj İstasyonlarının Elektrik Tüketimleri ve Payları | Enerji | XLSX | 2025-09-29 |  |
| 247 | Profil Grubu Bazındaki Elektrik Tüketiminin Bölgesel Dağılımı | Enerji | XLSX | 2025-09-29 |  |
| 248 | Tüketici Türüne Göre Elektrik Tüketimi | Enerji | CSV | 2025-09-26 |  |
| 249 | Istanbul Traffic Index | Mobilite | CSV | 2025-09-04 | B |
| 250 | Tech Istanbul Girişimcilerinin Verileri | Governance | XLSX | 2025-09-04 |  |
| 251 | Melen ve Yeşilçay Regülatörlerinden Alınan Su Miktarları | Çevre | CSV,XLSX | 2025-08-22 |  |
| 252 | Istanbul Annual Water Consumption Data | Çevre | XLSX | 2025-08-22 |  |
| 253 | Yıllık Tarımsal Destek İstatistiki Verileri | Yaşam; Çevre | XLSX | 2025-08-21 |  |
| 254 | Domestic Waste Amounts and Number of Trips Received at Transfer Centers | Mobilite; Çevre | XLSX | 2025-08-21 |  |
| 255 | Su Ürünleri Yetiştiricilik Üretim Miktarı | Ekonomi; Yaşam; Çevre | XLSX | 2025-08-14 |  |
| 256 | Bitkisel Ürünler Üretim Miktarı | Ekonomi; Çevre | XLSX | 2025-08-14 |  |
| 257 | İstanbul Kentsel Açık ve Yeşil Alan Koordinatları | Çevre | GEOJSON | 2025-07-17 |  |
| 258 | Tech İstanbul Eğitim ve Etkinlikleri | Governance | XLSX | 2025-06-26 |  |
| 259 | Tech İstanbul Girişimlerinin PoC Durumları | Governance | XLSX | 2025-06-26 |  |
| 260 | Tech İstanbul'a Kabul Edilen Girişim ve Girişimci Sayısı | Governance | XLSX | 2025-06-26 |  |
| 261 | Tech Istanbul Girişimlerinin Verileri | Governance | XLSX | 2025-06-26 |  |
| 262 | Kişi Başı Çekilen Günlük Su Miktarı | Çevre | XLSX | 2025-06-24 |  |
| 263 | Number of Benefits Per Thousand People from Public Libraries | Yaşam; İnsan | XLSX | 2025-06-24 |  |
| 264 | İkitelli Güneş Enerjisi Santrali Elektrik Üretim Miktarları | Enerji; Çevre | XLSX | 2025-06-24 |  |
| 265 | Psychological Counseling Centers | Yaşam | XLSX | 2025-06-23 |  |
| 266 | Transportation Expenditure Realizations | Ekonomi; Governance; Mobilite | XLSX | 2025-06-13 |  |
| 267 | İstanbul Senin Uygulamasının Aylara Göre İndirilme Sayısı | Bilgi ve İletişim Teknolojileri | CSV | 2025-06-05 |  |
| 268 | 1. Derece Acil Ulaşım Yolları Verisi | Mobilite | GEOJSON | 2025-06-05 | B |
| 269 | İBB Bütçe Raporları | Ekonomi; Governance | PDF | 2025-06-04 |  |
| 270 | İlçe Bazında Okuryazar/Okuryazar Olmayan Kişi Sayısı | İnsan | XLSX | 2025-06-03 |  |
| 271 | Yıllara Göre Kamulaştırma Maliyeti | Ekonomi; Governance | XLSX | 2025-05-23 |  |
| 272 | IMM Tax Revenues | Ekonomi | XLSX | 2025-05-23 |  |
| 273 | İBB Teşebbüs ve Mülkiyet Gelirleri | Ekonomi; Governance | XLSX | 2025-05-23 |  |
| 274 | IMM Personnel Expenditures Budget | Ekonomi; Governance | XLSX | 2025-05-23 |  |
| 275 | Elektrikli Araç Şarj istasyonları Soket Verileri | Enerji; Mobilite | CSV | 2025-05-20 | B |
| 276 | Elektrikli Araç Şarj İstasyonları Verisi | Enerji; Mobilite | GEOJSON | 2025-05-13 | B |
| 277 | Planlanan Duvar, Üstgeçit, Köprü Güçlendirme ve Heyelan Önleme Çalışmaları | Security | XLSX | 2025-04-28 |  |
| 278 | Yapısal Olmayan Yangınlar | Security | XLSX | 2025-04-28 |  |
| 279 | Sosyal Güvenlik Kapsamındaki Kişi Verileri | Ekonomi | CSV | 2025-04-21 |  |
| 280 | Türkiye ve İstanbul Geneli Dış Ticaret Rakamları | Ekonomi | CSV | 2025-04-21 |  |
| 281 | Merkezi Yönetim Bütçe Harcamaları Verisi | Ekonomi | CSV | 2025-04-21 |  |
| 282 | Merkezi Yönetim Bütçe Gelirlerinin Tahsilatı Verisi | Ekonomi | CSV | 2025-04-21 |  |
| 283 | İBB Cari Transferleri | Ekonomi; Governance | XLSX | 2025-04-18 |  |
| 284 | IMM Interest Expenses | Ekonomi; Governance | XLSX | 2025-04-18 |  |
| 285 | İBB Mal ve Hizmet Alım Giderleri | Ekonomi; Governance | XLSX | 2025-04-18 |  |
| 286 | IMM SSI Expenditure Budget | Ekonomi; Governance | XLSX | 2025-04-18 |  |
| 287 | İBB Çalışanları Öğrenim Durumuna Göre Dağılım Veri Seti | Governance; İnsan | XLSX | 2025-04-16 |  |
| 288 | Hourly Traffic Density Data Set | Mobilite | CSV | 2025-04-16 | B |
| 289 | Hourly Public Transport Data Set | Mobilite | CSV | 2025-04-15 | B |
| 290 | Traffic Density Map | Mobilite | HTML | 2025-04-15 | D |
| 291 | Maximum Number of Trips by Highways | Mobilite | XLSX | 2025-04-09 | D |
| 292 | Number of Vehicles and Lines by Highways | Mobilite | XLSX | 2025-04-09 | D |
| 293 | Istanbul Health Institutions and Organizations Data | Yaşam | XLSX | 2025-04-07 |  |
| 294 | İBB Çalışanları Yaş Durumuna Göre Cinsiyet Dağılımı Verisi | Governance; İnsan | XLSX | 2025-04-02 |  |
| 295 | İBB Çalışanları Statü Durumuna Göre Dağılım Veri Seti | Governance; İnsan | XLSX | 2025-03-28 |  |
| 296 | Toplu Taşıma Araçları Denetimleri | Governance; Mobilite | XLSX | 2025-03-26 |  |
| 297 | Meslek Tipine Göre İBB Çözüm Merkezi Başvuru İstatistikleri | Governance | XLSX | 2025-03-25 |  |
| 298 | Istanbul primary, primary and Secondary School student numbers | İnsan | XLSX | 2025-03-25 |  |
| 299 | Yıllara Göre Veteriner Hizmetlerine İlişkin Eğitimler ve Katılan Kişi Sayısı | Yaşam; İnsan | CSV | 2025-03-24 |  |
| 300 | Raylı Sistemler Yenidoğan-Emek Metro Hattı Bilgileri | Mobilite | XLSX | 2025-03-24 | D |
| 301 | IETT Personnel Number Data | Governance; İnsan | XLSX | 2025-03-21 |  |
| 302 | İstanbul Meteoroloji Gözlem İstasyon Konumları Veri Seti | Bilgi ve İletişim Teknolojileri; Çevre | CSV | 2025-03-20 |  |
| 303 | Uygulamaları Tamamlanan Mimari Aydınlatmalar | Enerji | XLSX | 2025-03-18 |  |
| 304 | İlçe Bazlı Ortalama Hane Halkı Büyüklüğü | Yaşam | XLSX | 2025-03-17 |  |
| 305 | İstanbul İlçelere ve Müşteri Tipine Göre Doğalgaz Verisi | Enerji | CSV | 2025-03-17 |  |
| 306 | İstanbul Gaz Tüketimi Verisi | Enerji | XLSX | 2025-03-17 |  |
| 307 | Kışla Mücadele Faaliyetleri Kapsamında Buzlanmayı Önlemek Amacıyla Kullanılan Yöntemler | Security | PDF,XLSX | 2025-03-14 |  |
| 308 | Public Transport GTFS Data | Mobilite | CSV | 2025-03-12 | B |
| 309 | Routes Shown On Varıous Message Boards In Istanbul | Mobilite | CSV,XLSX | 2025-03-12 | D |
| 310 | Günlük Araç Sayımı | Mobilite | CSV | 2025-03-07 | D |
| 311 | İstanbul Deniz Yüzeyinden Temizlenen Atık Verileri | Çevre | CSV | 2025-03-07 |  |
| 312 | Line Length Information of Rail Systems | Mobilite | XLSX | 2025-03-07 | B |
| 313 | Information on Transfer of Rail Systems | Mobilite | XLSX | 2025-03-07 | B |
| 314 | Contracted Piers Transferred in Istanbul | Mobilite | KML | 2025-03-05 | B |
| 315 | İstanbul Şehir Hatları İskeleleri | Mobilite | KML | 2025-03-05 | B |
| 316 | Istanbul, Mechanical Sweeping and Mechanical Washing Activities | Çevre | CSV | 2025-03-05 |  |
| 317 | İstanbul Büyükşehir Belediyesi Encümen Gündemleri | Governance | CSV,XLSX | 2025-03-04 |  |
| 318 | Değişken Mesaj Panolarında Gösterilen Bilgiler | Mobilite | CSV | 2025-03-03 | D |
| 319 | İstanbul İçin İlçelere Göre Mekanik Yıkama Araç Sayıları | Mobilite; Çevre | CSV | 2025-02-28 |  |
| 320 | İstanbul Çöp Gazından Üretilen Enerji Miktarları | Enerji; Çevre | XLSX | 2025-02-24 |  |
| 321 | Number of Belnet branches and members | Yaşam; İnsan | XLSX | 2025-02-24 |  |
| 322 | Number of People Receiving Support from Women's Solidarity Houses | Yaşam | XLSX | 2025-02-24 |  |
| 323 | Metro Ve Tramvay Araçlarının Özellikleri | Enerji | XLSX | 2025-02-17 |  |
| 324 | Data on Groups Not Included in Labor Force | Ekonomi | CSV | 2025-02-07 |  |
| 325 | Employment Data | Ekonomi | CSV | 2025-02-07 |  |
| 326 | Sen Oku Diye Eğitim Destek Paketi Verisi | İnsan | CSV | 2025-02-07 |  |
| 327 | Genç Üniversiteli Eğitim Yardımı Veri Seti | İnsan | CSV | 2025-02-04 |  |
| 328 | Metro Lines Energy Consumption | Enerji | XLSX | 2025-01-27 |  |
| 329 | İstanbul Senin Uygulaması Kullanım Sıklığı | Bilgi ve İletişim Teknolojileri | CSV | 2025-01-04 |  |
| 330 | İstanbul Senin Uygulamasında Kayıtlı Aktif Kullanıcı Sayısı | Bilgi ve İletişim Teknolojileri | CSV | 2025-01-02 |  |
| 331 | Ekonomik Faaliyetlere Göre Dış ticaret Verisi | Ekonomi | CSV | 2024-12-23 |  |
| 332 | İşsizlik Verileri | Ekonomi | CSV | 2024-12-23 |  |
| 333 | İşsizlik Ödeneği Verileri | Ekonomi | CSV | 2024-12-17 |  |
| 334 | Istanbul Retail Banking Data | Ekonomi | CSV | 2024-12-17 |  |
| 335 | Foreign Trade Figures by Chapters | Ekonomi | CSV | 2024-12-17 |  |
| 336 | Central Government Budget Revenue and Expenditure Comparison Data | Ekonomi | CSV | 2024-12-17 |  |
| 337 | Labor Data | Ekonomi | CSV | 2024-12-17 |  |
| 338 | Istanbul Province Tourism Business Certified Accommodation Facilities Statistics | Yaşam | CSV | 2024-11-27 |  |
| 339 | Türkiye Ölçeğinde Havalimanlarındaki Uçuş Sayısı | Yaşam | CSV | 2024-11-27 |  |
| 340 | Number of Monthly Commercial Flights of Airports in Turkey | Yaşam | CSV | 2024-11-27 |  |
| 341 | Number of Foreign Tourists Visiting Istanbul by Nationality | Yaşam | CSV | 2024-11-27 |  |
| 342 | Facility Statistics by Document Types in Turkey and Istanbul | Yaşam | CSV | 2024-11-27 |  |
| 343 | Istanbul Airports Monthly Freight Traffic | Yaşam | CSV | 2024-11-27 |  |
| 344 | Türkiye Ölçeğinde Havalimanlarının Aylık Yolcu Sayısı | Yaşam | CSV | 2024-11-27 |  |
| 345 | Monthly Cargo Carried by Airports in Turkey | Yaşam | CSV | 2024-11-27 |  |
| 346 | Number of Foreign Visitors in Turkey and Istanbul | Yaşam | CSV | 2024-11-27 |  |
| 347 | İETT Yolculuk ve Sefer Bilgileri Web Servisi | Mobilite | API,PDF | 2024-11-15 | A |
| 348 | Neighborhood-Based Building Numbers | Yaşam | CSV | 2024-11-13 |  |
| 349 | IETT Journey Web Service | Mobilite | API,PDF | 2024-11-06 | A |
| 350 | IETT Planned Time Web Service | Mobilite | API,PDF | 2024-11-06 | A |
| 351 | IETT Announcements Web Service | Mobilite | API,PDF | 2024-11-06 | A |
| 352 | IETT Vehicle Features Web Service | Enerji; Mobilite | API,PDF | 2024-11-06 | A2 |
| 353 | Ulaşım Sanat Yapıları Veri Seti | Mobilite | GEOJSON | 2024-10-23 | D |
| 354 | Labour Force General Data | Ekonomi | CSV | 2024-09-30 |  |
| 355 | Broadly Defined Unemployment Data | Ekonomi | CSV | 2024-09-30 |  |
| 356 | General Budget Tax Revenue Accrual and Collection Data | Ekonomi | CSV | 2024-09-30 |  |
| 357 | Number of Taxpayers by Activity | Ekonomi | CSV | 2024-09-30 |  |
| 358 | Türkiye ve İstanbul Ölçeğinde Konut Birim Fiyatları | Yaşam | CSV | 2024-09-30 |  |
| 359 | Türkiye ve İstanbul Ölçeğinde İlk ve İkinci El Konut Satış Adedi | Yaşam | CSV | 2024-09-30 |  |
| 360 | Türkiye ve İstanbul Ölçeğinde Yabancılara Satılan Konut Adedi | Yaşam | CSV | 2024-09-30 |  |
| 361 | Türkiye ve istanbul Ölçeğinde Yapı Ruhsatına ve Yapı Kullanma İzin Belgesine Göre Veriler | Yaşam | CSV | 2024-09-30 |  |
| 362 | Türkiye ve İstanbul Ölçeğinde Yeni ve Yeni Olmayan Konut Fiyat Endeksi | Yaşam | CSV | 2024-09-30 |  |
| 363 | Bölge Bazlı Elektrik Tüketimi | Enerji | CSV | 2024-09-30 |  |
| 364 | Dağıtım Bölgelerine Göre Elektrik Abone Sayısı | Çevre | CSV | 2024-09-30 |  |
| 365 | Dönemler Arası Sektörel Doğalgaz Tüketimi | Çevre | CSV | 2024-09-30 |  |
| 366 | Faturalanan Elektrik Tüketiminin Dağıtım Bölgesi Bazında Dağılımı | Çevre | CSV | 2024-09-30 |  |
| 367 | Faturalanan Elektrik Tüketiminin Tüketim Türü Bazında Dağılımı | Çevre | CSV | 2024-09-30 |  |
| 368 | İstanbul İli Gelen ve Giden Yolcu Sayısı | Yaşam | CSV | 2024-09-17 |  |
| 369 | İstanbul Genel Bütçe Gelirlerinin Tahsilatı | Ekonomi | CSV | 2024-09-17 |  |
| 370 | İstanbul Genel Bütçe Gelirlerinin Tahsilatı, Tahakkuku Ve Tahsilatın Tahakkuk Verileri | Ekonomi | CSV | 2024-09-17 |  |
| 371 | Türüne Göre Doğalgaz Tüketimi | Enerji | CSV | 2024-09-17 |  |
| 372 | Elektrik Tüketici Türünün Türkiye Bazında Dağılımının Karşılaştırılması | Enerji | CSV | 2024-09-17 |  |
| 373 | Elektrik Tüketici Sayısının Dağıtım Bölgesine Göre Dağılımı | Çevre | CSV | 2024-09-17 |  |
| 374 | Türkiye ve İstanbul Ölçeğinde İlk ve İkinci El İpotekli Konut Satış Sayıları | Yaşam | CSV | 2024-09-17 |  |
| 375 | İlçelere Göre Konut Satış Adedi | Yaşam | CSV | 2024-09-17 |  |
| 376 | Türkiye ve İstanbul Ölçeğinde Cinsiyete Göre Konut Satış Adedi | Yaşam | CSV | 2024-09-17 |  |
| 377 | İstanbul Çevresinde Gerçekleşen Depremler | Security | XLSX | 2024-09-03 |  |
| 378 | İSKİ, Number of Received and Answered Channel Malfunctions Based on District and Neighborhood | Governance; Çevre | XLSX | 2024-08-26 |  |
| 379 | İstanbul Barajlarında Yağış ve Günlük Tüketim Verileri | Çevre | XLSX | 2024-08-26 |  |
| 380 | İstanbul Batimetri Verisi | Çevre | CSV | 2024-08-26 |  |
| 381 | İstanbul Güneş Enerji Santralleri Üretim Miktarları | Enerji; Çevre | XLSX | 2024-08-26 |  |
| 382 | Signalized Junction Information Web Service | Mobilite | API,PDF | 2024-08-21 | A2 |
| 383 | Amount of Water Consumption by District | Çevre | XLSX | 2024-06-27 |  |
| 384 | City Map API | Bilgi ve İletişim Teknolojileri; Yaşam | HTML | 2024-05-20 | A2 |
| 385 | Veriye Dayalı Yönetim Modeli (VDYM) Araştırması B Bölümü Verileri | Governance | PDF,XLSX | 2024-05-09 |  |
| 386 | Veriye Dayalı Yönetim Modeli (VDYM) Araştırması C Bölümü Verileri | Governance | PDF,XLSX | 2024-05-09 |  |
| 387 | Veriye Dayalı Yönetim Modeli (VDYM) Araştırması D1 Bölümü Verileri | Governance | PDF,XLSX | 2024-05-09 |  |
| 388 | Veriye Dayalı Yönetim Modeli (VDYM) Araştırması D4 Bölümü Verileri | Governance | PDF,XLSX | 2024-05-09 |  |
| 389 | Veriye Dayalı Yönetim Modeli (VDYM) Araştırması A Bölümü Verileri | Governance | PDF,XLSX | 2024-05-09 |  |
| 390 | Veriye Dayalı Yönetim Modeli (VDYM) Araştırması D2 Bölümü Verileri | Governance | PDF,XLSX | 2024-05-09 |  |
| 391 | Veriye Dayalı Yönetim Modeli (VDYM) Araştırması D3 Bölümü Verileri | Governance | PDF,XLSX | 2024-05-09 |  |
| 392 | VDYM İlçe Bazında Kişilerin Sivil Toplum Kuruluşlarına Üyelik Durumları | Governance; Yaşam | XLSX | 2024-05-09 |  |
| 393 | VDYM İlçe Bazında Kişilerin Sokak Sanatçılarını Daha Fazla Görme İsteği | Governance; Yaşam | XLSX | 2024-05-09 |  |
| 394 | VDYM İlçe Bazında Kişilerin Sokak Sanatçılarını Görmek İstedikleri Yerler | Governance; Yaşam | XLSX | 2024-05-09 |  |
| 395 | VDYM İlçe Bazında Kişilerin Sokak Sanatçılarına Rastlama Sıklıkları | Governance | XLSX | 2024-05-09 |  |
| 396 | VDYM İlçe Bazında Kişilerin Dışlanmış Hissetme Durumları | Governance; Yaşam | XLSX | 2024-05-09 |  |
| 397 | VDYM İlçe Bazında Kişilerin Dışlanmış Hissetme Sebepleri | Governance; Yaşam | XLSX | 2024-05-09 |  |
| 398 | VDYM İlçe Bazında Kadınların Karşılaştığı Sorunlar | Governance; Yaşam | XLSX | 2024-05-09 |  |
| 399 | VDYM İlçe Bazında Eşlerin Ev İşlerini Paylaşma Durumları | Governance; Yaşam | XLSX | 2024-05-09 |  |
| 400 | VDYM İlçe Bazında Eşlerin Birbirleri ile Tanışma Biçimleri | Governance | XLSX | 2024-05-09 |  |
| 401 | VDYM İlçe Bazında Kişilerin, Kadının ve Ailenin Toplumdaki Değerine Bakışı | Governance; Yaşam | XLSX | 2024-05-09 |  |
| 402 | VDYM İlçe Bazında Kişilerin Mekana Göre Aidiyet Hissiyatları | Governance; Yaşam | XLSX | 2024-05-09 |  |
| 403 | VDYM İlçe Bazında Kişilerin Komşu Seçim Tercihleri | Governance | XLSX | 2024-05-09 |  |
| 404 | VDYM İlçe Bazında Kişilerin Sosyal Olgu Öncelikleri | Governance | XLSX | 2024-05-09 |  |
| 405 | VDYM İlçe Bazında Hanelerin Alışveriş Kanalı Tercihleri | Ekonomi; Governance | XLSX | 2024-05-09 |  |
| 406 | İSKİ Personel Sayısı | Governance; İnsan | XLSX | 2024-04-04 |  |
| 407 | Hava Kalitesi İstasyon Bilgileri Web Servisi | Çevre | API,PDF | 2024-03-28 | A |
| 408 | Hava Kalitesi İstasyon Ölçüm Sonuçları Web Servisi | Çevre | API,PDF | 2024-03-28 | A |
| 409 | Rail Systems Mahmutbey-Esenyurt Station Connection Points with Other Lines | Mobilite | XLSX | 2024-03-27 | B |
| 410 | Rail Systems Mahmutbey-Esenyurt Metro Line District Station Information | Mobilite | XLSX | 2024-03-27 | D |
| 411 | Rail Systems Kabataş-Mecidiyeköy-Mahmutbey Metro Line Information | Mobilite | XLSX | 2024-03-27 | D |
| 412 | Rail Systems Eminönü-Alibeyköy Tram Line Information | Mobilite | XLSX | 2024-03-27 | D |
| 413 | Rail Systems Kabataş-Mecidiyeköy-Mahmutbey Metro Line Parking Information | Mobilite | XLSX | 2024-03-27 | D |
| 414 | Rail Systems İkitelli-Ataköy Metro Line Data | Mobilite | XLSX | 2024-03-27 | D |
| 415 | Rail Systems Rumelihisarüstü-Aşiyan Funicular Line District Station Data | Mobilite | XLSX | 2024-03-27 | D |
| 416 | Vehicle Data Obtained by Image Processing | Mobilite | XLSX | 2024-03-27 |  |
| 417 | Raylı Sistemler Kirazlı-Halkalı Metro Hattı Bilgileri | Mobilite | XLSX | 2024-03-26 | D |
| 418 | İlçelere Göre Pandemi Döneminde Yapılan Yardımlar | Yaşam | XLSX | 2024-03-25 |  |
| 419 | İlçe Bazında Aylık Doğalgaz Tüketim Miktarı | Enerji | XLSX | 2024-03-20 |  |
| 420 | T5-Eminönü-Alibeyköy Tram Line Route Plan and Station Information | Mobilite | KMZ | 2024-03-19 | D |
| 421 | Rail Systems Mahmutbey-Esenyurt Metro Line Information | Mobilite | XLSX | 2024-03-19 | D |
| 422 | Aylık Perakende Gaz Satış Fiyatlandırılması | Ekonomi; Enerji | XLSX | 2024-03-18 |  |
| 423 | Rail Systems Karnarca-Icmeler Metro Line Information | Mobilite | XLSX | 2024-03-13 | D |
| 424 | İSKİ Duyuruları | Governance; Çevre | XLSX | 2024-03-13 |  |
| 425 | İdari Dava Sayıları | Governance | XLSX | 2024-03-12 |  |
| 426 | Ceza Dava Sayıları | Governance | XLSX | 2024-03-12 |  |
| 427 | Adli Dava Sayıları | Governance | XLSX | 2024-03-12 |  |
| 428 | İlçelere Göre İSKİ Abone Sayıları | Çevre | XLSX | 2024-03-01 |  |
| 429 | İstanbul Bölgelere Verilen Temiz Su Miktarları | Çevre | XLSX | 2024-03-01 |  |
| 430 | İSKİ İlçe Bazında Susuzluk Şikayet ve Cevap Sayıları | Governance; Çevre | XLSX | 2024-03-01 |  |
| 431 | İstanbul'a Verilen Temiz Su Miktarları | Çevre | XLSX | 2024-03-01 |  |
| 432 | İstanbul Barajları Günlük Doluluk Oranları | Çevre | XLSX | 2024-03-01 |  |
| 433 | Number of Incoming and Answered Water Faults of ISKI Based on District and Neighborhood | Çevre | XLSX | 2024-03-01 |  |
| 434 | Mahallem İstanbul Projesi SES (Sosyo Ekonomik Statü) Skorları | Ekonomi; Yaşam | XLSX | 2024-02-16 |  |
| 435 | Transportation Master Plan Household Surveys | Mobilite | CSV,PDF | 2024-02-14 | C |
| 436 | Sahiplendirilen Hayvanlar | Yaşam | XML | 2024-02-12 |  |
| 437 | Yıllara Göre Atıktan Geri Kazanım Miktarları | Çevre | XLSX | 2024-02-08 |  |
| 438 | 34 Dakika İstanbul Barınma İndeksi | Mobilite; Yaşam | GEOJSON,PDF | 2024-02-01 | D |
| 439 | 34 Dakika İstanbul Çalışma İndeksi | Mobilite; Yaşam | GEOJSON,PDF | 2024-02-01 | D |
| 440 | 34 Dakika İstanbul İhtiyacını Karşılama İndeksi | Mobilite; Yaşam | GEOJSON,PDF | 2024-02-01 | D |
| 441 | 34 Dakika İstanbul Yaşam Kalitesi İndeksi | Mobilite; Yaşam | GEOJSON,PDF | 2024-02-01 | D |
| 442 | 34 Dakika İstanbul Yürünebilirlik İndeksi | Mobilite | GEOJSON,PDF | 2024-02-01 | C |
| 443 | 34 Dakika İstanbul Karşılanabilirlik İndeksi | Mobilite | GEOJSON,PDF | 2024-02-01 | D |
| 444 | 34 Dakika İstanbul Vakit Geçirme İndeksi | Mobilite; Yaşam | GEOJSON,PDF | 2024-02-01 | D |
| 445 | 34 Dakika İstanbul Ulaşım İndeksi | Mobilite | GEOJSON,PDF | 2024-02-01 | C |
| 446 | 34 Minutes Istanbul Health Index | Mobilite; Yaşam | GEOJSON,PDF | 2024-02-01 | D |
| 447 | 34 Dakika İstanbul Öğrenme İndeksi | Mobilite; İnsan | GEOJSON,PDF | 2024-02-01 | D |
| 448 | 34 Dakika İstanbul Kültürel Aktivite İndeksi | Mobilite; Yaşam | GEOJSON,PDF | 2024-02-01 | D |
| 449 | 34 Dakika İstanbul Çeşitlilik İndeksi | Mobilite | GEOJSON,PDF | 2024-02-01 | D |
| 450 | İstanbul Bisiklet Haritası | Mobilite | HTML | 2024-01-29 | D |
| 451 | VDYM İlçe Bazında Hanelerin Elektronik Aletleri Yardım Almadan Kullanabilme İstatistikleri | Governance | XLSX | 2023-12-25 |  |
| 452 | VDYM İlçe Bazında Hanelerdeki Haftalık Spor Yapılan Gün Sayısı | Governance; Yaşam | XLSX | 2023-12-25 |  |
| 453 | VDYM İlçe Bazında Hanelerin Ev İnternetine Dair Memnuniyet Dereceleri | Bilgi ve İletişim Teknolojileri; Governance | XLSX | 2023-12-25 |  |
| 454 | VDYM İlçe Bazında Hanelerin Türkiye Gündemini Takip Ettiği Mecraları Kullanım Sıklığı | Bilgi ve İletişim Teknolojileri; Governance | XLSX | 2023-12-25 |  |
| 455 | VDYM İlçe Bazında Hanelerin Yıllık Tatil Tercihleri | Governance; Yaşam | XLSX | 2023-12-25 |  |
| 456 | VDYM İlçe Bazında Hanelerin Yıllık Ortalama Şehir Dışı Tatil Yapma Durumları | Governance; Yaşam | XLSX | 2023-12-25 |  |
| 457 | VDYM İlçe Bazında Hanelerin Haftasonu Aktivite Tercihleri | Governance; Yaşam | XLSX | 2023-12-25 |  |
| 458 | VDYM İlçe Bazında Hanelerdeki Düzenli Yapılan Spor Aktiviteleri | Governance; Yaşam | XLSX | 2023-12-25 |  |
| 459 | VDYM İlçe Bazında Hanelerdeki Haftalık Düzenli Spor Yapma Durumları | Governance; Yaşam | XLSX | 2023-12-25 |  |
| 460 | VDYM İlçe Bazında Hanelerdeki Duygu Durum İstatistikleri | Governance; Yaşam | XLSX | 2023-12-25 |  |
| 461 | Bicification Project Data | Mobilite | GEOJSON | 2023-11-29 | D |
| 462 | Data of Citizens who Received Education from Ismek | İnsan | CSV | 2023-11-15 |  |
| 463 | VDYM İlçe Bazında Hanelerdeki Teknolojik Araç Sayıları | Bilgi ve İletişim Teknolojileri; Governance | XLSX | 2023-09-07 |  |
| 464 | VDYM İlçe Bazında Hanelerde Evsel Atık Ayrıştırılma Durumları | Governance; Çevre | XLSX | 2023-09-06 |  |
| 465 | VDYM İlçe Bazında Hanelerin Gıda Alışveriş Yeri Tercihi | Ekonomi; Governance; Yaşam | XLSX | 2023-09-06 |  |
| 466 | VDYM İlçe Bazında Hanelerin Kamu Kurumlarından Sosyal Yardım Alma Durumu | Governance; Yaşam | XLSX | 2023-09-06 |  |
| 467 | VDYM İlçe Bazında Hane Yakınındaki Parklanma Tercihleri | Governance; Mobilite | XLSX | 2023-09-06 | D |
| 468 | VDYM İlçe Bazında Hanelerdeki Araç Sahipliği Durumu | Governance; Mobilite | XLSX | 2023-09-06 | D |
| 469 | VDYM İlçe Bazında Hanelerde Atıkların Ayrıştırılmama Sebepleri | Governance; Çevre | XLSX | 2023-09-06 |  |
| 470 | VDYM İlçe Bazında Hanelerdeki Evcil Hayvan Türleri | Governance; Yaşam | XLSX | 2023-09-06 |  |
| 471 | VDYM İlçe Bazında Hanelerdeki Evcil Hayvan Varlığı Durumu | Governance; Yaşam | XLSX | 2023-09-06 |  |
| 472 | VDYM İlçe Bazında Yapıya Ait Hizmet ve Kullanım Alanları Özellikleri | Governance | XLSX | 2023-09-06 |  |
| 473 | VDYM İlçe Bazında Hanelerdeki Riskli Yapı Durumu Bireysel Öngörüleri | Governance; Security | XLSX | 2023-09-06 |  |
| 474 | VDYM İlçe Bazında Hanelerdeki Kentsel Dönüşüm Fikri Bireysel Öngörüleri | Governance; Yaşam | XLSX | 2023-09-06 |  |
| 475 | VDYM İlçe Bazında Alzheimer/Demans Birey Bulunan Hane Sayısı | Governance; Yaşam | XLSX | 2023-09-06 |  |
| 476 | VDYM İlçe Bazında Otizm/Down Sendromlu Birey Bulunan Hane Sayısı | Governance; Yaşam | XLSX | 2023-09-06 |  |
| 477 | VDYM İlçe Bazında Psikolojik Rahatsızlığa Sahip Birey Bulunan Hane Sayısı | Governance; Yaşam | XLSX | 2023-09-06 |  |
| 478 | VDYM İlçe Bazında Engelli Birey Bulunan Hane Sayısı | Governance; İnsan | XLSX | 2023-09-06 |  |
| 479 | VDYM İlçe Bazında Kronik Hastalığı Olan Hane Sayısı | Governance; Yaşam | XLSX | 2023-09-06 |  |
| 480 | VDYM İlçe Bazında Hanelerdeki Isınma Yöntemleri | Enerji; Governance | XLSX | 2023-09-06 |  |
| 481 | VDYM İlçe Bazında Hanelerdeki Oda Sayılarının Dağılımları | Governance | XLSX | 2023-09-06 |  |
| 482 | VDYM İlçe Bazlı Ortalama Hane Büyüklükleri (m2) | Governance | XLSX | 2023-09-06 |  |
| 483 | VDYM İlçe Bazında Hanelerdeki Mülkiyet Durumu | Governance | XLSX | 2023-09-06 |  |
| 484 | VDYM İlçe Bazında Yaşanılan Konutta Ortalama İkamet Süresi (Ortalama Yıl) | Governance | XLSX | 2023-09-06 |  |
| 485 | İstanbul Barajları Konum Bilgileri | Çevre | KML | 2023-07-11 |  |
| 486 | The Number of Users of IMM Wifi Locations by Year | Bilgi ve İletişim Teknolojileri | CSV | 2023-07-11 |  |
| 487 | STATION INFORMATION OF RAIL SYSTEMS | Mobilite | XLSX | 2023-06-12 | B |
| 488 | Rail Systems Line Based Trip Numbers | Mobilite | XLSX | 2023-06-12 | D |
| 489 | Number and Rate of Passengers Based on Transport Mode Types | Mobilite | XLSX | 2023-06-12 | D |
| 490 | İstanbul Barajları Maksimum Biriktirme Hacimleri | Çevre | XLSX | 2023-06-08 |  |
| 491 | Daily Maximum Journeys of the Rail System | Mobilite | XLSX | 2023-06-08 | D |
| 492 | Rail System Construction Progress Rates | Mobilite | XLSX | 2023-06-07 |  |
| 493 | 2020 Yılı Lokasyon Bazında Çevresel İzleme ve Kontrol Verileri | Çevre | XLSX | 2023-06-05 |  |
| 494 | İstanbul Solar Enerji Potansiyeli Haritası | Bilgi ve İletişim Teknolojileri; Enerji | OGC WMS,PDF | 2023-05-22 |  |
| 495 | Istanbul Logistics External Section-Transit Transitions Report | Mobilite | PDF,TXT | 2023-05-18 |  |
| 496 | Istanbul Transportation Master Plan Household Survey | Mobilite | PDF,TXT | 2023-05-18 | C |
| 497 | Istanbul Focal Points Field Research Synthesis Report | Mobilite | PDF,TXT | 2023-05-18 |  |
| 498 | Istanbul Logistic Curtain Points Traffic Count Statistics Synthesis Report | Mobilite | PDF,TXT | 2023-05-18 |  |
| 499 | Istanbul Logistics Company Meetings Field Research Synthesis Report | Mobilite | PDF,TXT | 2023-05-18 |  |
| 500 | Mixed Municipal Waste Collected at Transfer Stations by Month Web Service | Çevre | API,PDF | 2023-05-12 |  |
| 501 | Road Maintenance Works Web Service | Mobilite; Security | API,PDF | 2023-05-05 | A |
| 502 | Ispark Parking List Web Service | Mobilite | API,PDF | 2023-05-05 | A |
| 503 | İstanbul Yıllara Göre Nüfus Projeksiyonu Verisi | İnsan | CSV | 2023-04-26 |  |
| 504 | Metro Istanbul Rail System Group List | Mobilite | API | 2023-04-25 | A2 |
| 505 | Direction Information List by Metro Istanbul Line Id and Station Id | Mobilite | API | 2023-04-25 | A2 |
| 506 | Direction Information List by Metro Istanbul by Line ID | Mobilite | API | 2023-04-25 | A2 |
| 507 | Metro Istanbul Line Direction Information List | Mobilite | API | 2023-04-25 | A2 |
| 508 | Metro Istanbul Line Information List | Mobilite | API | 2023-04-25 | A |
| 509 | Metro Istanbul Frequently Asked Questions List | Governance; Mobilite | API | 2023-04-25 | C |
| 510 | Metro Istanbul Event List | Mobilite; Yaşam | API | 2023-04-25 | A2 |
| 511 | Metro Istanbul News List According to Different Language Options | Mobilite; Yaşam | API | 2023-04-25 | A2 |
| 512 | Metro Istanbul Announcement List According to Different Language Options | Mobilite; Yaşam | API | 2023-04-25 | A |
| 513 | List of Distance Times of Metro Istanbul Stations to the Start Station | Mobilite | API | 2023-04-25 | A |
| 514 | Metro Istanbul Station Information List by Line Id | Mobilite | API | 2023-04-25 | A |
| 515 | Metro Istanbul Detail Information List of Defective Equipment | Mobilite | API | 2023-04-25 | A |
| 516 | Metro Istanbul Fault Types List | Mobilite | API | 2023-04-25 | A2 |
| 517 | Metro Istanbul Technical Object Types List | Mobilite | API | 2023-04-25 | A2 |
| 518 | Metro Istanbul Faulty Equipment and Technical Unit List | Mobilite | API | 2023-04-25 | A |
| 519 | Metro Istanbul Fault Types List | Mobilite | API | 2023-04-25 | A2 |
| 520 | Service Status List of Metro Istanbul Lines | Mobilite | API | 2023-04-25 | A |
| 521 | İtfai İstatistikler | Security | CSV | 2023-04-25 |  |
| 522 | Istanbul Building Density Map | Bilgi ve İletişim Teknolojileri; Yaşam | OGC WMS,PDF | 2023-04-17 |  |
| 523 | Proje Amacına Göre Kamulaştırılan Alan Miktarı | Governance | XLSX | 2023-04-11 |  |
| 524 | Yıllara Göre Kamulaştırılan Alan Miktarı | Governance | XLSX | 2023-04-10 |  |
| 525 | Etiket ve Tarife Denetim Faaliyetleri | Governance; Yaşam | CSV | 2023-04-05 |  |
| 526 | Gaz Birim Fiyatı ve Miktarı | Ekonomi; Enerji | XLSX | 2023-03-27 |  |
| 527 | Metro Istanbul Ticket Price List Web Service | Ekonomi; Mobilite | API | 2023-03-27 | A |
| 528 | RAIL SYSTEMS KIRAZLI-HALKALI METRO LINE STATION INFORMATION | Mobilite | XLSX | 2023-03-17 | D |
| 529 | NUMBER OF ELEVATORS AND ESCALATORS AT THE KIRAZLI-HALKALI STATION | Mobilite | XLSX | 2023-03-17 | B |
| 530 | RAIL SYSTEMS KIRAZLI-HALKALI STATION CONNECTIONS WITH OTHER LINES | Mobilite | XLSX | 2023-03-17 | B |
| 531 | IMM WiFi Location Data | Bilgi ve İletişim Teknolojileri | CSV | 2023-03-16 |  |
| 532 | Raylı Sistemler İkitelli-Ataköy Hattı Diğer Hatlarla Bağlantı Noktaları | Mobilite | XLSX | 2023-03-15 | B |
| 533 | Rehabilite Edilip Alındığı Ortama Bırakılan Hayvanlar | Yaşam | XML | 2023-03-06 |  |
| 534 | Müşahede Altına Alınan Hayvanlar | Yaşam | XML | 2023-03-06 |  |
| 535 | Deprem Senaryosu Analiz Sonuçları | Security; Yaşam | CSV | 2023-03-02 |  |
| 536 | ISPARK Parking Information | Mobilite | CSV | 2023-02-27 | B |
| 537 | Istanbul Sports Inventory | Yaşam | HTML | 2022-12-07 |  |
| 538 | IMM Lawsuit Information | Governance | XLSX | 2022-06-21 |  |
| 539 | İzcilik Faaliyetinden Faydalanan Kişi Sayısı | Yaşam | XLSX | 2022-06-20 |  |
| 540 | Spor Organizasyonları ve Sporcu Sayıları | Yaşam | XLSX | 2022-06-20 |  |
| 541 | Public Transportation Ticket Prices | Ekonomi; Mobilite | XLSX | 2022-06-10 | C |
| 542 | Günlük ibbWiFi Yeni Kullanıcı Verisi | Bilgi ve İletişim Teknolojileri | CSV | 2022-04-19 |  |
| 543 | Seismic Portal | Bilgi ve İletişim Teknolojileri; Security | HTML | 2022-04-05 |  |
| 544 | Number of People Attending Scout Camps | Yaşam; İnsan | CSV | 2022-04-04 |  |
| 545 | Daily Number of Distinc Users in IMM WiFi Points | Bilgi ve İletişim Teknolojileri | CSV | 2022-04-04 |  |
| 546 | Hal Products and Prices Web Service | Ekonomi; Yaşam | API | 2022-04-04 |  |
| 547 | Veterinary Rehabilitation Statistics | Yaşam | HTML,PDF | 2022-04-04 |  |
| 548 | Traffic Index Web Service | Mobilite | API | 2022-04-04 | A |
| 549 | Metro Istanbul Network Map List Web Service | Mobilite | API | 2022-04-04 | A2 |
| 550 | Metro Istanbul Address List Web Service According to Different Language Options | Mobilite | API | 2022-04-04 | A2 |
| 551 | Metro Istanbul Ongoing Project List Web Service | Mobilite | API | 2022-04-04 | A2 |
| 552 | Metro Istanbul Timetable Web Service | Mobilite | API | 2022-04-04 | A |
| 553 | Number of Plays Played in IMM City Theaters | Yaşam | XLSX | 2022-04-04 |  |
| 554 | Maximum Number of Trips Based on Marine Businesses | Mobilite | XLSX | 2022-04-04 | D |
| 555 | Plays performed at IMM City Theaters | Yaşam | XLSX | 2022-04-04 |  |
| 556 | İstanbul Ulaşım Modeli Zirve Saat Türel Dağılım Oranları | Mobilite | CSV | 2022-04-04 | D |
| 557 | Istanbul, Annual Excavation Waste Tonnages and Number of Vehicles | Mobilite; Çevre | CSV | 2022-04-04 |  |
| 558 | Istanbul General Dam Occupancy Rates | Çevre | CSV | 2022-03-18 |  |
| 559 | Monthly Excavation Waste Tonnages Data | Çevre | CSV | 2022-02-11 |  |
| 560 | Animals Operated | Yaşam | XML | 2021-12-13 |  |
| 561 | Animals Recorded with Microchips | Yaşam | XML | 2021-12-13 |  |
| 562 | Examined Animals | Yaşam | XML | 2021-12-13 |  |
| 563 | Treated Animals | Yaşam | XML | 2021-12-13 |  |
| 564 | Neutered Animals | Yaşam | XML | 2021-12-13 |  |
| 565 | Vaccinated Animals | Yaşam | XML | 2021-12-13 |  |
