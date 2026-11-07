# 0003 — İstasyon eşleştirme: ad bir hipotez, koordinat kanıt

- **Tarih:** 7 Kasım 2026
- **Durum:** Kabul edildi
- **Aşama:** Faz 1
- **Kanıt:** `scripts/validate_station_matches.py`, `datasets/station_name_map.csv`,
  `datasets/metro_line_integrity.csv`, `data/station_match_validation.json`
- **İlgili:** [0001](0001-veri-kaynaklari.md), [0002](0002-gtfs-servis-gunleri.md)

## Bağlam

2020 tarihli GTFS beslemesindeki istasyonların bugünkü ağa eşleştirilmesi
gerekiyor: `get_rail_travel_time` "Kadıköy'den Üsküdar'a" sorusunu beslemedeki
`stop_times` satırlarına çevirmek zorunda. İki kaynak aynı istasyonu farklı
yazıyor (API `YENIKAPI`, besleme `Yenikapı`; besleme `Mah.`, API `Mahallesi`).

İlk geçişte eşleştirme yalnızca ada dayanıyordu ve 248 istasyonun 194'ünü
eşleştirmişti. Ad eşleşmesi tek başına yeterli mi?

## Karar 1 — Her eşleşme koordinatla doğrulanır

**Ad bir hipotezdir.** İki istasyon aynı adı taşıyıp farklı yerler olabilir;
yeniden adlandırılan bir istasyon ise yerini korur. Bu yüzden ad eşleştiricinin
ürettiği **her** çift haversine mesafesiyle ölçülür.

- **300 m altı** → aynı istasyon, eşleşme kabul edilir.
- **300 m üstü** → eşleşme reddedilir, istasyon "eksik" sayılır ve
  `get_rail_travel_time` tahmin yoluna düşer.
- **Ad adayı yoksa** → mesafe ne olursa olsun eşleştirme yapılmaz.

### 300 m neden

İstanbul metro istasyonları birbirinden en az birkaç yüz metre uzakta. İki
kaynağın aynı istasyon için verdiği noktalar ise peron merkezi ile giriş noktası
kadar ayrışıyor: doğrulanan 184 eşleşmenin mesafeleri 2-162 m arasında. 300 m
bu gürültüyü içine alıyor, komşu istasyona ise ulaşmıyor.

### Mesafe tek başına yeterli değil

İlk denemede "adı farklı ama 29 m yakın" istasyonları da eşleştirmiştim.
T3'te `Rızapaşa` bu şekilde `Altıyol`'a oturdu — ve yanlıştı (aşağıya bakın).
Bu yüzden **yalnız mesafeye dayalı eşleştirme yapılmaz**: ad adayı ve mesafe
birlikte gerekir. Mesafe, adın söylediğini doğrular veya çürütür; adın yerine
geçmez.

### Sonuç

| Karar | Sayı |
|---|---|
| `AD EŞLEŞMESİ` — ad eşleşti, mesafe doğruladı | 184 |
| `ELLE EŞLEME` — ad adayı + mesafe onayladı | 3 |
| `ŞÜPHELİ — KULLANMA` — ad aynı, mesafe uzak | 1 |
| `HAT GÜVENİLMEZ` — bkz. karar 2 (düzeltildi: artık yalnızca koordinatları işaretli) | 11 |
| `GERÇEKTEN EKSİK` | 49 |
| **Toplam (API istasyon sayısı)** | **248** |

Açık bırakılan dört vakanın çözümü:

| Hat | API istasyonu | GTFS adayı | Ad skoru | Mesafe | Sonuç |
|---|---|---|---|---|---|
| M5 | İmam Hatip Lisesi | İmam Hatip | 0.80 | **78 m** | ✅ aynı istasyon |
| M6 | Boğaziçi Ü.-Hisarüstü | Boğaziçi Üniversitesi | 0.81 | **9 m** | ✅ aynı istasyon |
| M7 | Oruç Reis | Oruç Reis - Yüzyıl | 0.72 | **60 m** | ✅ aynı istasyon |
| T3 | Rızapaşa | — | 0.42 | — | ❌ eksik (hat da güvenilmez) |

Reddedilen tek ad eşleşmesi:

