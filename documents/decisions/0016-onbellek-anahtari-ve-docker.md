# 0016 — Önbellek anahtarı araç yüzeyini kapsıyor; Docker yolu doğrulandı

**Tarih:** 12 Kasım 2026
**Durum:** 1. bölüm uygulandı, 2. bölüm ölçülüyor
**İlgili:** [0014](0014-degerlendirmeyi-hizlandirma.md) (boşluğu açan karar), [0015](0015-olu-mod-degerleri.md) (anahtara uyarı metnini ekleyen karar), [0011](0011-degerlendirme-ve-paketleme.md) (paketleme)

## Bağlam

İki iş de **modele giden girdiye dokunmuyor**: prompt, uyarı metni ve araç
açıklamaları olduğu gibi duruyor. Son ölçüm (50/54, karar 0015) projenin
bugüne kadarki en iyi sonucu; onu bozma riski olan hiçbir değişiklik bu
turda yapılmadı. Ele alınan şey ölçümün **kendi güvenilirliği** ve
doğrulanmamış bir kurulum iddiası.

## 1. Önbellek anahtarı: araç yüzeyi artık anahtarda

### Boşluk

Karar 0014 anahtarı `sağlayıcı-model-promptözeti-senaryo` olarak kurmuş ve
"araç şemaları anahtarın dışında" diye açık bırakmıştı. Karar 0015 uyarı
metnini ekledi, ama aynı boşluğu yine açık bıraktı. Sonuç: CLAUDE.md
*"araç açıklamasını değiştirmek davranış değişikliğidir, değerlendirmeyi
yeniden çalıştır"* diyor, ama betik bunu kendisi uygulayamıyordu — açıklama
değişince önbellek geçerli görünüyor ve ölçüm **sessizce eski cevapları
yeni ölçüm diye raporluyordu**. Kuralın uygulanması insanın `--refresh`
yazmayı hatırlamasına bağlıydı.

Bu soyut bir risk değil: karar 0015'in kendisi üç aracın açıklamasını
değiştirdi (`geocode_place`, `find_nearby_stops`, `get_lines_at_stop` mod
listeleri) ve 54 senaryonun yeniden koşulması gerektiği elle fark edildi.

### Karar

`graph.tool_surface_digest(tools)`: **modelin gördüğü yüzeyin** özeti —
her aracın adı + açıklaması + girdi şeması, modele verildiği **sırayla**.
`istanbul_agent()` artık dördüncü öğe olarak araç listesini veriyor
(`(agent, provider, model, tools)`), çünkü yüzey yalnızca MCP oturumları
açıkken bilinebilir. `cache_key` bu özeti de alıyor.

Üç tasarım ayrıntısı, gerekçesiyle:

- **Sıralama yapılmıyor.** Araç sırası da modele giden girdinin parçası;
  sıralamak gerçek bir yeniden sıralamayı görünmez yapardı.
- **Sunucu altkümesi farklı özet verir.** `servers=("transit",)` ile açılan
  bir yığın 8 araç görür, 11 değil; kendi önbelleğini kullanması doğru.
- **Şema iki biçimde de işleniyor:** MCP araçları `args_schema`'yı sözlük
  olarak veriyor, LangChain aracı Pydantic modeli verebiliyor.

Ölçülen değer: 11 araç, özet `1d626e3dea52`.

### Kapanmayan boşluk — açıkça yazıyorum

Özet araç **gövdesini** kapsamıyor. Açıklama ve şema aynı kalıp dönen
değer değişirse (bir SQL sorgusu düzeltilir, bir TTL değişir) anahtar
değişmez. Bunu kapatmanın yolu sunucu kaynaklarını hashlemek olurdu, ama o
zaman biçimlendirme değişikliği bile 57 dakikalık koşuyu geçersiz kılardı.
Bu yüzden kural kalıyor: **araç gövdesi değişince ölçüm `--refresh` ile
koşulur.** Fark şu ki artık yüzey değişikliği için insana güvenmek
gerekmiyor; kalan tek elle iş gövde değişikliği.

### Hata sınıfını kapatan testler

0015'in yöntemi: tek tek değerler değil **ilişki** sınanıyor.

- aynı yüzey → aynı özet; açıklama / şema / sıra / araç sayısı değişince → farklı özet
- Pydantic şeması da özete giriyor (iki farklı model iki farklı özet)
- `cache_key`: yüzey değişince anahtar değişiyor; yüzey verilmezse 0015'teki anahtar
- senaryo, sağlayıcı ve model hâlâ anahtarı ayırıyor (dört ayrı anahtar)

### Mevcut önbellek taşındı

Anahtar biçimi değiştiği için 60 geçerli dosya (54 ollama = 50/54 koşusu,
6 gemini) geçersiz olacaktı. Taşımanın meşru olduğunu **git ile**
doğruladım: son ölçümden (`aec79b4`) beri `src/istanbul_agent/servers/`,
`agent/prompt.py` ve `agent/graph.py` hiç değişmedi, yani o cevaplar
bugünkü araç yüzeyiyle üretildi. Dosyalar yeni ada kopyalandı; eski adlar
geri dönüş için bırakıldı.

Taşımadan sonra dört senaryoluk kontrol: dördü de önbellekten geldi,
kayıttaki oran **50/54** olarak sabit kaldı. Yani değişiklik ölçümü
korudu, değiştirmedi — amaç tam olarak bu.
