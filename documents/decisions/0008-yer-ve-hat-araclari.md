# 0008 — Dört yeni araç: yer, çevre, duraktan hatlar, hattın güzergâhı

**Tarih:** 10 Kasım 2026
**Durum:** uygulandı
**İlgili:** [0003](0003-istasyon-eslestirme.md) (istasyon eşleştirme), [0006](0006-mcp-ve-agent.md) (ad arama, araç açıklamaları), [0007](0007-iett-beslemesi.md) (İETT beslemesi)

## Bağlam

Faz 1'in raylı yarısı bitmişti; otobüs ve konum tarafı yoktu. Karar 0007 ile
İETT beslemesi ve dört nokta katmanı depoya girdi. Bu not o verinin üstüne
kurulan dört aracın tasarımını kaydediyor: `geocode_place`,
`find_nearby_stops`, `get_lines_at_stop`, `get_line_route`. Araç sayısı
2 → 6 oldu (hedef 13, karar: `documents/PLAN.md`).

## Karar 1 — Yer araması küme döndürür, nokta döndürmez

Raylı sistemde bir ad genellikle bir istasyon demek. Durak tablosunda değil:

- "KADIKÖY" adını taşıyan **13** otobüs durağı kaydı birbirine 100 metre
  içinde (her yön ve her peron ayrı kayıt),
- aynı adı taşıyan başka bir durak **80 km** ötede (Beykoz'da),
- "D-100 Karayolu" adlı 100'den fazla minibüs durağı şehre dağılmış.

Bu yüzden arama, aynı ad + aynı mod altındaki noktaları **300 metre**
yarıçapında kümeliyor ve her kümeyi tek yer olarak döndürüyor; `point_count`
kaç kaydın birleştiğini söylüyor. Uzaktaki aynı adlı durak ayrı küme olarak
listeleniyor.

Alternatif — aynı adlı noktaların ortalamasını almak — reddedildi: şehrin
iki yakasındaki iki "D-100 Karayolu"nun ortası Boğaz'ın içine düşüyor.

## Karar 2 — Ad eşleştirme ortak, ama "teklik" varsayımı değil

Dört adım karar 0006/3'ten aynen devralındı (tam ad, yazım farkı, kelime
sırası, ön ek; sonra benzerlik). Üç şey ölçüm sonrası değişti.

### 2a. Ön ek birkaç ada uyuyorsa hepsi döner

İlk sürüm ön eki yalnızca **tek** adaya götürdüğünde kabul ediyordu. Sonuç:
`geocode_place("Kadıköy", mode="vapur")` hiçbir şey bulmadı, çünkü iskeleler
işletmeci ekiyle kayıtlı — "Kadıköy İDO", "Kadıköy ŞH. (Yeni)", "Kadıköy ŞH.
(Eski)", "Kadıköy Balon Turyol". Dördü de gerçekten Kadıköy iskelesi.

Agent denemesinde bunun bedeli görüldü: model "Kadıköy iskelesine en yakın
otobüs durakları" sorusunda tek araç çağırdı, "bulunamadı" aldı ve orada
durdu. Artık ön ek **5'e kadar** ayrı ada uyuyorsa hepsi sonuç olarak
dönüyor (`how: "ön ek (4 aday)"`). Daha fazlasında seçim kullanıcının:
mod verilmeden "kadikoy" onlarca durak adına uyuyor.

### 2b. Benzerlik eşitliği keyfî bozulmuyordu — şimdi bozulmuyor

Benzerlik adımı en yüksek puanlı tek adı seçiyordu. "Ortaköy" ile "ORTAKÖY
A" … "ORTAKÖY F" adlarının **altısı da 0.88** alıyor; biri seçildiğinde araç,
kullanıcının sormadığı durağı seçmiş oluyordu — sessizce. Artık eşitlik
bandı (±0.01) içindeki adlar eşit sayılıyor: 5'e kadar hepsi dönüyor,
üstünde araç seçim yapmıyor ve adayları veriyor.

### 2c. İşletmeci ekli adlar `related` olarak da dönüyor

Ortak `normalize()` "iskele", "metro", "durağı" gibi tesis eklerini atıyor
(station_names rule 5), bu yüzden "Kadıköy iskelesi" → "kadikoy" → "Kadıköy"
metro istasyonuna eşleşiyor. Doğru davranış, ama kullanıcı iskeleyi
kastetmiş olabilir. Çözüm: sorguyla başlayan tam adlar ayrıca `related`
alanında dönüyor.

Alternatif — "ŞH.", "İDO", "Turyol", "Dentur" eklerini `normalize()`'a
eklemek — reddedildi: o fonksiyon raylı sistem eşleştirmesinin de temeli ve
değiştirmek karar 0003'ün tüm doğrulamasını yeniden koşmak demek.

## Karar 3 — Mod adı eşanlamlı kabul eder, tanımadığını reddeder

Agent `mode="iskele"` yazdı. Tabloda değer `vapur`. İlk sürüm bu modu
filtreye koyup **boş sonuç** döndürdü, araç da "yer bulunamadı" dedi — yani
parametre hatasını veri yokluğu gibi gösterdi. Model kullanıcıya "böyle bir
iskele yok" diye cevapladı.

Artık `resolve_mode()` eşanlamlıları çeviriyor (`iskele`/`feribot`/`deniz` →
`vapur`, `otobüs`/`bus`/`iett` → `otobus`, `marmaray`/`banliyö` → `tren`,
`füniküler` → `funikuler`…) ve tanımadığı bir modda **geçerli değerleri
sayan** bir hata döndürüyor (`error: "gecersiz_mod"`).

