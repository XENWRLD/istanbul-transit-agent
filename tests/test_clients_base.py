"""Ortak istemci katmanı: TTL önbellek, yeniden deneme, hata mesajı.

Ağa çıkılmıyor; `httpx` istekleri taklit ediliyor. Sınanan şey canlı
verinin kendisi değil **kuralların uygulanması**: hata yutulmuyor mu,
yeniden deneme doğru hatalarda mı, önbellek kaynak bazında mı (karar 0012).
"""

from __future__ import annotations

import httpx
import pytest

from istanbul_agent.clients import base
from istanbul_agent.clients.base import (
    SOURCE_TTL,
    LiveDataError,
    TtlCache,
    fetch_json,
    ttl_for,
)


@pytest.fixture(autouse=True)
def clean_cache():
    base.cache.clear()
    yield
    base.cache.clear()


class TestTtlCache:
    def test_a_value_comes_back_before_it_expires(self):
        cache = TtlCache()
        cache.set("k", 42, ttl=60)
        assert cache.get("k") == 42

    def test_an_expired_value_is_gone(self, monkeypatch):
        cache = TtlCache()
        clock = [1000.0]
        monkeypatch.setattr(base.time, "monotonic", lambda: clock[0])
        cache.set("k", 42, ttl=10)
        clock[0] += 11
        assert cache.get("k") is None
        assert len(cache) == 0

    def test_an_unknown_key_is_none(self):
        assert TtlCache().get("yok") is None


class TestSourceTtl:
    def test_parking_is_cached_briefly_and_timetables_for_hours(self):
        """Doluluk dakikada değişir, tarife günde bir."""
        assert SOURCE_TTL["ispark"] <= 120
        assert SOURCE_TTL["iett_timetable"] >= 3600
        assert SOURCE_TTL["ispark"] < SOURCE_TTL["iett_timetable"]

    def test_an_unknown_source_gets_the_default(self):
        assert ttl_for("bilinmeyen") == base.DEFAULT_TTL


class TestRetryClassification:
    @pytest.mark.parametrize(
        "problem",
        [
            httpx.TimeoutException("zaman aşımı"),
            httpx.ConnectError("bağlantı yok"),
            httpx.HTTPStatusError(
                "sunucu hatası",
                request=httpx.Request("GET", "https://x"),
                response=httpx.Response(503),
            ),
        ],
    )
    def test_transient_failures_are_retried(self, problem):
        assert base._should_retry(problem) is True

    @pytest.mark.parametrize("status", [400, 401, 404, 422])
    def test_client_errors_are_not_retried(self, status):
        """İstek yanlışsa tekrarı da yanlış olur."""
        problem = httpx.HTTPStatusError(
            "istemci hatası",
            request=httpx.Request("GET", "https://x"),
            response=httpx.Response(status),
        )
        assert base._should_retry(problem) is False


class TestFetchJson:
    def test_a_successful_response_is_parsed_and_cached(self, monkeypatch):
        calls = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["count"] += 1
            return httpx.Response(200, json=[{"a": 1}])

        monkeypatch.setattr(base.httpx, "Client", _fake_client(handler))
        first = fetch_json("https://x/list", source="ispark")
        second = fetch_json("https://x/list", source="ispark")
        assert first == second == [{"a": 1}]
        assert calls["count"] == 1, "ikinci çağrı önbellekten gelmeli"

    def test_different_params_are_cached_separately(self, monkeypatch):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"url": str(request.url)})

        monkeypatch.setattr(base.httpx, "Client", _fake_client(handler))
        fetch_json("https://x/d", source="ispark", params={"id": 1})
        fetch_json("https://x/d", source="ispark", params={"id": 2})
        assert len(seen) == 2

    def test_a_failing_request_raises_a_user_facing_error(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        monkeypatch.setattr(base.httpx, "Client", _fake_client(handler))
        monkeypatch.setattr(base.time, "sleep", lambda _: None)
        with pytest.raises(LiveDataError) as error:
            fetch_json("https://x/list", source="ispark")
        assert error.value.source == "ispark"
        assert "ulaşılamadı" in error.value.message
        assert "HTTP 500" in error.value.message

    def test_a_non_json_body_is_reported_not_swallowed(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>bakım</html>")

        monkeypatch.setattr(base.httpx, "Client", _fake_client(handler))
        with pytest.raises(LiveDataError) as error:
            fetch_json("https://x/list", source="traffic")
        assert "JSON" in error.value.message

    def test_a_transient_failure_is_retried_then_succeeds(self, monkeypatch):
        attempts = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] == 1:
                return httpx.Response(503)
            return httpx.Response(200, json={"ok": True})

        monkeypatch.setattr(base.httpx, "Client", _fake_client(handler))
        monkeypatch.setattr(base.time, "sleep", lambda _: None)
        assert fetch_json("https://x/list", source="traffic") == {"ok": True}
        assert attempts["count"] == 2


# Gerçek sınıf yamadan **önce** yakalanıyor: `base.httpx.Client` yamalandığı
# için taklit sınıf içinde `httpx.Client(...)` çağırmak kendini çağırır ve
# `RecursionError` verir — ilk sürümde tam bu oldu.
_REAL_CLIENT = httpx.Client


def _fake_client(handler: object) -> type:
    """`httpx.Client` yerine geçen, isteği `handler`a veren sınıf."""

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._transport = httpx.MockTransport(handler)
            self._client = _REAL_CLIENT(transport=self._transport)

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *exc: object) -> None:
            self._client.close()

        def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
            response = self._client.request(method, url, **kwargs)
            response.raise_for_status()
            return response

    return FakeClient
