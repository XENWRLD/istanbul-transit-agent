# 0019 — `find_mobility_options`: üç kaynak, üç kusur, hepsi çıktıda

**Tarih:** 12 Kasım 2026
**Durum:** uygulandı, agent üzerinde ölçüldü
**İlgili:** [0012](0012-canli-veri-araclari.md) (şehir araçları, araç kayıt sırası), [0013](0013-canli-veri-dogrulama.md) (sıfır "bilinmiyor" değildir), [0015](0015-olu-mod-degerleri.md) (sözlük tabloyla karşılaştırılır), [0018](0018-olcumun-yinelenebilirligi.md) (ölçüm penceresi)

## Bağlam

PLAN'daki son bekleyen araç. Faz 2'de "düşük öncelikli, en sona" diye
bırakılmıştı; dört kaynak işaretliydi ama hiçbiri incelenmemişti.

## Önce ölçüm: `scripts/inspect_mobility_sources.py`

Proje kuralı gereği kod yazmadan önce dört kaynak indirilip ağzı ölçüldü.
Ölçüm araç tasarımını üç yerden değiştirdi.

| No | Kaynak | Kayıt | Portal tarihi | Biçim |
|---|---|---|---|---|
| 16 | Bisiklet/mikromobilite park alanları | 1300 nokta | 2026-11-04 | GeoJSON |
| 276 | Elektrikli araç şarj istasyonları | 2933 nokta | **2025-05-13** | GeoJSON |
| 275 | Şarj soketleri | 38 950 satır | **2025-05-20** | CSV |
| 1 | Bisiklet yolları | 477 çizgi, 132 426 düğüm | 2026-11-06 | GeoJSON |

### Kusur 1 — bisiklet yolu kayıtlarının %44'ü henüz yol değil

`PRJ_ASAMA` alanı ölçüldü:

| Aşama | Kayıt |
|---|---|
| Mevcut Ayrılmış | 238 |
| UTK kararı alınan | **204** |
| Mevcut Paylaşımlı | 27 |
| Etüt Aşamasında | 3 |
| Proje Aşamasında | 2 |
| İnşaat Aşamasında | 2 |
| Projesi Tamamlanan | 1 |

Mevcut olan 265, olmayan 212. Veri setinin adı "Bisiklet Yolları" ve alan
adları projeden söz etmiyor; aşamaya bakmayan bir araç kullanıcıya **henüz
yapılmamış bir yolu "var" diye** söyler. `exists_now` bu ayrımı taşıyor,
varsayılan olarak yalnızca mevcut olanlar dönüyor ve gizlenen kayıt sayısı
nota yazılıyor.

### Kusur 2 — soket sayıları alt sınır, toplam değil

Soket dosyasının yalnızca %23'ü (8877/38 950) istasyon numarası taşıyor.
İlk hipotezim "birleştirilmiş hücre, aşağı doldurmak gerekir" idi ve
**ölçüm onu çürüttü**: boş satırlar 8406. satırdan sonra blok halinde
geliyor ve kendi `SOKET_NO`'larını taşıyor. Aşağı doldurma 29 487 soketi
tek istasyona yazardı.

