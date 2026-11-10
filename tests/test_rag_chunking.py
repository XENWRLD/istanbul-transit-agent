"""Parçalama: bölünmeyen türler, örtüşmeli bölme, gürültü filtresi.

Her test ölçümle gelen bir kuralı sabitliyor (karar 0009): tablolar
bölünmez, soru-cevap atomiktir, anket formlarının boş alanları
indekslenmez ama sayı yoğun istatistik tabloları indekslenir.
"""

from __future__ import annotations

import pytest

from istanbul_agent.rag.chunking import (
    MAX_CHARS,
    MIN_LETTERS,
    chunk_documents,
    chunk_section,
    chunk_stats,
    is_informative,
)
from istanbul_agent.rag.documents import Document, Section, SourceMeta

META = SourceMeta(
    slug="test",
    title="Test Belgesi",
    dataset_no=1,
    data_as_of="2026-11-10",
    url=None,
    kind="pdf",
)


def document(sections: list[Section]) -> Document:
    return Document(meta=META, sections=sections)


class TestUnsplittableKinds:
    def test_a_table_is_never_split(self):
        rows = "\n".join(f"{year} | Tam | {year / 100:.2f}" for year in range(1000, 1400))
        section = Section(heading_path=("Fiyatlar",), text=rows, kind="table")
        parts = chunk_section(section)
        assert len(parts) == 1
        assert parts[0][1] is True  # hedefi aşıyor diye işaretli

    def test_a_question_answer_pair_is_never_split(self):
        long_answer = "Cevap. " * 500
        section = Section(
            heading_path=("SSS", "Soru?"), text=f"Soru: S\nCevap: {long_answer}", kind="qa"
        )
        parts = chunk_section(section)
        assert len(parts) == 1

    def test_two_question_answer_pairs_are_not_merged(self):
        sections = [
            Section(heading_path=("SSS", "Bir?"), text="Soru: Bir?\nCevap: Evet.", kind="qa"),
            Section(heading_path=("SSS", "İki?"), text="Soru: İki?\nCevap: Hayır.", kind="qa"),
        ]
        chunks = chunk_documents([document(sections)])
        assert len(chunks) == 2


class TestProseSplitting:
    def test_long_prose_is_split_at_sentence_boundaries(self):
        sentence = "İstanbul'da ulaşım planlaması kapsamlı bir çalışma gerektirir. "
        section = Section(heading_path=("Rapor", "Giriş"), text=sentence * 80, kind="prose")
        parts = chunk_section(section)
        assert len(parts) > 1
        assert all(len(text) <= MAX_CHARS for text, _ in parts)

    def test_consecutive_short_prose_sections_are_merged(self):
        sections = [
            Section(
                heading_path=("Rapor", "Bölüm"),
                text="Bu cümle yeterince uzun bir metin parçasıdır.",
                kind="prose",
            )
            for _ in range(6)
        ]
        chunks = chunk_documents([document(sections)])
        assert len(chunks) == 1

    def test_the_heading_path_is_prefixed_to_every_chunk(self):
        section = Section(
            heading_path=("Rapor", "3.2 Yöntem"),
            text="Ölçüm yöntemi şu şekilde tanımlanmıştır ve uygulanmıştır.",
            kind="prose",
        )
        chunk = chunk_documents([document([section])])[0]
        assert chunk.text.startswith("Rapor > 3.2 Yöntem")
        assert chunk.heading == "Rapor > 3.2 Yöntem"

    def test_a_single_very_long_sentence_is_still_cut(self):
        """Cümle sınırı yoksa sert kesme yapılır; parça sonsuz büyümez."""
        section = Section(heading_path=("Rapor",), text="a" * (MAX_CHARS * 3), kind="prose")
        parts = chunk_section(section)
        assert len(parts) > 1
        assert max(len(text) for text, _ in parts) <= MAX_CHARS


