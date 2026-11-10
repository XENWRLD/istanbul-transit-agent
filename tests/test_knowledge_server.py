"""`search_knowledge` aracı: sözleşmesi ve açıklaması.

Açıklama bilerek test ediliyor: araç seçimi ona dayanıyor, bu yüzden
eksik bir "NE ZAMAN KULLANMA" bölümü doküman değişikliği değil **davranış**
değişikliğidir (CLAUDE.md kuralı, karar 0006/2).

Arama testleri indeks kurulu değilse atlanıyor. İndeks kuruluysa bile
Qdrant yerel modu dizini tek sürece kilitlediği için başka bir süreç
(ingest, değerlendirme betiği) açıkken bu testler atlanır — kilidi
beklemek testleri askıda bırakır.
"""

from __future__ import annotations

import asyncio

import pytest

from istanbul_agent.rag.index import COLLECTION, QDRANT_PATH
from istanbul_agent.servers import knowledge_server as server

TOOLS = ("search_knowledge",)


def index_available() -> bool:
    """İndeks var ve açılabiliyor mu?"""
    if not QDRANT_PATH.exists():
        return False
    try:
        connection = server.open_client()
    except Exception:
        return False
    try:
        return bool(connection.collection_exists(COLLECTION))
    finally:
        connection.close()


needs_index = pytest.mark.skipif(
    not index_available(), reason="RAG indeksi kurulmamış veya başka süreç kilitlemiş"
)


@pytest.fixture(scope="module")
def listed():
    return {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}


class TestRegistration:
    def test_the_tool_is_exposed(self, listed):
        assert set(listed) == set(TOOLS)

    @pytest.mark.parametrize("name", TOOLS)
    def test_description_says_when_not_to_use_the_tool(self, listed, name):
        assert "NE ZAMAN KULLANMA" in (listed[name].description or "")

    @pytest.mark.parametrize("name", TOOLS)
    def test_description_documents_every_parameter(self, listed, name):
        description = listed[name].description or ""
        for parameter in (listed[name].inputSchema or {}).get("properties", {}):
            assert parameter in description, f"{name}: {parameter}"

    def test_the_description_names_the_indexed_sources(self, listed):
        """Agent neyin indeksli olduğunu açıklamadan öğreniyor."""
        description = listed["search_knowledge"].description or ""
        for source in ("sıkça sorulan", "bilet fiyat", "veri seti"):
            assert source in description.casefold()

    def test_the_description_sends_schedule_questions_elsewhere(self, listed):
        description = listed["search_knowledge"].description or ""
        assert "get_next_departures" in description
        assert "get_rail_travel_time" in description

    def test_only_query_is_required(self, listed):
        schema = listed["search_knowledge"].inputSchema
        assert set(schema["properties"]) == {"query", "limit", "source"}
        assert schema.get("required") == ["query"]


class TestRefusals:
    def test_an_empty_query_is_refused(self):
        result = server.search_knowledge("   ")
        assert result.ok is False
        assert "gerekli" in result.message


@needs_index
class TestSearch:
    def test_a_rule_question_finds_the_faq(self):
        result = server.search_knowledge("metroya bisikletle binebilir miyim", limit=3)
        assert result.ok is True
        assert result.passages[0].source_slug == "metro_faq"

    def test_every_passage_carries_its_source_and_date(self):
        result = server.search_knowledge("bilet fiyatı", limit=3)
        for passage in result.passages:
            assert passage.source
            assert passage.data_as_of != ""

    def test_the_current_price_outranks_the_2022_table(self):
        """Ölçümde eski tablo birinci geliyordu (karar 0009/6)."""
        result = server.search_knowledge("tam bilet kaç lira", limit=3)
        assert result.passages[0].source_slug == "metro_ticket_prices"
        assert result.passages[0].is_stale is False

    def test_a_question_about_the_past_gets_the_historical_table(self):
        result = server.search_knowledge("2019'da öğrenci bileti kaç liraydı", limit=3)
        slugs = [passage.source_slug for passage in result.passages]
        assert "ticket_prices_2017_2022" in slugs
        stale = next(p for p in result.passages if p.source_slug == "ticket_prices_2017_2022")
        assert stale.is_stale is True

    def test_the_limit_is_capped(self):
        result = server.search_knowledge("bilet", limit=99)
        assert len(result.passages) <= 10

    def test_a_source_filter_narrows_the_search(self):
        result = server.search_knowledge("bilet", limit=5, source="metro_faq")
        assert {passage.source_slug for passage in result.passages} == {"metro_faq"}

    def test_dataset_descriptions_answer_what_is_in_a_dataset(self):
        result = server.search_knowledge("İSPARK otopark verisinde ne var", limit=3)
        assert any(p.source_slug == "dataset_notes" for p in result.passages)