Dolu satırlar 2962 ayrı istasyona dağılıyor ve bunların **2931'i 276'daki
2933 istasyonla eşleşiyor**. Yani neredeyse her istasyonun bir miktar
soketi biliniyor, toplamı bilinmiyor. Sütun adı bu yüzden
`socket_count_at_least`; `sockets_known` false olduğunda sayı **sıfır
değil, bilinmiyor** (karar 0013/1'in aynısı) ve araç her şarj sonucunda
30 073/38 950 satırın bağlanamadığını not olarak söylüyor.

### Kusur 3 — aynı araç iki tazelikte veri döndürüyor

Park ve yol verisi 2026-11, şarj verisi 2025-05. Araç `is_stale`'i **kayıt
bazında** taşıyor: park sorgusunda `is_stale: false`, şarj sorgusunda
`true`. Tek bir tazelik damgası basmak yanlış olurdu — karar 0013'ün İSPARK
bulgusunun aynısı (damga partiye basılınca veri bildirmeyen otopark da taze
görünüyordu).

Ek olarak `HIZMET_SEKLI` 2029 halka açık / 904 özel ayırıyor; varsayılan
halka açık, gizlenen sayı nota yazılıyor.

## Uygulama

- `scripts/download_mobility.py` — adresler katalogdan çözülüyor, portal
  tarihi manifest'e yazılıyor (`data_as_of` tek yerden gelir).
- `store/ingest_mobility.py` — `mobility_points` (4233), `mobility_paths`
  (477), `mobility_path_nodes` (132 426). BOM'lu başlık `utf-8-sig` ile
  okunuyor; temizlenmezse alan adı hiçbir şeyle eşleşmiyor.
- `store/mobility.py` — mesafe Python tarafında ölçülüyor (4233 + 132 426
  satır tek sorguda dönüyor, spatial eklentisine gerek yok). Yol mesafesi
  en yakın **düğüm** mesafesi; parçaya dik mesafe hesaplanmıyor ve çıktı
  bunu kuş uçuşu diye söylüyor.
- `city_server.find_mobility_options` — `main()` bloğundan **önce** tanımlı
  (karar 0012: sonrasına yazılan araç stdio'da kaydolmuyor); stdio üzerinden
  12 araç doğrulandı.

### Kendi iki hatam, ingest'in kendi raporu yakaladı

İlk ingest 1300 park alanının tamamını "bilinmeyen tip" diye attı ve 265
mevcut yolu planlı gösterdi. Sebep: `normalize()` Türkçe harfleri de
katlıyor ("Park Alanı" → `park alani`, "Mevcut Ayrılmış" → `mevcut
ayrilmis`) ve sabit sözlükleri katlanmamış biçimde yazmıştım. İki test artık
anahtarların normalize edilmiş olmasını ve hiçbir eşanlamlının
`STORED_KINDS` dışına açılmamasını tutuyor (karar 0015'in hata sınıfı).

Bunu yakalatan şey test değil, ingest'in kendi çıktısıydı: sessizce boş
dönmek yerine "BİLİNMEYEN park tipi: {...}" diye sayıp bildirdi. Sayarak
bildiren ingest, sessizce atan ingest'ten bu yüzden iyidir.

## Agent üzerinde ölçüm

Üç yeni senaryo (`mob-01/02/03`) ve üç kontrol, önbellek devre dışı:

| Senaryo | Çağrı | Sonuç |
|---|---|---|
| `mob-01` "Kadıköy çevresinde scooter park alanı" | `find_mobility_options(kind="mikromobilite_park")` | ✓ |
| `mob-02` "Levent'te elektrikli arabamı nerede şarj ederim" | `geocode_place` → `find_mobility_options(kind="elektrikli", radius_m=2000)` | ✓ eskilik uyarısı cevapta |
| `mob-03` "Beşiktaş'ta kaç müsait soket var şu an" | `find_mobility_options(kind="sarj", limit=50)` | ✓ anlık doluluğun olmadığını söylüyor |
| `route-02`, `live-02`, `near-02` (kontrol) | — | ✓ ✓ ✓ |

Model eşanlamlıları kendiliğinden kullandı (`mikromobilite_park`,
`elektrikli`, `sarj`) — sözlük işini yapıyor, ama tam bu yüzden
`STORED_KINDS` dışına açılmaması gerekiyor.

## Araç yüzeyi büyüdü: ölçüldü, henüz sorun yok

Prompt + şemalar **~9600 token** (7 araçta ~5900 ölçülmüştü, karar 0010),
pencere `OLLAMA_NUM_CTX=16384`. Kontrol senaryoları araç çağırmaya devam
ediyor, yani pencere taşmıyor. Yine de pay azaldı: bir araç daha eklenirse
ölçüm yeniden yapılmalı, çünkü kararlar 0006 ve 0010'da pencere taşınca
model **hiç** araç çağırmamıştı.

## Yapılmayanlar

- **Bisiklet bakım istasyonları (109, XLSX)** eklenmedi: araç yüzeyi zaten
  büyüdü ve bakım istasyonu ayrı bir soruya cevap veriyor.
- **İsbike** eklenmedi: servis kapalı (karar 0001'de işaretli).
- Anlık doluluk ya da müsait soket **hiçbir kaynakta yok**; araç bunu
  açıklamasında ve notlarında söylüyor, tahmin üretmiyor.
- 57 senaryoluk tam oran bu araçla **ölçülmedi**: araç yüzeyi değiştiği için
  taban da aynı pencerede yeniden koşulmalı (karar 0018) ve bu ~2 saat.
