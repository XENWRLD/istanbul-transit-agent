# 0017 — `find_nearby_stops` merkezi modsuz arıyordu

**Tarih:** 12 Kasım 2026
**Durum:** kod uygulandı; agent ölçümü koşuldu ve **sonuç yorumlanamadı** (bkz. karar 0018)
**İlgili:** [0008](0008-yer-ve-hat-araclari.md) (yer araçları, küme kuralı), [0015](0015-olu-mod-degerleri.md) (aynı sınıf: parametre var, bir çağrı yeri kullanmıyor), [0016](0016-onbellek-anahtari-ve-docker.md) (önbellek anahtarı bu değişikliği kendisi geçersiz kılıyor)

## Bağlam: `near-04`'ün gerçek sebebi

Karar 0015 kalan dört başarısızlığı sıralarken `near-04` ("Bostancı
çevresindeki iskeleler neler?") için *"muhtemelen senaryo katılığı"* yazmış
ve kararı vermemişti: model `geocode_place` çağırıyor, senaryo
`find_nearby_stops` şart koşuyor, cevap yine de doğru görünüyor.

Kanıta bakınca hipotez yanlış çıktı — 0015'in kendi `rail-04` hipotezinde
olduğu gibi.

## Ölçüm

Önbellekteki koşu, modelin ne yaptığını söylüyor:

```
geocode_place(query="Bostancı", mode="vapur")
→ Bostancı Mavi Marmara, Bostancı İDO, Bostancı ŞH.
```

Senaryonun beklediği aracı aynı soruyla elle çağırdığımda çıkan şey:

| Çağrı | Sonuç |
|---|---|
| `find_nearby_stops(around="Bostancı", mode="vapur")` (varsayılan 500 m) | **boş liste** + *"yarıçapı artırmayı dene"* |
| aynı çağrı, 1000 m | **boş liste** |
| aynı çağrı, 2000 m | üç iskele, 1656–1729 m |

Sebep merkez seçimi: `find_places("Bostancı")` altı küme döndürüyor ve
**hiçbiri vapur değil** (iskelelerin adı "Bostancı Mavi Marmara", "Bostancı
İDO", "Bostancı ŞH."). Araç ilk kümeyi — metro istasyonunu (40.96484,
29.10460) — merkez alıyor, iskeleler 1.7 km uzakta kalıyor ve vapur
filtresi 500 m içinde hiçbir şey bulamıyor.

Yani **senaryonun şart koştuğu araç, varsayılan parametrelerle bu soruyu
cevaplayamıyordu.** Modelin `geocode_place`'e gitmesi, ölçümün "yanlış
araç" dediği şey, aslında daha iyi cevap üretmişti.

## Kusurun sınıfı: 0015 ile aynı

`find_places` **zaten** `modes` parametresi alıyor ve `geocode_place` onu
geçiriyor:

```python
search = find_places(query, modes=resolved_modes or None, limit=5)   # geocode_place
search = find_places(around, limit=3)                                 # find_nearby_stops
```

Tek fark bu satır. Karar 0015'te sözlük tabloyla hiç karşılaştırılmamıştı;
burada parametre bir çağrı yerinde düşmüş. İkisi de aynı deseni paylaşıyor:
**araç kendi belgelediği davranışı uygulamıyor** ve kusur boş bir sonuç
olarak çıktığı için hata gibi görünmüyor (kararlar 0006/7a, 0008/3: boş
sonuç parametre hatasını gizlemesin).

## Karar

Mod verildiyse merkez **önce o modda** aranır; o adda o modda bir yer yoksa
merkez eski yoldan seçilir ve not bunu açıkça söyler (*"Sorulan moda uyan
bu adda bir yer yok; merkez başka moddan alındı, sonuçlar yine sorulan
modda."*). Sonuç filtresi her durumda sorulan modda kalıyor.

Düşme yolunu bırakmanın sebebi: mod eşleşmesini zorunlu kılmak "Sultanbeyli
çevresindeki iskeleler" gibi sorularda aracı boş döndürmekten beter hale
getirirdi — merkez bulunamadığı için `yer_bulunamadi` dönerdi, oysa doğru
cevap "o civarda iskele yok".

### Ölçülen önce/sonra

| Çağrı | Önce | Sonra |
|---|---|---|
| `find_nearby_stops("Bostancı", mode="vapur")`, 500 m | boş liste | Mavi Marmara 0 m, İDO 89 m, ŞH. 91 m |
| `find_nearby_stops("Bostancı")` (modsuz) | metro merkez, 10 sonuç | **birebir aynı** |
| `find_nearby_stops("Taksim", mode="otobus")` | — | merkez TAKSİM otobüs durağı, 4 durak |
| `find_nearby_stops("Üsküdar", mode="rayli")` | — | merkez Üsküdar metro |

Modsuz çağrının değişmemesi kasıtlı: düzeltme yalnızca `mode` verildiğinde
devreye giriyor ve bir test bunu tutuyor.

## Testler

- merkez sorulan modu izliyor (Bostancı + vapur varsayılan yarıçapta üç iskele)
- mod eşleşmesi yokken merkez düşüyor **ve notu basıyor** (Sultanbeyli + vapur)
- modsuz arama merkezini koruyor (regresyon)

## Ölçüldü: sonuç yorumlanamıyor

54 senaryo yeni araç yüzeyiyle koşuldu (`--refresh`, ollama, 2375 s):
**47/54 (%87.0)**, taban 50/54 (%92.6). Araç doğruluğu %88.9 (taban %94.4),
kaynaksız cevap 2 (taban 1), ifade doğruluğu %96.2 (taban %92.3).

Bu sayıyı "düzeltme −3 verdi" diye raporlamak yanlış olurdu. Önce/sonra
önbellek dosyaları satır satır karşılaştırıldığında **54 senaryonun 12'si
araç çağrı dizisini değiştirdi** ve bunların hiçbiri bu düzeltmenin
dokunduğu yoldan geçmiyor (düzeltme yalnızca `find_nearby_stops` modlu
çağrıldığında devreye giriyor):

| Senaryo | Önce | Sonra |
|---|---|---|
| `rail-03` | `get_line_route`, `get_rail_travel_time` | **sıfır araç** |
| `rail-06` | `get_rail_travel_time` | **sıfır araç** |
| `know-06` | `search_knowledge` | `find_parking` |
| `know-08` | `get_service_alerts`, `search_knowledge` | `get_service_alerts` + `get_next_departures` ×6 |
| `rail-04` | sıfır araç | `geocode_place`×2 → `get_rail_travel_time` (geçti) |
| `refuse-03` | sıfır araç | `get_traffic_status` (geçti) |
| `chain-01`, `chain-04`, `live-05`, `near-01`, `refuse-05`, `refuse-08` | — | dizi uzadı ya da kısaldı |

Geçme durumu değişenler: `rail-04` düzeldi, `rail-03` / `rail-06` /
`know-06` / `know-08` düştü. Net −3, ama hiçbiri bu değişiklikle nedensel
olarak bağlanamıyor. Ölçümün kendi kararsızlığı karar 0018'de.

`near-04` **değişmedi**: model düzeltmeden sonra da `geocode_place`
çağırıyor. Yani araçtaki kusur gerçekti ve veri katmanında düzeldi, ama bu
senaryoyu kendiliğinden geçirmiyor; senaryonun katı olup olmadığı sorusu
hâlâ açık ve bu koşu onu cevaplamıyor.

## Bu bölüm yazıldığında ölçülmemişti

Bu değişiklik araç **çıktısını** ve **açıklamasını** değiştiriyor, yani
proje kuralına göre davranış değişikliğidir ve 54 senaryo yeniden
koşulmalıdır. Bu not yazıldığında **koşulmadı**: ölçüm sessiz makine
istiyor (karar 0014) ve bu turda makine Docker ölçümüyle meşguldü.

Karar 0016'nın kazancı burada görünüyor: araç açıklaması değiştiği için
önbellek anahtarındaki araç yüzeyi özeti kendiliğinden değişti, yani
`--refresh` unutulsa bile betik eski cevapları yeniden kullanamaz. Önceki
düzende bu değişiklik ölçümü sessizce yalanlardı.

**`near-04` senaryosuna dokunulmadı.** Senaryonun katı olup olmadığına
ancak aracın düzeltilmiş hâliyle koşulan ölçümden sonra karar verilebilir:
model artık `find_nearby_stops`'u çağırırsa senaryo kendiliğinden geçer;
hâlâ `geocode_place` çağırıyorsa soru "hangi araç doğru cevabı veriyor"dan
"araç seçimi ölçütü doğru mu"ya döner.
