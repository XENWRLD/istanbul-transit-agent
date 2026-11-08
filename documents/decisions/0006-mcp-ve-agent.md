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

## Yeniden gözden geçirme koşulları

- Araç sayısı arttığında (hedef ~15) tek bir ReAct grafiği yetmeyebilir;
  araç seçimi ölçülüp gerekiyorsa yönlendirme (router) eklenmeli.
- Araç açıklamaları değişirse senaryo betiği yeniden çalıştırılmalı.
- Ücretsiz Gemini katmanı dakikada 5 istek: senaryo betiği beş soruyu
  arka arkaya sorduğunda 429 alınabiliyor. Değerlendirme betiği yazılırken
  üstel geri çekilme ve önbellek gerekecek (CLAUDE.md kuralı).
