"""Arama hattının karar mantığı: tazelik sırası, geçmiş soruları, eşikler.

Model ve indeks kullanılmıyor; `Hit` nesneleri elle kuruluyor. Burada
sınanan şey gömme kalitesi değil, **hangi sonucun önce gösterileceği** —
ölçümde ortaya çıkan iki hata buradaydı (karar 0009).
"""

from __future__ import annotations

import pytest

from istanbul_agent.rag.index import Hit
from istanbul_agent.rag.search import (
    FRESHNESS_WINDOW,
    MIN_RERANK_SCORE,
    STALE_BEFORE,
    _prefer_fresh,
    asks_about_past,
    is_stale_source,
)


def hit(slug: str, data_as_of: str, score: float = 1.0) -> Hit:
    return Hit(
        chunk_id=f"{slug}:0:0",
        score=score,
        text=f"{slug} başlığı\n\nmetin",
        heading=f"{slug} başlığı",
        source_slug=slug,
        source_title=slug,
        citation=f"{slug} ({data_as_of})",
        data_as_of=data_as_of,
        kind="table",
        page=None,
        url=None,
    )


class TestStaleness:
    def test_the_old_price_table_is_stale(self):
        assert is_stale_source("ticket_prices_2017_2022", "2022-06-10") is True

    def test_the_live_price_service_is_not_stale(self):
        assert is_stale_source("metro_ticket_prices", "2026-11-10") is False

    def test_a_dated_research_report_is_not_stale(self):
        """2023 tarihli araştırma eskimiş değil, o yılın araştırmasıdır."""
        assert is_stale_source("uap_report", "2023-05-18") is False

    def test_the_threshold_is_the_documented_one(self):
        assert STALE_BEFORE == "2024-01-01"


class TestPastQuestions:
    @pytest.mark.parametrize(
        "question",
        [
            "2019'da öğrenci bileti kaç liraydı?",
            "2017 yılında tam bilet ne kadardı",
            "bilet fiyatlarının yıllara göre değişimi",
            "bilet fiyatı tarihçesi",
            "eskiden ne kadardı",
            "hangi yıl zam yapıldı",
        ],
    )
    def test_past_questions_are_recognised(self, question):
        assert asks_about_past(question) is True

    @pytest.mark.parametrize(
        "question",
        [
            "tam bilet kaç lira",
            "öğrenci kartı ücreti nedir",
            "2026 fiyatları ne olacak",
            "metroya bisikletle binebilir miyim",
        ],
    )
    def test_present_questions_are_not_past(self, question):
        assert asks_about_past(question) is False


class TestFreshnessOrder:
    def test_a_current_source_moves_ahead_of_a_stale_one(self):
        """Ölçümde "tam bilet kaç lira" sorusunda 2022 tablosu birinciydi."""
        hits = [
            hit("ticket_prices_2017_2022", "2022-06-10", 1.0),
            hit("metro_ticket_prices", "2026-11-10", 0.58),
        ]
        ordered = _prefer_fresh(hits, "tam bilet kaç lira")
        assert [item.source_slug for item in ordered] == [
            "metro_ticket_prices",
            "ticket_prices_2017_2022",
        ]

    def test_a_past_question_keeps_the_search_order(self):
        hits = [
            hit("ticket_prices_2017_2022", "2022-06-10", 1.0),
            hit("metro_ticket_prices", "2026-11-10", 0.58),
        ]
        ordered = _prefer_fresh(hits, "2019'da bilet ne kadardı")
        assert ordered[0].source_slug == "ticket_prices_2017_2022"

    def test_order_is_untouched_when_nothing_is_stale(self):
        hits = [hit("metro_faq", "2026-11-10", 1.0), hit("uap_report", "2023-05-18", 0.5)]
        assert _prefer_fresh(hits, "soru") == hits

    def test_only_the_leading_window_is_reordered(self):
        """Pencere dışındaki sıraya dokunulmaz; arama sırası korunur."""
        hits = [
            hit("ticket_prices_2017_2022", "2022-06-10", 1.0),
            hit("metro_faq", "2026-11-10", 0.9),
            hit("uap_report", "2023-05-18", 0.8),
            hit("dataset_notes", "2026-11-06", 0.7),
        ]
        ordered = _prefer_fresh(hits, "bilet")
        assert ordered[-1].source_slug == "dataset_notes"
        assert ordered[0].source_slug == "metro_faq"

    def test_the_window_is_the_documented_one(self):
        assert FRESHNESS_WINDOW == 3


class TestThresholds:
    def test_the_rerank_cutoff_is_the_documented_one(self):
        assert MIN_RERANK_SCORE == -2.0
