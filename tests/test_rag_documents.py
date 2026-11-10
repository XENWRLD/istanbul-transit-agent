"""Kaynak yükleme: glif onarımı, HTML temizliği, dedup, dil filtresi.

Testler ağ ya da model kullanmıyor; `data/knowledge` yerine geçici bir
dizin ve elle yazılmış manifest kullanılıyor. Her test gerçek veride
ölçülmüş bir davranışı sabitliyor (karar 0009).
"""

from __future__ import annotations

import json

import pytest

from istanbul_agent.rag import documents as docs
from istanbul_agent.rag.documents import (
    SourceMeta,
    decode_glyph_names,
    load_documents,
    strip_html,
)


def manifest(entries: list[dict[str, object]]) -> dict[str, object]:
    return {"downloaded_at": "2026-11-10T00:00:00+0300", "sources": entries}


@pytest.fixture
def knowledge(tmp_path, monkeypatch):
    """Küçük bir kaynak dizini kur ve modülü ona bakacak şekilde ayarla."""
    directory = tmp_path / "knowledge"
    directory.mkdir()
    monkeypatch.setattr(docs, "KNOWLEDGE_DIR", directory)
    monkeypatch.setattr(docs, "MANIFEST_PATH", directory / "_manifest.json")
    return directory


class TestGlyphRepair:
    def test_cp1254_glyph_names_become_turkish(self):
        """2006 anketinin metni `/MT221/MT83/MT84` olarak çıkıyor."""
        text, repaired = decode_glyph_names("/MT221/MT83/MT84/MT65/MT78/MT66/MT85/MT76")
        assert repaired is True
        assert text == "İSTANBUL"

    def test_plain_text_is_untouched(self):
        text, repaired = decode_glyph_names("İSTANBUL ULAŞIM ANA PLANI")
        assert repaired is False
        assert text == "İSTANBUL ULAŞIM ANA PLANI"

    def test_repair_is_refused_when_the_result_is_not_turkish(self):
        """Glif adları font içi indeks olabilir; o zaman çözüm harf çorbası olur."""
        text, repaired = decode_glyph_names("/MT1/MT2/MT3/MT4/MT5/MT6/MT7/MT8")
        assert repaired is False
        assert text == "/MT1/MT2/MT3/MT4/MT5/MT6/MT7/MT8"


class TestHtml:
    def test_entities_and_tags_are_removed(self):
        raw = "<p>Raylı sistem istasyonlarında stant a&ccedil;ma ile ilgili.</p>\n"
        assert strip_html(raw) == "Raylı sistem istasyonlarında stant açma ile ilgili."

    def test_line_breaks_survive(self):
        assert strip_html("bir<br>iki") == "bir\niki"

    def test_empty_input(self):
        assert strip_html("") == ""


