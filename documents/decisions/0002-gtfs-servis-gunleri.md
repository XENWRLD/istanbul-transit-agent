# 0002 — GTFS servis günleri: tarih penceresi yok sayılır

- **Tarih:** 7 Kasım 2026
- **Durum:** Kabul edildi
- **Aşama:** Faz 1
- **Kanıt:** `scripts/analyze_metro_schedule.py`, `scripts/analyze_metro_headways.py`,
  `data/metro_schedule_quality.json`, `datasets/metro_schedule_source.csv`
- **İlgili:** [0001](0001-veri-kaynaklari.md) (K1: metro sefer saatleri GTFS'e taşındı)

## Bağlam

Karar 0001'de Metro İstanbul'un `GetTimeTable` uç noktası sunucu taraflı bozuk
olduğu için (18 hat × tüm yönler = 43/43 deneme `IndexOutOfRange`)
`get_next_departures`'ın metro tarafı "Public Transport GTFS" beslemesine
(veri seti 308) taşındı. Bu besleme kullanılabilir mi, hangi tabloyla, sorusunun
cevabı ölçüldü.

## Bulgu 1 — `calendar.csv` tamamen süresi geçmiş

```
start_date : 20180801 .. 20221231
end_date   : 20190303 .. 20241231
bugün      : 20261107
```

20 metro hattının **hiçbirinde** bugün aktif servis yok. Besleme 2020 tarihli ve
portal notuna göre güncellenmeyecek; `end_date` alanları iki yıl önce dolmuş.

GTFS belirtimine harfiyen uyan bir okuyucu (`start_date <= bugün <= end_date`)
her hat için "sefer yok" döner. Yani besleme, kuralına göre okunursa tamamen
kullanılamaz.

Gün desenlerinin kendisi ise sağlam ve anlamlı:

| Desen | Günler | Servis sayısı |
|---|---|---|
| `1111100` | hafta içi | 5 |
| `1111110` | hafta içi + cumartesi | 7 |
| `1111111` | her gün | 10 |
| `0000010` | cumartesi | 5 |
| `0000001` | pazar | 8 |
| `0000011` | hafta sonu | 5 |
| **`0000000`** | **hiç** | **5** |

## Bulgu 2 — Günü hiç işaretli olmayan servisler

5 servis kaydında yedi günün tamamı `0`. Bu servislere bağlı seferler hiçbir gün
çalışmaz; GTFS'te bu kayıtlar `calendar_dates.csv` ile istisna tarihleri
üzerinden çalıştırılır, ama bu beslemede `calendar_dates.csv` **yok**. Dolayısıyla
o seferler erişilemez durumda.

## Seçenekler

1. **Belirtime harfiyen uy.** Tarih penceresini kontrol et. → Araç her zaman
   "sefer yok" döner. Kullanıcıya hiçbir değer üretmez.
2. **Beslemenin tarihlerini bugüne kaydır.** `end_date`'leri ileri al. →
   Veriyi tahrif etmek olur; hangi tarihin uydurma olduğu kaybolur.
3. **Tarih penceresini yok say, yalnızca hafta günü desenini kullan.** Pencere
   okunmaz; servis seçimi `monday..sunday` bayraklarına göre yapılır.
4. **Metro tarifesini tamamen bırak.** → Karar 0001 ile çelişir; elimizdeki tek
   metro tarife kaynağı bu.

## Karar

**Seçenek 3.** Servis günleri yalnızca hafta günü desenine göre eşleştirilir;
`start_date`/`end_date` alanları okunmaz. Yedi günü de `0` olan servisler ve
onlara bağlı seferler filtrelenir.

### Gerekçe

Tarih penceresi bu beslemede **verinin kendisi hakkında bir bilgi değil, yayın
sürecinin bir kalıntısı**. Beslemenin içeriği "hafta içi M2 her 3 dakikada bir
kalkar" bilgisini taşıyor ve bu bilgi 2020'den beri kabaca geçerli; penceresi
ise yalnızca "bu dosya 2024 sonuna kadar yayınlanacaktı" diyor. Pencereye uymak,
elimizdeki geçerli bilgiyi geçersiz bir meta veri yüzünden çöpe atmak olurdu.

Veriyi tahrif etmemek (seçenek 2) kritik: `data_as_of` alanı beslemenin **gerçek**
tarihini (2020-07-09) döndürür ve bu kaynaktan gelen her sonuç `is_stale: true`
taşır. Kullanıcı bilginin ne kadar eski olduğunu görür; biz sadece okunamaz hale
getiren pencereyi atlıyoruz.

### Sonuçları

- Tatiller ve istisna günleri bilinmiyor (`calendar_dates.csv` yok). Araç resmî
  tatillerde hafta içi/hafta sonu tarifesini yanlış seçebilir. Araç çıktısı bunu
  belirtmek zorunda.
- Bir hattın birden fazla aktif servisi olabilir (ör. hem `1111100` hem
  `1111111`); seferler bunların birleşimi olarak toplanır, tekilleştirme sefer
  kimliğine göre yapılır.
- Bu bir "düzeltme" değil, bilinçli bir sapma. Besleme güncellenirse (olası
  değil) veya Metro API'si düzelirse, pencere kontrolü geri açılmalı.

## Ek karar — tarife kaynağı hat bazında seçilir

Aynı ölçümde ikinci bir varsayım da çürüdü. Plan `get_next_departures`'ın
`frequencies.csv`'den okuyacağını söylüyordu; gerçek şu:

```
frequencies kaydı olan metro seferi: 66 / 10894
```

Yoğun hatlarda `frequencies.csv` **yalnızca gece servisini** tarif ediyor
(M2, M4, T1 için `23:50–02:00, her 10 dk`), gündüz tarifesi `stop_times`'ta.
2020'de yeni olan hatlarda ise tam tersi: M7 20 sefer, M8 ve M9 2'şer sefer
taşıyor ve gündüz aralıkları yalnızca `frequencies.csv`'de
(M8: `06:00–24:00, her 4 dk`).

`stop_times`'tan hesaplanan gerçek profil (örnek):

| Hat | Gün | Sefer | İlk | Son | Ortanca aralık |
|---|---|---|---|---|---|
| M2 | hafta içi | 291 | 05:57 | 23:50 | 3 dk |
| M2 | pazar | 173 | 05:58 | 23:50 | 6 dk |
| M4 | hafta içi | 197 | 06:00 | 23:54 | 4 dk |
| T1 | hafta içi | 191 | 06:00 | 23:59 | 5 dk |

**Karar:** iki tablo birbirinin alternatifi değil; `get_next_departures` kaynağı
**hat bazında** seçer. Eşleme `datasets/metro_schedule_source.csv` dosyasında:

| Kaynak | Hat sayısı | Hatlar |
|---|---|---|
| `stop_times` | 12 | F1, M1A, M1B, M2, M2A, M3, M3A, M4, M5, M6, T1, T4 |
| `frequencies` | 7 | F2, F3, M7, M8, M9, TF1, TF2 |
| yok | 1 | T3 (bkz. [0003](0003-istasyon-eslestirme.md)) |

Ölçüt: bir hattın 05:00–22:00 arasında başlayan **50 veya daha fazla** seferi
varsa `stop_times` onun gerçek tarifesidir; yoksa ve `frequencies.csv` öğleden
önce başlayan bir aralık tanımlıyorsa `frequencies` kullanılır.

Dosya `scripts/decide_schedule_source.py` tarafından **deterministik olarak**
üretilir: aynı besleme üzerinde tekrar çalıştırıldığında birebir aynı sonucu
verir. Karar çıktısı olduğu için `datasets/` altında ve commit'li; beslemeyi
yeniden indirip betiği çalıştırmak dosyayı tazeler.
