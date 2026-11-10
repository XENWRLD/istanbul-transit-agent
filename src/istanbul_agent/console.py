"""stdout rapor karakterlerini kodlayamadığında konsolu UTF-8'e çevir.

Windows bu makinede Python'a **cp1254** bir stdout veriyor. Rapor tabloları
ise cp1254'te karşılığı olmayan karakterler kullanıyor (`→` U+2192, `⚠`
U+26A0). Bunlardan birini basmak `UnicodeEncodeError` ile sağlıklı bir
betiği ya da testi düşürüyor — ölçüm doğru, yalnızca yazdırma kırılıyor.

Çözüm akışı stdout olduğu yerde düzeltmek: karakterleri her yerden
çıkarmak tabloların okunurluğunu kalıcı olarak bozardı. Betik giriş
noktaları ve `tests/conftest.py` bu fonksiyonu çağırır; kütüphane kodu
içinden çağrılmaz, global akışı değiştirmek giriş noktasının işidir.
"""

from __future__ import annotations

import sys
from typing import IO, Any

# cp1254'te yeri olmayan, raporlarda gerçekten kullanılan karakterler.
PROBE = "→⚠"


def _needs_utf8(stream: IO[Any]) -> bool:
    """Akım rapor karakterlerini kodlayamıyor mu?"""
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return False
    try:
        PROBE.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return True
    return False


def enable_unicode_output() -> bool:
    """Gerekiyorsa stdout/stderr'i UTF-8'e çevir; değiştirildi mi dön.

    pytest yakalama sırasında `sys.stdout`'u değiştirdiği, `capsys.disabled()`
    ise yakalamadan önceki akımı geri verdiği için `sys.__stdout__` de
    ayrıca ele alınır. Zaten UTF-8 olan (Linux, macOS, UTF-8 kod sayfalı
    Windows) akımlara dokunulmaz.
    """
    switched = False
    seen: set[int] = set()
    for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
        if stream is None or id(stream) in seen:
            continue
        seen.add(id(stream))
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None or not _needs_utf8(stream):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # Yönlendirilmiş veya kapanmış akım: rapor karakterleri kaybolur
            # ama betik çalışmaya devam eder.
            continue
        switched = True
    return switched
