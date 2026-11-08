# 0006 — İlk uçtan uca agent: MCP sunucusu + LangGraph ReAct

**Tarih:** 8 Kasım 2026
**Durum:** uygulandı
**İlgili:** [0004](0004-rayli-sure-modeli.md) (süre), [0005](0005-sefer-saatleri.md) (saatler)

## Bağlam

İki araç (`get_rail_travel_time`, `get_next_departures`) çalışıyordu ama
yalnızca Python'dan çağrılabiliyordu. Bu aşamanın işi onları MCP aracı
olarak sunmak ve bir LangGraph agent'ının gerçekten kullanabildiğini
göstermek.

## Karar 1 — Agent araçları MCP üzerinden görüyor, doğrudan değil

Araçları agent'ın içine gömmek daha az parça olurdu. Yine de `transit_server`
ayrı bir süreç olarak stdio üzerinden konuşuyor:

- Dışarıya sunulan araç ile agent'ın gördüğü araç **birebir aynı** oluyor.
  Araç açıklamasını değiştirmek iki yeri değiştirmek demiyor.
- Aynı sunucu Claude Desktop'a veya başka bir MCP istemcisine de
  bağlanabiliyor; projenin amacı buydu.
- Araç çıktıları Pydantic modeli olduğu için şema MCP'ye otomatik geçiyor;
  agent araç parametrelerini açıklamadan değil şemadan öğreniyor.

Maliyeti: her soru için sunucu süreci ayağa kalkıyor (~0.5 s) ve araç
çıktısı JSON'a serileşiyor.

## Karar 2 — Araç açıklaması davranıştır

Her iki aracın açıklamasında üç bölüm var: ne yaptığı, parametrelerin
anlamı, **ne zaman kullanılmaması gerektiği** (CLAUDE.md kuralı). İkisinin
karışması en olası hata olduğu için açıklamalar birbirini doğrudan
reddediyor:

> `get_rail_travel_time` → "İlk/son sefer saati veya 'kaçta kalkıyor / ne
> sıklıkla geliyor' sorularında kullanma — `get_next_departures` kullan. Bu
> araç yolculuk süresini verir, saat vermez."

> `get_next_departures` → "Yolculuk süresi için kullanma —
> `get_rail_travel_time` kullan. Bu araç saat ve sıklık verir, süre
> vermez."

Bu metinler `tests/test_transit_server.py` ile test ediliyor: "NE ZAMAN
KULLANMA" bölümü olmayan veya şemadaki bir parametreyi anlatmayan bir araç
testi kırıyor. Açıklama değişikliği davranış değişikliği olduğu için
değerlendirme yeniden çalıştırılmalı.

## Karar 3 — İstasyon adı: dört adım, sonra üç aday

Kullanıcı "kadıköy", "KADIKOY", "sabiha gökçen" yazıyor. Arama en katıdan
en gevşeğe dört adım (`store/station_search.py`):

1. normalize edilmiş tam ad (Türkçe harf ve büyük/küçük zaten katlanıyor),
2. boşluk/noktalama yok sayılmış hali, sonra kelime sırası yok sayılmış hali,
3. tek adaya götüren ön ek — "sabiha gökçen" → "Sabiha Gökçen Havalimanı",
4. benzerlik ≥ 0.85.

Hiçbiri tutmazsa **en yakın 3 ad** dönüyor ve araç `istasyon_bulunamadi`
diyor. Boş cevap dönmüyor: agent kullanıcıya "böyle bir istasyon yok, şunlar
var" diyebilsin. Hat verildiğinde arama o hatla sınırlı — M4'ün Göztepe'si
ile Marmaray'ın Göztepe'sini ayıran şey bu (karar 0004/4).

## Karar 4 — Duyurular canlı, tarife yerel

`get_next_departures` tarifeyi yerel depodan okuyor ama duyuruları canlı
çekiyor (5 dakikalık süreç içi önbellek). Duyuru çekilemezse araç cevap
vermeye devam ediyor ve uyarıya şunu ekliyor:

> Metro İstanbul duyuruları alınamadı (ConnectError); tarife değişikliği
> olup olmadığı bilinmiyor.

API kapalıyken sessizce "duyuru yok" demek, tarife değişikliğini gizlemek
olurdu.

## Karar 5 — Sistem prompt'unun işi veriyi dürüst aktarmak

Prompt (`agent/prompt.py`) üç şey söylüyor: Türkçe cevap ver, hangi soruda
hangi araç, ve **veri güvenilirliğini cevaba taşı**. Son kısım bu projenin
asıl meselesi olduğu için alan alan yazılmış: `estimated` → "yaklaşık" +
hata payı, `is_stale` → verinin tarihi, `hours_is_stale` ile
`headway_is_stale` **ayrı ayrı**, `runs_all_night` → son sefer yok ve gece
aralığı bilinmiyor, `alerts` → duyuru tarifenin önüne geçer.

## Arayüz

```bash
uv run python -m istanbul_agent.cli "Kadıköy'den Taksim'e metroyla kaç dakika?"
uv run python -m istanbul_agent.cli --provider ollama "Kadıköy'den son M4 kaçta?"
uv run python scripts/run_agent_questions.py          # 5 soru x 2 sağlayıcı
```

CLI varsayılan olarak çağrılan araçları ve parametrelerini de basıyor:
agent'ın doğru aracı seçip seçmediği cevaba bakarak anlaşılmıyor.

## Karar 6 — Yerel modelde düşünme kapalı, bağlam 8192