Not: mod çözümlemesi ortak `normalize()` ile yapılamıyor, çünkü o fonksiyon
tam bu kelimeleri tesis eki sayıp atıyor — `normalize("metro")` boş dize.

Bu, karar 0006/7a ile aynı hata sınıfı: "cevap verebiliyorum ama yanıltıcı
cevap veriyorum". Üçüncü kez görüldüğü için kural olarak yazmaya değer:
**bir aracın boş sonucu, parametre hatasını gizlememeli.**

## Karar 4 — Hat sorguları hat koduna göre toplanır

İETT beslemesi güzergâh varyantlarını ayrı hat kaydı tutuyor: 9274
`route_id`, 1095 hat kodu. 500T'nin iki yönünde toplam üç varyant var.
Sorgular bu yüzden `line_code` üzerinden toplanıyor ve:

- `get_line_route` her yön için **en eksiksiz** varyantı listeliyor,
  `variant_count` kaç varyant olduğunu söylüyor,
- sefer sayısı yöndeki **bütün** varyantların toplamı.

İkinci kural ölçümle geldi: 500T'nin iki varyantı da 66 durağa uğruyor ama
biri günde 451, öteki 3 sefer yapıyor. Eşitliği sefer sayısına bakmadan
bozmak hattı **günde 3 sefer** çalışıyor gibi gösteriyordu.

`get_lines_at_stop` da aynı sebeple iki aşamalı sorguyor: önce duraktan
geçen (varyant, yön) çiftleri, sonra o çiftlerin sefer sayısı. Tek sorguda
saymak, durağın güzergâhta iki kez geçtiği ring hatlarda seferleri ikiye
katlıyordu.

## Karar 5 — Durak kimliği ada değil, ad + konuma dayanır

`get_lines_at_stop` durağı ada göre bulduktan sonra GTFS kayıtlarını
**300 metre** içinde olmak koşuluyla eşliyor. Yoksa Beykoz'daki "KADIKÖY"
durağından geçen hatlar, Kadıköy'deki durağın cevabına karışıyor. Araç ayrıca
"bu adda N ayrı durak var, kullanılan şu konumdaki" notunu döndürüyor.

## Karar 6 — Hat bilgisi olmayan modlar bunu söyler

Minibüs, dolmuş ve iskeleler için depoda **yalnızca konum** var; hangi hattın
hangi duraktan geçtiği yok. `get_lines_at_stop` bu modlarda boş liste ve
açık bir not döndürüyor ("bu kaynak yalnızca durak konumlarını içeriyor").
Vapur hatları için veri seti 12 kapsama alınabilir; şimdilik kapsamda değil.

## Karar 7 — Nokta tablosunda tarih, koordinatın kaynağından gelir

Raylı yerler için ilk sürüm satırın `data_as_of` değerini (2020-07-09)
yazıp `is_stale: false` diyordu — yani 2020 tarihli bir konumu güncel
gösteriyordu, hem de kendi alanlarıyla çelişerek.

Doğrusu koordinatın kaynağına bakmak: 254 raylı yerin **210**'unun konumu
canlı Metro API'den (2026-11-08, `is_stale: false`), **41**'i 2020
beslemesinden (`is_stale: true`), 3'ü veri seti 6'dan. `detail` alanı konum
kaynağını da taşıyor.

T3'ün koordinatları bu sayede dışlanmadı: karar 0003 **beslemenin** T3
koordinatlarını güvenilmez ilan etmişti, oysa tablodaki T3 konumları canlı
API'den geliyor ve doğru. Hat bazlı `coords_reliable` bayrağı mesafeden süre
tahminini hâlâ reddediyor; o kararın kapsamı değişmedi.

## Ölçüm: Faz 1 kabul soruları

Beş soru, yerel model (`qwen3.5:4b`, `reasoning=False`, `num_ctx=8192`):

| Soru | Araçlar | Sonuç |
|---|---|---|
| Kadıköy iskelesine en yakın otobüs durakları | `geocode_place` → `find_nearby_stops` | ✓ 38 s |
| Kadıköy'deki otobüs durağından hangi hatlar | `geocode_place` → `get_lines_at_stop` | ✓ 100 s |
| 500T nerelerden geçiyor, kaç durak | `get_line_route` | ✓ |
| 34 hafta içi ilk kaçta kalkıyor | `get_line_route` | ✓ |
| Taksim'in konumu + 300 m içindeki duraklar | `geocode_place` → `find_nearby_stops` ×2 | ✓ |

Beşte beşinde doğru araç seçildi ("bitti" tanımı: en az dördü). İlk turda
S6 tek araçla kalıp "bulunamadı" demişti; karar 2a ve 3 bunu düzelttikten
sonra zincir kuruldu.

Gözlem (düzeltilmedi): yerel model bazı cevaplarda araç çıktısını özet
yerine **döküm** olarak sunuyor (72 hattı tek tek listeleyip JSON örneği
ekliyor). Bu bir prompt/model davranışı, araç kusuru değil; Faz 5'teki model
karşılaştırmasında ölçülecek.

## Yeniden gözden geçirme koşulları

- Araç sayısı 13'e yaklaşırken tek ReAct grafiği yetmeyebilir (karar 0006'nın
  koşulu); araç seçimi yeniden ölçülmeli.
- Nokta tablosu büyürse `load_places()` tabloyu tamamen okumayı bırakıp
  sınır kutusu ön filtresine geçmeli (23 690 satırda 0.15 s).
- Vapur hatları (veri seti 12) kapsama alınırsa karar 6 düşer.
- Kümeleme yarıçapı (300 m) ve ön ek yayılım sınırı (5) ölçümle değil
  tasarımla seçildi; kullanıcı sorularında yanlış küme görülürse ölçülüp
  güncellenmeli.