| Hat | İstasyon | Mesafe | Not |
|---|---|---|---|
| M7 | Yeşilpınar ↔ Yeşilpınar | **1444 m** | Aynı ad, farklı yer. Beslemedeki koordinat hatalı; istasyon eksik sayılır. |

Ayrıca eşleştirici kelime sırası farkını da öğrendi: besleme
`Tekstilkent - Giyimkent` yazarken API `Giyimkent-Tekstilkent` diyor. Token
sıralamasıyla (`token_key`) eşleşti ve 23 m mesafeyle doğrulandı.

## Karar 2 — T3: koordinatlar güvenilmez, süreler kullanılır

> **DÜZELTME (8 Kasım 2026).** Bu kararın ilk hali "T3'ün adları karışık, hat
> tamamen çıkarılır" diyordu. Ring yapısı ölçüldüğünde teşhis yanlış çıktı;
> karar daraltıldı. Aşağıdaki metin düzeltilmiş halidir, ilk halin neyi
> yanlış gördüğü de kayıtlı.

Doğrulama hat bazında bir bütünlük kontrolü içeriyor: her GTFS istasyonu için
"konumuna en yakın canlı istasyon hangisi ve aynı adı mı taşıyor?" T3 bu
kontrolde 10/10 başarısız oldu ve ilk yorumum "ad listesi koordinat listesine
ters sırada eşlenmiş" olmuştu.

### Ölçüm bunu çürüttü

`scripts/inspect_t3_ring.py` ve `scripts/inspect_t3_durations.py`:

| Bulgu | Sonuç |
|---|---|
| Ad sıraları | **Tutarlı.** GTFS sırası, canlı API sırasının 1 pozisyon döndürülmüş hali; API'de fazladan yalnızca `Rızapaşa` var (2020 sonrası). |
| Hat şekli | **Ring.** İlk↔son istasyon 122 m, tipik aralık 239 m. |
| Aynı adlı çiftlerin kayması | **Dağınık:** 164-763 m, ortanca 393 m. Tipik aralığın 1.64 katı — sabit bir kayma değil. |
| Segment süreleri | **Tutarlı.** 9 segment, 41-99 s; ring turu 8.6 dk. Hiçbiri makul aralık dışında değil. |

Gerçek açıklama: T3 istasyonları birbirine **239 m** aralıkla sıkışık. GTFS'in
başka hatlarda zararsız olan koordinat belirsizliği (doğrulanan eşleşmelerde
2-162 m) burada istasyon aralığını aşıyor. Yani en-yakın-komşu ile ad
doğrulaması bu hatta çalışmıyor — veri karışık olduğu için değil, hat bu
yöntem için fazla sıkışık olduğu için.

**Adlar ve `stop_times` süreleri doğru; güvenilmez olan koordinatlar.**

### Daraltılmış karar

| T3'te | Durum |
|---|---|
| `stop_times` süreleri | ✅ kullanılır (`method: gtfs`) |
| Veri seti 312 ray uzunlukları | ✅ kullanılır (koordinattan türetilmiyor) |
| Koordinat mesafesinden tahmin | ❌ reddedilir |
| Ring hesabı | ✅ iki yönden kısa olanı seçilir |

Bütünlük eşiği (ad-koordinat uyumu < %60) artık **hattı dışlamıyor**, yalnızca
o hattın koordinatlarını işaretliyor. Veri tabanında kolon adı bunu yansıtacak
şekilde `gtfs_reliable` → `coords_reliable` olarak değişti.

M7'nin skoru 0.81 (3 uyumsuz). İncelendiğinde ikisi yanlış alarm: `Çırçır Mah.`
ile `Yeşilpınar` API'de birbirine 124 m yakın, en-yakın-komşu testi şaşırıyor.
Üçüncüsü gerçek (1444 m), o istasyon tek tek dışlandı.

### Neden bu hatayı yaptım