İlk uçtan uca denemede qwen3.5:4b beş sorudan dördünü doğru cevapladı ama
**iki araç gerektiren soruda** (S5: "ne kadar sürer, şu an hangi sıklıkta
tren var") hiç araç çağırmadı ve **boş cevap** döndü. İki denemede de aynı.

Mesaj dökümü gösterdi ki hata ReAct döngüsünde değil: grafik üç mesajla
bitiyor (sistem, kullanıcı, boş `AIMessage`) — model ilk turda 114 saniye
sonra hiçbir şey üretmemiş, `tool_calls=[]`, içerik 0 karakter, hata yok.

Dört yapılandırma aynı soruyla ölçüldü:

| Yapılandırma | Süre | Araçlar | Cevap |
|---|---|---|---|
| varsayılan (düşünme açık) | **116 s** | **YOK** | **boş** |
| `reasoning=False` | 21 s | ikisi de | ✗ hat karıştı ("Üsküdar M4'te bulunamıyor") |
| `num_ctx=8192` | 93 s | ikisi de | ✓ doğru |
| **`reasoning=False` + `num_ctx=8192`** | **28 s** | **ikisi de** | **✓ doğru** |

İki ayrı sorun olduğu buradan anlaşılıyor:

1. **Düşünme kipi araç çağırmayı bozuyor.** Model düşünme kanalına yazıyor
   ve üretim oradan çıkamadan tükeniyor; dışarıya ne metin ne araç çağrısı
   geliyor. Kapatınca süre 116 s → 21 s.
2. **Varsayılan bağlam penceresi yetmiyor.** Sistem prompt'u (1885 karakter)
   + iki aracın şeması ve açıklamaları (~3100 karakter) + araç çıktıları tek
   turda pencereyi dolduruyor; model hızlı ama yanlış cevap veriyor, hangi
   hattın sorulduğunu kaybediyor. 8192'de düzeliyor.

Karar: `ollama_reasoning = False` ve `ollama_num_ctx = 8192` **varsayılan**
oldu (`config.py`, `.env.example` ile `OLLAMA_REASONING` / `OLLAMA_NUM_CTX`).
Düşünme tek çağrı için geri açılabiliyor (`get_llm("ollama",
reasoning=True)`); `tests/test_llm.py` hem varsayılanları hem bu kapıyı
sabitliyor.

Gemini tarafında bir değişiklik gerekmedi; o modelin aynı soruda iki aracı
çağırması 7.6 saniye sürüyordu.

## Karar 7 — Agent denemesinin açığa çıkardığı iki araç hatası

İki model aynı araçları kullanınca, modelin değil **aracın** iki kusuru
göründü. İkisi de "cevap verebiliyorum ama yanıltıcı veriyorum" türünde.

### 7a. "Bu hatta yok" çıkmaz sokaktı

qwen S5'te `get_next_departures(line="M4", station="Üsküdar")` çağırdı. Araç
"'Üsküdar' M4 hattında bulunamadı" dedi ve model orada durup kullanıcıya
soru sorarak bitirdi — oysa Üsküdar gerçek bir istasyon, sadece M4'te değil.

Artık istasyon ağ genelinde de aranıyor ve bulunursa **onu işleten hatlar**
aday olarak dönüyor:

> 'Üsküdar' M4 hattında değil; şu hatlarda var: M5, Marmaray1, Marmaray.
> Aynı aracı bu hatlardan biriyle yeniden çağır.

Sonuç: qwen aynı soruda üç çağrı yapıyor (M4 → yönlendirme → M5) ve doğru
cevaba kendi kendine varıyor, 26 s.

### 7b. Son durakta "kalkış saati" yoktu, ama araç veriyordu

"Kadıköy'den son M4 kaçta" sorusunda qwen **00:49** dedi. O saat, hattın
diğer ucundan kalkan son trenin Kadıköy'e **varış** saati; Kadıköy'den son
kalkış 00:00. Araç o yönün saatini `last_departure` alanında veriyordu, yani
hatayı araç davet ediyordu.

Düzeltme: sorulan istasyon o yönün son durağıysa `first_departure` ve
`last_departure` **boş** dönüyor, saatler `first_arrival` / `last_arrival`
alanlarına geçiyor, `is_arrival_at_station: true` oluyor. Ara istasyonlarda
(Bostancı) iki yön de kalkış veriyor, varış alanları boş.

Ders: bir sayıyı yanlış adı taşıyan alana koymak, modelin onu yanlış
kullanmasını istemek demek. Not olarak uyarmak yetmedi; alanın kendisinin
doğru olması gerekiyordu.

## Yeniden gözden geçirme koşulları

- Araç sayısı arttığında (hedef ~15) tek bir ReAct grafiği yetmeyebilir;
  araç seçimi ölçülüp gerekiyorsa yönlendirme (router) eklenmeli.
- Araç açıklamaları değişirse senaryo betiği yeniden çalıştırılmalı.
- Ücretsiz Gemini katmanı **dakikada 5, günde 20 istek** (8 Kasım 2026'da
  ölçüldü: gün sınırı dolunca 429 ve `retryDelay: 44784s`). Beş soruyu arka
  arkaya sormak dakika sınırını, bir günde birkaç tur denemek gün sınırını
  aşıyor. Değerlendirme betiği önbellek ve üstel geri çekilme olmadan
  çalıştırılamaz (CLAUDE.md kuralı); kota dolduğunda yerel model tek
  seçenek kalıyor.
- Yerel model değişirse (`OLLAMA_MODEL`) düşünme ve bağlam ayarları
  yeniden ölçülmeli: ikisi de bu modele göre seçildi.
