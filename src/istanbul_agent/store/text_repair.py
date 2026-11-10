"""İETT GTFS beslemesindeki çifte kodlanmış metni onar.

Besleme UTF-8 ama içindeki metin bir kez yanlış okunmuş: `KADIKÖY` dosyada
`KADIKÃ–Y` olarak duruyor, yani UTF-8 baytları tek baytlık bir kod sayfası
sanılıp yeniden UTF-8'e yazılmış. `routes.csv`'de ölçülen: 9279 satırda
11 867 bozuk değer (`route_long_name`, `route_desc`, `route_short_name`,
`route_code`).

Onarımın tek kod sayfasıyla yapılamadığı ölçümle görüldü (karar 0007):

- `KADIKÃ–Y` → `–` (U+2013) **cp1252**'de 0x96, latin1'de hiç yok,
- `ESATPAÅ\x9eA` → `\x9e` (U+009E) **latin1**'de 0x9E, cp1252'de hiç yok,

ve ikisi aynı dizede birlikte görünüyor. Bu yüzden geri çevirme karakter
bazında yapılır: her karakter için önce cp1252, olmazsa latin1. Böylece
11 867/11 867 değer onarıldı, düzgün hiçbir değer bozulmadı.

Bir karakter iki kod sayfasına da sığmıyorsa (gerçek `İ`, `ş`, `ğ`) dize
zaten bozuk değildir ve olduğu gibi döner: yarım onarım, bozuk bırakmaktan
daha kötüdür çünkü hangi değerin düzeldiği anlaşılmaz.
"""

from __future__ import annotations

__all__ = ["looks_double_encoded", "repair_double_encoded"]

# Yanlış okumanın gerçekleştiği kod sayfaları, denenme sırasıyla.
MISREAD_AS = ("cp1252", "latin1")

# Çifte kodlama izi. `Â` bilerek listede değil: "güzergâh" gerçek Türkçe ve
# onu iz sayan bir tespit, onarılmış dizeleri hâlâ bozuk gösteriyordu.
MARKS = ("Ã", "Ä", "Å")


def looks_double_encoded(text: str) -> bool:
    """Dize çifte kodlama izi taşıyor mu?

    Kesin bir test değil, ucuz bir göstergedir: onarımı yalnızca gereken
    değerlerde denemek ve ölçüm raporlarında bozuk değeri saymak için.
    """
    return any(mark in text for mark in MARKS)


def _misread_bytes(text: str) -> bytes | None:
    """Metni, yanlış okunduğu baytlara karakter bazında geri çevir."""
    out = bytearray()
    for char in text:
        for encoding in MISREAD_AS:
            try:
                out += char.encode(encoding)
                break
            except UnicodeEncodeError:
                continue
        else:
            # Karakter hiçbir tek baytlık kod sayfasına sığmıyor: dize gerçek
            # Türkçe harf taşıyor, çifte kodlanmış olamaz.
            return None
    return bytes(out)


def repair_double_encoded(text: str) -> str:
    """Çifte kodlanmış metni onar; onarılamıyorsa metni olduğu gibi döndür.

    Onarım yalnızca iz taşıyan dizelerde denenir, çünkü geri çevirme düzgün
    bir dizede de teknik olarak başarılı olabilir (örneğin saf ASCII metin
    kendisine döner) ve her dizeyi denemek gereksiz iş demektir.
    """
    if not text or not looks_double_encoded(text):
        return text
    raw = _misread_bytes(text)
    if raw is None:
        return text
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return text