Bütünlük kontrolü doğru bir sinyal üretti (T3'te bir şey yanlış) ama ben
sinyali fazla okudum: "adlar koordinatlarla uyuşmuyor" gözleminden "adlar
karışık" sonucunu çıkardım. Oysa aynı gözlem "koordinatlar bu hatta ayırt
edici değil" ile de açıklanıyordu ve ikinci açıklama doğruydu. Ayırt etmek
için gereken ölçüm — ad sırasının döngü olarak karşılaştırılması ve
sürelerin kendi içinde tutarlılığı — ilk turda yapılmamıştı.

### Ring hatları için genel çözüm

T3'ün veri kusurundan bağımsız olarak ring hatlar ayrı ele alınmalı:
doğrusal bir istasyon dizisini tek yönde yürümek, turun kısa tarafını
kaçırır. `same_line_leg` artık ring işaretli hatlarda **iki yönü de hesaplayıp
kısa olanı** döndürüyor. Ring tespiti ölçümle yapılıyor: ilk ve son
istasyonun arası, tipik istasyon aralığının 2.5 katından küçükse hat ring
kabul edilir (T3: 122 m / 239 m = 0.51).

## Normalize etme kuralı

`src/istanbul_agent/store/station_names.py`. Sırası önemli:

1. Unicode fold (NFKC + birleşik işaretleri at)
2. **Türkçe casefold** — `I` ve `İ` ikisi de `i`'ye. Sıra kritik: önce
   `str.lower()` çağrılırsa `I` → `ı` olur ve `YENIKAPI` ↔ `Yenikapı` kırılır.
   Bu yüzden harf eşlemesi küçültmeden **önce** yapılır.
3. Noktalama → boşluk
4. Kısaltma açma: `Mah.`→`mahallesi`, `Teks. Mrk.`→`tekstil merkezi`,
   `Ü.`→`universitesi`, `Camii`→`cami`, `Meydanı`→`meydan`
5. Operasyonel ek atma: `metro`, `istasyonu`, `iskelesi`, `tramvay`, `aktarma`,
   `peron`, sondaki peron numarası
6. **`Meydan` korunur.** M1B/T4'te hem "Bağcılar" hem "Bağcılar Meydan" var;
   atmak iki ayrı istasyonu birleştirirdi. `drop_square=True` ile ikinci, gevşek
   geçiş için açılabilir.
7. Boşluk sıkıştırma

Üç arama anahtarı, katıdan gevşeğe:

| Anahtar | Ne çözer | Örnek |
|---|---|---|
| `normalize()` | yazım, kısaltma, ek | `YENIKAPI` ↔ `Yenikapı` |
| `collapsed()` | kelime sınırı anlaşmazlığı | `Fıstıkağacı` ↔ `Fıstık Ağacı` |
| `token_key()` | kelime sırası | `Giyimkent-Tekstilkent` ↔ `Tekstilkent - Giyimkent` |

Gevşek anahtarların ikisi de ayrı tutuldu; `normalize()` gevşetilmedi. Sebebi:
bir anahtarı gevşetmek her aramayı sessizce etkiler, ayrı anahtar ise bilinçli
bir tercih olarak kalır ve her isabeti yine mesafe kontrolünden geçer.

## Dosyalar

| Dosya | İçerik | Commit'li |
|---|---|---|
| `datasets/station_name_map.csv` | 248 istasyonun eşleme kararı, mesafesiyle | ✅ |
| `datasets/metro_line_integrity.csv` | Hat bazında ad-koordinat uyum skoru | ✅ |
| `data/station_match_validation.json` | Tam doğrulama çıktısı | hayır (üretilebilir) |

İlk ikisi karar çıktısı olduğu için commit'li ve
`scripts/validate_station_matches.py` tarafından deterministik üretiliyor.

## Riskler

- 49 istasyon gerçekten eksik (2020 sonrası uzatmalar: M3 Bakırköy/Kayaşehir,
  M4 Sabiha Gökçen, M5 Sultanbeyli, M9 Ataköy, F4 ve T5 hatlarının tamamı).
  Bunlar `get_rail_travel_time`'ın tahmin yoluna düşecek; tahmin yönteminin
  doğruluğu ayrıca ölçülecek.
- 300 m eşiği ve %60 bütünlük eşiği bu besleme için ölçüldü. Yeni bir besleme
  geldiğinde `validate_station_matches.py` yeniden çalıştırılmalı; eşikler
  komut satırından değiştirilebilir (`--radius`).
