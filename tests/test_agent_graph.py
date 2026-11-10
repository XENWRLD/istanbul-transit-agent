"""Agent grafiği: sunucu kaydı, uyarı turu, kaynaksız cevap işareti.

LLM çağrısı yok; burada sınanan şey modelin cevabı değil **grafiğin
davranışı**: hangi sunucular bağlı, araç çağrılmadığında ne oluyor, araç
çağrıları mesaj akışından nasıl toplanıyor.

Uyarı turu ve `unverified` işareti ölçümle geldi (karar 0010): yerel model
"metroya bisikletle binebilir miyim" sorusunda araç çağırmadan cevap
uydurdu, prompt'a kaynak gösterme kuralı eklendiğinde de kaynağı uydurdu.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from istanbul_agent.agent.graph import (
    DEFAULT_SERVERS,
    NUDGE,
    SERVER_COMMANDS,
    AgentRun,
    ToolCall,
    _collect,
    _text,
)


class TestServerRegistry:
    def test_both_servers_are_registered(self):
        assert set(DEFAULT_SERVERS) == {"transit", "knowledge"}
        for name in DEFAULT_SERVERS:
            assert name in SERVER_COMMANDS

    def test_servers_start_as_modules_with_the_project_venv(self):
        """Sunucu dışarıdan bir MCP istemcisinin başlatacağı şekilde kalkar."""
        for name in DEFAULT_SERVERS:
            command = SERVER_COMMANDS[name]
            assert command["args"][0] == "-m"
            assert command["args"][1].startswith("istanbul_agent.servers.")
            assert command["transport"] == "stdio"

    def test_the_city_server_is_not_wired_yet(self):
        """Faz 2 ertelendi; city_server dosyası yok, listede de olmamalı."""
        assert "city" not in SERVER_COMMANDS


class TestNudge:
    def test_the_nudge_restates_the_question(self):
        """İlk sürüm soruyu tekrarlamıyordu ve model 'hangi soru?' diye sordu."""
        text = NUDGE.format(question="Metroya bisikletle binebilir miyim?")
        assert "Metroya bisikletle binebilir miyim?" in text

    def test_the_nudge_forbids_inventing_a_citation(self):
        text = NUDGE.format(question="x")
        assert "kaynak" in text.casefold()

    def test_the_nudge_names_what_cannot_be_recalled(self):
        text = NUDGE.format(question="x").casefold()
        for term in ("saat", "fiyat", "kural", "durak"):
            assert term in text


class TestAgentRun:
    def test_tool_names_lists_the_calls_in_order(self):
        run = AgentRun(
            question="s",
            answer="c",
            provider="ollama",
            model="m",
            seconds=1.0,
            tool_calls=[ToolCall("geocode_place", {}), ToolCall("find_nearby_stops", {})],
        )
        assert run.tool_names == ["geocode_place", "find_nearby_stops"]

    def test_an_answer_without_tools_defaults_to_verified_only_when_set(self):
        """`unverified` yalnızca `answer()` tarafından hesaplanır."""
        run = AgentRun(question="s", answer="c", provider="ollama", model="m", seconds=1.0)
        assert run.unverified is False
        assert run.nudged is False


class TestCollect:
    def test_tool_calls_are_paired_with_their_results(self):
        messages = [
            HumanMessage("soru"),
            AIMessage(
                content="",
                tool_calls=[{"name": "geocode_place", "args": {"query": "Kadıköy"}, "id": "a"}],
            ),
            ToolMessage(content="{}", tool_call_id="a"),
            AIMessage(content="cevap"),
        ]
        calls = _collect(messages)
        assert [call.name for call in calls] == ["geocode_place"]
        assert calls[0].arguments == {"query": "Kadıköy"}
        assert calls[0].result == "{}"

    def test_calls_keep_their_order(self):
        messages = [
            AIMessage(
                content="",
                tool_calls=[{"name": "find_nearby_stops", "args": {}, "id": "1"}],
            ),
            ToolMessage(content="x", tool_call_id="1"),
            AIMessage(
                content="",
                tool_calls=[{"name": "get_lines_at_stop", "args": {}, "id": "2"}],
            ),
            ToolMessage(content="y", tool_call_id="2"),
        ]
        assert [call.name for call in _collect(messages)] == [
            "find_nearby_stops",
            "get_lines_at_stop",
        ]

    def test_no_tool_calls_is_an_empty_list(self):
        assert _collect([HumanMessage("soru"), AIMessage(content="cevap")]) == []


class TestText:
    def test_a_plain_string_passes_through(self):
        assert _text("merhaba") == "merhaba"

    def test_gemini_content_blocks_are_joined(self):
        """Gemini cevabı metin bloğu listesi döndürüyor."""
        blocks = [{"text": "bir"}, {"signature": "opaque"}, {"text": "iki"}]
        assert _text(blocks) == "bir\niki"

    def test_empty_content(self):
        assert _text(None) == ""
        assert _text([]) == ""