class TestMetroFaq:
    def test_only_turkish_entries_are_loaded(self, knowledge):
        """Servis aynı soruyu iki dilde veriyor; İngilizcesi sonuç yeri harcıyordu."""
        payload = {
            "Success": True,
            "Data": [
                {
                    "Question": "Bisikletle binebilir miyim?",
                    "Answer": "<p>Evet</p>",
                    "Language": "TR",
                },
                {"Question": "Can I ride by bicycle?", "Answer": "<p>Yes</p>", "Language": "EN"},
            ],
        }
        (knowledge / "metro_faq.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        (knowledge / "_manifest.json").write_text(
            json.dumps(
                manifest(
                    [
                        {
                            "slug": "metro_faq",
                            "status": "indirildi",
                            "kind": "json",
                            "title": "Metro SSS",
                            "dataset_no": 509,
                            "data_as_of": "2026-11-10",
                        }
                    ]
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        loaded = load_documents(include_catalog=False)
        assert len(loaded) == 1
        assert [section.kind for section in loaded[0].sections] == ["qa"]
        assert "Bisikletle" in loaded[0].sections[0].text
        assert "Can I ride" not in loaded[0].sections[0].text

    def test_question_and_answer_stay_in_one_section(self, knowledge):
        payload = {
            "Data": [
                {"Question": "Soru mu?", "Answer": "Cevap.", "Language": "TR"},
            ]
        }
        (knowledge / "metro_faq.json").write_text(json.dumps(payload), encoding="utf-8")
        (knowledge / "_manifest.json").write_text(
            json.dumps(manifest([{"slug": "metro_faq", "status": "indirildi", "kind": "json"}])),
            encoding="utf-8",
        )
        section = load_documents(include_catalog=False)[0].sections[0]
        assert section.text.startswith("Soru: Soru mu?")
        assert "Cevap: Cevap." in section.text


class TestTicketPrices:
    def test_each_card_type_is_a_section_with_its_date(self, knowledge):
        payload = {
            "Data": [
                {
                    "Type": "Anonim İstanbul Kart",
                    "TicketPrices": [{"Name": "Tam", "Price": "46,20 ₺"}],
                }
            ]
        }
        (knowledge / "metro_ticket_prices.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        (knowledge / "_manifest.json").write_text(
            json.dumps(
                manifest(
                    [
                        {
                            "slug": "metro_ticket_prices",
                            "status": "indirildi",
                            "kind": "json",
                            "title": "Güncel bilet fiyatları",
                            "data_as_of": "2026-11-10",
                        }
                    ]
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        document = load_documents(include_catalog=False)[0]
        assert len(document.sections) == 1
        section = document.sections[0]
        # Tablo bölünmez; tarih metnin içinde de yazılı olmalı ki parça tek
        # başına okunduğunda fiyatın ne zamana ait olduğu belli olsun.
        assert section.kind == "table"
        assert "46,20" in section.text
        assert "2026-11-10" in section.text


class TestDeduplication:
    def test_the_same_file_is_indexed_once(self, knowledge, monkeypatch):
        """445 ve 442 veri setleri bayt bayt aynı PDF'i yayımlıyor."""
        payload = json.dumps({"Data": [{"Question": "S", "Answer": "C", "Language": "TR"}]})
        for slug in ("metro_faq", "metro_faq_copy"):
            (knowledge / f"{slug}.json").write_text(payload, encoding="utf-8")
        (knowledge / "_manifest.json").write_text(
            json.dumps(
                manifest(
                    [
                        {
                            "slug": "metro_faq",
                            "status": "indirildi",
                            "kind": "json",
                            "dataset_no": 509,
                        },
                        {
                            "slug": "metro_faq_copy",
                            "status": "indirildi",
                            "kind": "json",
                            "dataset_no": 510,
                        },
                    ]
                )
            ),
            encoding="utf-8",
        )
        # İkinci slug için de SSS yükleyicisi kullanılsın.
        monkeypatch.setitem(docs.LOADERS, "metro_faq_copy", docs.load_metro_faq)

        loaded = load_documents(include_catalog=False)
        assert len(loaded) == 1
        assert [other.slug for other in loaded[0].also_from] == ["metro_faq_copy"]


class TestCitation:
    def test_citation_names_the_dataset_and_date(self):
        meta = SourceMeta(
            slug="x",
            title="Metro SSS",
            dataset_no=509,
            data_as_of="2026-11-10",
            url=None,
            kind="json",
        )
        assert meta.citation == "Metro SSS (veri seti 509, 2026-11-10)"

    def test_citation_without_a_dataset_number(self):
        meta = SourceMeta(
            slug="x",
            title="Katalog",
            dataset_no=None,
            data_as_of="2026-11-06",
            url=None,
            kind="catalog",
        )
        assert meta.citation == "Katalog (2026-11-06)"


class TestFailedDownloads:
    def test_a_source_that_failed_to_download_is_skipped(self, knowledge):
        (knowledge / "_manifest.json").write_text(
            json.dumps(manifest([{"slug": "metro_faq", "status": "hata", "kind": "json"}])),
            encoding="utf-8",
        )
        assert load_documents(include_catalog=False) == []
