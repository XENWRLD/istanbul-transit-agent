# 0015 — Ölü mod değerleri: eşanlamlı sözlüğü tabloyla hiç karşılaştırılmamış

**Tarih:** 11 Kasım 2026
**Durum:** uygulandı
**İlgili:** [0008](0008-yer-ve-hat-araclari.md) (yer ve hat araçları), [0013](0013-canli-veri-dogrulama.md) (araç yazdıktan sonra kaynağa bakmak), [0014](0014-degerlendirmeyi-hizlandirma.md) (düşen senaryolar)

## Bağlam

Karar 0014'ün taze koşusunda yedi senaryo düşüyordu. Birini (`rail-04`,
"Üsküdar'dan Sultanbeyli'ye ne kadar sürer?") incelerken tuhaf bir şey
çıktı: model `get_rail_travel_time` yerine iki kez `geocode_place` çağırmış,
ikincisinden "Sultanbeyli bulunamadı" almış ve kullanıcıya *"Sultanbeyli'nin
hangi istasyon adıyla kayıtlı olduğunu sormanız gerekir"* demiş. Ama
`chain-02` aynı istasyon çiftini aynı koşuda M5 üzerinden 43 dakika olarak
cevaplıyordu. Yani istasyon depoda vardı ve araç onu bulamıyordu.

## Ölçüm — iki mod değeri hiçbir satıra uymuyor

`transit_places` tablosunun gerçekten taşıdığı modlar:

| mod | satır |
|---|---|
| otobus | 15 386 |
| minibus | 7 908 |
| metro | 177 |
| vapur | 98 |
| tramvay | 66 |
| dolmus | 44 |
| funikuler | 7 |
| teleferik | 4 |

`MODE_SYNONYMS` ise iki değere daha açılıyordu: `rayli` (kendine) ve `tren`
(`tren`/`banliyo`/`banliyö`/`marmaray` buna eşleniyordu). Tabloda bu iki
modda **tek satır yok**. Sonuç, bu modlarla yapılan her aramanın boş
dönmesi:

| Çağrı | Önce | Sonra |
|---|---|---|
| `geocode_place("Sultanbeyli", mode="rayli")` | `yer_bulunamadi`, aday yok | Sultanbeyli (metro, M5) |
| `geocode_place("Üsküdar", mode="marmaray")` | `yer_bulunamadi` | Üsküdar (metro) |
| `geocode_place("Sirkeci", mode="tren")` | `yer_bulunamadi` | Sirkeci (metro) |
| `geocode_place("Kabataş", mode="raylı")` | `yer_bulunamadi` | Kabataş (füniküler) |
| `find_nearby_stops("Bostancı", mode="rayli")` | boş liste + *"yarıçapı artırmayı dene"* | Bostancı, Emin Ali Paşa, Kozyatağı |
| `get_lines_at_stop("Sirkeci", mode="marmaray")` | `bulunamadi` | Marmaray, Marmaray1, T1 |

Üç araç da etkileniyordu (`geocode_place`, `find_nearby_stops`,
`get_lines_at_stop`), çünkü üçü de aynı `resolve_mode`'u kullanıyor.

Kusur **araç açıklamasında da yazılıydı**: üç aracın docstring'i geçerli
modlar arasında `tren`'i sayıyordu ve `InvalidMode` hatası da onu geçerli
değer olarak listeliyordu. Yani araç, hiç sonuç döndürmeyen bir parametreyi
kendi belgesinde öneriyordu.

## Niçin yakalanmadı

`tests/test_places.py` bu eşleştirmeyi **test ediyordu**:

```python
("marmaray", "tren"),   # beklenen değer
```

Test geçiyordu çünkü eşanlamlının kendi adına doğru çevrildiğini
doğruluyordu — o adın tabloda karşılığı olup olmadığını sormuyordu. Karar
0013'ün kuralı burada bir kez daha geçerli: *fixture tabanlı test kodun
kendi varsayımını doğrular, kaynağı doğrulamaz.* Mod sözlüğü raylı sistem
tablolarının (`rail_stations`, mod ayrımı yok) döneminden kalmıştı ve
`transit_places` geldiğinde kimse ikisini karşılaştırmadı.

## Karar

1. **`STORED_MODES`** sabiti eklendi: tablonun gerçekten ürettiği sekiz
   değer. Eşanlamlı sözlüğü bunun dışına açılamaz.
2. **`resolve_mode` tek mod değil mod demeti döndürüyor.** Boş demet "kısıt
   yok" demek. Bu, şemsiye modu mümkün kıldı: `rayli`/`raylı` → metro +
   tramvay + füniküler + teleferik. Kullanıcı "raylı sistem" diye sorduğunda
   kastettiği tam bu.
3. **`marmaray`/`tren`/`banliyo` → `metro`.** Marmaray ayrı bir mod değil;
   istasyonları `metro` satırı olarak duruyor ve hat kodu `code` alanında
   ("Sirkeci" → `Marmaray, Marmaray1, T1`). 47 metro satırı `Marmaray`
   kodu taşıyor.
4. **`MODE_VOCABULARY`**: hata mesajının gösterdiği sözcükler. Mesaj ne
   yazıyorsa araç onu kabul etmek zorunda — bunu bir test tutuyor.
5. `MODE_LABELS` ve `MODE_PRIORITY`'den ölü anahtarlar (`tren`, `rayli`)
   çıkarıldı; ikisi de artık tam olarak `STORED_MODES`'u kapsıyor.

## Hata sınıfını kapatan testler

Tek tek değerleri değil **ilişkiyi** tutan dört test eklendi:

- her eşanlamlının açıldığı modlar ⊆ `STORED_MODES`
- `MODE_LABELS` ve `MODE_PRIORITY` anahtarları = `STORED_MODES`
- `MODE_VOCABULARY`'deki her sözcük `resolve_mode`'dan hatasız geçiyor
- `rayli` dört moda açılıyor, `marmaray` metroya

İlk test, bu notun anlattığı hatayı düzeltme yapılmadan koşulduğunda
yakalıyor. Tablo yeni bir mod kazanırsa (ör. vapur hatları ayrılırsa)
ikinci test düşer ve sözlük güncellenmeye zorlanır.

## Agent üzerindeki etkisi: henüz ölçülmedi

Bu düzeltme üç aracın açıklamasını da değiştirdi; CLAUDE.md'nin kuralına
göre bu bir davranış değişikliğidir ve 54 senaryonun yeniden koşulmasını
gerektirir. `rail-04`'ün bu yüzden düştüğü **makul** ama kanıtlanmış değil:
modelin hangi mod değerini verdiği eval kaydında görünmüyor (yalnızca araç
adları kaydediliyor — ayrı bir eksik). Etki, prompt düzeltmeleriyle birlikte
tek bir temiz koşuda ölçülecek.