class TestNoiseFilter:
    @pytest.mark.parametrize(
        "noise",
        [
            "SAAT  ____  :  ____",
            "01  1  2  1  2 02  1  2  1  2",
            "12345  4321",
            "____  ____",
        ],
    )
    def test_form_blanks_are_not_informative(self, noise):
        assert is_informative(f"Anket > SAAT\n\n{noise}") is False

    def test_a_table_of_contents_line_is_dropped(self):
        line = "4.2. Eğitim Düzeyi Mekânsal Dağılımı ................ 37"
        assert is_informative(f"Rapor > İçindekiler\n\n{line}") is False

    def test_a_number_dense_statistics_table_is_kept(self):
        """Sayı yoğunluğu gürültü işareti değil; etiketsizlik gürültü işareti."""
        table = (
            "İlçeler Nüfus Yoğunluğu (Kişi/Ha) Toplam Hane Sayısı Kira Bedeli\n"
            "Adalar 12 3.456 7.890\nBeşiktaş 98 76.543 21.098"
        )
        assert is_informative(f"Rapor > Tablo 4.1\n\n{table}") is True

    def test_the_heading_alone_does_not_make_a_chunk_informative(self):
        heading = "Çok uzun ve bilgilendirici görünen bir başlık yolu buraya yazıldı"
        assert is_informative(f"{heading}\n\nSAAT ____") is False

    def test_noise_sections_do_not_become_chunks(self):
        sections = [
            Section(heading_path=("Anket", "SAAT"), text="____  :  ____", kind="table"),
            Section(
                heading_path=("Anket", "Soru"),
                text="Hanehalkında kaç kişi yaşıyor ve kaç tanesi çalışıyor?",
                kind="prose",
            ),
        ]
        chunks = chunk_documents([document(sections)])
        assert len(chunks) == 1
        assert "Hanehalkında" in chunks[0].text

    def test_the_letter_threshold_is_the_documented_one(self):
        assert MIN_LETTERS == 25


class TestChunkMetadata:
    def test_every_chunk_carries_its_source_and_date(self):
        section = Section(
            heading_path=("Rapor",), text="Yeterince uzun bir metin parçası burada.", kind="prose"
        )
        chunk = chunk_documents([document([section])])[0]
        assert chunk.source_slug == "test"
        assert chunk.data_as_of == "2026-11-10"
        assert chunk.citation == "Test Belgesi (veri seti 1, 2026-11-10)"

    def test_chunk_ids_are_unique(self):
        sections = [
            Section(
                heading_path=("SSS", f"Soru {index}"),
                text=f"Soru: {index}?\nCevap: Yeterince uzun cevap.",
                kind="qa",
            )
            for index in range(20)
        ]
        chunks = chunk_documents([document(sections)])
        assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)

    def test_the_duplicate_source_is_recorded_on_each_chunk(self):
        doc = document(
            [
                Section(
                    heading_path=("Rapor",),
                    text="Yeterince uzun bir metin parçası burada.",
                    kind="prose",
                )
            ]
        )
        doc.also_from.append(
            SourceMeta(
                slug="kopya",
                title="Kopya",
                dataset_no=2,
                data_as_of="2026-11-10",
                url=None,
                kind="pdf",
            )
        )
        chunk = chunk_documents([doc])[0]
        assert chunk.also_from == ("kopya",)


class TestStats:
    def test_stats_report_the_distribution(self):
        sections = [
            Section(
                heading_path=("SSS", "Bir"),
                text="Soru: Bir?\nCevap: Yeterince uzun bir cevap.",
                kind="qa",
            ),
            Section(
                heading_path=("Fiyat",), text="Yıl | Bilet | Fiyat\n2017 | Tam | 2.60", kind="table"
            ),
        ]
        stats = chunk_stats(chunk_documents([document(sections)]))
        assert stats["chunks"] == 2
        assert stats["by_kind"] == {"qa": 1, "table": 1}
        assert stats["by_source"] == {"test": 2}
