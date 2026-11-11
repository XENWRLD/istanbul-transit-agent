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

## Agent üzerindeki etkisi: ölçüldü, ve hipotezim yanlış çıktı

Bu düzeltme üç aracın açıklamasını da değiştirdi, yani 54 senaryo yeniden
koşuldu. Aynı turda eval kayıtlarına **araç argümanları** da eklendi; ilk
faydası bu notun kendi hipotezini çürütmek oldu.

`rail-04`'ün ölü mod değeri yüzünden düştüğünü varsaymıştım. Kayıt bunu
söylemiyor:

```
geocode_place(query="Üsküdar",      mode="metro")
geocode_place(query="Sultanbeyler", mode="metro")
```

Model `rayli` ya da `tren` değil **`metro`** verdi — o değer düzeltmeden
önce de çalışıyordu. Senaryonun düşme sebebi istasyon adını yanlış
yazması: "Sultanbeyli" yerine **"Sultanbeyler"**. Araç haklı olarak
bulamadı, model de vazgeçip kullanıcıya doğru adı sormayı önerdi.

Yani bu notun düzelttiği hata gerçek ve veri katmanında kanıtlı (yukarıdaki
önce/sonra tablosu), ama **ölçülen senaryo başarısızlıklarının hiçbirine
sebep olduğu gösterilemedi.** İki ayrı şey: bir kusurun var olması ile o
kusurun ölçümde görünmesi. Düzeltme, aracın belgelediği bir parametrenin
hiç çalışmaması olduğu için kendi başına haklı.

Bir kenar bulgu: aynı koşuda `rail-04` iki kez farklı davrandı — tek
senaryoluk kontrolde `get_rail_travel_time`'a gidip geçti (82.7 s), tam
koşuda aynı girdiyle iki `geocode_place` çağırıp düştü (31.8 s). Karar
0014 *"davranış tekrarlanıyor, oynayan tek şey süre"* demişti; bu ona
karşı bir kanıt. Sıcaklık 0.0 ama tohum yok ve `llama.cpp` kısmi GPU
boşaltmayla bit düzeyinde yinelenebilir değil. Tek senaryoluk sonuçtan
"düzeldi" diye rapor etmek bu yüzden yanlıştı.

## Aynı turdaki prompt değişiklikleri: net −2, geri alındı

Mod düzeltmesiyle birlikte üç prompt/nudge değişikliği de yapıldı
(commit `58e61ee`):

1. **Nudge'ın kaçış kapısı kapatıldı** — "araçlardan hiçbiri cevaplayamıyorsa
   yok de" cümlesi, model uyarıyı aldıktan sonra da araç çağırmamasına izin
   veriyordu. Yerine: yokluk da bir araç çıktısına dayanmak zorunda.
2. Aynı kural prompt'a da bir bölüm olarak eklendi.
3. **"Araç çıktısı kullanıcının verdiği veri değildir"** kuralı eklendi —
   model `get_line_route` çıktısını alıp veri setini tanıtmaya başlıyordu.

Üçü **aynı anda** değiştirildi ve tek ölçüm alındı. Bu bir yöntem hatası:
hangi değişikliğin neyi kazandırdığı ayırt edilemiyor. Karar 0014 tam bu
dersi veriyordu.

| Ölçüt | Önce (0014) | Sonra |
|---|---|---|
| geçen senaryo | 47/54 (%87.0) | **45/54 (%83.3)** |
| araç doğruluğu | %88.9 | %87.0 |
| ifade doğruluğu | %92.3 | %88.5 |
| **kaynaksız cevap** | 3 | **1** |
| uyarılan tur | 8 | 10 |
| gecikme ortancası | 36.0 s | 33.7 s |

Düzelen: `route-04`. Yeni düşen: `lines-03`, `know-06`, `refuse-05`.

Ölçüm iki yönlü ve bu yüzden tek sayıyla özetlenemez:

- **Kazanç gerçek.** Kaynaksız cevap 3'ten 1'e indi. `route-04` ("YOK99
  hattının güzergâhı") artık aracı çağırıp adaylara bakarak cevaplıyor.
  `rail-06` ("Paris'ten Taksim'e") de davranış olarak düzeldi — *"Araç
  'Paris' adında bir metro istasyonu olmadığını gösterdi ve İstanbul'daki
  adayları (Çarşı, Parseller, Darıca) döndürdü"* — ama senaryo
  `get_rail_travel_time`'ı ve "bulunamadı/bulamadım/yok" kelimelerini şart
  koştuğu için yine düşük sayılıyor. Model "olmadığını" demiş.
- **Kayıp da gerçek ve daha ağır.** `lines-03` eskiden doğru cevap
  veriyordu ("iskeleler için hat bilgisi yok"); şimdi **uyduruyor**: *"F1
  fünikler hattı Kadıköy'den Taksim-Kabataş'a gidiyor… Kadıköy ŞH.'den
  kalkan vapurlar genellikle Marmaray hattını kullanır."* Projenin önlemeye
  çalıştığı kusurun tam kendisi. `refuse-05` düşünce zincirini cevap olarak
  yazıyor ve 685 saniye sürüyor.

**Karar: prompt ve nudge değişiklikleri geri alındı.** Ölçülmüş bir
gerileme tutulmaz. Mod düzeltmesi ve argüman kaydı kalıyor — ikisi de bu
ölçümden bağımsız olarak doğru. Nudge değişikliği tek başına, kendi
ölçümüyle yeniden denenecek.

## Önbellek anahtarı nudge metnini de kapsamalı

Nudge'ı tek başına ölçmek, anahtarın onu da kapsamasını gerektiriyor:
anahtar yalnızca `SYSTEM_PROMPT`'un özetini alıyordu, yani `graph.py`
içindeki uyarı metnini değiştirip prompt'a dokunmamak önbelleği geçersiz
kılmıyordu ve ölçüm sessizce eski cevapları raporlardı. Anahtar artık
prompt + nudge üzerinden hesaplanıyor. Araç şemaları hâlâ anahtarın dışında
(karar 0014'te açılan boşluk, kapanmadı).
