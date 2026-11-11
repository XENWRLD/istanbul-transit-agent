"""Canlı API'ler için ortak katman: zaman aşımı, yeniden deneme, TTL önbellek.

Üç kural burada toplanıyor, üçü de projenin "kaynak göster, uydurma"
duruşundan geliyor:

1. **Hata yutulmaz.** Erişilemeyen bir kaynak `LiveDataError` fırlatıyor ve
   araç bunu kullanıcıya dönük Türkçe bir cümleyle aktarıyor. "Veri yok"
   ile "veriye ulaşamadım" aynı şey değil.
2. **Yeniden deneme yalnızca geçici hatalarda.** Zaman aşımı, bağlantı
   hatası ve 5xx yeniden denenir; 4xx denenmez (istek yanlış, tekrarı da
   yanlış olur).
3. **TTL önbellek kaynak bazında.** Otopark doluluğu bir dakikada değişir,
   otobüs tarifesi günde bir. Tek bir TTL ikisine de yanlış gelir;
   değerler `SOURCE_TTL` içinde ve gerekçeleri yanında yazılı.

Önbellek süreç içi: MCP sunucusu uzun yaşayan bir süreç olduğu için bu
yeterli. Birden çok süreç ölçeğinde paylaşılan bir önbellek gerekir.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from istanbul_agent.config import get_settings

__all__ = [
    "SOURCE_TTL",
    "LiveDataError",
    "TtlCache",
    "cache",
    "fetch_json",
    "fetch_text",
]


class LiveDataError(RuntimeError):
    """Canlı veri alınamadı; mesaj kullanıcıya gösterilmeye uygun.

    Araçlar bu hatayı yakalayıp `message` alanını cevaba taşıyor: API
    kapalıyken sessizce boş liste döndürmek, "otopark yok" demekle aynı
    şey olurdu.
    """

    def __init__(self, source: str, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.source = source
        self.message = message
        self.cause = cause


# Kaynak başına önbellek süresi (saniye) ve gerekçesi.
SOURCE_TTL: dict[str, int] = {
    # Doluluk dakikalar içinde değişiyor; İSPARK'ın kendi `updateDate`
    # alanı da dakika hassasiyetinde.
    "ispark": 60,
    # Trafik indeksi 5 dakikalık adımlarla yayımlanıyor (ölçüm: 288 kayıt /
    # 24 saat), daha sık sormak aynı değeri getirir.
    "traffic": 300,
    # Hava kalitesi saatlik okuma veriyor.
    "air_quality": 900,
    # Duyurular gün içinde eklenir; 5 dakika agent için yeterince taze.
    "iett_alerts": 300,
    "metro_alerts": 300,
    # Arıza listesi gün içinde değişir ama saniyede değil.
    "metro_equipment": 300,
    # Planlanan sefer saatleri tarife verisi: gün içinde değişmiyor.
    "iett_timetable": 21_600,
    # İstasyon listesi ve detayları neredeyse statik.
    "metro_stations": 86_400,
}
DEFAULT_TTL = 300


@dataclass
class _Entry:
    value: Any
    expires_at: float


class TtlCache:
    """Süreç içi, iş parçacığı güvenli TTL önbellek."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                del self._entries[key]
                return None
            return entry.value

    def set(self, key: str, value: Any, ttl: int) -> None:
        with self._lock:
            self._entries[key] = _Entry(value=value, expires_at=time.monotonic() + ttl)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


cache = TtlCache()


def ttl_for(source: str) -> int:
    return SOURCE_TTL.get(source, DEFAULT_TTL)


def _cache_key(source: str, url: str, params: dict[str, Any] | None, body: str | None) -> str:
    parts = [source, url]
    if params:
        parts.append("&".join(f"{key}={params[key]}" for key in sorted(params)))
    if body:
        parts.append(str(hash(body)))
    return "|".join(parts)


def _should_retry(problem: Exception) -> bool:
    """Geçici hata mı?

    4xx yeniden denenmiyor: istek yanlışsa tekrarı da yanlış olur ve
    kotayı boşa harcar.
    """
    if isinstance(problem, httpx.HTTPStatusError):
        return problem.response.status_code >= 500
    return isinstance(problem, (httpx.TimeoutException, httpx.TransportError))


def _request(
    method: str,
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    retries: int | None = None,
) -> httpx.Response:
    settings = get_settings()
    attempts = (retries if retries is not None else settings.llm_max_retries) + 1
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=timeout or settings.ibb_request_timeout) as client:
                response = client.request(
                    method, url, params=params, content=content, headers=headers
                )
                response.raise_for_status()
                return response
        except Exception as problem:
            last = problem
            if attempt + 1 >= attempts or not _should_retry(problem):
                break
            # Üstel geri çekilme: 0.5 s, 1 s, 2 s …
            time.sleep(0.5 * (2**attempt))

    assert last is not None
    status = (
        f" (HTTP {last.response.status_code})" if isinstance(last, httpx.HTTPStatusError) else ""
    )
    raise LiveDataError(
        source,
        f"{source} verisine ulaşılamadı{status}: {type(last).__name__}. "
        "Kaynak şu an erişilemiyor; bu bilgi olmadan cevap verilmemeli.",
        cause=last,
    )


def fetch_json(
    url: str,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    ttl: int | None = None,
    timeout: float | None = None,
) -> Any:
    """JSON getir; aynı istek TTL içinde ikinci kez ağa çıkmaz."""
    key = _cache_key(source, url, params, None)
    cached = cache.get(key)
    if cached is not None:
        return cached
    response = _request("GET", url, source=source, params=params, timeout=timeout)
    try:
        payload = response.json()
    except ValueError as problem:
        raise LiveDataError(
            source,
            f"{source} beklenen JSON'ı döndürmedi; cevap {len(response.text)} karakterlik "
            "başka bir içerik.",
            cause=problem,
        ) from problem
    cache.set(key, payload, ttl if ttl is not None else ttl_for(source))
    return payload


def fetch_text(
    url: str,
    *,
    source: str,
    method: str = "POST",
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
    ttl: int | None = None,
    timeout: float | None = None,
) -> str:
    """Metin gövdesi getir (SOAP zarfı için); aynı istek TTL içinde önbellekten."""
    body = content.decode("utf-8", errors="replace") if content else None
    key = _cache_key(source, url, None, body)
    cached = cache.get(key)
    if cached is not None:
        return str(cached)
    response = _request(
        method, url, source=source, content=content, headers=headers, timeout=timeout
    )
    text = response.text
    cache.set(key, text, ttl if ttl is not None else ttl_for(source))
    return text
